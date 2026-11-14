"""Pilot regressions for the small capture/find/edit terminal surface."""

from __future__ import annotations

import asyncio
import struct
import threading

import httpx
import pytest
from textual.widgets import Input, OptionList, Static, TabbedContent, TabPane, Tabs, TextArea

from sticky.core.embeddings import EmbeddingEngine
from sticky.core.models import Thought
from sticky.core.service import StickyService
from sticky.tui.app import StickyApp
from sticky.tui.views.detail import DetailScreen
from sticky.tui.views.home import HomeView, ThoughtList
from sticky.tui.views.search_view import SearchResultsList, SearchView
from sticky.tui.widgets.capture_input import CaptureInput
from sticky.tui.widgets.status_bar import StatusBar
from sticky.tui.widgets.thought_row import _category_badge, _relative_time


@pytest.fixture
def tui_environment(env_override, tmp_path, monkeypatch):
    """Use real service/SQLite with cheap vectors and fail on any cloud call."""
    import sticky.core.config as config

    monkeypatch.setattr(config, "_default_config_dir", lambda: tmp_path / "config")
    monkeypatch.delenv("NO_COLOR", raising=False)
    calls = {"embeddings": 0, "cloud": 0}

    def embed(engine, text):
        calls["embeddings"] += 1
        return struct.pack("384f", 1.0, *([0.0] * 383))

    def cloud(*args, **kwargs):
        calls["cloud"] += 1
        raise AssertionError("TUI must not call a cloud model")

    async def async_cloud(*args, **kwargs):
        return cloud(*args, **kwargs)

    monkeypatch.setattr(EmbeddingEngine, "embed", embed)
    monkeypatch.setattr(httpx.Client, "post", cloud)
    monkeypatch.setattr(httpx.AsyncClient, "post", async_cloud)
    return calls


async def settled(app, pilot):
    """Flush events, then wait for the actual workers and their callbacks."""
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


async def worker_started(event):
    assert await asyncio.to_thread(event.wait, 3), "worker did not reach its controlled gate"


async def wait_until(pilot, predicate, description):
    """Wait for an actual UI state without changing Textual's native timers."""
    deadline = asyncio.get_running_loop().time() + 3
    while not predicate():
        assert asyncio.get_running_loop().time() < deadline, description
        await pilot.pause(0.01)


def insert_note(app, content="Meeting at 2 pm [keep this literally]"):
    thought = Thought.create(content=content, category="meeting", source="test")
    app._get_service().db.insert_thought(thought)
    return thought


async def open_note(app, pilot, thought):
    app.push_screen(DetailScreen(thought.id, [thought.id]), callback=app._on_detail_dismissed)
    await settled(app, pilot)
    return app.screen


@pytest.mark.asyncio
async def test_startup_is_local_and_only_mounts_three_tabs(tui_environment):
    app = StickyApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await settled(app, pilot)
        assert app.title == "sticky"
        assert {pane.id for pane in app.query(TabPane)} == {"home", "search", "about"}
        assert app.query_one(TabbedContent).active == "home"
        assert app.focused is app.query_one(CaptureInput)
        assert not list(app.query("#digest-banner, #search-mode, #search-filter-bar, #more-btn"))
        assert tui_environment == {"embeddings": 0, "cloud": 0}


@pytest.mark.asyncio
async def test_populated_startup_does_not_generate_or_embed(tui_environment):
    app = StickyApp()
    insert_note(app)
    async with app.run_test(size=(120, 30)) as pilot:
        await settled(app, pilot)
        assert len(app.query_one(ThoughtList).children) == 1
        assert app.query_one(StatusBar).thought_count == 1
        assert tui_environment == {"embeddings": 0, "cloud": 0}


