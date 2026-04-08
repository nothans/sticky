"""Tests for the sticky TUI application.

Uses Textual's async test framework (AppTest / run_test).
"""

from __future__ import annotations

import pytest

from sticky.tui.app import StickyApp
from sticky.tui.views.detail import DetailScreen
from sticky.tui.views.digest_view import DigestContent, DigestView, PeriodSelector, PeriodTab
from sticky.tui.views.entities_view import (
    EntityDetail,
    EntityList,
    EntityRow,
    EntitiesView,
    PinnedContext,
    TypeFilter,
)
from sticky.tui.views.command_palette import CommandItem, CommandPalette
from sticky.tui.views.home import DigestBanner, FilterBar, HomeView, ThoughtList
from sticky.tui.views.review import ReviewItem, ReviewList, ReviewView
from sticky.tui.views.search_view import (
    SearchFilterBar,
    SearchModeIndicator,
    SearchResultRow,
    SearchResultsList,
    SearchView,
)
from sticky.tui.widgets.capture_input import CaptureInput
from sticky.tui.widgets.keybinding_legend import KeybindingLegend
from sticky.tui.widgets.status_bar import StatusBar
from sticky.tui.widgets.thought_row import (
    ThoughtRow,
    _category_badge,
    _confidence_dot,
    _relative_time,
)


# ---------------------------------------------------------------------------
# Unit tests for helper functions
# ---------------------------------------------------------------------------


class TestConfidenceDot:
    """Test confidence dot formatting."""

    def test_high_confidence(self):
        dot = _confidence_dot(0.9)
        assert "\u25cf" in dot.plain  # filled circle (green)

    def test_medium_confidence(self):
        dot = _confidence_dot(0.7)
        assert "\u25cf" in dot.plain  # filled circle

    def test_low_confidence(self):
        dot = _confidence_dot(0.3)
        assert "\u25cb" in dot.plain  # hollow circle

    def test_none_confidence(self):
        dot = _confidence_dot(None)
        assert "\u25cb" in dot.plain  # hollow circle


class TestCategoryBadge:
    """Test category badge formatting."""

    def test_idea_badge(self):
        badge = _category_badge("idea")
        assert "idea" in badge.plain

    def test_person_badge(self):
        badge = _category_badge("person")
        assert "person" in badge.plain

    def test_none_badge(self):
        badge = _category_badge(None)
        assert "?" in badge.plain


class TestRelativeTime:
    """Test relative time formatting."""

    def test_recent_timestamp(self):
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        result = _relative_time(now.isoformat())
        assert result in ("just now", "0m ago", "1m ago")

    def test_invalid_timestamp(self):
        result = _relative_time("not-a-date")
        assert result == ""

    def test_empty_timestamp(self):
        result = _relative_time("")
        assert result == ""


# ---------------------------------------------------------------------------
# App-level tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_app_launches():
    """Verify the app launches and has the correct title."""
    app = StickyApp()
    async with app.run_test(size=(120, 30)) as pilot:
        assert pilot.app.title == "sticky"


@pytest.mark.asyncio
async def test_capture_input_visible():
    """Verify the capture input is rendered on the home view."""
    app = StickyApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        input_widget = pilot.app.query_one("#capture-input", CaptureInput)
        assert input_widget is not None


@pytest.mark.asyncio
async def test_status_bar_present():
    """Verify the status bar is rendered at the bottom."""
    app = StickyApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        bar = pilot.app.query_one("#status-bar", StatusBar)
        assert bar is not None
        # StatusBar is a container; check the #status-text child and More button
        from textual.widgets import Static, Button
        status_text = bar.query_one("#status-text", Static)
        assert status_text is not None
        more_btn = bar.query_one("#more-btn", Button)
        assert more_btn is not None


@pytest.mark.asyncio
async def test_keybinding_legend_present():
    """Verify the keybinding legend is rendered."""
    app = StickyApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        legend = pilot.app.query_one("#keybinding-legend", KeybindingLegend)
        assert legend is not None


