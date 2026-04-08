"""Settings view for sticky TUI.

Config editor with three-column table: KEY | VALUE | SOURCE.
Pushable as a screen (accessible via command palette, not a tab).

Features:
- Source column shows where value comes from: env, config file, default
- API keys masked (e.g., sk-or-v1-****)
- Enter to edit a value inline
- R to reset to default
- ? to show inline docs for selected key
- Esc to dismiss and return to previous screen
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from textual import on, work
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import DataTable, Input, Static

if TYPE_CHECKING:
    from textual.app import ComposeResult

logger = logging.getLogger(__name__)


# Documentation strings for each config key
_CONFIG_DOCS: dict[str, str] = {
    "data_dir": "Directory for all sticky data (DB, exports). Platform-specific default.",
    "embedding_model": "Sentence-transformers model for local embeddings. "
    "Locked after first thought (changing would invalidate existing embeddings).",
    "embedding_dimensions": "Dimensions of the embedding vectors. "
    "Determined by the embedding model. Locked after first thought.",
    "confidence_threshold": "Minimum confidence score (0.0-1.0) for auto-accepting "
    "a classification. Below this, thoughts go to Review.",
    "search_vector_weight": "Weight for vector (semantic) search in hybrid mode. "
    "Combined with search_fts_weight (should sum to 1.0).",
    "search_fts_weight": "Weight for FTS (keyword) search in hybrid mode. "
    "Combined with search_vector_weight (should sum to 1.0).",
    "search_mode": "Default search mode: hybrid, vector, or fts.",
    "openrouter_api_key": "API key for OpenRouter. Set via STICKY_OPENROUTER_API_KEY env var. "
    "Required for classification and digest generation.",
    "openrouter_model": "LLM model to use via OpenRouter for classification and digests.",
    "digest_default_period": "Default time period for digest generation: day, week, or month.",
    "default_search_limit": "Default number of results for search queries.",
    "default_list_limit": "Default page size for thought listings.",
    "tui_default_view": "Initial TUI view: home, digest, or auto (switches to digest if new thoughts).",
    "tui_show_filter_bar": "Whether the filter bar on the home view is visible by default.",
    "tui_score_hint_shown": "Whether the score hint has been shown to the user.",
    "review_auto_resolve_days": "Days after which unresolved review items are auto-resolved.",
    "privacy_show_in_status": "Show privacy indicators (LOCAL/CLOUD) in the status bar.",
}

# Keys that are locked after first thought is captured
_LOCKED_KEYS = {"embedding_model", "embedding_dimensions"}

# Source style colors
_SOURCE_STYLES: dict[str, str] = {
    "env": "green bold",
    "config file": "yellow",
    "default": "dim",
}


class SettingsTable(DataTable):
    """DataTable showing config keys, values, and sources."""

    DEFAULT_CSS = """
    SettingsTable {
        height: 1fr;
    }
    """


class SettingsScreen(Screen):
    """Settings screen — pushable config editor.

    Shows all config values with their sources.
    Allows inline editing and reset to defaults.
    """

    BINDINGS = [
        Binding("escape", "dismiss_screen", "Home", show=True),
        Binding("enter", "edit_value", "Edit", show=True),
        Binding("r", "reset_value", "Reset", show=True),
        Binding("question_mark", "show_docs", "Docs", show=True),
    ]

    DEFAULT_CSS = """
    SettingsScreen {
        layout: vertical;
    }

    #settings-header {
        height: 1;
        padding: 0 2;
        text-style: bold;
        background: $surface;
    }

    #settings-table {
        height: 1fr;
    }

    #settings-edit-container {
        height: auto;
        max-height: 3;
        padding: 0 2;
        display: none;
    }

    #settings-edit-container.visible {
        display: block;
    }

    #settings-docs {
        height: auto;
        max-height: 3;
        padding: 0 2;
        color: $text-muted;
        text-style: italic;
        display: none;
    }

    #settings-docs.visible {
        display: block;
    }

    #settings-legend {
        dock: bottom;
        height: 1;
        background: $surface-darken-1;
        color: $text;
        padding: 0 1;
    }
    """

    _config_keys: list[str] = []
    _editing_key: str | None = None
    _has_thoughts: bool = False

    def compose(self) -> ComposeResult:
        yield Static("Settings", id="settings-header")
        yield SettingsTable(id="settings-table")
        with Vertical(id="settings-edit-container"):
            yield Input(placeholder="Enter new value...", id="settings-edit-input")
        yield Static("", id="settings-docs")
        yield Static(
            "\\[Enter] Edit  \\[R] Reset  \\[?] Docs  \\[Esc] Home",
            id="settings-legend",
        )

    def on_mount(self) -> None:
        """Set up table and load config data."""
        table = self.query_one("#settings-table", SettingsTable)
        table.add_columns("KEY", "VALUE", "SOURCE")
        table.cursor_type = "row"
        self.load_config()

    @work(thread=True)
    def load_config(self) -> None:
        """Load config values from service (worker thread)."""
        try:
            svc = self.app._get_service()
            config_display = svc.get_config_display()

            # Check if any thoughts exist (for locking embedding keys)
            stats = svc.db.get_stats()
            has_thoughts = stats.get("thoughts", {}).get("total", 0) > 0

            self.app.call_from_thread(
                self._populate_table, config_display, has_thoughts
            )
        except Exception as exc:
            logger.warning("Failed to load config: %s", exc)

    def _populate_table(
        self, config_display: dict[str, dict[str, Any]], has_thoughts: bool
    ) -> None:
        """Populate the settings table (main thread)."""
        self._has_thoughts = has_thoughts
        table = self.query_one("#settings-table", SettingsTable)
        table.clear()
        self._config_keys = []

        for key, info in config_display.items():
            value = info.get("value", "")
            source = info.get("source", "default")

            # Format value display
            display_value = str(value)
            if len(display_value) > 50:
                display_value = display_value[:47] + "..."

            # Mark locked keys
            is_locked = key in _LOCKED_KEYS and has_thoughts
            if is_locked:
                display_value = f"{display_value} [locked]"

            self._config_keys.append(key)
            table.add_row(key, display_value, source, key=key)

    def _get_selected_key(self) -> str | None:
        """Get the config key for the currently selected row."""
        try:
            table = self.query_one("#settings-table", SettingsTable)
            cursor_row = table.cursor_row
            if cursor_row is not None and 0 <= cursor_row < len(self._config_keys):
                return self._config_keys[cursor_row]
        except Exception:
            pass
        return None

    def action_edit_value(self) -> None:
        """Show inline edit input for the selected config key."""
        key = self._get_selected_key()
        if key is None:
            return

        # Check if key is locked
        if key in _LOCKED_KEYS and self._has_thoughts:
            self.app.notify(
                f"{key} is locked (changing would invalidate embeddings).",
                severity="warning",
            )
            return

        self._editing_key = key

        # Show edit container
        try:
            container = self.query_one("#settings-edit-container")
            container.add_class("visible")
            edit_input = self.query_one("#settings-edit-input", Input)
            edit_input.placeholder = f"New value for {key}..."
            edit_input.value = ""
            edit_input.focus()
        except Exception:
            pass

        # Hide docs if showing
        try:
            docs = self.query_one("#settings-docs", Static)
            docs.remove_class("visible")
        except Exception:
            pass

    @on(Input.Submitted, "#settings-edit-input")
    def handle_edit_submit(self, event: Input.Submitted) -> None:
        """Handle edit input submission — save the new value."""
        new_value = event.value.strip()
        if not new_value or self._editing_key is None:
            self._hide_edit()
            return

        key = self._editing_key
        self._hide_edit()
        self._save_config_value(key, new_value)

    def _hide_edit(self) -> None:
        """Hide the edit input container."""
        self._editing_key = None
        try:
            container = self.query_one("#settings-edit-container")
            container.remove_class("visible")
        except Exception:
            pass
        # Re-focus the table
        try:
            table = self.query_one("#settings-table", SettingsTable)
            table.focus()
        except Exception:
            pass

    @work(thread=True)
    def _save_config_value(self, key: str, raw_value: str) -> None:
        """Save a config value via the service (worker thread)."""
        try:
            svc = self.app._get_service()

            # Convert to appropriate type
            field_info = svc.config.model_fields.get(key)
            if field_info:
                annotation = field_info.annotation
                if annotation is bool:
                    value: Any = raw_value.lower() in ("true", "1", "yes")
                elif annotation is int:
                    value = int(raw_value)
                elif annotation is float:
                    value = float(raw_value)
                else:
                    value = raw_value
            else:
                value = raw_value

            svc.set_config_value(key, value)

            self.app.call_from_thread(
                self.app.notify,
                f"Updated {key}",
                severity="information",
            )
            # Reload the table
            self.load_config()
        except Exception as exc:
            logger.warning("Failed to save config value: %s", exc)
            self.app.call_from_thread(
                self.app.notify,
                f"Failed to save {key}: {exc}",
                severity="error",
            )

    def action_reset_value(self) -> None:
        """Reset the selected config key to its default value."""
        key = self._get_selected_key()
        if key is None:
            return

        if key in _LOCKED_KEYS and self._has_thoughts:
            self.app.notify(
                f"{key} is locked (changing would invalidate embeddings).",
                severity="warning",
            )
            return

        self._reset_config_value(key)

    @work(thread=True)
    def _reset_config_value(self, key: str) -> None:
        """Reset a config value to default (worker thread)."""
        try:
            from sticky.core.config import StickyConfig

            defaults = StickyConfig()
            default_value = getattr(defaults, key, None)
            if default_value is None:
                return

            svc = self.app._get_service()
            svc.set_config_value(key, default_value)

            self.app.call_from_thread(
                self.app.notify,
                f"Reset {key} to default",
                severity="information",
            )
            self.load_config()
        except Exception as exc:
            logger.warning("Failed to reset config value: %s", exc)
            self.app.call_from_thread(
                self.app.notify,
                f"Failed to reset {key}: {exc}",
                severity="error",
            )

    def action_show_docs(self) -> None:
        """Show inline documentation for the selected config key."""
        key = self._get_selected_key()
        if key is None:
            return

        doc = _CONFIG_DOCS.get(key, f"No documentation available for {key}.")

        try:
            docs_widget = self.query_one("#settings-docs", Static)
            docs_widget.update(f"  {key}: {doc}")
            docs_widget.toggle_class("visible")
        except Exception:
            pass

    def action_dismiss_screen(self) -> None:
        """Dismiss the settings screen."""
        self.dismiss()
