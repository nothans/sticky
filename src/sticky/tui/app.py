"""Main Textual TUI application for sticky.

Manages views, tabs, status bar, and keybinding legend.
Smart default view: checks for new thoughts since last digest.
"""

from __future__ import annotations

import logging
import threading

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Button, Header, Static, TabbedContent, TabPane

from textual.widgets import ListView

from sticky.tui.views.about import LOGO, LOGO_COLORS, TAGLINE, AboutView
from sticky.tui.views.command_palette import CommandPalette
from sticky.tui.views.detail import DetailScreen
from sticky.tui.views.digest_view import DigestView
from sticky.tui.views.entities_view import EntitiesView
from sticky.tui.views.home import HomeView
from sticky.tui.views.review import ReviewView
from sticky.tui.views.search_view import SearchView
from sticky.tui.views.settings import SettingsScreen
from sticky.tui.views.stats_view import StatsView
from sticky.tui.widgets.keybinding_legend import KeybindingLegend
from sticky.tui.widgets.status_bar import StatusBar
from sticky.tui.widgets.thought_row import ThoughtRow

logger = logging.getLogger(__name__)

SPLASH_DURATION = 2.0


class SplashScreen(Screen):
    """Brief splash screen showing the animated logo on startup."""

    DEFAULT_CSS = """
    SplashScreen {
        align: center middle;
        background: $surface;
    }
    #splash-container {
        width: 60;
        height: auto;
        align: center middle;
    }
    #splash-logo {
        width: 1fr;
        height: auto;
        text-align: center;
    }
    #splash-tagline {
        width: 1fr;
        text-align: center;
        color: $text-muted;
        text-style: italic;
        padding: 1 0;
    }
    """

    def compose(self) -> ComposeResult:
        from textual.containers import Vertical
        with Vertical(id="splash-container"):
            color = LOGO_COLORS[0]
            yield Static(f"[bold {color}]{LOGO}[/]", id="splash-logo")
            yield Static(f"[italic]{TAGLINE}[/]", id="splash-tagline")

    def on_mount(self) -> None:
        self._color_index = 0
        self._timer = self.set_interval(0.4, self._cycle_color)
        self.set_timer(SPLASH_DURATION, self._dismiss)

    def _cycle_color(self) -> None:
        self._color_index = (self._color_index + 1) % len(LOGO_COLORS)
        color = LOGO_COLORS[self._color_index]
        self.query_one("#splash-logo", Static).update(f"[bold {color}]{LOGO}[/]")

    def _dismiss(self) -> None:
        self._timer.stop()
        self.dismiss()

    def on_key(self, event) -> None:
        """Allow skipping the splash with any key press."""
        self._timer.stop()
        self.dismiss()

    def on_click(self, event) -> None:
        """Allow skipping the splash with a click."""
        self._timer.stop()
        self.dismiss()


class QuitScreen(Screen):
    """Simple quit confirmation with a clickable button."""

    DEFAULT_CSS = """
    QuitScreen {
        align: center middle;
        background: $surface 80%;
    }
    #quit-box {
        width: 44;
        height: 11;
        border: solid $accent;
        background: $surface;
        padding: 1 2;
        layout: vertical;
    }
    #quit-label {
        content-align: center middle;
        width: 1fr;
        height: 3;
        text-align: center;
    }
    #quit-buttons {
        layout: horizontal;
        align: center middle;
        height: 5;
    }
    .quit-btn {
        width: 16;
        min-width: 16;
        height: 3;
        margin: 0 1;
    }
    """

    def compose(self) -> ComposeResult:
        from textual.containers import Horizontal, Vertical
        with Vertical(id="quit-box"):
            yield Static("Quit sticky?", id="quit-label")
            with Horizontal(id="quit-buttons"):
                yield Button("Quit", variant="error", id="quit-yes", classes="quit-btn")
                yield Button("Cancel", variant="default", id="quit-cancel", classes="quit-btn")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "quit-yes":
            self.app.exit()
        else:
            self.dismiss()

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss()


CSS = """
Screen {
    layout: vertical;
}

TabbedContent {
    height: 1fr;
}

.search-header {
    height: 3;
    margin: 0 1;
}

.search-header Input {
    width: 1fr;
}

.search-clear-btn {
    width: 10;
    height: 3;
    content-align: center middle;
    color: $text-muted;
}

.placeholder-view {
    padding: 2 4;
    color: $text-muted;
}

TabPane {
    padding: 0;
}
"""