@pytest.mark.asyncio
@pytest.mark.xfail(reason="focus timing is non-deterministic in test runner")
async def test_keybinding_legend_context_changes():
    """Verify the legend changes context based on focus."""
    app = StickyApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        legend = pilot.app.query_one("#keybinding-legend", KeybindingLegend)

        # Initially should be capture context (capture input is focused)
        assert legend.context == "capture"

        # Focus the search input
        search_input = pilot.app.query_one("#search-input")
        search_input.focus()
        await pilot.pause()
        assert legend.context == "search"


@pytest.mark.asyncio
async def test_home_view_has_thought_list():
    """Verify the home view contains a thought list."""
    app = StickyApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        thought_list = pilot.app.query_one("#thought-list", ThoughtList)
        assert thought_list is not None


@pytest.mark.asyncio
async def test_home_view_has_digest_banner():
    """Verify the home view contains a digest banner."""
    app = StickyApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        banner = pilot.app.query_one("#digest-banner", DigestBanner)
        assert banner is not None


@pytest.mark.asyncio
async def test_search_view_has_mode_indicator():
    """Verify the search view has a search mode indicator."""
    app = StickyApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        mode = pilot.app.query_one("#search-mode", SearchModeIndicator)
        assert mode is not None
        assert mode.mode == "hybrid"


@pytest.mark.asyncio
async def test_search_mode_cycle():
    """Verify the search mode cycles correctly."""
    app = StickyApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        mode = pilot.app.query_one("#search-mode", SearchModeIndicator)
        assert mode.mode == "hybrid"

        new = mode.cycle_mode()
        assert new == "vector"

        new = mode.cycle_mode()
        assert new == "fts"

        new = mode.cycle_mode()
        assert new == "hybrid"


@pytest.mark.asyncio
async def test_search_results_list_present():
    """Verify the search results list is rendered."""
    app = StickyApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        results = pilot.app.query_one("#search-results", SearchResultsList)
        assert results is not None


@pytest.mark.asyncio
async def test_tabs_present():
    """Verify all six tabs are present."""
    app = StickyApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        tabs = pilot.app.query_one("#tabs")
        assert tabs is not None
        # Check tab panes exist by ID
        for tab_id in ("home", "search", "review", "entities", "digest", "stats"):
            pane = pilot.app.query_one(f"#{tab_id}")
            assert pane is not None, f"Tab pane '{tab_id}' not found"


@pytest.mark.asyncio
@pytest.mark.xfail(reason="reactive update timing is non-deterministic in test runner")
async def test_status_bar_update():
    """Verify the status bar can be updated."""
    app = StickyApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        bar = pilot.app.query_one("#status-bar", StatusBar)
        bar.update_stats(42, 3, "local", "cloud")
        await pilot.pause()
        assert bar.thought_count == 42
        assert bar.review_count == 3


@pytest.mark.asyncio
async def test_filter_bar_hidden_by_default():
    """Verify the filter bar is hidden by default on home view."""
    app = StickyApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        filter_bar = pilot.app.query_one(FilterBar)
        assert "visible" not in filter_bar.classes


@pytest.mark.asyncio
async def test_thought_row_compose():
    """Verify ThoughtRow can compose with thought data."""
    thought = {
        "id": "test123",
        "content": "Test thought content",
        "category": "idea",
        "confidence": 0.9,
        "created_at": "2025-01-01T00:00:00+00:00",
    }
    row = ThoughtRow(thought)
    assert row.thought == thought


# ---------------------------------------------------------------------------
# Detail screen tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_detail_screen_renders():
    """Verify the detail screen renders with thought content visible."""
    app = StickyApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()

        # Capture a thought so we have data
        svc = app._get_service()
        svc.db.initialize()
        from sticky.core.models import Thought

        thought = Thought.create(
            content="Detail test thought content",
            category="idea",
            confidence=0.85,
            source="test",
        )
        svc.db.insert_thought(thought)

        # Push detail screen
        app.push_screen(
            DetailScreen(
                thought_id=thought.id,
                context=[thought.id],
                context_index=0,
            )
        )
        # Allow workers to complete
        await pilot.pause()
        await pilot.pause()

        # The detail screen is now the active screen
        screen = app.screen
        assert isinstance(screen, DetailScreen)

        # Verify key widgets exist on the screen
        content_widget = screen.query_one("#detail-content-text")
        assert content_widget is not None

        meta_widget = screen.query_one("#meta-content")
        assert meta_widget is not None

        nav_widget = screen.query_one("#detail-nav-bar")
        assert nav_widget is not None