@pytest.mark.asyncio
async def test_failed_capture_retains_exact_draft_and_successful_retry(tui_environment, monkeypatch):
    original = StickyService.capture

    def fail(*args, **kwargs):
        raise OSError("disk write failure")

    monkeypatch.setattr(StickyService, "capture", fail)
    app = StickyApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await settled(app, pilot)
        inp = app.query_one(CaptureInput)
        draft = "    return \"保留 👩🏽‍💻 [literal]\"    "
        inp.value = draft
        await pilot.press("enter")
        await settled(app, pilot)
        assert inp.value == draft
        assert not inp.disabled
        assert app.focused is inp
        assert app._get_service().db.get_stats()["thoughts"]["total"] == 0
        assert "disk write failure" in str(app.query_one("#capture-status", Static).render())
        monkeypatch.setattr(StickyService, "capture", original)
        await pilot.press("enter")
        await settled(app, pilot)
        assert inp.value == ""
        notes = app._get_service().list_thoughts()["thoughts"]
        assert len(notes) == 1
        assert notes[0]["content"] == draft
        assert len(app.query_one(ThoughtList).children) == 1
        assert app.query_one(StatusBar).thought_count == 1
        assert "Note saved" in str(app.query_one("#capture-status", Static).render())
        assert tui_environment["cloud"] == 0


@pytest.mark.asyncio
async def test_capture_blocks_second_submit_until_persisted(tui_environment, monkeypatch):
    original = StickyService.capture
    started = threading.Event()
    release = threading.Event()
    submitted = []

    def slow_capture(self, *args, **kwargs):
        submitted.append(kwargs["content"])
        started.set()
        assert release.wait(10), "test did not release capture"
        return original(self, *args, **kwargs)

    monkeypatch.setattr(StickyService, "capture", slow_capture)
    app = StickyApp()
    try:
        async with app.run_test(size=(120, 30)) as pilot:
            await settled(app, pilot)
            inp = app.query_one(CaptureInput)
            inp.value = "Save only once"
            await pilot.press("enter")
            await pilot.pause()
            assert started.is_set()
            assert inp.disabled
            assert inp.value == "Save only once"
            await pilot.press("enter")
            assert len(submitted) == 1
            release.set()
            await settled(app, pilot)
            assert app._get_service().db.get_stats()["thoughts"]["total"] == 1
            assert inp.value == ""
            assert not inp.disabled
    finally:
        release.set()


@pytest.mark.asyncio
async def test_search_uses_default_behavior_and_escape_returns_home(tui_environment, monkeypatch):
    observed = []
    original = StickyService.search

    def search(self, **kwargs):
        observed.append(kwargs)
        return original(self, **kwargs)

    monkeypatch.setattr(StickyService, "search", search)
    app = StickyApp()
    thought = insert_note(app, "Passport expires in December")
    async with app.run_test(size=(120, 30)) as pilot:
        await settled(app, pilot)
        await pilot.press("ctrl+f")
        await pilot.pause()
        inp = app.query_one("#search-input", Input)
        assert app.focused is inp
        inp.value = "Passport"
        await pilot.press("enter")
        await settled(app, pilot)
        assert observed == [{"query": "Passport"}]
        result_list = app.query_one(SearchResultsList)
        assert [child.result["id"] for child in result_list.children] == [thought.id]
        result_list.focus()
        await pilot.press("enter")
        await settled(app, pilot)
        assert isinstance(app.screen, DetailScreen)
        await pilot.press("escape")
        await settled(app, pilot)
        assert app.query_one(TabbedContent).active == "search"
        await pilot.press("escape")
        await settled(app, pilot)
        assert app.query_one(TabbedContent).active == "home"
        assert app.focused is app.query_one(CaptureInput)
        assert tui_environment["cloud"] == 0


@pytest.mark.asyncio
async def test_search_clear_is_working_control(tui_environment):
    app = StickyApp()
    insert_note(app, "Passport expires in December")
    async with app.run_test(size=(120, 30)) as pilot:
        await settled(app, pilot)
        await pilot.press("ctrl+f")
        await pilot.pause()
        app.query_one("#search-input", Input).value = "Passport"
        await pilot.press("enter")
        await settled(app, pilot)
        assert len(app.query_one(SearchResultsList).children) == 1
        await pilot.click("#search-clear")
        await settled(app, pilot)
        assert app.query_one("#search-input", Input).value == ""
        assert not app.query_one(SearchResultsList).children


