"""Read, edit, categorize, or delete a source note."""

from __future__ import annotations

import logging

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, OptionList, Static, TextArea

logger = logging.getLogger(__name__)
CATEGORIES = ["idea", "project", "person", "meeting", "action", "reference", "journal"]


class DetailScreen(Screen):
    """All mutations use the service; failed edits retain their draft."""

    BINDINGS = [
        Binding("e", "edit", "Edit", show=False),
        Binding("d", "delete", "Delete", show=False),
        Binding("c", "category", "Category", show=False),
        Binding("ctrl+s", "save", "Save", show=False, priority=True),
        Binding("left", "prev_thought", "Previous", show=False),
        Binding("right", "next_thought", "Next", show=False),
        Binding("escape", "go_back", "Back", show=False, priority=True),
    ]
    DEFAULT_CSS = """
    DetailScreen { layout: vertical; background: #20252b; color: #dde3e8; }
    #detail-nav-bar { height: 3; margin: 0 1; }
    #nav-label { width: 1fr; height: 3; content-align: center middle; }
    #detail-body { height: 1fr; padding: 0 2; }
    #meta-content { height: auto; margin-bottom: 1; }
    #detail-content-text { height: auto; }
    #detail-edit-area { display: none; height: 1fr; min-height: 5; }
    #detail-edit-area.visible { display: block; }
    #detail-content-text.hidden { display: none; }
    #detail-status { height: auto; padding: 0 2; }
    #detail-action-bar { height: 3; }
    #category-picker { display: none; height: 9; margin: 0 2; }
    #category-picker.visible { display: block; }
    #delete-confirm { display: none; height: 5; margin: 0 2; }
    #delete-confirm.visible { display: block; }
    #delete-confirm > Horizontal { height: 3; }
    Button { min-width: 10; }
    """

    def __init__(self, thought_id: str, context: list[str] | None = None, context_index: int = 0) -> None:
        super().__init__()
        self.thought_id = thought_id
        self.context = context or [thought_id]
        self.context_index = context_index
        self._thought_data: dict | None = None
        self._edit_mode = False
        self._busy = False
        self._read_generation = 0

    def compose(self) -> ComposeResult:
        with Horizontal(id="detail-nav-bar"):
            yield Button("← Previous", id="nav-prev")
            yield Static("Loading note…", id="nav-label")
            yield Button("Next →", id="nav-next")
        with VerticalScroll(id="detail-body"):
            yield Static("", id="meta-content", markup=False)
            yield Static("", id="detail-content-text", markup=False)
            yield TextArea(id="detail-edit-area")
        yield Static("", id="detail-status", markup=False)
        with Horizontal(id="detail-action-bar"):
            yield Button("Edit (E)", id="action-edit")
            yield Button("Delete (D)", id="action-delete")
            yield Button("Category (C)", id="action-category")
            yield Button("Save (Ctrl+S)", id="action-save")
            yield Button("Back (Esc)", id="action-back")
        yield OptionList(*CATEGORIES, id="category-picker")
        with Vertical(id="delete-confirm"):
            yield Static("Delete this note permanently?")
            with Horizontal():
                yield Button("Delete", variant="error", id="delete-yes")
                yield Button("Cancel", id="delete-cancel")

    def on_mount(self) -> None:
        self._set_controls()
        self._load_thought(self.thought_id)

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action == "go_back":
            return not self._busy
        if action == "save":
            return self._edit_mode and not self._busy
        if self._edit_mode or self._busy:
            return False
        return True

    def _load_thought(self, thought_id: str) -> None:
        self._read_generation += 1
        self._read_thought(thought_id, self._read_generation)

    @work(thread=True, exclusive=True, group="detail-read")
    def _read_thought(self, thought_id: str, generation: int) -> None:
        try:
            thought = self.app._get_service().get_thought(thought_id)
            self.app.call_from_thread(self._populate_detail, thought, generation)
        except Exception as exc:
            logger.warning("Failed to load note: %s", exc)
            self.app.call_from_thread(self._load_failed, thought_id, generation, str(exc))

    def _load_failed(self, thought_id: str, generation: int, error: str) -> None:
        if self.is_mounted and thought_id == self.thought_id and generation == self._read_generation:
            self._set_status(f"Could not load note: {error}")

    def _populate_detail(self, thought: dict, generation: int | None = None) -> None:
        if not self.is_mounted or thought.get("id") != self.thought_id:
            return
        if generation is not None and generation != self._read_generation:
            return
        self._thought_data = thought
        self.query_one("#detail-content-text", Static).update(thought.get("content", ""))
        category = thought.get("category") or "Uncategorized"
        metadata = Text(f"{category} · {thought.get('source', '')}\n")
        metadata.append(f"Captured: {_format_timestamp(thought.get('created_at', ''))}\n")
        metadata.append(f"Updated: {_format_timestamp(thought.get('updated_at', ''))}\n")
        metadata.append(f"ID: {thought.get('id', '')}")
        if thought.get("source_url"):
            metadata.append(f"\nSource URL: {thought['source_url']}")
        thread = (thought.get("metadata") or {}).get("thread")
        if thread:
            metadata.append(f"\nThread: {thread}")
        self.query_one("#meta-content", Static).update(metadata)
        self.query_one("#nav-label", Static).update(f"Note {self.context_index + 1} of {len(self.context)}")
        self._set_controls()

    def _set_status(self, message: str) -> None:
        self.query_one("#detail-status", Static).update(message)

    def _set_controls(self) -> None:
        for button in self.query(Button):
            button.disabled = self._busy or self._thought_data is None
        self.query_one("#action-back", Button).disabled = self._busy
        for action in ("edit", "delete", "category"):
            self.query_one(f"#action-{action}", Button).display = not self._edit_mode
        self.query_one("#action-save", Button).display = self._edit_mode
        self.query_one("#nav-prev", Button).disabled = self._busy or self._edit_mode or self.context_index <= 0
        self.query_one("#nav-next", Button).disabled = self._busy or self._edit_mode or self.context_index >= len(self.context) - 1
        self.query_one(TextArea).disabled = self._busy

    def _close_overlays(self) -> None:
        self.query_one("#delete-confirm").remove_class("visible")
        self.query_one("#category-picker").remove_class("visible")

    def action_go_back(self) -> None:
        if self._busy:
            return
        if self._edit_mode:
            self._exit_edit_mode()
        elif self.query(".visible"):
            self._close_overlays()
            self.query_one("#action-edit", Button).focus()
        else:
            self.dismiss(self._thought_data)

    def action_edit(self) -> None:
        if self._busy or self._edit_mode or self._thought_data is None:
            return
        self._close_overlays()
        self._edit_mode = True
        editor = self.query_one(TextArea)
        editor.load_text(self._thought_data.get("content", ""))
        editor.add_class("visible")
        self.query_one("#detail-content-text").add_class("hidden")
        self._set_status("Ctrl+S saves. Escape cancels the edit.")
        self._set_controls()
        editor.focus()

    def _exit_edit_mode(self) -> None:
        self._edit_mode = False
        self.query_one(TextArea).remove_class("visible")
        self.query_one("#detail-content-text").remove_class("hidden")
        self._set_status("")
        self._set_controls()
        self.query_one("#action-edit", Button).focus()

    def action_save(self) -> None:
        if not self._edit_mode or self._busy:
            return
        content = self.query_one(TextArea).text
        if not content.strip():
            self._set_status("A note cannot be empty. Enter text before saving.")
            return
        self._begin_mutation("Saving…")
        self._mutate("update", content)

    def action_delete(self) -> None:
        if self._busy or self._edit_mode or self._thought_data is None:
            return
        self._close_overlays()
        self.query_one("#delete-confirm").add_class("visible")
        self.query_one("#delete-cancel", Button).focus()

    def action_category(self) -> None:
        if self._busy or self._edit_mode or self._thought_data is None:
            return
        self._close_overlays()
        picker = self.query_one(OptionList)
        picker.add_class("visible")
        picker.focus()

    def _begin_mutation(self, message: str) -> None:
        self._busy = True
        self._set_status(message)
        self._set_controls()

    @work(thread=True)
    def _mutate(self, operation: str, value: str | None = None) -> None:
        try:
            svc = self.app._get_service()
            if operation == "delete":
                svc.delete(self.thought_id)
                result = None
            elif operation == "update":
                result = svc.update(self.thought_id, value)
            else:
                result = svc.classify_thought(self.thought_id, value)
        except Exception as exc:
            logger.warning("%s failed: %s", operation, exc)
            self.app.call_from_thread(self._mutation_failed, operation, str(exc))
        else:
            self.app.call_from_thread(self._mutation_finished, operation, result)

    def _mutation_failed(self, operation: str, error: str) -> None:
        self._busy = False
        self._set_controls()
        recovery = "Your edit is retained; retry or cancel." if self._edit_mode else "Retry or cancel."
        self._set_status(f"{operation.capitalize()} failed: {error}. {recovery}")
        if self._edit_mode:
            self.query_one(TextArea).focus()

    def _mutation_finished(self, operation: str, result: dict | None) -> None:
        self._busy = False
        if operation == "delete":
            self.dismiss(None)
            return
        self._close_overlays()
        if self._edit_mode:
            self._exit_edit_mode()
        self._populate_detail(result)
        self._set_status("Note saved." if operation == "update" else "Category saved.")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        button_id = event.button.id
        if button_id == "delete-yes":
            self._begin_mutation("Deleting…")
            self._mutate("delete")
        elif button_id == "delete-cancel":
            self._close_overlays()
            self.query_one("#action-edit", Button).focus()
        else:
            actions = {
                "action-edit": self.action_edit,
                "action-delete": self.action_delete,
                "action-category": self.action_category,
                "action-save": self.action_save,
                "action-back": self.action_go_back,
                "nav-prev": self.action_prev_thought,
                "nav-next": self.action_next_thought,
            }
            action = actions.get(button_id)
            if action:
                action()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self._close_overlays()
        self._begin_mutation("Saving category…")
        self._mutate("category", str(event.option.prompt))

    def _navigate(self, index: int) -> None:
        if self._busy or self._edit_mode or not 0 <= index < len(self.context):
            return
        self._close_overlays()
        self.context_index = index
        self.thought_id = self.context[index]
        self._thought_data = None
        self.query_one("#detail-content-text", Static).update("")
        self.query_one("#meta-content", Static).update("")
        self.query_one("#nav-label", Static).update("Loading note…")
        self._set_status("")
        self._set_controls()
        self._load_thought(self.thought_id)

    def action_prev_thought(self) -> None:
        self._navigate(self.context_index - 1)

    def action_next_thought(self) -> None:
        self._navigate(self.context_index + 1)


def _format_timestamp(timestamp: str) -> str:
    from datetime import datetime

    try:
        return datetime.fromisoformat(timestamp).astimezone().strftime("%b %d, %Y %I:%M%p %Z")
    except (ValueError, TypeError):
        return timestamp