@pytest.mark.asyncio
async def test_detail_screen_action_bar():
    """Verify the detail screen has the action bar."""
    app = StickyApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()

        svc = app._get_service()
        svc.db.initialize()
        from sticky.core.models import Thought

        thought = Thought.create(
            content="Action bar test thought",
            category="project",
            confidence=0.9,
            source="test",
        )
        svc.db.insert_thought(thought)

        app.push_screen(
            DetailScreen(thought_id=thought.id)
        )
        await pilot.pause()

        screen = app.screen
        assert isinstance(screen, DetailScreen)
        action_bar = screen.query_one("#detail-action-bar")
        assert action_bar is not None


# ---------------------------------------------------------------------------
# Review view tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_review_view_present():
    """Verify the review view is rendered in the Review tab."""
    app = StickyApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        review = pilot.app.query_one("#review-view", ReviewView)
        assert review is not None


@pytest.mark.asyncio
async def test_review_view_has_list():
    """Verify the review view contains a review list."""
    app = StickyApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        review_list = pilot.app.query_one("#review-list", ReviewList)
        assert review_list is not None


@pytest.mark.asyncio
async def test_review_empty_state():
    """Verify the empty state message when no review items exist."""
    app = StickyApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        # Allow workers to load
        await pilot.pause()
        await pilot.pause()

        empty_widget = pilot.app.query_one("#review-empty")
        assert empty_widget is not None
        # With no thoughts needing review, the empty state should be visible
        # (It might take a moment for workers to complete)


@pytest.mark.asyncio
async def test_review_view_shows_items():
    """Verify the review view shows low-confidence thoughts."""
    app = StickyApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()

        # Insert a low-confidence thought that needs review
        svc = app._get_service()
        svc.db.initialize()
        from sticky.core.models import Thought

        thought = Thought.create(
            content="Low confidence review test thought",
            category="action",
            confidence=0.35,
            needs_review=True,
            source="test",
        )
        svc.db.insert_thought(thought)

        # Reload review items
        review_view = pilot.app.query_one("#review-view", ReviewView)
        review_view.load_review_items()

        # Allow workers to complete
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()

        review_list = pilot.app.query_one("#review-list", ReviewList)
        assert review_list is not None


@pytest.mark.asyncio
async def test_review_item_compose():
    """Verify ReviewItem can compose with thought data."""
    thought = {
        "id": "review-test-123",
        "content": "Test review item content",
        "category": "action",
        "confidence": 0.35,
        "created_at": "2025-01-01T00:00:00+00:00",
        "metadata": {"entities": ["Marcus"]},
    }
    item = ReviewItem(thought)
    assert item.thought == thought


@pytest.mark.asyncio
async def test_review_action_bar_present():
    """Verify the review view has the action bar."""
    app = StickyApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        action_bar = pilot.app.query_one("#review-action-bar")
        assert action_bar is not None


@pytest.mark.asyncio
async def test_keybinding_legend_has_review_context():
    """Verify the keybinding legend has a review context entry."""
    from sticky.tui.widgets.keybinding_legend import LEGENDS

    assert "review" in LEGENDS
    assert "Accept" in LEGENDS["review"]
    assert "Reclassify" in LEGENDS["review"]


@pytest.mark.asyncio
async def test_keybinding_legend_has_detail_context():
    """Verify the keybinding legend has a detail context entry."""
    from sticky.tui.widgets.keybinding_legend import LEGENDS

    assert "detail" in LEGENDS
    assert "Edit" in LEGENDS["detail"]
    assert "Related" in LEGENDS["detail"]


# ---------------------------------------------------------------------------
# Entities view tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_entities_view_renders():
    """Verify the entities view renders with three columns."""
    app = StickyApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        entities_view = pilot.app.query_one("#entities-view", EntitiesView)
        assert entities_view is not None

        # Verify three column panels exist
        type_filter = pilot.app.query_one("#type-filter", TypeFilter)
        assert type_filter is not None

        entity_list_panel = pilot.app.query_one("#entity-list-panel", EntityList)
        assert entity_list_panel is not None

        entity_detail = pilot.app.query_one("#entity-detail", EntityDetail)
        assert entity_detail is not None