@pytest.mark.asyncio
async def test_deferred_focus_cannot_reopen_inactive_tab(tui_environment):
    app = StickyApp()
    async with app.run_test(size=(80, 24)) as pilot:
        await settled(app, pilot)
        await pilot.press("ctrl+f")
        await wait_until(pilot, lambda: app.query_one(TabbedContent).active == "search" and app.focused is app.query_one("#search-input", Input), "Search navigation did not complete")
        app._focus_home_input()  # A delayed startup callback must not steal focus.
        await settled(app, pilot)
        assert app.query_one(TabbedContent).active == "search"
        assert app.focused is app.query_one("#search-input", Input)
        await pilot.press("escape")
        await wait_until(pilot, lambda: app.query_one(TabbedContent).active == "home" and app.focused is app.query_one(CaptureInput), "Escape navigation did not complete")
        app._focus_search_input()  # A delayed Search callback must not reopen it.
        await settled(app, pilot)
        assert app.query_one(TabbedContent).active == "home"
        assert app.focused is app.query_one(CaptureInput)


@pytest.mark.asyncio
async def test_about_stays_visible_after_search(tui_environment):
    app = StickyApp()
    async with app.run_test(size=(80, 24)) as pilot:
        await settled(app, pilot)
        await pilot.press("ctrl+f")
        await pilot.click("#--content-tab-about")
        await settled(app, pilot)
        assert app.query_one(TabbedContent).active == "about"
        assert app.query_one("#about-info").is_on_screen
        await pilot.press("escape")
        await settled(app, pilot)
        assert app.query_one(TabbedContent).active == "home"


@pytest.mark.asyncio
async def test_home_list_opens_literal_content_in_detail(tui_environment):
    app = StickyApp()
    thought = insert_note(app)
    async with app.run_test(size=(120, 30)) as pilot:
        await settled(app, pilot)
        app.query_one(ThoughtList).focus()
        await pilot.press("enter")
        await settled(app, pilot)
        screen = app.screen
        assert isinstance(screen, DetailScreen)
        assert screen._thought_data["id"] == thought.id
        assert "[keep this literally]" in str(screen.query_one("#detail-content-text", Static).render())
        assert not list(screen.query("#action-ai-classify, #detail-related, #entities-content"))


@pytest.mark.asyncio
async def test_edit_typing_shortcut_letters_and_save(tui_environment):
    app = StickyApp()
    thought = insert_note(app, "Original note")
    async with app.run_test(size=(120, 30)) as pilot:
        await settled(app, pilot)
        screen = await open_note(app, pilot, thought)
        await pilot.press("e")
        editor = screen.query_one(TextArea)
        editor.load_text("")
        await pilot.press("e", "d", "c", "a", "l", "y")
        assert editor.text == "edcaly"
        await pilot.press("ctrl+s")
        await settled(app, pilot)
        assert app._get_service().db.get_thought(thought.id).content == "edcaly"
        assert not screen._edit_mode
        assert tui_environment["cloud"] == 0


@pytest.mark.asyncio
async def test_edit_escape_cancels_without_changing_saved_note(tui_environment):
    app = StickyApp()
    thought = insert_note(app, "Original note")
    async with app.run_test(size=(120, 30)) as pilot:
        await settled(app, pilot)
        screen = await open_note(app, pilot, thought)
        await pilot.press("e")
        screen.query_one(TextArea).load_text("Unsaved edit")
        await pilot.press("escape")
        assert not screen._edit_mode
        assert app._get_service().db.get_thought(thought.id).content == "Original note"
        assert isinstance(app.screen, DetailScreen)
        await pilot.press("escape")
        assert not isinstance(app.screen, DetailScreen)


