"""One search box using the shared hybrid search behavior."""

from __future__ import annotations

import logging

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Button, Input, ListItem, ListView, Static, TabbedContent

from sticky.tui.widgets.thought_row import _category_badge, _relative_time

logger = logging.getLogger(__name__)


class SearchResultRow(ListItem):
    DEFAULT_CSS = """
    SearchResultRow { height: auto; min-height: 2; padding: 0 1; }
    SearchResultRow > Static { height: auto; }
    """

    def __init__(self, result: dict, **kwargs) -> None:
        super().__init__(**kwargs)
        self.result = result

    def compose(self) -> ComposeResult:
        line = Text()
        line.append_text(_category_badge(self.result.get("category")))
        line.append(_relative_time(self.result.get("created_at", "")).ljust(10))
        line.append(" " + self.result.get("content", "").replace("\n", " "))
        yield Static(line)


class SearchResultsList(ListView):
    DEFAULT_CSS = "SearchResultsList { height: 1fr; }"


class SearchView(Static):
    DEFAULT_CSS = """
    SearchView { height: 1fr; }
    #search-status { height: auto; margin: 0 2 1 2; }
    #search-clear { min-width: 9; }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._last_query = ""
        self._search_generation = 0

    def compose(self) -> ComposeResult:
        with Horizontal(classes="search-header"):
            yield Input(placeholder="Find a note…", id="search-input")
            yield Button("Clear", id="search-clear")
        yield Static("Enter searches your notes. Escape returns Home.", id="search-status", markup=False)
        yield SearchResultsList(id="search-results")

    @on(Input.Submitted, "#search-input")
    def handle_search(self, event: Input.Submitted) -> None:
        query = event.value.strip()
        if query:
            self._start_search(query)

    def _start_search(self, query: str) -> None:
        self._last_query = query
        self._search_generation += 1
        self.query_one("#search-status", Static).update("Searching…")
        self.perform_search(query, self._search_generation)

    @work(thread=True, exclusive=True)
    def perform_search(self, query: str, generation: int) -> None:
        try:
            results = self.app._get_service().search(query=query)
        except Exception as exc:
            logger.warning("Search failed: %s", exc)
            self.app.call_from_thread(self._search_failed, str(exc), generation)
        else:
            self.app.call_from_thread(self._populate_results, results, generation)

    async def _search_failed(self, error: str, generation: int) -> None:
        if not self.is_mounted or generation != self._search_generation:
            return
        await self.query_one(SearchResultsList).clear()
        if generation == self._search_generation:
            self.query_one("#search-status", Static).update(f"Search failed: {error}. Press Enter to retry.")

    async def _populate_results(self, results: list[dict], generation: int) -> None:
        if not self.is_mounted or generation != self._search_generation:
            return
        result_list = self.query_one(SearchResultsList)
        await result_list.clear()
        if generation != self._search_generation:
            return
        rows = [SearchResultRow(result) for result in results]
        await result_list.extend(rows)
        if generation != self._search_generation:
            # A Clear or newer query may arrive while rows are mounting.
            await result_list.remove_children(row for row in rows if row.parent is result_list)
            return
        result_list.index = 0 if results else None
        noun = "note" if len(results) == 1 else "notes"
        message = f"{len(results)} {noun} found. Select a note to open it." if results else "No matching notes. Try different words."
        self.query_one("#search-status", Static).update(message)

    @on(Button.Pressed, "#search-clear")
    async def clear_search(self) -> None:
        self._search_generation += 1
        generation = self._search_generation
        self._last_query = ""
        self.query_one("#search-input", Input).value = ""
        await self.query_one(SearchResultsList).clear()
        if generation != self._search_generation:
            return
        self.query_one("#search-status", Static).update("Enter searches your notes. Escape returns Home.")
        self.focus_search_input()

    def refresh_results(self) -> None:
        if self._last_query:
            self._start_search(self._last_query)

    def focus_search_input(self) -> None:
        if not self.is_mounted or self.screen is not self.app.screen:
            return
        if self.screen.query_one("#tabs", TabbedContent).active != "search":
            return
        self.screen.set_focus(self.query_one("#search-input", Input))