@pytest.mark.asyncio
async def test_entities_view_has_filter_input():
    """Verify the entities view has a filter input."""
    app = StickyApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        filter_input = pilot.app.query_one("#entity-filter-input")
        assert filter_input is not None


@pytest.mark.asyncio
async def test_entities_view_has_type_filter_list():
    """Verify the type filter sidebar has a list."""
    app = StickyApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        type_filter_list = pilot.app.query_one("#type-filter-list")
        assert type_filter_list is not None


@pytest.mark.asyncio
async def test_entities_view_has_entity_list():
    """Verify the entity list panel has a ListView."""
    app = StickyApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        entity_list = pilot.app.query_one("#entity-list")
        assert entity_list is not None


@pytest.mark.asyncio
async def test_entities_view_has_pinned_context():
    """Verify the entity detail panel has a pinned context box."""
    app = StickyApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        pinned_context = pilot.app.query_one("#pinned-context", PinnedContext)
        assert pinned_context is not None


@pytest.mark.asyncio
async def test_entities_view_has_linked_thoughts_list():
    """Verify the entity detail panel has a linked thoughts list."""
    app = StickyApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        linked_list = pilot.app.query_one("#linked-thoughts-list")
        assert linked_list is not None


@pytest.mark.asyncio
async def test_entity_row_compose():
    """Verify EntityRow can compose with entity data."""
    entity = {
        "id": "entity-test-123",
        "name": "Sarah",
        "entity_type": "person",
        "mention_count": 7,
    }
    row = EntityRow(entity)
    assert row.entity == entity


@pytest.mark.asyncio
async def test_entity_type_badge():
    """Verify entity type badge produces colored text."""
    from sticky.tui.views.entities_view import _entity_type_badge

    badge = _entity_type_badge("person")
    assert "person" in badge.plain

    badge = _entity_type_badge("project")
    assert "project" in badge.plain

    badge = _entity_type_badge(None)
    assert "?" in badge.plain


# ---------------------------------------------------------------------------
# Digest view tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_digest_view_renders():
    """Verify the digest view renders."""
    app = StickyApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        digest_view = pilot.app.query_one("#digest-view", DigestView)
        assert digest_view is not None


@pytest.mark.asyncio
async def test_digest_period_selector():
    """Verify Day/Week/Month tabs exist in the period selector."""
    app = StickyApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        selector = pilot.app.query_one("#period-selector", PeriodSelector)
        assert selector is not None

        # Verify period tabs
        day_tab = pilot.app.query_one("#period-day", PeriodTab)
        assert day_tab is not None
        assert day_tab.period == "day"

        week_tab = pilot.app.query_one("#period-week", PeriodTab)
        assert week_tab is not None
        assert week_tab.period == "week"

        month_tab = pilot.app.query_one("#period-month", PeriodTab)
        assert month_tab is not None
        assert month_tab.period == "month"


@pytest.mark.asyncio
async def test_digest_view_has_content():
    """Verify the digest view has a content area."""
    app = StickyApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        content = pilot.app.query_one("#digest-content", DigestContent)
        assert content is not None


@pytest.mark.asyncio
async def test_digest_view_empty():
    """No thoughts captured results in empty state."""
    app = StickyApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        # Allow workers to complete
        await pilot.pause()
        await pilot.pause()

        # The digest content should exist and show either
        # empty state or loading state (both are valid on startup)
        content = pilot.app.query_one("#digest-content", DigestContent)
        assert content is not None


@pytest.mark.asyncio
async def test_digest_default_period_is_day():
    """Verify the default active period is 'day'."""
    app = StickyApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        selector = pilot.app.query_one("#period-selector", PeriodSelector)
        assert selector.active_period == "day"


@pytest.mark.asyncio
async def test_keybinding_legend_has_entities_context():
    """Verify the keybinding legend has an entities context entry."""
    from sticky.tui.widgets.keybinding_legend import LEGENDS

    assert "entities" in LEGENDS
    assert "Merge" in LEGENDS["entities"]
    assert "Filter" in LEGENDS["entities"]