@pytest.mark.asyncio
async def test_failed_edit_retains_draft_until_retry(tui_environment, monkeypatch):
    original = StickyService.update

    def fail(*args, **kwargs):
        raise OSError("update write failed")

    monkeypatch.setattr(StickyService, "update", fail)
    app = StickyApp()
    thought = insert_note(app, "Original note")
    async with app.run_test(size=(120, 30)) as pilot:
        await settled(app, pilot)
        screen = await open_note(app, pilot, thought)
        await pilot.press("e")
        screen.query_one(TextArea).load_text("Changed note")
        await pilot.press("ctrl+s")
        await settled(app, pilot)
        assert screen._edit_mode
        assert screen.query_one(TextArea).text == "Changed note"
        assert not screen.query_one(TextArea).disabled
        assert app._get_service().db.get_thought(thought.id).content == "Original note"
        monkeypatch.setattr(StickyService, "update", original)
        await pilot.press("ctrl+s")
        await settled(app, pilot)
        assert app._get_service().db.get_thought(thought.id).content == "Changed note"


@pytest.mark.asyncio
async def test_manual_category_uses_service_without_cloud(tui_environment):
    app = StickyApp()
    thought = insert_note(app)
    async with app.run_test(size=(120, 30)) as pilot:
        await settled(app, pilot)
        screen = await open_note(app, pilot, thought)
        await pilot.press("c")
        picker = screen.query_one(OptionList)
        assert app.focused is picker
        picker.highlighted = 0
        await pilot.press("enter")
        await settled(app, pilot)
        assert app._get_service().db.get_thought(thought.id).category == "idea"
        assert tui_environment["cloud"] == 0


@pytest.mark.asyncio
async def test_delete_requires_confirmation_and_cancel_preserves_note(tui_environment):
    app = StickyApp()
    thought = insert_note(app)
    async with app.run_test(size=(120, 30)) as pilot:
        await settled(app, pilot)
        screen = await open_note(app, pilot, thought)
        await pilot.press("d")
        assert "visible" in screen.query_one("#delete-confirm").classes
        await pilot.press("escape")
        assert app._get_service().db.get_thought(thought.id) is not None
        await pilot.press("d")
        await pilot.click("#delete-yes")
        await settled(app, pilot)
        assert app._get_service().db.get_thought(thought.id) is None
        assert not isinstance(app.screen, DetailScreen)
        assert not app.query_one(ThoughtList).children
        assert app.query_one(StatusBar).thought_count == 0


@pytest.mark.asyncio
async def test_context_navigation_and_disabled_boundaries(tui_environment):
    app = StickyApp()
    first = insert_note(app, "First note")
    second = insert_note(app, "Second note")
    async with app.run_test(size=(120, 30)) as pilot:
        await settled(app, pilot)
        app.push_screen(DetailScreen(first.id, [first.id, second.id]))
        await settled(app, pilot)
        screen = app.screen
        assert screen.query_one("#nav-prev").disabled
        await pilot.press("right")
        await settled(app, pilot)
        assert screen._thought_data["id"] == second.id
        assert screen.query_one("#nav-next").disabled
        await pilot.press("left")
        await settled(app, pilot)
        assert screen._thought_data["id"] == first.id


def test_retained_row_helpers():
    assert "idea" in _category_badge("idea").plain
    assert "?" in _category_badge(None).plain
    assert _relative_time("not-a-date") == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("destination", ["search", "detail"])
async def test_capture_completion_preserves_current_navigation(tui_environment, monkeypatch, destination):
    app = StickyApp()
    existing = insert_note(app, "Already saved note")
    started, release = threading.Event(), threading.Event()
    original = StickyService.capture

    def slow_capture(self, *args, **kwargs):
        started.set()
        assert release.wait(8)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(StickyService, "capture", slow_capture)
    try:
        async with app.run_test(size=(80, 24)) as pilot:
            await settled(app, pilot)
            app.query_one(CaptureInput).value = "Finish this save locally"
            await pilot.press("enter")
            await worker_started(started)
            if destination == "search":
                await pilot.press("ctrl+f")
                await pilot.pause()
                current_input = app.query_one("#search-input", Input)
                current_input.value = "Keep this search draft"
                assert app.focused is current_input
            else:
                app.push_screen(DetailScreen(existing.id))
                await pilot.pause()
            current_screen = app.screen
            tabs = app.query_one(TabbedContent)
            old_pane = app.query_one("#home" if destination == "search" else "#search", TabPane)
            tabs.post_message(TabPane.Focused(old_pane))
            app._focus_home_input()
            release.set()
            await settled(app, pilot)
            assert app.screen is current_screen
            assert app._get_service().stats()["thoughts"]["total"] == 2
            if destination == "search":
                assert tabs.active == "search"
                assert app.focused is current_input
                assert current_input.value == "Keep this search draft"
                assert current_input.is_on_screen
            else:
                assert tabs.active == "home"
                assert app.screen._thought_data["id"] == existing.id
                assert app.focused is not app.query_one(CaptureInput)
                assert app.focused.screen is current_screen
    finally:
        release.set()


