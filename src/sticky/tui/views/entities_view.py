"""Entity browser view for sticky TUI.

Three-column layout:
- LEFT: Type filter sidebar (All, Person, Project, Concept with counts)
- MIDDLE: Entity list with NAME, TYPE badge, MENTIONS columns
- RIGHT: Linked thoughts for selected entity with pinned context box

Features:
- Search/filter bar at top
- M key opens merge entity dialog
- Tab cycles between panes
- Enter on entity opens first linked thought
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from rich.text import Text
from textual import on, work
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import Input, Label, ListItem, ListView, Static

from sticky.tui.widgets.thought_row import _category_badge, _relative_time

if TYPE_CHECKING:
    from textual.app import ComposeResult

logger = logging.getLogger(__name__)


# Entity type badge colors
_ENTITY_TYPE_COLORS: dict[str, str] = {
    "person": "#c678dd",   # magenta / purple
    "project": "#4ec9b0",  # green / teal
    "concept": "#61afef",  # blue
}


def _entity_type_badge(entity_type: str | None) -> Text:
    """Return a colored entity type badge."""
    if entity_type is None:
        return Text("?       ", style="dim")
    color = _ENTITY_TYPE_COLORS.get(entity_type, "white")
    padded = entity_type.ljust(8)
    return Text(padded, style=f"{color} bold")


# ---------------------------------------------------------------------------
# Type filter sidebar (left column)
# ---------------------------------------------------------------------------


class TypeFilterItem(ListItem):
    """A single type filter item: e.g. 'Person (6)'."""

    DEFAULT_CSS = """
    TypeFilterItem {
        height: 1;
        padding: 0 1;
    }
    TypeFilterItem > Static {
        height: 1;
    }
    """

    def __init__(self, label: str, entity_type: str | None, count: int = 0) -> None:
        super().__init__()
        self.filter_label = label
        self.entity_type = entity_type
        self.count = count

    def compose(self) -> ComposeResult:
        text = Text()
        text.append(f"{self.filter_label} ({self.count})")
        yield Static(text)


class TypeFilter(Static):
    """Left sidebar with entity type filter options."""

    DEFAULT_CSS = """
    TypeFilter {
        width: 20;
        height: 1fr;
        border-right: solid $surface-darken-1;
        padding: 0;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("Type Filter  >", classes="type-filter-header")
        yield ListView(id="type-filter-list")

    def set_counts(
        self,
        total: int = 0,
        person: int = 0,
        project: int = 0,
        concept: int = 0,
    ) -> None:
        """Populate the type filter list with counts."""
        filter_list = self.query_one("#type-filter-list", ListView)
        filter_list.clear()
        filter_list.append(TypeFilterItem("All", None, total))
        filter_list.append(TypeFilterItem("Person", "person", person))
        filter_list.append(TypeFilterItem("Project", "project", project))
        filter_list.append(TypeFilterItem("Concept", "concept", concept))


# ---------------------------------------------------------------------------
# Entity list (middle column)
# ---------------------------------------------------------------------------


class EntityRow(ListItem):
    """A single entity row: NAME  TYPE_BADGE  MENTIONS."""

    DEFAULT_CSS = """
    EntityRow {
        height: 1;
        padding: 0 1;
    }
    EntityRow > Static {
        height: 1;
    }
    """

    def __init__(self, entity: dict, **kwargs) -> None:
        super().__init__(**kwargs)
        self.entity = entity

    def compose(self) -> ComposeResult:
        e = self.entity
        name = e.get("name", "")
        entity_type = e.get("entity_type", "")
        mention_count = e.get("mention_count", 0)

        line = Text()
        # Prefix indicator for selected/hover
        line.append("  ")
        line.append(name.ljust(16))
        line.append_text(_entity_type_badge(entity_type))
        line.append(f"{mention_count}x".rjust(4), style="dim")

        yield Static(line)


class EntityList(Static):
    """Middle column showing the entity list with header."""

    DEFAULT_CSS = """
    EntityList {
        width: 1fr;
        height: 1fr;
        border-right: solid $surface-darken-1;
        padding: 0;
    }
    """

    def compose(self) -> ComposeResult:
        header = Text()
        header.append("  NAME            ", style="bold dim")
        header.append("TYPE     ", style="bold dim")
        header.append("MENTIONS", style="bold dim")
        yield Static(header, classes="entity-list-header")
        yield ListView(id="entity-list")


# ---------------------------------------------------------------------------
# Entity detail (right column) — linked thoughts + pinned context
# ---------------------------------------------------------------------------


class PinnedContext(Static):
    """Pinned context summary box for the selected entity.

    Shows a 2-3 line auto-summary of entity context.
    """

    DEFAULT_CSS = """
    PinnedContext {
        height: auto;
        min-height: 3;
        max-height: 5;
        margin: 0 1;
        padding: 0 1;
        border: solid $surface-darken-1;
        color: $text-muted;
    }
    """

    def set_summary(self, summary: str) -> None:
        """Update the pinned context summary text."""
        self.update(summary)


class LinkedThoughtRow(ListItem):
    """A linked thought row in the entity detail panel."""

    DEFAULT_CSS = """
    LinkedThoughtRow {
        height: auto;
        min-height: 2;
        padding: 0 1;
    }
    LinkedThoughtRow > Static {
        height: auto;
    }
    """

    def __init__(self, thought: dict, **kwargs) -> None:
        super().__init__(**kwargs)
        self.thought = thought

    def compose(self) -> ComposeResult:
        t = self.thought
        created_at = t.get("created_at", "")
        category = t.get("category")
        content = t.get("content", "").replace("\n", " ")

        line = Text()
        time_str = _relative_time(created_at).ljust(10)
        line.append(time_str, style="dim")
        line.append_text(_category_badge(category))
        # Truncate content
        max_content = 50
        if len(content) > max_content:
            content = content[: max_content - 3] + "..."
        line.append(content)
        yield Static(line)


class EntityDetail(Static):
    """Right panel showing linked thoughts for the selected entity."""

    DEFAULT_CSS = """
    EntityDetail {
        width: 2fr;
        height: 1fr;
        padding: 0;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static(
            "Linked Thoughts",
            id="entity-detail-title",
            classes="entity-detail-title",
        )
        yield PinnedContext(id="pinned-context")
        yield ListView(id="linked-thoughts-list")

    def set_entity_name(self, name: str) -> None:
        """Update the title with the entity name."""
        try:
            title = self.query_one("#entity-detail-title", Static)
            title.update(f"Linked Thoughts for {name}")
        except Exception:
            pass

    def set_context_summary(self, summary: str) -> None:
        """Update the pinned context box."""
        try:
            ctx = self.query_one("#pinned-context", PinnedContext)
            ctx.set_summary(summary)
        except Exception:
            pass

    def set_thoughts(self, thoughts: list[dict]) -> None:
        """Populate the linked thoughts list."""
        try:
            thought_list = self.query_one("#linked-thoughts-list", ListView)
            thought_list.clear()
            for t in thoughts:
                thought_list.append(LinkedThoughtRow(t))
        except Exception:
            pass

    def clear(self) -> None:
        """Clear the entity detail panel."""
        self.set_entity_name("")
        self.set_context_summary("Select an entity to see details.")
        try:
            thought_list = self.query_one("#linked-thoughts-list", ListView)
            thought_list.clear()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Merge dialog
# ---------------------------------------------------------------------------


class MergeEntityItem(ListItem):
    """An entity item in the merge dialog picker."""

    DEFAULT_CSS = """
    MergeEntityItem {
        height: 1;
        padding: 0 1;
    }
    MergeEntityItem > Static {
        height: 1;
    }
    """

    def __init__(self, entity: dict) -> None:
        super().__init__()
        self.entity = entity

    def compose(self) -> ComposeResult:
        e = self.entity
        line = Text()
        line.append(e.get("name", "").ljust(20))
        line.append(f"({e.get('mention_count', 0)} mentions)", style="dim")
        yield Static(line)


class MergeDialog(ModalScreen):
    """Modal: select target entity to merge into.

    Lists same-type entities; selecting one merges the source into the target.
    """

    BINDINGS = [
        Binding("escape", "dismiss", "Cancel"),
    ]

    DEFAULT_CSS = """
    MergeDialog {
        align: center middle;
    }
    #merge-dialog-container {
        width: 50;
        height: 20;
        border: solid $accent;
        background: $surface;
        padding: 1 2;
    }
    """

    def __init__(
        self, source_entity_id: str, same_type_entities: list[dict]
    ) -> None:
        super().__init__()
        self.source_entity_id = source_entity_id
        self.same_type_entities = same_type_entities

    def compose(self) -> ComposeResult:
        with Vertical(id="merge-dialog-container"):
            yield Static("Merge Entity Into...", classes="merge-dialog-title")
            yield Static(
                "Select target entity (source will be merged into it):",
                classes="merge-dialog-hint",
            )
            yield ListView(id="merge-target-list")
            yield Static("\\[Enter] Merge  \\[Esc] Cancel", classes="merge-dialog-keys")

    def on_mount(self) -> None:
        """Populate the merge target list."""
        merge_list = self.query_one("#merge-target-list", ListView)
        for entity in self.same_type_entities:
            if entity.get("id") != self.source_entity_id:
                merge_list.append(MergeEntityItem(entity))

    @on(ListView.Selected, "#merge-target-list")
    def handle_merge_select(self, event: ListView.Selected) -> None:
        """Handle selection of merge target."""
        item = event.item
        if isinstance(item, MergeEntityItem):
            target_id = item.entity.get("id")
            if target_id:
                self.dismiss(
                    {"source_id": self.source_entity_id, "target_id": target_id}
                )

    def action_dismiss(self) -> None:
        """Cancel the merge dialog."""
        self.dismiss(None)


# ---------------------------------------------------------------------------
# Main entities view
# ---------------------------------------------------------------------------


class EntitiesView(Static):
    """Entity browser with three-column layout.

    LEFT: type filter sidebar
    MIDDLE: entity list
    RIGHT: linked thoughts with pinned context
    """

    DEFAULT_CSS = """
    EntitiesView {
        height: 1fr;
    }

    .entities-filter-bar {
        height: 3;
        margin: 0 1;
    }

    .entities-filter-bar Input {
        width: 1fr;
    }

    .entities-columns {
        height: 1fr;
    }

    .type-filter-header {
        height: 1;
        padding: 0 1;
        color: $text;
        text-style: bold;
    }

    .entity-list-header {
        height: 1;
        padding: 0 1;
    }

    .entity-detail-title {
        height: 1;
        padding: 0 1;
        text-style: bold;
    }

    .merge-dialog-title {
        height: 1;
        text-style: bold;
        color: $accent;
    }

    .merge-dialog-hint {
        height: 1;
        color: $text-muted;
    }

    .merge-dialog-keys {
        height: 1;
        dock: bottom;
        color: $text-muted;
    }
    """

    BINDINGS = [
        Binding("m", "merge_entity", "Merge", show=False),
        Binding("slash", "focus_filter", "Filter", show=False),
    ]

    # Currently selected entity type filter (None = All)
    _current_type_filter: str | None = None
    _current_filter_query: str = ""
    _selected_entity: dict | None = None
    _entities: list[dict] = []

    def compose(self) -> ComposeResult:
        with Horizontal(classes="entities-filter-bar"):
            yield Input(
                placeholder="/ Filter entities...",
                id="entity-filter-input",
            )
        with Horizontal(classes="entities-columns"):
            yield TypeFilter(id="type-filter")
            yield EntityList(id="entity-list-panel")
            yield EntityDetail(id="entity-detail")

    def on_mount(self) -> None:
        """Load entities on mount."""
        self.load_entities()

    @on(Input.Submitted, "#entity-filter-input")
    def handle_filter(self, event: Input.Submitted) -> None:
        """Handle filter input submission."""
        self._current_filter_query = event.value.strip()
        self.load_entities()

    @on(Input.Changed, "#entity-filter-input")
    def handle_filter_change(self, event: Input.Changed) -> None:
        """Handle filter input change for live filtering."""
        self._current_filter_query = event.value.strip()
        self.load_entities()

    @on(ListView.Selected, "#type-filter-list")
    def handle_type_filter_select(self, event: ListView.Selected) -> None:
        """Handle type filter selection."""
        item = event.item
        if isinstance(item, TypeFilterItem):
            self._current_type_filter = item.entity_type
            self.load_entities()

    @on(ListView.Selected, "#entity-list")
    def handle_entity_select(self, event: ListView.Selected) -> None:
        """Handle entity selection — load detail panel."""
        item = event.item
        if isinstance(item, EntityRow):
            self._selected_entity = item.entity
            self.load_entity_detail(item.entity)

    @work(thread=True)
    def load_entities(self) -> None:
        """Load entities from the service (worker thread)."""
        try:
            svc = self.app._get_service()
            kwargs: dict = {"limit": 100, "sort": "last_seen"}

            if self._current_type_filter:
                kwargs["entity_type"] = self._current_type_filter

            if self._current_filter_query:
                kwargs["query"] = self._current_filter_query

            result = svc.list_entities(**kwargs)
            entities = result.get("entities", [])

            # Get counts per type for the sidebar
            all_result = svc.list_entities(limit=100000)
            all_entities = all_result.get("entities", [])
            total = len(all_entities)
            person_count = sum(
                1 for e in all_entities if e.get("entity_type") == "person"
            )
            project_count = sum(
                1 for e in all_entities if e.get("entity_type") == "project"
            )
            concept_count = sum(
                1 for e in all_entities if e.get("entity_type") == "concept"
            )

            self.app.call_from_thread(
                self._populate_entities,
                entities,
                total,
                person_count,
                project_count,
                concept_count,
            )
        except Exception as exc:
            logger.warning("Failed to load entities: %s", exc)

    def _populate_entities(
        self,
        entities: list[dict],
        total: int,
        person_count: int,
        project_count: int,
        concept_count: int,
    ) -> None:
        """Populate the entity list and type filter counts (main thread)."""
        self._entities = entities

        # Update type filter counts
        try:
            type_filter = self.query_one("#type-filter", TypeFilter)
            type_filter.set_counts(total, person_count, project_count, concept_count)
        except Exception:
            pass

        # Update entity list
        try:
            entity_list = self.query_one("#entity-list", ListView)
            entity_list.clear()
            for e in entities:
                entity_list.append(EntityRow(e))
        except Exception:
            pass

        # If no entity selected, clear detail
        if not entities:
            try:
                detail = self.query_one("#entity-detail", EntityDetail)
                detail.clear()
            except Exception:
                pass

    @work(thread=True)
    def load_entity_detail(self, entity: dict) -> None:
        """Load entity detail: context summary + linked thoughts (worker thread)."""
        try:
            svc = self.app._get_service()
            entity_id = entity.get("id", "")
            entity_name = entity.get("name", "")

            # Get context summary
            summary = svc.entity_context_summary(entity_id)

            # Get linked thoughts
            thoughts = svc.db.get_thoughts_for_entity(entity_id, limit=20)
            thought_dicts = [t.to_display() for t in thoughts]

            self.app.call_from_thread(
                self._populate_entity_detail,
                entity_name,
                summary,
                thought_dicts,
            )
        except Exception as exc:
            logger.warning("Failed to load entity detail: %s", exc)

    def _populate_entity_detail(
        self,
        entity_name: str,
        summary: str,
        thoughts: list[dict],
    ) -> None:
        """Populate the entity detail panel (main thread)."""
        try:
            detail = self.query_one("#entity-detail", EntityDetail)
            detail.set_entity_name(entity_name)
            detail.set_context_summary(summary)
            detail.set_thoughts(thoughts)
        except Exception:
            pass

    def action_merge_entity(self) -> None:
        """Open the merge dialog for the selected entity."""
        if self._selected_entity is None:
            self.app.notify("Select an entity first.", severity="warning")
            return

        entity_type = self._selected_entity.get("entity_type")
        # Filter same-type entities for merge targets
        same_type = [
            e
            for e in self._entities
            if e.get("entity_type") == entity_type
            and e.get("id") != self._selected_entity.get("id")
        ]

        if not same_type:
            self.app.notify("No other entities of the same type to merge with.", severity="warning")
            return

        source_id = self._selected_entity.get("id", "")
        self.app.push_screen(
            MergeDialog(source_id, same_type),
            callback=self._handle_merge_result,
        )

    def _handle_merge_result(self, result: dict | None) -> None:
        """Handle merge dialog result."""
        if result is None:
            return

        source_id = result.get("source_id", "")
        target_id = result.get("target_id", "")
        if source_id and target_id:
            self._perform_merge(source_id, target_id)

    @work(thread=True)
    def _perform_merge(self, source_id: str, target_id: str) -> None:
        """Perform the entity merge (worker thread)."""
        try:
            svc = self.app._get_service()
            merged = svc.entity_resolver.merge_entities(source_id, target_id)
            if merged:
                self.app.call_from_thread(
                    self.app.notify,
                    f"Merged into {merged.name}",
                    severity="information",
                )
                self.app.call_from_thread(self._after_merge)
            else:
                self.app.call_from_thread(
                    self.app.notify, "Merge failed.", severity="error"
                )
        except Exception as exc:
            logger.warning("Merge failed: %s", exc)
            self.app.call_from_thread(
                self.app.notify, f"Merge failed: {exc}", severity="error"
            )

    def _after_merge(self) -> None:
        """Refresh after a merge."""
        self._selected_entity = None
        self.load_entities()

    def action_focus_filter(self) -> None:
        """Focus the filter input."""
        try:
            inp = self.query_one("#entity-filter-input", Input)
            inp.focus()
        except Exception:
            pass
