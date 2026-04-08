"""Context-adaptive keybinding legend for sticky TUI.

Changes display based on the currently focused widget.
Inspired by htop/lazygit pattern.
"""

from __future__ import annotations

from textual.reactive import reactive
from textual.widgets import Static


# Predefined legend sets per context
# Brackets are escaped as \\[ for Textual markup
LEGENDS: dict[str, str] = {
    "capture": "\\[Enter] Capture  \\[Ctrl+T] Template  \\[Tab] List  \\[Esc] Cancel",
    "thought_list": "\\[Enter] Open  \\[E] Edit  \\[D] Del  \\[/] Search  \\[F] Filter  \\[Ctrl+R] Refresh",
    "filter_bar": "\\[Enter] Apply  \\[Esc] Close filters  \\[Tab] Next filter",
    "search": "\\[Enter] Open  \\[Ctrl+M] Mode  \\[Esc] Home",
    "review": "\\[Enter] Accept  \\[C] Reclassify  \\[D] Dismiss  \\[X] Delete",
    "detail": "\\[E] Edit  \\[D] Del  \\[C] Reclassify  \\[Y] Copy ID  \\[L] Related  \\[Esc] Back",
    "entities": "\\[Enter] Open  \\[M] Merge  \\[/] Filter  \\[Tab] Pane  \\[Esc] Home",
    "digest": "\\[G] Regenerate  \\[Enter] Expand topic  \\[Tab] Period  \\[Esc] Home",
    "stats": "\\[Esc] Home",
    "default": "\\[/] Search  \\[G] Digest  \\[R] Review  \\[Ctrl+R] Refresh  \\[Q] Quit",
}


class KeybindingLegend(Static):
    """Context-adaptive keybinding bar that changes based on focused widget."""

    context: reactive[str] = reactive("default")

    DEFAULT_CSS = """
    KeybindingLegend {
        dock: bottom;
        height: 1;
        background: $surface-darken-1;
        color: $text;
        padding: 0 1;
    }
    """

    def render(self) -> str:
        return LEGENDS.get(self.context, LEGENDS["default"])

    def set_context(self, context: str) -> None:
        """Set the current context for the keybinding legend."""
        self.context = context
