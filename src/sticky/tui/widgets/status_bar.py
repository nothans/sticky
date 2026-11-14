"""Local note count, without optional feature or cloud status controls."""

from textual.reactive import reactive
from textual.widgets import Static


class StatusBar(Static):
    thought_count: reactive[int] = reactive(0)
    DEFAULT_CSS = """
    StatusBar { dock: bottom; height: 1; padding: 0 1; background: #252c34; color: #dde3e8; }
    """

    def render(self) -> str:
        return f"{self.thought_count} notes | Local | Ctrl+F Search | Esc Home | Ctrl+R Refresh | Ctrl+C Quit"

    def update_stats(self, thought_count: int) -> None:
        self.thought_count = thought_count
