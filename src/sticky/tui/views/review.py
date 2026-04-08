"""Review view for sticky TUI.

Shows low-confidence thoughts needing user review.

Features:
- Header: "Low-confidence thoughts need your input. N remaining."
- Each review item shows confidence dot, category badge, content, entities
- Per-item actions: Accept, Reclassify, Dismiss, Delete
- Items removed after action, counter decrements
- Empty state: "All caught up. No thoughts need review."
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import (
    Button,
    Label,
    ListItem,
    ListView,
    OptionList,
    Static,
)

from sticky.tui.widgets.thought_row import (
    _category_badge,
    _confidence_dot,
    _CATEGORY_COLORS,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

CATEGORIES = ["idea", "project", "person", "meeting", "action", "reference", "journal"]


def _confidence_score_text(confidence: float | None) -> Text:
    """Return colored confidence score text."""
    if confidence is None:
        return Text("N/A", style="#555555")
    if confidence < 0.6:
        color = "#555555"
    elif confidence < 0.8:
        color = "#d4a843"
    else:
        color = "#4ec9b0"
    return Text(f"[{confidence:.2f}]", style=color)


class ReviewItem(ListItem):
    """A single review item showing a low-confidence thought.

    Displays confidence dot, category badge, confidence score, content,
    entities, suggested category, and per-item action hints.
    """

    DEFAULT_CSS = """
    ReviewItem {
        height: auto;
        min-height: 4;
        padding: 0 1;
        border-bottom: dashed $surface-darken-2;
    }
    ReviewItem > Static {
        height: auto;
    }
    ReviewItem.--highlight {
        background: $surface-lighten-1;
    }
    """

    def __init__(self, thought: dict, **kwargs) -> None:
        super().__init__(**kwargs)
        self.thought = thought

    def compose(self) -> ComposeResult:
        t = self.thought
        confidence = t.get("confidence")
        category = t.get("category")
        content = t.get("content", "").replace("\n", " ")

        # Line 1: confidence dot + ! + category badge + confidence score + content
        line1 = Text()
        line1.append_text(_confidence_dot(confidence))
        line1.append("! ", style="yellow")
        line1.append_text(_category_badge(category))
        line1.append_text(_confidence_score_text(confidence))
        line1.append(" ")
        # Truncate content for display
        max_len = 80
        if len(content) > max_len:
            content = content[: max_len - 3] + "..."
        line1.append(content)
        yield Static(line1)

        # Line 2: entities
        metadata = t.get("metadata", {})
        entities = []
        if isinstance(metadata, dict):
            entities = metadata.get("entities", [])
        ent_str = ", ".join(entities) if entities else "(none)"
        yield Static(
            Text.assemble(
                ("  Entities: ", "dim"),
                (ent_str, "dim italic"),
            )
        )

        # Line 3: suggested category
        suggested = category or "uncategorized"
        yield Static(
            Text.assemble(
                ("  Suggested: ", "dim"),
                (suggested, "dim"),
            )
        )

        # Line 4: per-item action hints
        yield Static(
            Text(
                "  [Enter] Accept  [C] Reclassify  "
                "[D] Dismiss  [X] Delete",
                style="dim",
            )
        )


class ReviewList(ListView):
    """Scrollable list of review items."""

    DEFAULT_CSS = """
    ReviewList {
        height: 1fr;
    }
    """


class ReviewView(Static):
    """Review view showing low-confidence thoughts needing user input."""

    DEFAULT_CSS = """
    ReviewView {
        height: 1fr;
    }

    #review-header {
        height: 1;
        margin: 0 2;
        color: $text;
    }

    #review-empty {
        padding: 2 4;
        color: $text-muted;
        text-style: italic;
        display: none;
    }

    #review-empty.visible {
        display: block;
    }

    #review-action-bar {
        dock: bottom;
        height: 1;
        background: $surface-darken-1;
        padding: 0 1;
    }

    #review-reclassify-overlay {
        display: none;
        layer: overlay;
        width: 30;
        height: auto;
        margin: 2 4;
        background: $surface;
        border: solid $accent;
        padding: 1;
    }

    #review-reclassify-overlay.visible {
        display: block;
    }

    #review-delete-confirm {
        display: none;
        layer: overlay;
        width: 40;
        height: auto;
        margin: 3 4;
        background: $surface;
        border: solid $error;
        padding: 1;
    }

    #review-delete-confirm.visible {
        display: block;
    }
    """

    BINDINGS = [
        Binding("enter", "accept", "Accept", show=True),
        Binding("c", "reclassify_item", "Reclassify", show=True),
        Binding("d", "dismiss_item", "Dismiss", show=True),
        Binding("x", "delete_item", "Delete", show=True),
    ]

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._review_items: list[dict] = []
        self._pending_action_id: str | None = None

    def compose(self) -> ComposeResult:
        yield Static("", id="review-header")
        yield Static(
            "All caught up. No thoughts need review.",
            id="review-empty",
        )
        yield ReviewList(id="review-list")
        yield Static(
            "\\[Enter] Accept  \\[C] Reclassify  "
            "\\[D] Dismiss  \\[X] Delete",
            id="review-action-bar",
        )
        # Overlays
        with Vertical(id="review-reclassify-overlay"):
            yield Static("Choose category:")
            yield OptionList(
                *CATEGORIES,
                id="review-category-picker",
            )
        with Vertical(id="review-delete-confirm"):
            yield Static("Delete this thought permanently?")
            from textual.containers import Horizontal

            with Horizontal():
                yield Button("Yes, delete", variant="error", id="review-delete-yes")
                yield Button("Cancel", id="review-delete-cancel")

    def on_mount(self) -> None:
        """Load review items on mount."""
        self.load_review_items()

    @work(thread=True)
    def load_review_items(self) -> None:
        """Load review items from the service."""
        try:
            svc = self.app._get_service()
            result = svc.get_review_items(limit=50)
            items = result.get("items", [])
            total = result.get("total", 0)
            self.app.call_from_thread(self._populate_items, items, total)
        except Exception as exc:
            logger.warning("Failed to load review items: %s", exc)

    def _populate_items(self, items: list[dict], total: int) -> None:
        """Populate the review list (main thread)."""
        self._review_items = items

        # Update header
        try:
            header = self.query_one("#review-header", Static)
            if items:
                header.update(
                    f"Low-confidence thoughts need your input. "
                    f"{total} remaining."
                )
            else:
                header.update("")
        except Exception:
            pass

        # Show/hide empty state
        try:
            empty = self.query_one("#review-empty", Static)
            if items:
                empty.remove_class("visible")
            else:
                empty.add_class("visible")
        except Exception:
            pass

        # Populate list
        try:
            review_list = self.query_one("#review-list", ReviewList)
            review_list.clear()
            for item in items:
                review_list.append(ReviewItem(item))
        except Exception:
            pass

    def _get_selected_thought(self) -> dict | None:
        """Get the currently selected thought from the review list."""
        try:
            review_list = self.query_one("#review-list", ReviewList)
            if review_list.highlighted_child is None:
                return None
            item = review_list.highlighted_child
            if isinstance(item, ReviewItem):
                return item.thought
        except Exception:
            pass
        return None

    def _remove_current_item(self) -> None:
        """Remove the currently selected item from the list."""
        try:
            review_list = self.query_one("#review-list", ReviewList)
            idx = review_list.index
            if idx is not None and 0 <= idx < len(self._review_items):
                self._review_items.pop(idx)
                review_list.remove_children([review_list.highlighted_child])

                # Update header
                remaining = len(self._review_items)
                header = self.query_one("#review-header", Static)
                if remaining > 0:
                    header.update(
                        f"Low-confidence thoughts need your input. "
                        f"{remaining} remaining."
                    )
                else:
                    header.update("")
                    self.query_one("#review-empty", Static).add_class("visible")

                # Refresh status bar + review tab count
                self.app.refresh_status_bar()
        except Exception as exc:
            logger.warning("Failed to remove item: %s", exc)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_accept(self) -> None:
        """Accept the suggested category for the selected thought."""
        thought = self._get_selected_thought()
        if thought is None:
            return
        category = thought.get("category")
        if category:
            self._do_classify(thought["id"], category)
        else:
            # No category to accept — treat as dismiss
            self._do_dismiss(thought["id"])

    def action_reclassify_item(self) -> None:
        """Show the category picker for the selected thought."""
        thought = self._get_selected_thought()
        if thought is None:
            return
        self._pending_action_id = thought["id"]
        try:
            self.query_one("#review-reclassify-overlay").add_class("visible")
            self.query_one("#review-category-picker", OptionList).focus()
        except Exception:
            pass

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Handle category selection from the picker."""
        category = str(event.option.prompt)
        try:
            self.query_one("#review-reclassify-overlay").remove_class("visible")
        except Exception:
            pass
        if self._pending_action_id:
            self._do_classify(self._pending_action_id, category)
            self._pending_action_id = None

    def action_dismiss_item(self) -> None:
        """Dismiss the selected thought (mark as reviewed without changing category)."""
        thought = self._get_selected_thought()
        if thought is None:
            return
        self._do_dismiss(thought["id"])

    def action_delete_item(self) -> None:
        """Show delete confirmation for the selected thought."""
        thought = self._get_selected_thought()
        if thought is None:
            return
        self._pending_action_id = thought["id"]
        try:
            self.query_one("#review-delete-confirm").add_class("visible")
        except Exception:
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses in overlays."""
        if event.button.id == "review-delete-yes":
            try:
                self.query_one("#review-delete-confirm").remove_class("visible")
            except Exception:
                pass
            if self._pending_action_id:
                self._do_delete(self._pending_action_id)
                self._pending_action_id = None
        elif event.button.id == "review-delete-cancel":
            try:
                self.query_one("#review-delete-confirm").remove_class("visible")
            except Exception:
                pass
            self._pending_action_id = None

    def on_key(self, event) -> None:
        """Handle Escape to close overlays."""
        if event.key == "escape":
            closed = False
            try:
                overlay = self.query_one("#review-reclassify-overlay")
                if "visible" in overlay.classes:
                    overlay.remove_class("visible")
                    closed = True
            except Exception:
                pass
            try:
                confirm = self.query_one("#review-delete-confirm")
                if "visible" in confirm.classes:
                    confirm.remove_class("visible")
                    closed = True
            except Exception:
                pass
            if closed:
                event.prevent_default()

    # ------------------------------------------------------------------
    # Service operations
    # ------------------------------------------------------------------

    @work(thread=True)
    def _do_classify(self, thought_id: str, category: str) -> None:
        """Classify a thought (sets confidence=1.0, needs_review=False)."""
        try:
            svc = self.app._get_service()
            svc.classify_thought(thought_id, category)
            self.app.call_from_thread(self._remove_current_item)
            self.app.call_from_thread(
                self.app.notify,
                f"Classified as '{category}'",
                severity="information",
            )
        except Exception as exc:
            logger.warning("Classify failed: %s", exc)
            self.app.call_from_thread(
                self.app.notify, f"Classify failed: {exc}", severity="error"
            )

    @work(thread=True)
    def _do_dismiss(self, thought_id: str) -> None:
        """Dismiss a thought (set needs_review=False without changing category)."""
        try:
            svc = self.app._get_service()
            svc.db.update_thought(thought_id, needs_review=False)
            self.app.call_from_thread(self._remove_current_item)
            self.app.call_from_thread(
                self.app.notify, "Dismissed", severity="information"
            )
        except Exception as exc:
            logger.warning("Dismiss failed: %s", exc)
            self.app.call_from_thread(
                self.app.notify, f"Dismiss failed: {exc}", severity="error"
            )

    @work(thread=True)
    def _do_delete(self, thought_id: str) -> None:
        """Delete a thought permanently."""
        try:
            svc = self.app._get_service()
            svc.delete(thought_id)
            self.app.call_from_thread(self._remove_current_item)
            self.app.call_from_thread(
                self.app.notify, "Thought deleted", severity="information"
            )
        except Exception as exc:
            logger.warning("Delete failed: %s", exc)
            self.app.call_from_thread(
                self.app.notify, f"Delete failed: {exc}", severity="error"
            )