@pytest.mark.asyncio
@pytest.mark.parametrize("old_fails", [False, True])
async def test_obsolete_home_refresh_cannot_replace_newer_list(tui_environment, monkeypatch, old_fails):
    app = StickyApp()
    first = insert_note(app, "Earlier note")
    started, release = threading.Event(), threading.Event()
    completed = asyncio.Event()
    original_list = StickyService.list_thoughts
    original_populate = HomeView._populate_thoughts
    original_failure = HomeView._load_failed
    first_request = True

    def snapshots(self, **kwargs):
        nonlocal first_request
        result = original_list(self, **kwargs)
        if first_request:
            first_request = False
            started.set()
            assert release.wait(8)
            if old_fails:
                raise OSError("obsolete refresh failure")
        return result

    async def observe_population(self, thoughts, generation):
        await original_populate(self, thoughts, generation)
        if generation == old_generation:
            completed.set()

    def observe_failure(self, error, generation):
        original_failure(self, error, generation)
        if generation == old_generation:
            completed.set()

    try:
        async with app.run_test(size=(80, 24)) as pilot:
            await settled(app, pilot)
            home = app.query_one(HomeView)
            monkeypatch.setattr(StickyService, "list_thoughts", snapshots)
            monkeypatch.setattr(HomeView, "_populate_thoughts", observe_population)
            monkeypatch.setattr(HomeView, "_load_failed", observe_failure)
            notices = []
            monkeypatch.setattr(app, "notify", lambda message, **kwargs: notices.append(message))
            home.load_thoughts()
            old_generation = home._load_generation
            await worker_started(started)
            second = insert_note(app, "Newly saved note")
            home.load_thoughts()
            await settled(app, pilot)
            assert {row.thought["id"] for row in app.query_one(ThoughtList).children} == {first.id, second.id}
            release.set()
            await asyncio.wait_for(completed.wait(), 3)
            await pilot.pause()
            assert {row.thought["id"] for row in app.query_one(ThoughtList).children} == {first.id, second.id}
            assert notices == []
    finally:
        release.set()


@pytest.mark.asyncio
async def test_failed_search_clears_previous_matches_and_allows_retry(tui_environment, monkeypatch):
    app = StickyApp()
    thought = insert_note(app, "Passport renewal in December")
    original = StickyService.search

    def fail_new_query(self, query, **kwargs):
        if query == "medical appointment":
            raise OSError("search failure")
        return original(self, query=query, **kwargs)

    monkeypatch.setattr(StickyService, "search", fail_new_query)
    async with app.run_test(size=(80, 24)) as pilot:
        await settled(app, pilot)
        await pilot.press("ctrl+f")
        await pilot.pause()
        inp = app.query_one("#search-input", Input)
        inp.value = "Passport"
        await pilot.press("enter")
        await settled(app, pilot)
        assert [row.result["id"] for row in app.query_one(SearchResultsList).children] == [thought.id]
        inp.value = "medical appointment"
        await pilot.press("enter")
        await settled(app, pilot)
        assert not app.query_one(SearchResultsList).children
        assert inp.value == "medical appointment"
        assert "Search failed" in str(app.query_one("#search-status", Static).render())
        monkeypatch.setattr(StickyService, "search", original)
        await pilot.press("enter")
        await settled(app, pilot)
        assert "Search failed" not in str(app.query_one("#search-status", Static).render())


