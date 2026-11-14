"""Thought row widget for displaying a single thought in a list."""

from __future__ import annotations

from datetime import datetime, timezone

from rich.text import Text
from textual.widgets import ListItem, Static


def _category_badge(category: str | None) -> Text:
    """A quiet label; one hue for categories rather than a color legend."""
    if category is None:
        return Text("?       ", style="#dde3e8")
    # Pad to 8 chars for alignment
    padded = category.ljust(8)
    return Text(padded, style="#9bbbd4")


def _relative_time(created_at: str) -> str:
    """Convert ISO timestamp to relative time string.

    Returns: "2h ago", "yesterday", "Mar 18", etc.
    """
    try:
        dt = datetime.fromisoformat(created_at)
        # Make timezone-aware if naive
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        diff = now - dt
        seconds = diff.total_seconds()

        if seconds < 60:
            return "just now"
        if seconds < 3600:
            mins = int(seconds // 60)
            return f"{mins}m ago"
        if seconds < 86400:
            hours = int(seconds // 3600)
            return f"{hours}h ago"
        if seconds < 172800:
            return "yesterday"
        if seconds < 604800:
            days = int(seconds // 86400)
            return f"{days}d ago"
        # Older than a week — show date
        return dt.strftime("%b %d")
    except (ValueError, TypeError):
        return ""


class ThoughtRow(ListItem):
    """A source note's category, capture time, and literal content."""

    DEFAULT_CSS = """
    ThoughtRow {
        height: 1;
        padding: 0 1;
    }
    ThoughtRow > Static {
        height: 1;
    }
    """

    def __init__(self, thought: dict, **kwargs) -> None:
        super().__init__(**kwargs)
        self.thought = thought

    def compose(self):
        """Compose the row content."""
        thought = self.thought
        category = thought.get("category")
        created_at = thought.get("created_at", "")
        content = thought.get("content", "").replace("\n", " ")

        # Build the row text
        line = Text()
        line.append_text(_category_badge(category))
        time_str = _relative_time(created_at).ljust(10)
        line.append(time_str, style="#b1bec9")
        line.append(" ")
        # Truncate content to fit
        max_content = 80
        if len(content) > max_content:
            content = content[: max_content - 3] + "..."
        line.append(content)

        yield Static(line)
