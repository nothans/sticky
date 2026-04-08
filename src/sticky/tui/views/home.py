"""Home view for sticky TUI.

Features:
- Capture input always at top, pre-focused on load
- Digest banner (dim) when new thoughts since last digest
- Filter bar hidden by default (toggle with F key)
- Thought list with confidence dots, category badges, timestamps
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from textual import on, work
from textual.containers import Vertical
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Input, ListView, Static

from sticky.tui.widgets.capture_input import CaptureInput
from sticky.tui.widgets.thought_row import ThoughtRow

if TYPE_CHECKING:
    from textual.app import ComposeResult

logger = logging.getLogger(__name__)


class DigestBanner(Static):
    """Shows count of new thoughts since last digest.

    Hidden when there are no new thoughts.
    """

    DEFAULT_CSS = """
    DigestBanner {
        height: 1;
        margin: 0 2;
        color: $text-muted;
        text-style: dim;
        display: none;
    }
    DigestBanner.visible {
        display: block;
    }
    """

    def set_count(self, count: int) -> None:
        """Update the banner with the new thought count."""
        if count > 0:
            self.update(
                f"{count} new thought{'s' if count != 1 else ''} "
                "since your last digest \u2014 press G"
            )
            self.add_class("visible")
        else:
            self.remove_class("visible")


_CATEGORIES = ["All", "idea", "project", "person", "meeting", "action", "reference", "journal"]


class FilterBar(Static):
    """Filter bar for narrowing thought list.

    Hidden by default; toggled with F key.
    Click the category area (x < 25) to cycle categories.
    Click the needs-review area to toggle the boolean.
    """

    DEFAULT_CSS = """
    FilterBar {
        height: 1;
        margin: 0 1;
        color: $text;
        background: $surface;
        display: none;
        padding: 0 1;
    }
    FilterBar.visible {
        display: block;
    }
    """

    category: reactive[str] = reactive("All")
    needs_review: reactive[bool] = reactive(False)

    class FiltersChanged(Message):
        """Posted when any filter value changes."""

        def __init__(self, category: str, needs_review: bool) -> None:
            self.category = category
            self.needs_review = needs_review
            super().__init__()

    def render(self) -> str:
        cat_label = self.category
        cat_style = "[bold]" if self.category != "All" else ""
        cat_end = "[/bold]" if self.category != "All" else ""
        nr_label = "Yes" if self.needs_review else "No"
        nr_style = "[bold]" if self.needs_review else ""
        nr_end = "[/bold]" if self.needs_review else ""
        return (
            f"{cat_style}Category \\[{cat_label}]{cat_end}"
            f"  {nr_style}Needs Review \\[{nr_label}]{nr_end}"
        )

    def on_click(self, event) -> None:
        """Cycle category (left region) or toggle needs_review (right region)."""
        if event.x < 25:
            idx = _CATEGORIES.index(self.category) if self.category in _CATEGORIES else 0
            self.category = _CATEGORIES[(idx + 1) % len(_CATEGORIES)]
        else:
            self.needs_review = not self.needs_review

    def watch_category(self, _old: str, _new: str) -> None:
        """Post FiltersChanged when category changes."""
        self.post_message(self.FiltersChanged(self.category, self.needs_review))

    def watch_needs_review(self, _old: bool, _new: bool) -> None:
        """Post FiltersChanged when needs_review changes."""
        self.post_message(self.FiltersChanged(self.category, self.needs_review))


class ThoughtList(ListView):
    """Scrollable list of thoughts with selection support."""

    DEFAULT_CSS = """
    ThoughtList {
        height: 1fr;
        margin: 0 0;
    }
    """


class HomeView(Static):
    """The home view with capture input and thought list."""

    DEFAULT_CSS = """
    HomeView {
        height: 1fr;
    }
    """

    _filter_category: str | None = None
    _filter_needs_review: bool = False

    class ThoughtCaptured(Message):
        """Posted when a thought is captured."""

        def __init__(self, content: str) -> None:
            self.content = content
            super().__init__()

    def compose(self) -> ComposeResult:
        yield CaptureInput()
        yield DigestBanner(id="digest-banner")
        yield FilterBar()
        yield ThoughtList(id="thought-list")

    def on_mount(self) -> None:
        """Focus the capture input on mount and load thoughts."""
        self.load_thoughts()

    @on(Input.Submitted, "#capture-input")
    def handle_capture(self, event: Input.Submitted) -> None:
        """Handle Enter in the capture input — capture a thought."""
        content = event.value.strip()
        if not content:
            return
        event.input.value = ""
        self.capture_thought(content)

    @on(FilterBar.FiltersChanged)
    def handle_filters_changed(self, event: FilterBar.FiltersChanged) -> None:
        """Update filter state and reload thoughts when filters change."""
        self._filter_category = event.category if event.category != "All" else None
        self._filter_needs_review = event.needs_review
        self.load_thoughts()

    @work(thread=True)
    def capture_thought(self, content: str) -> None:
        """Capture a thought via the service (in worker thread)."""
        try:
            svc = self.app._get_service()
            svc.capture(content=content, source="tui")
            self.app.call_from_thread(self._refresh_after_capture)
        except Exception as exc:
            logger.warning("Capture failed: %s", exc)
            self.app.call_from_thread(
                self.app.notify, f"Capture failed: {exc}", severity="error"
            )

    def _refresh_after_capture(self) -> None:
        """Refresh the thought list and status bar after capture."""
        self.load_thoughts()
        self.app.refresh_status_bar()
        self.app.notify("Thought captured!", severity="information")

    @work(thread=True)
    def load_thoughts(self) -> None:
        """Load thoughts from the service (in worker thread)."""
        try:
            svc = self.app._get_service()
            kwargs: dict = {"limit": 50, "sort": "created_at_desc"}
            if self._filter_category:
                kwargs["category"] = self._filter_category
            if self._filter_needs_review:
                kwargs["needs_review"] = True
            result = svc.list_thoughts(**kwargs)
            thoughts = result.get("thoughts", [])
            self.app.call_from_thread(self._populate_thoughts, thoughts)
        except Exception as exc:
            logger.warning("Failed to load thoughts: %s", exc)

    def _populate_thoughts(self, thoughts: list[dict]) -> None:
        """Replace the thought list contents (must be called from main thread)."""
        thought_list = self.query_one("#thought-list", ThoughtList)
        thought_list.clear()
        for t in thoughts:
            thought_list.append(ThoughtRow(t))

    @work(thread=True)
    def check_digest_banner(self) -> None:
        """Check if there are new thoughts since the last digest."""
        try:
            svc = self.app._get_service()
            # Get last digest
            rows = svc.db.execute(
                "SELECT period_end FROM digests ORDER BY created_at DESC LIMIT 1"
            ).fetchall()

            if not rows:
                # No digests yet — check if any thoughts exist
                stats = svc.db.get_stats()
                total = stats.get("thoughts", {}).get("total", 0)
                if total > 0:
                    self.app.call_from_thread(self._update_digest_banner, total)
                return

            last_end = rows[0]["period_end"]
            # Count thoughts after last digest
            count_row = svc.db.execute(
                "SELECT COUNT(*) FROM thoughts WHERE created_at > ?",
                (last_end,),
            ).fetchone()
            count = count_row[0] if count_row else 0

            if count > 0:
                self.app.call_from_thread(self._update_digest_banner, count)
        except Exception as exc:
            logger.warning("Failed to check digest banner: %s", exc)

    def _update_digest_banner(self, count: int) -> None:
        """Update the digest banner widget."""
        try:
            banner = self.query_one("#digest-banner", DigestBanner)
            banner.set_count(count)
        except Exception:
            pass

    def toggle_filter_bar(self) -> None:
        """Toggle the filter bar visibility."""
        try:
            filter_bar = self.query_one(FilterBar)
            filter_bar.toggle_class("visible")
        except Exception:
            pass

    def focus_capture_input(self) -> None:
        """Focus the capture input."""
        try:
            inp = self.query_one("#capture-input", CaptureInput)
            inp.focus()
        except Exception:
            pass