@pytest.mark.asyncio
@pytest.mark.parametrize("old_fails", [False, True])
async def test_old_detail_read_cannot_replace_a_fresh_read_of_same_note(tui_environment, monkeypatch, old_fails):
    app = StickyApp()
    first = insert_note(app, "Old first note content")
    second = insert_note(app, "Second note")
    started, release = threading.Event(), threading.Event()
    completed = asyncio.Event()
    original_read = StickyService.get_thought
    original_populate = DetailScreen._populate_detail
    original_failure = DetailScreen._load_failed
    first_request = True

    def old_snapshot(self, thought_id):
        nonlocal first_request
        snapshot = original_read(self, thought_id)
        if thought_id == first.id and first_request:
            first_request = False
            started.set()
            assert release.wait(8)
            if old_fails:
                raise OSError("obsolete first note failure")
        return snapshot

    def observe_population(self, thought, generation=None):
        original_populate(self, thought, generation)
        if generation == 1:
            completed.set()

    def observe_failure(self, thought_id, generation, error):
        original_failure(self, thought_id, generation, error)
        if generation == 1:
            completed.set()

    monkeypatch.setattr(StickyService, "get_thought", old_snapshot)
    monkeypatch.setattr(DetailScreen, "_populate_detail", observe_population)
    monkeypatch.setattr(DetailScreen, "_load_failed", observe_failure)
    try:
        async with app.run_test(size=(80, 24)) as pilot:
            await settled(app, pilot)
            app.push_screen(DetailScreen(first.id, [first.id, second.id]))
            await worker_started(started)
            screen = app.screen
            await pilot.press("right")
            await settled(app, pilot)
            assert screen._thought_data["id"] == second.id
            app._get_service().update(first.id, "Fresh first note content")
            await pilot.press("left")
            await settled(app, pilot)
            assert screen._thought_data["content"] == "Fresh first note content"
            release.set()
            await asyncio.wait_for(completed.wait(), 3)
            await pilot.pause()
            assert screen._thought_data["content"] == "Fresh first note content"
            assert str(screen.query_one("#detail-status", Static).render()) == ""
    finally:
        release.set()


@pytest.mark.asyncio
@pytest.mark.parametrize("phase,new_query", [("clear", False), ("extend", False), ("extend", True)])
async def test_clear_invalidates_pending_result_population(tui_environment, monkeypatch, phase, new_query):
    app = StickyApp()
    old_note = insert_note(app, "Passport renewal in December")
    new_note = insert_note(app, "Dentist appointment next week")
    started, release = asyncio.Event(), asyncio.Event()
    original = getattr(SearchResultsList, phase)
    first_request = True

    async def delayed_update(self, *args):
        nonlocal first_request
        if first_request:
            first_request = False
            if phase == "clear":
                await original(self, *args)
            started.set()
            await release.wait()
            if phase == "extend":
                await original(self, *args)
        else:
            await original(self, *args)

    async with app.run_test(size=(80, 24)) as pilot:
        await settled(app, pilot)
        await pilot.press("ctrl+f")
        await pilot.pause()
        monkeypatch.setattr(SearchResultsList, phase, delayed_update)
        inp = app.query_one("#search-input", Input)
        inp.value = "Passport"
        app.query_one(SearchView)._start_search("Passport")
        await asyncio.wait_for(started.wait(), 3)
        try:
            await pilot.click("#search-clear")
            assert inp.value == ""
            assert not app.query_one(SearchResultsList).children
            if new_query:
                inp.value = "Dentist"
                await pilot.press("enter")
                await settled(app, pilot)
                assert [row.result["id"] for row in app.query_one(SearchResultsList).children] == [new_note.id]
            release.set()
            await settled(app, pilot)
            ids = [row.result["id"] for row in app.query_one(SearchResultsList).children]
            assert ids == ([new_note.id] if new_query else [])
            assert old_note.id not in ids
            message = str(app.query_one("#search-status", Static).render())
            assert message == ("1 note found. Select a note to open it." if new_query else "Enter searches your notes. Escape returns Home.")
        finally:
            release.set()


