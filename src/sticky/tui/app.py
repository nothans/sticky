"""A small terminal interface for capturing and finding notes."""

from __future__ import annotations

import logging
import threading

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Header, ListView, TabbedContent, TabPane

from sticky.tui.views.about import AboutView
from sticky.tui.views.detail import DetailScreen
from sticky.tui.views.home import HomeView
from sticky.tui.views.search_view import SearchResultRow, SearchView
from sticky.tui.widgets.status_bar import StatusBar
from sticky.tui.widgets.thought_row import ThoughtRow

logger = logging.getLogger(__name__)


class NavigationTabs(TabbedContent):
    """Tabs change through explicit navigation, never a queued pane focus."""

    @on(TabPane.Focused)
    def keep_explicit_navigation(self, event: TabPane.Focused) -> None:
        # Textual normally activates the pane, even if this focus message is old.
        # Tab clicks, arrow keys, and app shortcuts already set the active tab.
        event.prevent_default()
        event.stop()


class StickyApp(App):
    """Home, Search, and About; startup only reads local data."""

    TITLE = "sticky"
    ENABLE_COMMAND_PALETTE = False
    CSS = """
    Screen { layout: vertical; background: #20252b; color: #dde3e8; }
    TabbedContent { height: 1fr; }
    TabPane { padding: 0; }
    .search-header { height: 3; margin: 0 1; }
    .search-header Input { width: 1fr; }
    Header { background: #252c34; color: #dde3e8; }
    Input { background: #252c34; color: #dde3e8; border: tall #778da0; }
    Input:focus { border: tall #9bbbd4; }
    Input > .input--placeholder { color: #b1bec9; }
    Button { background: #29394a; color: #dde3e8; border: tall #778da0; }
    Button:focus { background: #3d5469; border: tall #9bbbd4; }
    Button:hover { background: #3d5469; }
    TextArea { background: #252c34; color: #dde3e8; border: tall #778da0; }
    ListView { background: #20252b; }
    ListView > ListItem.--highlight { background: #3d5469; }
    """
    BINDINGS = [
        Binding("q", "quit", "Quit", show=False),
        Binding("ctrl+c", "quit", "Quit", show=False, priority=True),
        Binding("ctrl+f", "focus_search", "Search", show=False, priority=True),
        Binding("slash", "focus_search", "Search", show=False),
        Binding("escape", "home", "Home", show=False),
        Binding("ctrl+r", "refresh", "Refresh", show=False),
    ]

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._thread_local = threading.local()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if isinstance(self.screen, DetailScreen) and action in {"focus_search", "refresh", "home"}:
            return False
        return True

    def _get_service(self):
        """Keep SQLite connections on the thread that owns them."""
        svc = getattr(self._thread_local, "service", None)
        if svc is None:
            from sticky.core.service import StickyService

            svc = StickyService()
            svc.initialize()
            self._thread_local.service = svc
        return svc

    def compose(self) -> ComposeResult:
        yield Header()
        with NavigationTabs(id="tabs"):
            with TabPane("Home", id="home"):
                yield HomeView(id="home-view")
            with TabPane("Search", id="search"):
                yield SearchView(id="search-view")
            with TabPane("About", id="about"):
                yield AboutView(id="about-view")
        yield StatusBar(id="status-bar")

    def on_mount(self) -> None:
        self.call_after_refresh(self._focus_home_input)
        self.refresh_status_bar()

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        tab_id = event.pane.id if event.pane else ""
        if tab_id == "home":
            self.query_one(HomeView).load_thoughts()
            self.call_after_refresh(self._focus_home_input)
        elif tab_id == "search":
            self.call_after_refresh(self._focus_search_input)
        elif tab_id == "about":
            self.call_after_refresh(self._focus_about)
        self.refresh_status_bar()

    def _focus_home_input(self) -> None:
        if self.query_one("#tabs", TabbedContent).active == "home":
            self.query_one(HomeView).focus_capture_input()

    def _focus_search_input(self) -> None:
        if self.query_one("#tabs", TabbedContent).active == "search":
            self.query_one(SearchView).focus_search_input()

    def _focus_about(self) -> None:
        view = self.query_one(AboutView)
        if view.screen is self.screen and self.query_one("#tabs", TabbedContent).active == "about":
            self.screen.set_focus(view)

    def _switch_to_tab(self, tab_id: str) -> None:
        self.query_one("#tabs", TabbedContent).active = tab_id

    @work(thread=True, exclusive=True)
    def refresh_status_bar(self) -> None:
        try:
            stats = self._get_service().stats()
            count = stats.get("thoughts", {}).get("total", 0)
            self.call_from_thread(self._update_status_bar, count)
        except Exception as exc:
            logger.warning("Failed to refresh status: %s", exc)

    def _update_status_bar(self, count: int) -> None:
        self.query_one(StatusBar).update_stats(count)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if isinstance(item, ThoughtRow):
            thought = item.thought
        elif isinstance(item, SearchResultRow):
            thought = item.result
        else:
            return

        context = []
        for child in event.list_view.children:
            data = child.thought if isinstance(child, ThoughtRow) else getattr(child, "result", {})
            if "id" in data:
                context.append(data["id"])
        self.push_screen(
            DetailScreen(thought["id"], context, context.index(thought["id"])),
            callback=self._on_detail_dismissed,
        )

    def _on_detail_dismissed(self, result: object) -> None:
        self.query_one(HomeView).load_thoughts()
        self.query_one(SearchView).refresh_results()
        self.refresh_status_bar()

    def action_home(self) -> None:
        self._switch_to_tab("home")
        self.call_after_refresh(self._focus_home_input)

    def action_focus_search(self) -> None:
        self._switch_to_tab("search")
        self.call_after_refresh(self._focus_search_input)

    def action_refresh(self) -> None:
        active = self.query_one("#tabs", TabbedContent).active
        if active == "home":
            self.query_one(HomeView).load_thoughts()
        elif active == "search":
            self.query_one(SearchView).refresh_results()
        self.refresh_status_bar()
