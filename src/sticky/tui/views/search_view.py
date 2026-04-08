"""Search view for sticky TUI.

Features:
- Search input focused when view is active
- Mode indicator: HYBRID / VECTOR / FTS — Ctrl+M cycles
- Dismissable score legend hint for first-time users
- Results list with colored score brackets
- Filter bar with dropdowns
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from rich.text import Text
from textual import on, work
from textual.containers import Horizontal
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Input, ListItem, ListView, Static

from sticky.tui.widgets.thought_row import (
    _category_badge,
    _relative_time,
)

if TYPE_CHECKING:
    from textual.app import ComposeResult

logger = logging.getLogger(__name__)


# Search modes
_SEARCH_MODES = ["hybrid", "vector", "fts"]

# Category filter options — "All" means no filter
_SEARCH_CATEGORIES = ["All", "idea", "project", "person", "meeting", "action", "reference", "journal"]


def _score_bracket(score: float) -> Text:
    """Return a colored score bracket.

    - green (#4ec9b0): score >= 0.7
    - yellow (#d4a843): score 0.4-0.7
    - red (#e06c75): score < 0.4
    """
    if score >= 0.7:
        color = "#4ec9b0"
    elif score >= 0.4:
        color = "#d4a843"
    else:
        color = "#e06c75"
    return Text(f"[{score:.2f}]", style=color)


class ScoreLegendHint(Static):
    """Dismissable first-time hint explaining score colors.

    Only shown until dismissed (tracked via config).
    """

    DEFAULT_CSS = """
    ScoreLegendHint {
        height: 1;
        margin: 0 1;
        color: $text-muted;
        text-style: dim;
        display: none;
    }
    ScoreLegendHint.visible {
        display: block;
    }
    """

    def __init__(self) -> None:
        super().__init__(
            "Scores: green >0.7 strong | yellow >0.4 partial | "
            "red <0.4 weak  \\[Enter] Got it",
            id="score-hint",
        )

    def show(self) -> None:
        self.add_class("visible")

    def dismiss(self) -> None:
        self.remove_class("visible")


class SearchModeIndicator(Static):
    """Shows the current search mode: HYBRID, VECTOR, FTS."""

    DEFAULT_CSS = """
    SearchModeIndicator {
        height: 1;
        margin: 0 2;
        color: $text-muted;
    }
    """

    mode: reactive[str] = reactive("hybrid")

    def render(self) -> str:
        return f"Mode: \\[{self.mode.upper()}]"

    def cycle_mode(self) -> str:
        """Cycle through search modes and return the new mode."""
        idx = _SEARCH_MODES.index(self.mode) if self.mode in _SEARCH_MODES else 0
        new_idx = (idx + 1) % len(_SEARCH_MODES)
        self.mode = _SEARCH_MODES[new_idx]
        return self.mode


class SearchFilterBar(Static):
    """Filter bar for search results.

    Click the category area (x < 25) to cycle through categories.
    """

    DEFAULT_CSS = """
    SearchFilterBar {
        height: 1;
        margin: 0 1;
        color: $text-muted;
        display: none;
    }
    SearchFilterBar.visible {
        display: block;
    }
    """

    category: reactive[str] = reactive("All")
    result_count: reactive[int] = reactive(0)
    search_time: reactive[str] = reactive("")

    class FiltersChanged(Message):
        """Posted when any filter value changes."""

        def __init__(self, category: str | None) -> None:
            self.category = category
            super().__init__()

    def render(self) -> str:
        cat_label = self.category
        cat_style = "[bold]" if self.category != "All" else ""
        cat_end = "[/bold]" if self.category != "All" else ""
        parts = [
            f"{cat_style}Category \\[{cat_label}]{cat_end}"
            f"  Entity \\[All]  Date \\[All]"
        ]
        if self.result_count > 0:
            time_info = f" ({self.search_time})" if self.search_time else ""
            parts.append(f" \u2014 {self.result_count} results{time_info}")
        return "".join(parts)

    def on_click(self, event) -> None:
        """Cycle category when clicking the category area."""
        if event.x < 25:
            idx = _SEARCH_CATEGORIES.index(self.category) if self.category in _SEARCH_CATEGORIES else 0
            self.category = _SEARCH_CATEGORIES[(idx + 1) % len(_SEARCH_CATEGORIES)]

    def watch_category(self, _old: str, _new: str) -> None:
        """Post FiltersChanged when category changes."""
        cat = self.category if self.category != "All" else None
        self.post_message(self.FiltersChanged(cat))


class SearchResultRow(ListItem):
    """A search result row with colored score bracket.

    Format: [0.83] person  Mar 22  Sarah mentioned...
    """

    DEFAULT_CSS = """
    SearchResultRow {
        height: auto;
        min-height: 2;
        padding: 0 1;
    }
    SearchResultRow > Static {
        height: auto;
    }
    """

    def __init__(self, result: dict, **kwargs) -> None:
        super().__init__(**kwargs)
        self.result = result

    def compose(self):
        """Compose the result row content."""
        r = self.result
        score = r.get("score", 0.0)
        category = r.get("category")
        created_at = r.get("created_at", "")
        content = r.get("content", "").replace("\n", " ")

        # First line: score + category + time + content
        line1 = Text()
        line1.append_text(_score_bracket(score))
        line1.append(" ")
        line1.append_text(_category_badge(category))
        line1.append(_relative_time(created_at).ljust(10), style="dim")
        line1.append(" ")
        # Truncate content
        max_content = 70
        if len(content) > max_content:
            content = content[: max_content - 3] + "..."
        line1.append(content)

        # Second line: entities if present
        metadata = r.get("metadata", {})
        entities = []
        if isinstance(metadata, dict):
            entities = metadata.get("entities", [])

        if entities:
            line2 = Text()
            line2.append("       Entities: ", style="dim")
            line2.append(", ".join(entities), style="dim italic")
            yield Static(line1)
            yield Static(line2)
        else:
            yield Static(line1)


class SearchResultsList(ListView):
    """Scrollable list of search results."""

    DEFAULT_CSS = """
    SearchResultsList {
        height: 1fr;
        margin: 0 0;
    }
    """


class SearchView(Static):
    """The search view with search input, mode indicator, and results list."""

    DEFAULT_CSS = """
    SearchView {
        height: 1fr;
    }
    """

    _filter_category: str | None = None
    _last_query: str = ""

    class SearchPerformed(Message):
        """Posted when a search is performed."""

        def __init__(self, query: str, result_count: int) -> None:
            self.query = query
            self.result_count = result_count
            super().__init__()

    def compose(self) -> ComposeResult:
        with Horizontal(classes="search-header"):
            yield Input(
                placeholder="Search...",
                id="search-input",
            )
            yield Static("\\[x] clear", id="search-clear", classes="search-clear-btn")
        yield SearchModeIndicator(id="search-mode")
        yield SearchFilterBar(id="search-filter-bar")
        yield ScoreLegendHint()
        yield SearchResultsList(id="search-results")

    def on_mount(self) -> None:
        """Show score hint if not yet dismissed."""
        self._check_score_hint()

    def _check_score_hint(self) -> None:
        """Show the score legend hint if not previously dismissed."""
        try:
            svc = self.app._get_service()
            if not svc.config.tui_score_hint_shown:
                hint = self.query_one("#score-hint", ScoreLegendHint)
                hint.show()
        except Exception:
            pass

    @on(Input.Submitted, "#search-input")
    def handle_search(self, event: Input.Submitted) -> None:
        """Handle Enter in the search input — perform search."""
        query = event.value.strip()
        if not query:
            return
        self.perform_search(query)

    @on(SearchFilterBar.FiltersChanged)
    def handle_filters_changed(self, event: SearchFilterBar.FiltersChanged) -> None:
        """Update filter state and re-run the last search when filters change."""
        self._filter_category = event.category
        if self._last_query:
            self.perform_search(self._last_query)

    @work(thread=True)
    def perform_search(self, query: str) -> None:
        """Perform search via the service (in worker thread)."""
        self._last_query = query
        try:
            svc = self.app._get_service()
            mode_indicator = self.app.call_from_thread(
                self.query_one, "#search-mode", SearchModeIndicator
            )
            mode = mode_indicator.mode if mode_indicator else "hybrid"

            kwargs: dict = {"query": query, "limit": 20, "mode": mode}
            if self._filter_category:
                kwargs["category"] = self._filter_category

            results = svc.search(**kwargs)
            self.app.call_from_thread(self._populate_results, results, query)
        except Exception as exc:
            logger.warning("Search failed: %s", exc)
            self.app.call_from_thread(
                self.app.notify, f"Search failed: {exc}", severity="error"
            )

    def _populate_results(self, results: list[dict], query: str) -> None:
        """Replace the result list contents (must be called from main thread)."""
        results_list = self.query_one("#search-results", SearchResultsList)
        results_list.clear()
        for r in results:
            results_list.append(SearchResultRow(r))

        # Update filter bar
        filter_bar = self.query_one("#search-filter-bar", SearchFilterBar)
        filter_bar.result_count = len(results)
        if results:
            search_time = results[0].get("search_time_ms", 0)
            filter_bar.search_time = f"{search_time:.0f}ms"
        else:
            filter_bar.search_time = ""
        filter_bar.add_class("visible")

    def focus_search_input(self) -> None:
        """Focus the search input."""
        try:
            inp = self.query_one("#search-input", Input)
            inp.focus()
        except Exception:
            pass

    def cycle_search_mode(self) -> None:
        """Cycle through search modes."""
        try:
            mode_indicator = self.query_one("#search-mode", SearchModeIndicator)
            new_mode = mode_indicator.cycle_mode()
            self.app.notify(f"Search mode: {new_mode.upper()}", severity="information")
        except Exception:
            pass

    def dismiss_score_hint(self) -> None:
        """Dismiss the score legend hint."""
        try:
            hint = self.query_one("#score-hint", ScoreLegendHint)
            hint.dismiss()
            # Persist dismissal
            svc = self.app._get_service()
            svc.set_config_value("tui_score_hint_shown", True)
        except Exception:
            pass
