"""Thought row widget for displaying a single thought in a list."""

from __future__ import annotations

from datetime import datetime, timezone

from rich.text import Text
from textual.widgets import ListItem, Static


# Category badge colors matching CLI and wireframe
_CATEGORY_COLORS: dict[str, str] = {
    "idea": "cyan",
    "person": "magenta",
    "meeting": "blue",
    "action": "yellow",
    "project": "green",
    "ref": "white",
    "reference": "white",
    "reflection": "green",
    "journal": "bright_black",
}


def _confidence_dot(confidence: float | None) -> Text:
    """Return a colored confidence dot.

    - green (#4ec9b0): confidence >= 0.8
    - yellow (#d4a843): confidence 0.6-0.8
    - dim (#555555): confidence < 0.6 or None
    """
    if confidence is None or confidence < 0.6:
        return Text("\u25cb ", style="#555555")  # hollow circle
    if confidence < 0.8:
        return Text("\u25cf ", style="#d4a843")  # yellow filled
    return Text("\u25cf ", style="#4ec9b0")  # green filled


def _category_badge(category: str | None) -> Text:
    """Return a colored category badge."""
    if category is None:
        return Text("?       ", style="dim")
    color = _CATEGORY_COLORS.get(category, "white")
    # Pad to 8 chars for alignment
    padded = category.ljust(8)
    return Text(padded, style=color)


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
    """A single thought row with confidence dot, category badge, time, content.

    Format: . idea     2h ago   What if we used ULID instead of UUID...
    """

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
        confidence = thought.get("confidence")
        category = thought.get("category")
        created_at = thought.get("created_at", "")
        content = thought.get("content", "").replace("\n", " ")

        # Build the row text
        line = Text()
        line.append_text(_confidence_dot(confidence))
        line.append_text(_category_badge(category))
        time_str = _relative_time(created_at).ljust(10)
        line.append(time_str, style="dim")
        line.append(" ")
        # Truncate content to fit
        max_content = 80
        if len(content) > max_content:
            content = content[: max_content - 3] + "..."
        line.append(content)

        yield Static(line)
