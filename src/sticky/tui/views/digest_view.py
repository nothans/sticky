"""Digest view for sticky TUI.

The morning briefing view showing:
- Period selector tabs: Day | Week | Month
- Header with date and thought count
- TOPICS section with expandable source tracing
- ACTION ITEMS with carried markers
- PEOPLE MENTIONED with context
- FROM YOUR ARCHIVE (resurfaced older thought)

Features:
- G key regenerates digest for current period
- Enter expands topic to show source thoughts
- Tab cycles period selector
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from rich.text import Text
from textual import on, work
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Label, ListItem, ListView, Static

from sticky.tui.widgets.thought_row import _category_badge, _relative_time

if TYPE_CHECKING:
    from textual.app import ComposeResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Period selector
# ---------------------------------------------------------------------------


class PeriodTab(Static):
    """A single period tab: Day, Week, or Month.

    Posts a PeriodTabClicked message when clicked.
    """

    DEFAULT_CSS = """
    PeriodTab {
        width: auto;
        height: 1;
        padding: 0 2;
        color: $text-muted;
    }
    PeriodTab.active {
        color: $text;
        text-style: bold;
        background: $accent;
    }
    PeriodTab:hover {
        background: $surface-darken-1;
    }
    """

    class PeriodTabClicked(Message):
        """Message posted when a period tab is clicked."""

        def __init__(self, period: str) -> None:
            self.period = period
            super().__init__()

    def __init__(self, label: str, period: str, **kwargs) -> None:
        super().__init__(label, **kwargs)
        self.period = period

    def on_click(self) -> None:
        """Handle click — post message to parent."""
        self.post_message(self.PeriodTabClicked(self.period))


class PeriodSelector(Static):
    """Day | Week | Month period selector tabs."""

    DEFAULT_CSS = """
    PeriodSelector {
        height: 1;
        margin: 0 1;
    }
    .period-tabs {
        height: 1;
    }
    .period-generated {
        height: 1;
        color: $text-muted;
        text-style: dim;
        content-align: right middle;
        width: 1fr;
    }
    """

    active_period: reactive[str] = reactive("day")

    class PeriodChanged(Message):
        """Message posted when the period changes."""

        def __init__(self, period: str) -> None:
            self.period = period
            super().__init__()

    def compose(self) -> ComposeResult:
        with Horizontal(classes="period-tabs"):
            yield PeriodTab("Day", "day", id="period-day")
            yield PeriodTab("Week", "week", id="period-week")
            yield PeriodTab("Month", "month", id="period-month")
            yield Static("", id="period-generated", classes="period-generated")

    def on_mount(self) -> None:
        """Set initial active period."""
        self._update_active_display()

    def watch_active_period(self, new_period: str) -> None:
        """Update display when period changes."""
        self._update_active_display()

    def _update_active_display(self) -> None:
        """Highlight the active period tab."""
        for period_id in ("period-day", "period-week", "period-month"):
            try:
                tab = self.query_one(f"#{period_id}", PeriodTab)
                if tab.period == self.active_period:
                    tab.add_class("active")
                else:
                    tab.remove_class("active")
            except Exception:
                pass

    def set_generated_time(self, time_str: str) -> None:
        """Set the 'Generated:' timestamp text."""
        try:
            gen = self.query_one("#period-generated", Static)
            gen.update(f"Generated: {time_str}")
        except Exception:
            pass

    @on(PeriodTab.PeriodTabClicked)
    def handle_tab_click(self, event: PeriodTab.PeriodTabClicked) -> None:
        """Handle click on a period tab."""
        self.active_period = event.period
        self.post_message(self.PeriodChanged(event.period))


# ---------------------------------------------------------------------------
# Topic item (expandable with source tracing)
# ---------------------------------------------------------------------------


class TopicItem(Static):
    """An expandable topic with source tracing.

    Shows topic label + summary. Enter toggles expansion
    showing source thought snippets.
    """

    DEFAULT_CSS = """
    TopicItem {
        height: auto;
        margin: 0 1;
        padding: 0 1;
    }
    TopicItem:hover {
        background: $surface-darken-1;
    }
    .topic-header {
        height: auto;
    }
    .topic-sources {
        height: auto;
        margin: 0 2;
        padding: 0 1;
        border-left: solid $surface-darken-1;
        color: $text-muted;
        display: none;
    }
    .topic-sources.expanded {
        display: block;
    }
    """

    _expanded: bool = False

    can_focus = True

    def __init__(
        self,
        label: str,
        summary: str,
        thought_count: int = 0,
        source_thought_ids: list[str] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.topic_label = label
        self.summary = summary
        self.thought_count = thought_count
        self.source_thought_ids = source_thought_ids or []
        self._source_snippets: list[str] = []

    def compose(self) -> ComposeResult:
        header_text = Text()
        prefix = ">> " if not self._expanded else "vv "
        header_text.append(prefix, style="dim")
        header_text.append(f"{self.topic_label} ", style="bold")
        header_text.append(
            f"({self.thought_count} thought{'s' if self.thought_count != 1 else ''})",
            style="dim",
        )
        yield Static(header_text, classes="topic-header")
        # Summary line
        yield Static(f"  {self.summary}", classes="topic-summary")
        # Sources container (hidden by default)
        yield Static("", id=f"sources-{id(self)}", classes="topic-sources")

    def toggle_expansion(self) -> None:
        """Toggle the expanded state showing source thoughts."""
        self._expanded = not self._expanded
        try:
            sources = self.query_one(f"#sources-{id(self)}", Static)
            if self._expanded:
                sources.add_class("expanded")
                # Load source thoughts if not yet loaded
                if not self._source_snippets and self.source_thought_ids:
                    self._load_sources()
                else:
                    self._render_sources()
            else:
                sources.remove_class("expanded")

            # Update header prefix
            header = self.query_one(".topic-header", Static)
            header_text = Text()
            prefix = "vv " if self._expanded else ">> "
            header_text.append(prefix, style="dim")
            header_text.append(f"{self.topic_label} ", style="bold")
            header_text.append(
                f"({self.thought_count} thought{'s' if self.thought_count != 1 else ''})",
                style="dim",
            )
            header.update(header_text)
        except Exception:
            pass

    @work(thread=True)
    def _load_sources(self) -> None:
        """Load source thought snippets (worker thread)."""
        try:
            svc = self.app._get_service()
            snippets = []
            for tid in self.source_thought_ids[:5]:
                thought = svc.db.get_thought(tid)
                if thought:
                    created = _relative_time(thought.created_at)
                    content = thought.content[:100].replace("\n", " ")
                    snippets.append(f"{created}: {content}")
            self._source_snippets = snippets
            self.app.call_from_thread(self._render_sources)
        except Exception as exc:
            logger.warning("Failed to load source thoughts: %s", exc)

    def _render_sources(self) -> None:
        """Render source thought snippets into the sources container."""
        try:
            sources = self.query_one(f"#sources-{id(self)}", Static)
            if self._source_snippets:
                lines = ["Sources:"]
                for snippet in self._source_snippets:
                    lines.append(f"  {snippet}")
                sources.update("\n".join(lines))
            else:
                sources.update("  No source thoughts available.")
        except Exception:
            pass

    def on_key(self, event) -> None:
        """Handle Enter to toggle expansion."""
        if event.key == "enter":
            self.toggle_expansion()
            event.stop()


# ---------------------------------------------------------------------------
# Action item display
# ---------------------------------------------------------------------------


class ActionItemRow(Static):
    """An action item row with checkbox-style display."""

    DEFAULT_CSS = """
    ActionItemRow {
        height: 1;
        padding: 0 2;
    }
    """

    def __init__(self, action: dict, **kwargs) -> None:
        super().__init__(**kwargs)
        self.action = action

    def compose(self) -> ComposeResult:
        a = self.action
        content = a.get("content", "")
        person = a.get("person")
        carried = a.get("carried", False)

        line = Text()
        line.append("[ ] ", style="dim")
        line.append(content)
        if person:
            line.append(f" ({person})", style="#c678dd")
        if carried:
            line.append(" [carried]", style="yellow dim")
        yield Static(line)


# ---------------------------------------------------------------------------
# Resurfaced thought (FROM YOUR ARCHIVE)
# ---------------------------------------------------------------------------


class ResurfacedBox(Static):
    """Box showing a resurfaced older thought."""

    DEFAULT_CSS = """
    ResurfacedBox {
        height: auto;
        min-height: 3;
        margin: 1 2;
        padding: 0 1;
        border: solid $warning-darken-1;
        color: $text;
        background: $surface-darken-1;
        display: none;
    }
    ResurfacedBox.visible {
        display: block;
    }
    """

    def set_thought(self, thought: dict | None) -> None:
        """Set the resurfaced thought content."""
        if thought is None:
            self.remove_class("visible")
            return

        created_at = thought.get("created_at", "")
        content = thought.get("content", "").replace("\n", " ")
        time_str = _relative_time(created_at)

        text = Text()
        text.append("FROM YOUR ARCHIVE\n", style="bold dim")
        text.append(f"  {content[:200]}", style="")
        text.append(f" (captured {time_str})", style="dim")
        text.append("\n  \\[Enter] Open this thought", style="dim")
        self.update(text)
        self.add_class("visible")


# ---------------------------------------------------------------------------
# Digest content — the main body
# ---------------------------------------------------------------------------


class DigestContent(Static):
    """Renders the full digest with topics, actions, people, archive."""

    DEFAULT_CSS = """
    DigestContent {
        height: 1fr;
        overflow-y: auto;
        padding: 0 1;
    }
    .digest-section-header {
        height: 1;
        margin: 1 0 0 1;
        text-style: bold;
        color: $text-muted;
    }
    .digest-header-line {
        height: 1;
        margin: 0 1;
        text-style: bold;
    }
    .digest-people {
        height: auto;
        margin: 0 2;
        padding: 0 1;
    }
    .digest-loading {
        height: 3;
        margin: 2 4;
        color: $text-muted;
        text-style: italic;
    }
    .digest-empty {
        height: 3;
        margin: 2 4;
        color: $text-muted;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("", id="digest-header", classes="digest-header-line")
        yield Static("", id="digest-status", classes="digest-loading")
        # Sections will be dynamically added
        yield Vertical(id="digest-body")

    def show_loading(self, thought_count: int = 0, period: str = "daily") -> None:
        """Show loading state."""
        try:
            status = self.query_one("#digest-status", Static)
            if thought_count > 0:
                status.update(
                    f"Generating your {period} digest... Analyzing {thought_count} thoughts."
                )
            else:
                status.update(f"Generating your {period} digest...")
            status.display = True
        except Exception:
            pass

    def show_empty(self) -> None:
        """Show empty state."""
        try:
            status = self.query_one("#digest-status", Static)
            status.update("No thoughts captured in this period.")
            status.add_class("digest-empty")
            status.remove_class("digest-loading")
            status.display = True
        except Exception:
            pass

    def set_header(self, period: str, date_str: str, thought_count: int) -> None:
        """Set the digest header line."""
        try:
            header = self.query_one("#digest-header", Static)
            period_label = {
                "day": "YOUR DAY IN THOUGHTS",
                "week": "YOUR WEEK IN THOUGHTS",
                "month": "YOUR MONTH IN THOUGHTS",
            }.get(period, "YOUR DIGEST")
            header.update(
                f"{period_label} -- {date_str} -- "
                f"{thought_count} thought{'s' if thought_count != 1 else ''} captured"
            )
        except Exception:
            pass

    def populate(self, digest_data: dict) -> None:
        """Populate the digest content from service data."""
        try:
            # Hide loading
            status = self.query_one("#digest-status", Static)
            status.display = False
        except Exception:
            pass

        try:
            body = self.query_one("#digest-body", Vertical)
            # Remove previous children
            body.remove_children()
        except Exception:
            return

        source_map = digest_data.get("source_map", {})
        digest_text = digest_data.get("digest", "")
        action_items = digest_data.get("action_items", [])
        people = digest_data.get("people_mentioned", [])
        resurfaced = digest_data.get("resurfaced")

        # -- TOPICS section --
        body.mount(Static("-- TOPICS", classes="digest-section-header"))

        if source_map:
            for topic_label, thought_ids in source_map.items():
                # Extract a summary from the digest text for this topic
                summary = self._extract_topic_summary(digest_text, topic_label)
                topic = TopicItem(
                    label=topic_label,
                    summary=summary,
                    thought_count=len(thought_ids) if isinstance(thought_ids, list) else 0,
                    source_thought_ids=thought_ids if isinstance(thought_ids, list) else [],
                )
                body.mount(topic)
        else:
            # No source map — show raw digest text as a single block
            if digest_text:
                body.mount(Static(f"  {digest_text[:500]}", classes="digest-people"))

        # -- ACTION ITEMS section --
        body.mount(Static("-- ACTION ITEMS", classes="digest-section-header"))
        if action_items:
            for action in action_items:
                body.mount(ActionItemRow(action))
        else:
            body.mount(Static("  No action items.", classes="digest-people"))

        # -- PEOPLE MENTIONED section --
        body.mount(Static("-- PEOPLE MENTIONED", classes="digest-section-header"))
        if people:
            people_text = Text()
            for i, person in enumerate(people):
                if i > 0:
                    people_text.append(", ")
                people_text.append(person, style="#c678dd bold")
            body.mount(Static(people_text, classes="digest-people"))
        else:
            body.mount(Static("  No people mentioned.", classes="digest-people"))

        # -- FROM YOUR ARCHIVE section --
        archive_box = ResurfacedBox(id="resurfaced-box")
        body.mount(archive_box)
        archive_box.set_thought(resurfaced)

    def _extract_topic_summary(self, digest_text: str, topic_label: str) -> str:
        """Try to extract a summary snippet for a topic from the digest text.

        Falls back to a generic message if the topic label isn't found.
        """
        # Simple heuristic: look for the topic label in the digest text
        lower_text = digest_text.lower()
        lower_label = topic_label.lower()
        idx = lower_text.find(lower_label)
        if idx >= 0:
            # Extract a snippet after the label
            start = idx + len(lower_label)
            snippet = digest_text[start: start + 200].strip()
            # Clean up leading punctuation
            snippet = snippet.lstrip(":.;- ")
            # Take first sentence or 150 chars
            for end_char in (".", "!", "\n"):
                end_idx = snippet.find(end_char)
                if 0 < end_idx < 150:
                    snippet = snippet[: end_idx + 1]
                    break
            if len(snippet) > 150:
                snippet = snippet[:147] + "..."
            return snippet if snippet else f"Discussion of {topic_label}."
        return f"Discussion of {topic_label}."


# ---------------------------------------------------------------------------
# Main digest view
# ---------------------------------------------------------------------------


class DigestView(Static):
    """Digest view — the morning briefing.

    Shows generated digest with topics, action items,
    people mentioned, and resurfaced archive thoughts.
    """

    DEFAULT_CSS = """
    DigestView {
        height: 1fr;
    }
    """

    BINDINGS = [
        Binding("g", "regenerate", "Regenerate", show=False),
    ]

    _current_period: str = "day"
    _digest_data: dict | None = None

    def compose(self) -> ComposeResult:
        yield PeriodSelector(id="period-selector")
        yield DigestContent(id="digest-content")

    def on_mount(self) -> None:
        """Load digest on mount."""
        self.generate_digest()

    def on_period_selector_period_changed(self, message) -> None:
        """Handle period change from the selector."""
        if hasattr(message, "period"):
            self._current_period = message.period
            self.generate_digest()

    @work(thread=True)
    def generate_digest(self) -> None:
        """Generate digest via the service (worker thread)."""
        try:
            svc = self.app._get_service()

            # Count thoughts in period first for loading state
            self.app.call_from_thread(self._show_loading)

            result = svc.digest(period=self._current_period)
            self._digest_data = result

            self.app.call_from_thread(self._populate_digest, result)
        except Exception as exc:
            logger.warning("Failed to generate digest: %s", exc)
            self.app.call_from_thread(self._show_error, str(exc))

    def _show_loading(self) -> None:
        """Show loading state."""
        try:
            content = self.query_one("#digest-content", DigestContent)
            period_label = {"day": "daily", "week": "weekly", "month": "monthly"}.get(
                self._current_period, self._current_period
            )
            content.show_loading(period=period_label)
        except Exception:
            pass

    def _show_error(self, error: str) -> None:
        """Show error in digest."""
        self.app.notify(f"Digest generation failed: {error}", severity="error")
        try:
            content = self.query_one("#digest-content", DigestContent)
            content.show_empty()
        except Exception:
            pass

    def _populate_digest(self, result: dict) -> None:
        """Populate the digest view with results (main thread)."""
        thought_count = result.get("thought_count", 0)

        if thought_count == 0:
            try:
                content = self.query_one("#digest-content", DigestContent)
                content.show_empty()
            except Exception:
                pass
            return

        # Set header
        now = datetime.now(timezone.utc)
        date_str = now.strftime("%B %d, %Y")

        try:
            content = self.query_one("#digest-content", DigestContent)
            content.set_header(self._current_period, date_str, thought_count)
            content.populate(result)
        except Exception:
            pass

        # Set generated time
        try:
            selector = self.query_one("#period-selector", PeriodSelector)
            gen_time = now.strftime("%b %d, %Y %I:%M%p")
            selector.set_generated_time(gen_time)
        except Exception:
            pass

    def action_regenerate(self) -> None:
        """Regenerate the digest for the current period."""
        self.app.notify(
            f"Regenerating {self._current_period} digest...",
            severity="information",
        )
        self.generate_digest()
