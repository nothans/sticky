"""Bottom status bar for sticky TUI.

Displays: 47 thoughts | Review 2 | Embed: local | LLM: cloud | [More]
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Button, Static


class StatusBar(Horizontal):
    """Bottom status bar with thought count, review count, privacy, and More button."""

    thought_count: reactive[int] = reactive(0)
    review_count: reactive[int] = reactive(0)
    embed_location: reactive[str] = reactive("local")
    llm_location: reactive[str] = reactive("cloud")

    DEFAULT_CSS = """
    StatusBar {
        dock: bottom;
        height: 3;
        max-height: 3;
        background: $surface;
        color: $text-muted;
        padding: 0;
    }

    #status-text {
        width: 1fr;
        height: 3;
        padding: 0 1;
        content-align: left middle;
        color: $text-muted;
    }

    #more-btn {
        width: 12;
        min-width: 12;
        height: 3;
        border: none;
        background: $accent;
        color: $text;
    }

    #more-btn:hover {
        background: $accent-lighten-1;
    }
    """

    class MorePressed(Message):
        """Emitted when the More button is clicked."""

    def compose(self) -> ComposeResult:
        yield Static("", id="status-text")
        yield Button("More", id="more-btn")

    def _update_text(self) -> None:
        parts = [f"{self.thought_count} thoughts"]
        if self.review_count > 0:
            parts.append(f"Review {self.review_count}")
        parts.append(f"Embed: {self.embed_location}")
        parts.append(f"LLM: {self.llm_location}")
        try:
            self.query_one("#status-text", Static).update(" | ".join(parts))
        except Exception:
            pass

    def watch_thought_count(self, value: int) -> None:
        self._update_text()

    def watch_review_count(self, value: int) -> None:
        self._update_text()

    def watch_embed_location(self, value: str) -> None:
        self._update_text()

    def watch_llm_location(self, value: str) -> None:
        self._update_text()

    def on_mount(self) -> None:
        self._update_text()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "more-btn":
            event.stop()
            self.post_message(self.MorePressed())

    def update_stats(
        self,
        thought_count: int = 0,
        review_count: int = 0,
        embed_location: str = "local",
        llm_location: str = "cloud",
    ) -> None:
        """Update all status bar values at once."""
        self.thought_count = thought_count
        self.review_count = review_count
        self.embed_location = embed_location
        self.llm_location = llm_location
