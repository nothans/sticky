"""TUI views for sticky."""

from sticky.tui.views.command_palette import CommandPalette
from sticky.tui.views.detail import DetailScreen
from sticky.tui.views.digest_view import DigestView
from sticky.tui.views.entities_view import EntitiesView
from sticky.tui.views.home import HomeView
from sticky.tui.views.review import ReviewView
from sticky.tui.views.search_view import SearchView
from sticky.tui.views.settings import SettingsScreen
from sticky.tui.views.stats_view import StatsView

__all__ = [
    "CommandPalette",
    "DetailScreen",
    "DigestView",
    "EntitiesView",
    "HomeView",
    "ReviewView",
    "SearchView",
    "SettingsScreen",
    "StatsView",
]