class StickyApp(App):
    """The sticky TUI application."""

    TITLE = "sticky"
    CSS = CSS
    BINDINGS = [
        Binding("q", "request_quit", "Quit", show=False),
        Binding("ctrl+c", "request_quit", "Quit", show=False, priority=True),
        Binding("ctrl+k", "command_palette", "Commands", show=False),
        Binding("slash", "focus_search", "Search", show=False),
        Binding("g", "show_digest", "Digest", show=False),
        Binding("r", "show_review", "Review", show=False),
        Binding("f", "toggle_filter", "Filter", show=False),
        Binding("ctrl+r", "refresh", "Refresh", show=False),
    ]

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._thread_local = threading.local()

    def _get_service(self):
        """Return a thread-local StickyService instance.

        Each thread (main or worker) gets its own service with its own
        SQLite connection to avoid thread-safety issues.
        """
        svc = getattr(self._thread_local, "service", None)
        if svc is None:
            from sticky.core.service import StickyService

            svc = StickyService()
            svc.initialize()
            self._thread_local.service = svc
        return svc

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(id="tabs"):
            with TabPane("Home", id="home"):
                yield HomeView(id="home-view")
            with TabPane("Search", id="search"):
                yield SearchView(id="search-view")
            with TabPane("Review", id="review"):
                yield ReviewView(id="review-view")
            with TabPane("Entities", id="entities"):
                yield EntitiesView(id="entities-view")
            with TabPane("Digest", id="digest"):
                yield DigestView(id="digest-view")
            with TabPane("Stats", id="stats"):
                yield StatsView(id="stats-view")
            with TabPane("About", id="about"):
                yield AboutView(id="about-view")
            with TabPane("Quit", id="quit-tab"):
                yield Static("")
        yield KeybindingLegend(id="keybinding-legend")
        yield StatusBar(id="status-bar")

    def on_mount(self) -> None:
        """Set up on app mount: show splash, focus capture input, load stats."""
        self.push_screen(SplashScreen(), callback=self._on_splash_dismissed)
        self.refresh_status_bar()

    def _on_splash_dismissed(self, result: object) -> None:
        """After splash screen closes, finish setting up the app."""
        self._focus_home_input()
        self._check_default_view()
        # Start periodic refresh timer (every 5 seconds)
        self.set_interval(5.0, self._periodic_refresh)

    def _periodic_refresh(self) -> None:
        """Periodically refresh the active view to pick up external changes."""
        try:
            tabs = self.query_one("#tabs", TabbedContent)
            active = tabs.active
        except Exception:
            return

        try:
            if active == "home":
                self.query_one("#home-view", HomeView).load_thoughts()
            elif active == "review":
                self.query_one("#review-view", ReviewView).load_review_items()
        except Exception:
            pass
        self.refresh_status_bar()

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        """Refresh data when the user switches tabs."""
        tab_id = event.pane.id if event.pane else ""
        try:
            if tab_id == "home":
                home = self.query_one("#home-view", HomeView)
                home.load_thoughts()
            elif tab_id == "review":
                review = self.query_one("#review-view", ReviewView)
                review.load_review_items()
            elif tab_id == "stats":
                stats = self.query_one("#stats-view", StatsView)
                if hasattr(stats, "load_stats"):
                    stats.load_stats()
            elif tab_id == "quit-tab":
                # Switch back to previous tab and show quit dialog
                self._switch_to_tab("home")
                self.action_request_quit()
                return
        except Exception:
            pass
        self.refresh_status_bar()

    def _focus_home_input(self) -> None:
        """Focus the capture input on the home view."""
        try:
            home = self.query_one("#home-view", HomeView)
            home.focus_capture_input()
        except Exception:
            pass

    def _check_default_view(self) -> None:
        """Switch to the correct default view based on config."""
        try:
            svc = self._get_service()
            default_view = svc.config.tui_default_view

            if default_view == "digest":
                self._switch_to_tab("digest")
            elif default_view == "auto":
                self._check_auto_view()
            # default_view == "home" or anything else: stay on Home
        except Exception as exc:
            logger.warning("Failed to check default view: %s", exc)

    @work(thread=True)
    def _check_auto_view(self) -> None:
        """Check if we should auto-switch to digest view and show digest banner.

        Consolidates the digest count query so both the tab switch and the
        home-view banner share the same DB read on the same worker thread.
        """
        try:
            svc = self._get_service()
            rows = svc.db.execute(
                "SELECT period_end FROM digests ORDER BY created_at DESC LIMIT 1"
            ).fetchall()

            if not rows:
                # No digests yet — check if any thoughts exist (first-time users)
                stats = svc.db.get_stats()
                total = stats.get("thoughts", {}).get("total", 0)
                if total > 0:
                    self.app.call_from_thread(self._show_digest_banner, total)
                return

            last_end = rows[0]["period_end"]
            count_row = svc.db.execute(
                "SELECT COUNT(*) FROM thoughts WHERE created_at > ?",
                (last_end,),
            ).fetchone()
            count = count_row[0] if count_row else 0

            if count > 0:
                self.app.call_from_thread(self._switch_to_tab, "digest")
                self.app.call_from_thread(self._show_digest_banner, count)
        except Exception as exc:
            logger.warning("Auto-view check failed: %s", exc)

    def _show_digest_banner(self, count: int) -> None:
        """Update the home view's digest banner with the given count."""
        try:
            from sticky.tui.views.home import DigestBanner

            home = self.query_one("#home-view", HomeView)
            banner = home.query_one("#digest-banner", DigestBanner)
            banner.set_count(count)
        except Exception:
            pass

    def _switch_to_tab(self, tab_id: str) -> None:
        """Switch to the given tab pane."""
        try:
            tabs = self.query_one("#tabs", TabbedContent)
            tabs.active = tab_id
        except Exception:
            pass

    @work(thread=True)
    def refresh_status_bar(self) -> None:
        """Refresh the status bar with current stats."""
        try:
            svc = self._get_service()
            db_stats = svc.db.get_stats()
            thought_count = db_stats.get("thoughts", {}).get("total", 0)
            review_count = db_stats.get("thoughts", {}).get("needs_review", 0)

            # Privacy info
            privacy = svc.privacy_info()
            flow = privacy.get("data_flow", {})
            embed_loc = flow.get("embeddings", "local").lower()
            llm_loc = flow.get("classification", "cloud").lower()

            self.app.call_from_thread(
                self._update_status_bar,
                thought_count,
                review_count,
                embed_loc,
                llm_loc,
            )

            self.app.call_from_thread(self._update_review_tab, review_count)
        except Exception as exc:
            logger.warning("Failed to refresh status bar: %s", exc)

    def _update_status_bar(
        self,
        thought_count: int,
        review_count: int,
        embed_loc: str,
        llm_loc: str,
    ) -> None:
        """Update status bar widget values (main thread)."""
        try:
            bar = self.query_one("#status-bar", StatusBar)
            bar.update_stats(thought_count, review_count, embed_loc, llm_loc)
        except Exception:
            pass

    def _update_review_tab(self, review_count: int) -> None:
        """Update the Review tab title with count badge."""
        try:
            tabs = self.query_one("#tabs", TabbedContent)
            tab = tabs.get_tab("review")
            if review_count > 0:
                tab.label = f"Review {review_count}"
            else:
                tab.label = "Review"
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Focus tracking for keybinding legend
    # ------------------------------------------------------------------

    def on_descendant_focus(self, event) -> None:
        """Update keybinding legend based on which widget gained focus."""
        try:
            legend = self.query_one("#keybinding-legend", KeybindingLegend)
            widget = event.widget

            widget_id = getattr(widget, "id", "") or ""

            if widget_id == "capture-input":
                legend.set_context("capture")
            elif widget_id == "search-input":
                legend.set_context("search")
            elif widget_id == "thought-list":
                legend.set_context("thought_list")
            elif widget_id == "search-results":
                legend.set_context("search")
            elif widget_id == "review-list":
                legend.set_context("review")
            else:
                # Walk up to check if inside a search, home, review, entities, or digest view
                node = widget
                while node is not None:
                    nid = getattr(node, "id", "") or ""
                    if nid == "search-view":
                        legend.set_context("search")
                        return
                    if nid == "home-view":
                        legend.set_context("thought_list")
                        return
                    if nid == "review-view":
                        legend.set_context("review")
                        return
                    if nid == "entities-view":
                        legend.set_context("entities")
                        return
                    if nid == "digest-view":
                        legend.set_context("digest")
                        return
                    if nid == "stats-view":
                        legend.set_context("stats")
                        return
                    node = getattr(node, "parent", None)
                legend.set_context("default")
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Open detail screen on thought selection
    # ------------------------------------------------------------------

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Open detail screen when a thought is selected (Enter) in any list."""
        item = event.item
        thought: dict | None = None

        # ThoughtRow from Home view
        if isinstance(item, ThoughtRow):
            thought = item.thought
        # SearchResultRow from Search view
        elif hasattr(item, "result"):
            thought = item.result
        # ReviewItem from Review view
        elif hasattr(item, "thought"):
            thought = item.thought

        if thought is None or "id" not in thought:
            return

        # Build context list from the parent ListView
        context_ids: list[str] = []
        context_index = 0
        try:
            parent_list = event.list_view
            for idx, child in enumerate(parent_list.children):
                child_thought = None
                if isinstance(child, ThoughtRow):
                    child_thought = child.thought
                elif hasattr(child, "result"):
                    child_thought = child.result
                elif hasattr(child, "thought"):
                    child_thought = child.thought

                if child_thought and "id" in child_thought:
                    context_ids.append(child_thought["id"])
                    if child_thought["id"] == thought["id"]:
                        context_index = len(context_ids) - 1
        except Exception:
            context_ids = [thought["id"]]
            context_index = 0

        self.push_screen(
            DetailScreen(
                thought_id=thought["id"],
                context=context_ids,
                context_index=context_index,
            ),
            callback=self._on_detail_dismissed,
        )

    def _on_detail_dismissed(self, result: object) -> None:
        """Handle detail screen dismissal — refresh views."""
        self.refresh_status_bar()
        try:
            home = self.query_one("#home-view", HomeView)
            home.load_thoughts()
        except Exception:
            pass
        try:
            review = self.query_one("#review-view", ReviewView)
            review.load_review_items()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Actions (key bindings)
    # ------------------------------------------------------------------

    def action_refresh(self) -> None:
        """Refresh the currently active view."""
        try:
            tabs = self.query_one("#tabs", TabbedContent)
            active = tabs.active
        except Exception:
            return

        try:
            if active == "home":
                self.query_one("#home-view", HomeView).load_thoughts()
            elif active == "review":
                self.query_one("#review-view", ReviewView).load_review_items()
            elif active == "entities":
                self.query_one("#entities-view", EntitiesView).load_entities()
            elif active == "stats":
                stats = self.query_one("#stats-view", StatsView)
                if hasattr(stats, "load_stats"):
                    stats.load_stats()
        except Exception:
            pass
        self.refresh_status_bar()
        self.notify("Refreshed", severity="information")

    def action_request_quit(self) -> None:
        """Show quit confirmation with clickable button."""
        self.push_screen(QuitScreen())

    def action_focus_search(self) -> None:
        """Switch to search tab and focus the search input."""
        focused = self.focused
        if focused and hasattr(focused, "id") and focused.id in (
            "capture-input",
            "search-input",
        ):
            return

        self._switch_to_tab("search")
        try:
            search_view = self.query_one("#search-view", SearchView)
            search_view.focus_search_input()
        except Exception:
            pass

    def action_show_digest(self) -> None:
        """Switch to the digest tab."""
        focused = self.focused
        if focused and hasattr(focused, "id") and focused.id in (
            "capture-input",
            "search-input",
        ):
            return
        self._switch_to_tab("digest")

    def action_show_review(self) -> None:
        """Switch to the review tab."""
        focused = self.focused
        if focused and hasattr(focused, "id") and focused.id in (
            "capture-input",
            "search-input",
        ):
            return
        self._switch_to_tab("review")

    def action_toggle_filter(self) -> None:
        """Toggle filter bar in the home view."""
        focused = self.focused
        if focused and hasattr(focused, "id") and focused.id in (
            "capture-input",
            "search-input",
        ):
            return
        try:
            home = self.query_one("#home-view", HomeView)
            home.toggle_filter_bar()
        except Exception:
            pass

    def on_status_bar_more_pressed(self, event: StatusBar.MorePressed) -> None:
        """Handle the More button click from the status bar."""
        self.action_command_palette()

    def action_command_palette(self) -> None:
        """Open the command palette."""
        self.push_screen(CommandPalette(), callback=self._handle_command)

    def _handle_command(self, result: str | None) -> None:
        """Handle a command selected from the palette."""
        if result is None:
            return

        if result == "settings":
            self.push_screen(SettingsScreen())
        elif result == "digest":
            self._switch_to_tab("digest")
        elif result == "review":
            self._switch_to_tab("review")
        elif result == "search":
            self.action_focus_search()
        elif result == "stats":
            self._switch_to_tab("stats")
        elif result == "export_markdown":
            self.notify("Use CLI: sticky export --format markdown", severity="information")
        elif result == "export_json":
            self.notify("Use CLI: sticky export --format json", severity="information")
        elif result == "import_data":
            self.notify("Use CLI: sticky import <path>", severity="information")
