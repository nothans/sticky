"""Command palette for sticky TUI.

Simple filterable command list accessible via Ctrl+K.
Lists available actions: Settings, Export, Import, Generate Digest, etc.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from textual import on
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, ListItem, ListView, Static

if TYPE_CHECKING:
    from textual.app import ComposeResult

logger = logging.getLogger(__name__)


# Available commands: (display label, action key)
_COMMANDS: list[tuple[str, str]] = [
    ("Settings             Configure sticky", "settings"),
    ("Generate Digest      Create a new digest", "digest"),
    ("Export Markdown      Export thoughts as .md files", "export_markdown"),
    ("Export JSON          Export thoughts as JSON", "export_json"),
    ("Import               Import thoughts from file", "import_data"),
    ("Stats                View stats dashboard", "stats"),
    ("Review               Review low-confidence thoughts", "review"),
    ("Search               Search your thoughts", "search"),
]


class CommandItem(ListItem):
    """A single command in the palette."""

    DEFAULT_CSS = """
    CommandItem {
        height: 1;
        padding: 0 1;
    }
    CommandItem > Static {
        height: 1;
    }
    """

    def __init__(self, label: str, action_key: str) -> None:
        super().__init__()
        self.command_label = label
        self.action_key = action_key

    def compose(self) -> ComposeResult:
        yield Static(self.command_label)


class CommandList(ListView):
    """Filterable list of commands."""

    DEFAULT_CSS = """
    CommandList {
        height: 1fr;
        max-height: 12;
    }
    """


class CommandPalette(ModalScreen):
    """Simple command palette listing available actions.

    Ctrl+K opens this modal. Type to filter, Enter to execute.
    """

    BINDINGS = [
        Binding("escape", "dismiss_palette", "Close"),
    ]

    DEFAULT_CSS = """
    CommandPalette {
        align: center middle;
    }

    #palette-container {
        width: 60;
        max-height: 18;
        border: solid $accent;
        background: $surface;
        padding: 1 2;
    }

    #palette-title {
        height: 1;
        text-style: bold;
        color: $accent;
    }

    #palette-input {
        margin: 1 0;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="palette-container"):
            yield Static("Commands", id="palette-title")
            yield Input(
                placeholder="Type to filter...",
                id="palette-input",
            )
            yield CommandList(id="palette-list")

    def on_mount(self) -> None:
        """Populate commands and focus the input."""
        self._populate_commands()
        try:
            inp = self.query_one("#palette-input", Input)
            inp.focus()
        except Exception:
            pass

    def _populate_commands(self, filter_text: str = "") -> None:
        """Populate the command list, optionally filtered."""
        try:
            cmd_list = self.query_one("#palette-list", CommandList)
            cmd_list.clear()
            for label, action_key in _COMMANDS:
                if filter_text and filter_text.lower() not in label.lower():
                    continue
                cmd_list.append(CommandItem(label, action_key))
        except Exception:
            pass

    @on(Input.Changed, "#palette-input")
    def handle_filter_change(self, event: Input.Changed) -> None:
        """Filter commands as user types."""
        self._populate_commands(event.value.strip())

    @on(ListView.Selected, "#palette-list")
    def handle_command_select(self, event: ListView.Selected) -> None:
        """Execute the selected command."""
        item = event.item
        if isinstance(item, CommandItem):
            action_key = item.action_key
            self.dismiss(action_key)

    def action_dismiss_palette(self) -> None:
        """Close the command palette."""
        self.dismiss(None)