@pytest.mark.asyncio
async def test_long_unicode_edit_preserves_content_and_displays_attribution(tui_environment):
    app = StickyApp()
    content = "猫 🐈 café e\u0301 👩🏽‍💻 [literal] <html>\n" + "保留每一个字和换行。" * 100
    edited = "    " + content + "\n\n"
    thought = Thought.create(
        content=content, source="import",
        source_url="https://example.test/original/article?tag=[literal]",
        metadata={"thread": "Source thread [literal] 👩🏽‍💻", "custom": "keep attribution"},
    )
    app._get_service().db.insert_thought(thought)
    async with app.run_test(size=(80, 24)) as pilot:
        await settled(app, pilot)
        screen = await open_note(app, pilot, thought)
        assert screen._thought_data["content"] == content
        meta_text = str(screen.query_one("#meta-content", Static).render())
        assert thought.source_url in meta_text
        assert thought.metadata["thread"] in meta_text
        await pilot.press("e")
        screen.query_one(TextArea).load_text(edited)
        await pilot.press("ctrl+s")
        await settled(app, pilot)
        saved = app._get_service().get_thought(thought.id)
        assert saved["content"] == edited
        assert saved["source_url"] == thought.source_url
        assert saved["metadata"] == thought.metadata


@pytest.mark.asyncio
async def test_failed_delete_remains_retryable(tui_environment, monkeypatch):
    app = StickyApp()
    thought = insert_note(app)
    original = StickyService.delete
    attempts = 0

    def fail_once(self, thought_id):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("delete disk failure")
        return original(self, thought_id)

    monkeypatch.setattr(StickyService, "delete", fail_once)
    async with app.run_test(size=(80, 24)) as pilot:
        await settled(app, pilot)
        screen = await open_note(app, pilot, thought)
        await pilot.press("d")
        await pilot.click("#delete-yes")
        await settled(app, pilot)
        assert app._get_service().db.get_thought(thought.id) is not None
        assert not screen._busy
        assert "delete disk failure" in str(screen.query_one("#detail-status", Static).render())
        assert "visible" in screen.query_one("#delete-confirm").classes
        # Textual ignores clicks during the button's native pressed effect.
        delete_button = screen.query_one("#delete-yes")
        await wait_until(pilot, lambda: not delete_button.has_class("-active"), "Delete retry button did not become ready")
        await pilot.click("#delete-yes")
        await settled(app, pilot)
        assert app._get_service().db.get_thought(thought.id) is None
        assert not isinstance(app.screen, DetailScreen)
        assert attempts == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("destination", ["home", "search"])
async def test_queued_pane_focus_cannot_reverse_explicit_navigation(tui_environment, destination):
    app = StickyApp()
    async with app.run_test(size=(80, 24)) as pilot:
        await settled(app, pilot)
        tabs = app.query_one(TabbedContent)
        if destination == "home":
            await pilot.press("ctrl+f")
            await settled(app, pilot)
            old_pane = app.query_one("#search", TabPane)
            await pilot.press("escape")
            intended = app.query_one(CaptureInput)
        else:
            old_pane = app.query_one("#home", TabPane)
            await pilot.press("ctrl+f")
            intended = app.query_one("#search-input", Input)
        # This is the real queued message that previously undid the key action.
        tabs.post_message(TabPane.Focused(old_pane))
        await settled(app, pilot)
        assert tabs.active == destination
        assert app.focused is intended
        assert intended.is_on_screen


@pytest.mark.asyncio
async def test_header_clicks_and_arrow_keys_still_change_tabs(tui_environment):
    app = StickyApp()
    async with app.run_test(size=(80, 24)) as pilot:
        await settled(app, pilot)
        tabs = app.query_one(TabbedContent)
        for tab_id in ("search", "about", "home"):
            await pilot.click(f"#--content-tab-{tab_id}")
            await settled(app, pilot)
            assert tabs.active == tab_id
            assert app.focused.is_on_screen
        header = tabs.query_one(Tabs)
        for key, tab_id in (("right", "search"), ("right", "about"), ("left", "search")):
            header.focus()
            await wait_until(pilot, lambda: app.focused is header, "Header tabs did not focus")
            await pilot.press(key)
            await settled(app, pilot)
            assert tabs.active == tab_id
            assert app.focused.is_on_screen
