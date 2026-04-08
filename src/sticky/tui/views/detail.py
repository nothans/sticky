"""Detail screen for viewing a single thought.

Features:
- Header with prev/next navigation through a context list
- Full content display
- Metadata panel: category, confidence, source, timestamps, ULID, storage path
- Entities panel with type and mention count
- Topics section
- Related thoughts with similarity scores (toggle with L)
- Action bar: Edit, Delete, Reclassify, Copy ID, Related, Back
- Edit mode: TextArea replaces content, Ctrl+S saves, Esc cancels
- Delete: confirmation dialog
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import (
    Button,
    Label,
    ListView,
    ListItem,
    OptionList,
    Static,
    TextArea,
)

from sticky.tui.widgets.thought_row import (
    _category_badge,
    _confidence_dot,
    _relative_time,
    _CATEGORY_COLORS,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

CATEGORIES = ["idea", "project", "person", "meeting", "action", "reference", "journal"]


def _confidence_color(confidence: float | None) -> str:
    """Return color string based on confidence level."""
    if confidence is None or confidence < 0.6:
        return "#555555"
    if confidence < 0.8:
        return "#d4a843"
    return "#4ec9b0"


class DetailScreen(Screen):
    """Full-screen detail view for a single thought."""

    BINDINGS = [
        Binding("e", "edit", "Edit", show=True, priority=True),
        Binding("d", "delete", "Delete", show=True, priority=True),
        Binding("c", "reclassify", "Reclassify", show=True, priority=True),
        Binding("a", "ai_classify", "AI Classify", show=True, priority=True),
        Binding("y", "copy_id", "Copy ID", show=True, priority=True),
        Binding("l", "toggle_related", "Related", show=True, priority=True),
        Binding("left", "prev_thought", "Prev", show=False, priority=True),
        Binding("right", "next_thought", "Next", show=False, priority=True),
        Binding("escape", "go_back", "Back", show=True, priority=True),
    ]

    DEFAULT_CSS = """
    DetailScreen {
        layout: vertical;
        background: $surface;
    }

    #detail-nav-bar {
        height: 3;
        max-height: 3;
        margin: 0 2;
        align: center middle;
        layout: horizontal;
    }

    .nav-btn {
        min-width: 10;
        height: 3;
        border: none;
        background: transparent;
        color: $text-muted;
    }

    .nav-btn:hover {
        background: $surface-darken-1;
        color: $text;
    }

    #nav-label {
        height: 3;
        color: $text-muted;
        content-align: center middle;
        width: 1fr;
    }

    #detail-body {
        height: 1fr;
        margin: 0 1;
        overflow-y: auto;
    }

    #detail-meta {
        width: 30;
        min-width: 30;
        padding: 0 1;
    }

    .detail-section-title {
        text-style: bold;
        color: $text;
        margin: 1 0 0 0;
    }

    #detail-content-area {
        width: 1fr;
        padding: 0 2;
    }

    #detail-content-text {
        height: auto;
        min-height: 3;
    }

    #detail-entities-panel {
        width: 25;
        min-width: 20;
        padding: 0 1;
    }

    #detail-topics {
        height: auto;
        margin: 1 2 0 2;
        color: $text-muted;
    }

    #detail-related {
        height: auto;
        margin: 0 2;
        display: none;
    }

    #detail-related.visible {
        display: block;
    }

    #detail-action-bar {
        dock: bottom;
        height: 3;
        max-height: 3;
        background: $surface-darken-1;
        padding: 0;
        layout: horizontal;
    }

    .action-btn {
        min-width: 12;
        height: 3;
        border: none;
        background: $surface-darken-1;
        color: $text;
    }

    .action-btn:hover {
        background: $accent;
        color: $text;
    }

    .action-btn:focus {
        background: $accent;
    }

    #detail-edit-area {
        display: none;
        height: auto;
        min-height: 5;
    }

    #detail-edit-area.visible {
        display: block;
    }

    #detail-content-text.hidden {
        display: none;
    }

    #reclassify-overlay {
        display: none;
        layer: overlay;
        width: 30;
        height: auto;
        margin: 2 4;
        background: $surface;
        border: solid $accent;
        padding: 1;
    }

    #reclassify-overlay.visible {
        display: block;
    }

    #delete-confirm {
        display: none;
        layer: overlay;
        width: 40;
        height: auto;
        margin: 3 4;
        background: $surface;
        border: solid $error;
        padding: 1;
    }

    #delete-confirm.visible {
        display: block;
    }
    """

    def __init__(
        self,
        thought_id: str,
        context: list[str] | None = None,
        context_index: int = 0,
        context_label: str | None = None,
    ) -> None:
        super().__init__()
        self.thought_id = thought_id
        self.context = context or [thought_id]
        self.context_index = context_index
        self.context_label = context_label
        self._thought_data: dict | None = None
        self._entities: list[dict] = []
        self._related: list[dict] = []
        self._related_visible = False
        self._edit_mode = False

    def compose(self) -> ComposeResult:
        # Navigation header with clickable prev/next buttons
        with Horizontal(id="detail-nav-bar"):
            yield Button("< prev", id="nav-prev", classes="nav-btn")
            yield Static("", id="nav-label")
            yield Button("next >", id="nav-next", classes="nav-btn")
        with Horizontal(id="detail-body"):
            with Vertical(id="detail-meta"):
                yield Static("METADATA", classes="detail-section-title")
                yield Static("", id="meta-content")
            with Vertical(id="detail-content-area"):
                yield Static("CONTENT", classes="detail-section-title")
                yield Static("", id="detail-content-text")
                yield TextArea(id="detail-edit-area")
                yield Static("", id="detail-topics")
                yield Static("", id="detail-related")
            with Vertical(id="detail-entities-panel"):
                yield Static("ENTITIES", classes="detail-section-title")
                yield Static("", id="entities-content")
        # Clickable action bar buttons
        with Horizontal(id="detail-action-bar"):
            yield Button("[E] Edit", id="action-edit", classes="action-btn")
            yield Button("[D] Del", id="action-delete", classes="action-btn")
            yield Button("[C] Reclassify", id="action-reclassify", classes="action-btn")
            yield Button("[A] AI Classify", id="action-ai-classify", classes="action-btn")
            yield Button("[Y] Copy ID", id="action-copy-id", classes="action-btn")
            yield Button("[L] Related", id="action-related", classes="action-btn")
            yield Button("[Esc] Back", id="action-back", classes="action-btn")
        # Overlays
        with Vertical(id="reclassify-overlay"):
            yield Static("Choose category:")
            yield OptionList(
                *CATEGORIES,
                id="category-picker",
            )
        with Vertical(id="delete-confirm"):
            yield Static("Delete this thought permanently?")
            with Horizontal():
                yield Button("Yes, delete", variant="error", id="delete-yes")
                yield Button("Cancel", id="delete-cancel")

    def on_mount(self) -> None:
        """Load thought data on mount."""
        self._load_thought()

    @work(thread=True)
    def _load_thought(self) -> None:
        """Load the thought and its metadata from the service."""
        try:
            svc = self.app._get_service()
            thought = svc.db.get_thought(self.thought_id)
            if thought is None:
                self.app.call_from_thread(
                    self.app.notify, "Thought not found", severity="error"
                )
                return

            thought_data = thought.to_display()

            # Get entities for this thought
            entity_rows = svc.db.execute(
                """SELECT e.name, e.entity_type, e.mention_count
                   FROM entities e
                   JOIN entity_mentions em ON em.entity_id = e.id
                   WHERE em.thought_id = ?
                   ORDER BY e.mention_count DESC""",
                (self.thought_id,),
            ).fetchall()
            entities = [dict(row) for row in entity_rows]

            # Storage path
            thought_data["db_path"] = str(svc.config.db_path)

            self.app.call_from_thread(
                self._populate_detail, thought_data, entities
            )
        except Exception as exc:
            logger.warning("Failed to load thought: %s", exc)

    def _populate_detail(self, thought_data: dict, entities: list[dict]) -> None:
        """Populate all detail fields (main thread)."""
        self._thought_data = thought_data
        self._entities = entities

        # Navigation header
        idx = self.context_index + 1
        total = len(self.context)
        label = self.context_label or ""
        nav_text = f"Thought {idx} of {total}"
        if label:
            nav_text += f" in '{label}'"
        try:
            self.query_one("#nav-label", Static).update(nav_text)
        except Exception:
            pass
        # Enable/disable prev/next buttons based on position
        try:
            prev_btn = self.query_one("#nav-prev", Button)
            prev_btn.disabled = self.context_index <= 0
        except Exception:
            pass
        try:
            next_btn = self.query_one("#nav-next", Button)
            next_btn.disabled = self.context_index >= len(self.context) - 1
        except Exception:
            pass

        # Content
        content = thought_data.get("content", "")
        try:
            self.query_one("#detail-content-text", Static).update(content)
        except Exception:
            pass

        # Metadata panel
        category = thought_data.get("category", "?")
        confidence = thought_data.get("confidence")
        source = thought_data.get("source", "?")
        created = thought_data.get("created_at", "")
        updated = thought_data.get("updated_at", "")
        tid = thought_data.get("id", "")
        db_path = thought_data.get("db_path", "")

        conf_str = f"{confidence:.2f}" if confidence is not None else "N/A"
        conf_color = _confidence_color(confidence)

        cat_color = _CATEGORY_COLORS.get(category or "", "white")

        meta_text = Text()
        meta_text.append("Category: ")
        meta_text.append(f" {category or '?'} ", style=f"bold {cat_color}")
        meta_text.append(f" [{conf_str}]", style=conf_color)
        meta_text.append(f"\nSource: {source}")
        meta_text.append(f"\nCaptured: {_format_timestamp(created)}")
        meta_text.append(f"\nUpdated: {_format_timestamp(updated)}")
        meta_text.append(f"\nID: {tid}")
        meta_text.append(f"\nStored: {db_path}")

        try:
            self.query_one("#meta-content", Static).update(meta_text)
        except Exception:
            pass

        # Entities panel
        if entities:
            ent_text = Text()
            for ent in entities:
                name = ent.get("name", "")
                etype = ent.get("entity_type", "")
                count = ent.get("mention_count", 0)
                ent_text.append(f"  {name} ")
                ent_text.append(f"({etype})", style="dim")
                ent_text.append(f"  {count}x\n", style="dim")
            try:
                self.query_one("#entities-content", Static).update(ent_text)
            except Exception:
                pass
        else:
            try:
                self.query_one("#entities-content", Static).update(
                    Text("(none)", style="dim")
                )
            except Exception:
                pass

        # Topics (from metadata)
        metadata = thought_data.get("metadata", {})
        topics = metadata.get("topics", [])
        if topics:
            topics_str = ", ".join(topics)
            try:
                topics_text = Text()
                topics_text.append("TOPICS\n", style="bold")
                topics_text.append(topics_str, style="dim")
                self.query_one("#detail-topics", Static).update(topics_text)
            except Exception:
                pass

    @work(thread=True)
    def _load_related(self) -> None:
        """Load related thoughts from the service."""
        try:
            svc = self.app._get_service()
            related = svc.related_thoughts(self.thought_id, limit=3)
            self.app.call_from_thread(self._populate_related, related)
        except Exception as exc:
            logger.warning("Failed to load related thoughts: %s", exc)

    def _populate_related(self, related: list[dict]) -> None:
        """Populate the related thoughts section."""
        self._related = related
        widget = self.query_one("#detail-related", Static)

        if not related:
            text = Text()
            text.append("RELATED THOUGHTS\n", style="bold")
            text.append("(no related thoughts found)", style="dim")
            widget.update(text)
            return

        text = Text()
        text.append("RELATED THOUGHTS\n", style="bold")
        for r in related:
            score = r.get("score", 0.0)
            created = r.get("created_at", "")
            content = r.get("content", "").replace("\n", " ")
            if len(content) > 60:
                content = content[:57] + "..."

            # Score color
            if score >= 0.7:
                score_color = "#4ec9b0"
            elif score >= 0.4:
                score_color = "#d4a843"
            else:
                score_color = "#e06c75"

            text.append(f"[{score:.2f}]", style=score_color)
            text.append(f" {_relative_time(created).ljust(8)}", style="dim")
            text.append(f' "{content}"\n')

        widget.update(text)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_go_back(self) -> None:
        """Pop this screen and return."""
        if self._edit_mode:
            self._exit_edit_mode()
            return
        # Hide overlays if open
        try:
            reclass = self.query_one("#reclassify-overlay")
            if "visible" in reclass.classes:
                reclass.remove_class("visible")
                return
        except Exception:
            pass
        try:
            confirm = self.query_one("#delete-confirm")
            if "visible" in confirm.classes:
                confirm.remove_class("visible")
                return
        except Exception:
            pass
        self.dismiss(self._thought_data)

    def action_edit(self) -> None:
        """Enter edit mode: replace content Static with TextArea."""
        if self._edit_mode or self._thought_data is None:
            return
        self._edit_mode = True
        content = self._thought_data.get("content", "")

        try:
            edit_area = self.query_one("#detail-edit-area", TextArea)
            edit_area.load_text(content)
            edit_area.add_class("visible")
            edit_area.focus()

            content_static = self.query_one("#detail-content-text", Static)
            content_static.add_class("hidden")

            # Hide action bar buttons and show edit-mode hint
            self._set_action_bar_edit_mode(True)
        except Exception as exc:
            logger.warning("Failed to enter edit mode: %s", exc)

    def _exit_edit_mode(self) -> None:
        """Exit edit mode without saving."""
        self._edit_mode = False
        try:
            edit_area = self.query_one("#detail-edit-area", TextArea)
            edit_area.remove_class("visible")

            content_static = self.query_one("#detail-content-text", Static)
            content_static.remove_class("hidden")

            # Restore action bar buttons
            self._set_action_bar_edit_mode(False)
        except Exception:
            pass

    def _set_action_bar_edit_mode(self, editing: bool) -> None:
        """Toggle action bar between edit mode and normal mode."""
        try:
            action_btn_ids = [
                "action-edit", "action-delete", "action-reclassify",
                "action-ai-classify", "action-copy-id", "action-related",
                "action-back",
            ]
            for btn_id in action_btn_ids:
                try:
                    btn = self.query_one(f"#{btn_id}", Button)
                    btn.display = not editing
                except Exception:
                    pass
            # In edit mode, show a save/cancel hint via a static if needed
            # The TextArea itself handles Ctrl+S via on_key and Esc via action_go_back
        except Exception:
            pass

    def on_key(self, event) -> None:
        """Handle key events directly as fallback for bindings."""
        if self._edit_mode:
            if event.key == "ctrl+s":
                event.prevent_default()
                self._save_edit()
            return
        # Fallback key handling in case bindings don't fire
        key_action_map = {
            "e": self.action_edit,
            "d": self.action_delete,
            "c": self.action_reclassify,
            "a": self.action_ai_classify,
            "y": self.action_copy_id,
            "l": self.action_toggle_related,
            "left": self.action_prev_thought,
            "right": self.action_next_thought,
        }
        action = key_action_map.get(event.key)
        if action is not None:
            event.prevent_default()
            action()

    def _save_edit(self) -> None:
        """Save edited content."""
        try:
            edit_area = self.query_one("#detail-edit-area", TextArea)
            new_content = edit_area.text
        except Exception:
            return

        if not new_content.strip():
            self.app.notify("Content cannot be empty", severity="error")
            return

        self._exit_edit_mode()
        self._do_update(new_content.strip())

    @work(thread=True)
    def _do_update(self, content: str) -> None:
        """Update the thought content via service."""
        try:
            svc = self.app._get_service()
            result = svc.update(self.thought_id, content)
            self.app.call_from_thread(self.app.notify, "Thought updated!")
            # Reload
            self.app.call_from_thread(self._load_thought)
        except Exception as exc:
            logger.warning("Update failed: %s", exc)
            self.app.call_from_thread(
                self.app.notify, f"Update failed: {exc}", severity="error"
            )

    def action_delete(self) -> None:
        """Show delete confirmation dialog."""
        if self._edit_mode:
            return
        try:
            self.query_one("#delete-confirm").add_class("visible")
        except Exception:
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses in overlays and action bar."""
        btn_id = event.button.id
        if btn_id == "delete-yes":
            self._do_delete()
        elif btn_id == "delete-cancel":
            try:
                self.query_one("#delete-confirm").remove_class("visible")
            except Exception:
                pass
        # Action bar buttons
        elif btn_id == "action-edit":
            self.action_edit()
        elif btn_id == "action-delete":
            self.action_delete()
        elif btn_id == "action-reclassify":
            self.action_reclassify()
        elif btn_id == "action-ai-classify":
            self.action_ai_classify()
        elif btn_id == "action-copy-id":
            self.action_copy_id()
        elif btn_id == "action-related":
            self.action_toggle_related()
        elif btn_id == "action-back":
            self.action_go_back()
        # Navigation buttons
        elif btn_id == "nav-prev":
            self.action_prev_thought()
        elif btn_id == "nav-next":
            self.action_next_thought()

    @work(thread=True)
    def _do_delete(self) -> None:
        """Delete the thought via service."""
        try:
            svc = self.app._get_service()
            svc.delete(self.thought_id)
            self.app.call_from_thread(
                self.app.notify, "Thought deleted", severity="information"
            )
            self.app.call_from_thread(self.dismiss, None)
        except Exception as exc:
            logger.warning("Delete failed: %s", exc)
            self.app.call_from_thread(
                self.app.notify, f"Delete failed: {exc}", severity="error"
            )

    def action_reclassify(self) -> None:
        """Show the category picker overlay."""
        if self._edit_mode:
            return
        try:
            self.query_one("#reclassify-overlay").add_class("visible")
            self.query_one("#category-picker", OptionList).focus()
        except Exception:
            pass

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Handle category selection from the picker."""
        category = str(event.option.prompt)
        try:
            self.query_one("#reclassify-overlay").remove_class("visible")
        except Exception:
            pass
        self._do_reclassify(category)

    @work(thread=True)
    def _do_reclassify(self, category: str) -> None:
        """Reclassify the thought via service."""
        try:
            svc = self.app._get_service()
            svc.classify_thought(self.thought_id, category)
            self.app.call_from_thread(
                self.app.notify,
                f"Reclassified as '{category}'",
                severity="information",
            )
            self.app.call_from_thread(self._load_thought)
        except Exception as exc:
            logger.warning("Reclassify failed: %s", exc)
            self.app.call_from_thread(
                self.app.notify, f"Reclassify failed: {exc}", severity="error"
            )

    def action_ai_classify(self) -> None:
        """Re-run AI classification on this thought."""
        if self._edit_mode or self._thought_data is None:
            return
        self.app.notify("Running AI classification...", severity="information")
        self._do_ai_classify()

    @work(thread=True)
    def _do_ai_classify(self) -> None:
        """Re-classify the thought using the LLM."""
        try:
            svc = self.app._get_service()
            content = self._thought_data.get("content", "")
            template = (self._thought_data.get("metadata") or {}).get("template")

            classification = svc.classifier.classify_sync(content, template)
            if classification is None:
                self.app.call_from_thread(
                    self.app.notify,
                    "AI classification failed — check OpenRouter API key",
                    severity="error",
                )
                return

            # Update the thought with AI results
            metadata = {
                "topics": classification.topics,
                "people": classification.people,
                "projects": classification.projects,
                "actions": classification.actions,
            }
            if template:
                metadata["template"] = template

            svc.db.update_thought(
                self.thought_id,
                category=classification.category,
                confidence=classification.confidence,
                needs_review=classification.confidence < svc.config.confidence_threshold,
                metadata=metadata,
            )

            # Re-resolve entities
            svc.db.delete_mentions_for_thought(self.thought_id)
            entities = svc.entity_resolver.resolve_entities(
                classification, self.thought_id,
            )

            self.app.call_from_thread(
                self.app.notify,
                f"AI classified as '{classification.category}' "
                f"({classification.confidence:.0%} confidence)",
                severity="information",
            )
            self.app.call_from_thread(self._load_thought)
        except Exception as exc:
            logger.warning("AI classification failed: %s", exc)
            self.app.call_from_thread(
                self.app.notify, f"AI classify failed: {exc}", severity="error"
            )

    def action_copy_id(self) -> None:
        """Copy the thought ULID to clipboard."""
        if self._thought_data is None:
            return
        tid = self._thought_data.get("id", "")
        try:
            import pyperclip

            pyperclip.copy(tid)
            self.app.notify(f"Copied: {tid}", severity="information")
        except ImportError:
            # Fallback: just notify
            self.app.notify(
                f"ID: {tid} (pyperclip not installed for clipboard)",
                severity="warning",
            )

    def action_toggle_related(self) -> None:
        """Toggle the related thoughts section."""
        if self._edit_mode:
            return
        self._related_visible = not self._related_visible
        try:
            widget = self.query_one("#detail-related", Static)
            if self._related_visible:
                widget.add_class("visible")
                if not self._related:
                    self._load_related()
            else:
                widget.remove_class("visible")
        except Exception:
            pass

    def action_prev_thought(self) -> None:
        """Navigate to the previous thought in context."""
        if self.context_index > 0:
            self.context_index -= 1
            self.thought_id = self.context[self.context_index]
            self._related = []
            self._related_visible = False
            try:
                self.query_one("#detail-related", Static).remove_class("visible")
            except Exception:
                pass
            self._load_thought()

    def action_next_thought(self) -> None:
        """Navigate to the next thought in context."""
        if self.context_index < len(self.context) - 1:
            self.context_index += 1
            self.thought_id = self.context[self.context_index]
            self._related = []
            self._related_visible = False
            try:
                self.query_one("#detail-related", Static).remove_class("visible")
            except Exception:
                pass
            self._load_thought()


def _format_timestamp(ts: str) -> str:
    """Format ISO timestamp for display."""
    if not ts:
        return "N/A"
    try:
        from datetime import datetime, timezone

        dt = datetime.fromisoformat(ts)
        return dt.strftime("%b %d, %Y %I:%M%p")
    except (ValueError, TypeError):
        return ts
