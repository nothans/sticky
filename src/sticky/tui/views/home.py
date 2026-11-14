"""Capture notes and browse recent notes."""

from __future__ import annotations

import logging

from textual import on, work
from textual.app import ComposeResult
from textual.widgets import Input, ListView, Static, TabbedContent

from sticky.tui.widgets.capture_input import CaptureInput
from sticky.tui.widgets.thought_row import ThoughtRow

logger = logging.getLogger(__name__)


class ThoughtList(ListView):
    DEFAULT_CSS = "ThoughtList { height: 1fr; }"


class HomeView(Static):
    """A draft stays visible until the service confirms persistence."""

    DEFAULT_CSS = """
    HomeView { height: 1fr; }
    #capture-status { height: auto; margin: 0 2 1 2; }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._saving = False
        self._load_generation = 0

    def compose(self) -> ComposeResult:
        yield CaptureInput()
        yield Static("Enter saves a note. Select a recent note to edit or delete.", id="capture-status", markup=False)
        yield ThoughtList(id="thought-list")

    def on_mount(self) -> None:
        self.load_thoughts()

    @on(Input.Submitted, "#capture-input")
    def handle_capture(self, event: Input.Submitted) -> None:
        content = event.value
        if not content.strip() or self._saving:
            return
        self._saving = True
        event.input.disabled = True
        self.query_one("#capture-status", Static).update("Saving…")
        self.capture_thought(content)

    @work(thread=True)
    def capture_thought(self, content: str) -> None:
        try:
            self.app._get_service().capture(content=content, source="tui")
        except Exception as exc:
            logger.warning("Capture failed: %s", exc)
            self.app.call_from_thread(self._capture_finished, str(exc))
        else:
            self.app.call_from_thread(self._capture_finished, None)

    def _capture_finished(self, error: str | None) -> None:
        inp = self.query_one(CaptureInput)
        self._saving = False
        inp.disabled = False
        status = self.query_one("#capture-status", Static)
        if error is not None:
            status.update(f"Save failed: {error}. Your draft is retained; press Enter to retry.")
        else:
            inp.value = ""
            status.update("Note saved.")
            self.load_thoughts()
            self.app.refresh_status_bar()
        self.focus_capture_input()

    def load_thoughts(self) -> None:
        self._load_generation += 1
        self._load_thoughts(self._load_generation)

    @work(thread=True, exclusive=True, group="recent-notes")
    def _load_thoughts(self, generation: int) -> None:
        try:
            result = self.app._get_service().list_thoughts(sort="created_at_desc")
            self.app.call_from_thread(self._populate_thoughts, result.get("thoughts", []), generation)
        except Exception as exc:
            logger.warning("Failed to load notes: %s", exc)
            self.app.call_from_thread(self._load_failed, str(exc), generation)

    def _load_failed(self, error: str, generation: int) -> None:
        if self.is_mounted and generation == self._load_generation:
            self.app.notify(f"Could not load notes: {error}", severity="error")

    async def _populate_thoughts(self, thoughts: list[dict], generation: int) -> None:
        if not self.is_mounted or generation != self._load_generation:
            return
        thought_list = self.query_one(ThoughtList)
        await thought_list.clear()
        if generation != self._load_generation:
            return
        rows = [ThoughtRow(thought) for thought in thoughts]
        await thought_list.extend(rows)
        if generation != self._load_generation:
            # Only remove this obsolete batch, never a newer refresh's rows.
            await thought_list.remove_children(row for row in rows if row.parent is thought_list)
            return
        thought_list.index = 0 if thoughts else None

    def focus_capture_input(self) -> None:
        if not self.is_mounted or self.screen is not self.app.screen:
            return
        if self.screen.query_one("#tabs", TabbedContent).active != "home":
            return
        inp = self.query_one(CaptureInput)
        if not inp.disabled:
            self.screen.set_focus(inp)