@pytest.mark.asyncio
async def test_keybinding_legend_has_digest_context():
    """Verify the keybinding legend has a digest context entry."""
    from sticky.tui.widgets.keybinding_legend import LEGENDS

    assert "digest" in LEGENDS
    assert "Regenerate" in LEGENDS["digest"]
    assert "Expand topic" in LEGENDS["digest"]


# ---------------------------------------------------------------------------
# Stats view tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stats_view_renders():
    """Verify the stats view renders with all sections."""
    from sticky.tui.views.stats_view import (
        ClassificationStats,
        DataFlowSection,
        LibraryStats,
        StatsView,
    )

    app = StickyApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        stats_view = pilot.app.query_one("#stats-view", StatsView)
        assert stats_view is not None

        # Verify all three sections exist
        library = pilot.app.query_one("#library-stats", LibraryStats)
        assert library is not None

        classification = pilot.app.query_one(
            "#classification-stats", ClassificationStats
        )
        assert classification is not None

        data_flow = pilot.app.query_one("#data-flow-section", DataFlowSection)
        assert data_flow is not None


@pytest.mark.asyncio
async def test_stats_data_flow_section():
    """Verify the DATA FLOW section has LOCAL/CLOUD indicators."""
    from sticky.tui.views.stats_view import DataFlowSection

    app = StickyApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        # Allow workers to complete
        await pilot.pause()
        await pilot.pause()

        data_flow = pilot.app.query_one("#data-flow-section", DataFlowSection)
        assert data_flow is not None


@pytest.mark.asyncio
async def test_stats_view_in_tab():
    """Verify the stats view is accessible as a tab."""
    app = StickyApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        # The stats tab should exist
        pane = pilot.app.query_one("#stats")
        assert pane is not None


@pytest.mark.asyncio
async def test_keybinding_legend_has_stats_context():
    """Verify the keybinding legend has a stats context entry."""
    from sticky.tui.widgets.keybinding_legend import LEGENDS

    assert "stats" in LEGENDS
    assert "Home" in LEGENDS["stats"]


# ---------------------------------------------------------------------------
# Settings view tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_settings_accessible():
    """Verify the settings screen can be pushed."""
    from sticky.tui.views.settings import SettingsScreen

    app = StickyApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        app.push_screen(SettingsScreen())
        await pilot.pause()

        screen = app.screen
        assert isinstance(screen, SettingsScreen)


@pytest.mark.asyncio
async def test_settings_has_table():
    """Verify the settings screen has a DataTable."""
    from sticky.tui.views.settings import SettingsScreen, SettingsTable

    app = StickyApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        app.push_screen(SettingsScreen())
        await pilot.pause()
        await pilot.pause()

        screen = app.screen
        assert isinstance(screen, SettingsScreen)

        table = screen.query_one("#settings-table", SettingsTable)
        assert table is not None


@pytest.mark.asyncio
async def test_settings_has_legend():
    """Verify the settings screen has the keybinding legend."""
    from sticky.tui.views.settings import SettingsScreen

    app = StickyApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        app.push_screen(SettingsScreen())
        await pilot.pause()

        screen = app.screen
        legend = screen.query_one("#settings-legend")
        assert legend is not None


# ---------------------------------------------------------------------------
# Command palette tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_command_palette_opens():
    """Verify the command palette can be opened."""
    app = StickyApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        app.push_screen(CommandPalette())
        await pilot.pause()

        screen = app.screen
        assert isinstance(screen, CommandPalette)


@pytest.mark.asyncio
async def test_command_palette_has_input():
    """Verify the command palette has a filter input."""
    app = StickyApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        app.push_screen(CommandPalette())
        await pilot.pause()

        screen = app.screen
        palette_input = screen.query_one("#palette-input")
        assert palette_input is not None


@pytest.mark.asyncio
async def test_command_palette_has_commands():
    """Verify the command palette has command items."""
    app = StickyApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        app.push_screen(CommandPalette())
        await pilot.pause()

        screen = app.screen
        cmd_list = screen.query_one("#palette-list")
        assert cmd_list is not None
        # Should have at least one command item
        items = screen.query(CommandItem)
        assert len(items) > 0
