"""Stats dashboard view for sticky TUI.

Read-only transparency dashboard showing:
- LIBRARY stats: total thoughts, needs review, entities, digests, DB size, dates
- TOP ENTITIES: name + type badge + mention count (top 5)
- CLASSIFICATION: classified count/%, avg confidence, category breakdown with bars
- SYSTEM INFO: embedding model, LLM, search weights, paths, version
- DATA FLOW: LOCAL vs CLOUD indicators for each component

Only keybinding: [Esc] Home (read-only view).
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from rich.text import Text
from textual import work
from textual.containers import Horizontal, Vertical
from textual.widgets import Static

if TYPE_CHECKING:
    from textual.app import ComposeResult

logger = logging.getLogger(__name__)


# Entity type badge colors (reused from entities_view)
_ENTITY_TYPE_COLORS: dict[str, str] = {
    "person": "#c678dd",
    "project": "#4ec9b0",
    "concept": "#61afef",
}


def _entity_type_badge_inline(entity_type: str | None) -> Text:
    """Return a compact colored entity type badge."""
    if entity_type is None:
        return Text("?", style="dim")
    color = _ENTITY_TYPE_COLORS.get(entity_type, "white")
    return Text(entity_type, style=f"{color} bold")


def _bar_chart(count: int, total: int, bar_width: int = 10) -> str:
    """Return a simple text bar chart like: ████████░░"""
    if total == 0:
        return "░" * bar_width
    filled = round(count / total * bar_width)
    filled = min(filled, bar_width)
    empty = bar_width - filled
    return "█" * filled + "░" * empty


def _relative_date(iso_str: str | None) -> str:
    """Convert an ISO timestamp to a short relative date string."""
    if not iso_str:
        return "—"
    try:
        from datetime import datetime, timezone

        dt = datetime.fromisoformat(iso_str)
        now = datetime.now(timezone.utc)
        delta = now - dt
        if delta.days == 0:
            hours = delta.seconds // 3600
            if hours == 0:
                return "just now"
            return f"{hours}h ago"
        elif delta.days == 1:
            return "yesterday"
        elif delta.days < 30:
            return f"{delta.days}d ago"
        else:
            return dt.strftime("%b %d")
    except (ValueError, TypeError):
        return iso_str[:10] if len(iso_str) >= 10 else iso_str


def _format_bytes(size_bytes: int) -> str:
    """Format byte count into human-readable string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


# ---------------------------------------------------------------------------
# Library stats (left column, top)
# ---------------------------------------------------------------------------


class LibraryStats(Static):
    """Left column: LIBRARY stats + TOP ENTITIES."""

    DEFAULT_CSS = """
    LibraryStats {
        width: 1fr;
        height: 1fr;
        padding: 1 2;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("", id="library-stats-content")

    def set_data(
        self,
        total_thoughts: int = 0,
        needs_review: int = 0,
        total_entities: int = 0,
        total_digests: int = 0,
        db_size: str = "—",
        last_capture: str = "—",
        first_capture: str = "—",
        top_entities: list[dict] | None = None,
    ) -> None:
        """Update the library stats display."""
        try:
            content = self.query_one("#library-stats-content", Static)
        except Exception:
            return

        text = Text()
        text.append("LIBRARY\n", style="bold")
        text.append(f"  Total thoughts:  {total_thoughts}\n")
        text.append(f"  Needs review:    {needs_review}\n")
        text.append(f"  Total entities:  {total_entities}\n")
        text.append(f"  Total digests:   {total_digests}\n")
        text.append(f"  DB size:         {db_size}\n")
        text.append(f"  Last capture:    {last_capture}\n")
        text.append(f"  First capture:   {first_capture}\n")
        text.append("\n")

        # Top entities section
        text.append("TOP ENTITIES\n", style="bold")
        if top_entities:
            for entity in top_entities[:5]:
                name = entity.get("name", "?")
                etype = entity.get("entity_type", "?")
                mentions = entity.get("mention_count", 0)
                text.append(f"  {name.ljust(12)} ")
                text.append_text(_entity_type_badge_inline(etype))
                text.append(f"  {mentions}x\n", style="dim")
        else:
            text.append("  No entities yet.\n", style="dim")

        content.update(text)


# ---------------------------------------------------------------------------
# Classification stats (right column, top)
# ---------------------------------------------------------------------------

# Category colors for the bar chart
_CATEGORY_COLORS: dict[str, str] = {
    "idea": "#e5c07b",
    "action": "#e06c75",
    "meeting": "#61afef",
    "person": "#c678dd",
    "project": "#4ec9b0",
    "note": "#abb2bf",
    "question": "#d19a66",
    "journal": "#98c379",
}


class ClassificationStats(Static):
    """Right column: CLASSIFICATION breakdown + SYSTEM INFO."""

    DEFAULT_CSS = """
    ClassificationStats {
        width: 1fr;
        height: 1fr;
        padding: 1 2;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("", id="classification-stats-content")

    def set_data(
        self,
        total_thoughts: int = 0,
        classified_count: int = 0,
        avg_confidence: float = 0.0,
        by_category: dict[str, int] | None = None,
        embedding_model: str = "—",
        embedding_dimensions: int = 0,
        llm_model: str = "—",
        search_weights: dict | None = None,
        db_path: str = "—",
        confidence_threshold: float = 0.6,
        version: str = "0.1.0",
    ) -> None:
        """Update the classification stats display."""
        try:
            content = self.query_one("#classification-stats-content", Static)
        except Exception:
            return

        text = Text()

        # CLASSIFICATION section
        pct = round(classified_count / total_thoughts * 100) if total_thoughts > 0 else 0
        text.append("CLASSIFICATION\n", style="bold")
        text.append(f"  Classified:  {classified_count} ({pct}%)\n")
        text.append(f"  Avg confidence: {avg_confidence:.2f}\n")

        # Category breakdown with bar charts
        if by_category:
            # Sort by count descending
            sorted_cats = sorted(by_category.items(), key=lambda x: x[1], reverse=True)
            for cat, count in sorted_cats:
                pct_cat = round(count / total_thoughts * 100) if total_thoughts > 0 else 0
                color = _CATEGORY_COLORS.get(cat, "white")
                bar = _bar_chart(count, total_thoughts)
                text.append(f"  {cat.ljust(10)} ", style=f"{color} bold")
                text.append(f"{str(count).rjust(3)} ")
                text.append(f"{bar} ", style=color)
                text.append(f"{pct_cat}%\n", style="dim")
        text.append("\n")

        # SYSTEM INFO section
        text.append("SYSTEM INFO\n", style="bold")
        text.append(f"  Embeddings:  {embedding_model} ({embedding_dimensions}d)\n")
        text.append(f"  LLM:         {llm_model}\n")
        if search_weights:
            vec = search_weights.get("vector", 0.6)
            fts = search_weights.get("fts", 0.4)
            text.append(f"  Search:      {vec} vector + {fts} FTS\n")
        text.append(f"  DB path:     {db_path}\n")
        text.append(f"  Conf threshold: {confidence_threshold}\n")
        text.append(f"  Version:     {version}\n")

        content.update(text)


# ---------------------------------------------------------------------------
# Data flow section (full width, bottom)
# ---------------------------------------------------------------------------


class DataFlowSection(Static):
    """Full-width section showing LOCAL vs CLOUD for each component."""

    DEFAULT_CSS = """
    DataFlowSection {
        height: auto;
        min-height: 8;
        padding: 1 2;
        border-top: solid $surface-darken-1;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("", id="data-flow-content")

    def set_data(
        self,
        embedding_model: str = "all-MiniLM-L6-v2",
        db_path: str = "~/.sticky/sticky.db",
    ) -> None:
        """Update the data flow display."""
        try:
            content = self.query_one("#data-flow-content", Static)
        except Exception:
            return

        text = Text()
        text.append("DATA FLOW\n", style="bold")

        # Embeddings - LOCAL
        text.append("  Embeddings       ")
        text.append("LOCAL ", style="green bold")
        text.append(f"  {embedding_model} runs on your machine\n")

        # Classification - CLOUD
        text.append("  Classification   ")
        text.append("CLOUD ", style="yellow bold")
        text.append("  sent to OpenRouter for category/entity extraction\n")

        # Digest - CLOUD
        text.append("  Digest           ")
        text.append("CLOUD ", style="yellow bold")
        text.append("  sent to OpenRouter for summarization\n")

        # Storage - LOCAL
        text.append("  Storage          ")
        text.append("LOCAL ", style="green bold")
        text.append(f"  SQLite at {db_path}\n")

        text.append("\n")
        text.append(
            '  "No data is stored by OpenRouter (stateless API calls)."',
            style="dim italic",
        )

        content.update(text)


# ---------------------------------------------------------------------------
# Main stats view
# ---------------------------------------------------------------------------


class StatsView(Static):
    """Stats dashboard — read-only transparency view.

    Two-column layout with LIBRARY + TOP ENTITIES on the left,
    CLASSIFICATION + SYSTEM INFO on the right, and DATA FLOW at the bottom.
    """

    DEFAULT_CSS = """
    StatsView {
        height: 1fr;
    }

    .stats-columns {
        height: 1fr;
    }
    """

    def compose(self) -> ComposeResult:
        with Horizontal(classes="stats-columns"):
            yield LibraryStats(id="library-stats")
            yield ClassificationStats(id="classification-stats")
        yield DataFlowSection(id="data-flow-section")

    def on_mount(self) -> None:
        """Load stats on mount."""
        self.load_stats()

    @work(thread=True)
    def load_stats(self) -> None:
        """Load all stats from the service (worker thread)."""
        try:
            svc = self.app._get_service()
            db_stats = svc.db.get_stats()
            system_stats = svc.stats()

            # Get DB file size
            db_path = str(svc.config.db_path)
            try:
                db_size_bytes = os.path.getsize(db_path)
                db_size = _format_bytes(db_size_bytes)
            except OSError:
                db_size = "—"

            # Get top entities with entity_type (db.get_stats only has name + count)
            top_entities_with_type = []
            try:
                rows = svc.db.execute(
                    "SELECT name, entity_type, mention_count FROM entities "
                    "ORDER BY mention_count DESC LIMIT 5"
                ).fetchall()
                for row in rows:
                    top_entities_with_type.append({
                        "name": row["name"],
                        "entity_type": row["entity_type"],
                        "mention_count": row["mention_count"],
                    })
            except Exception:
                pass

            # Calculate classified count and avg confidence
            thoughts_data = db_stats.get("thoughts", {})
            total_thoughts = thoughts_data.get("total", 0)
            by_category = thoughts_data.get("by_category", {})
            classified_count = sum(by_category.values())

            avg_confidence = 0.0
            try:
                row = svc.db.execute(
                    "SELECT AVG(confidence) as avg_conf FROM thoughts "
                    "WHERE category IS NOT NULL"
                ).fetchone()
                if row and row["avg_conf"] is not None:
                    avg_confidence = round(row["avg_conf"], 2)
            except Exception:
                pass

            # System info
            sys_info = system_stats.get("system", {})
            embedding_model = sys_info.get("embedding_model", "all-MiniLM-L6-v2")
            embedding_dimensions = svc.config.embedding_dimensions
            llm_model = sys_info.get("llm_model", "—")
            search_weights = sys_info.get("search_weights", {})
            confidence_threshold = sys_info.get("confidence_threshold", 0.6)

            from sticky import __version__

            self.app.call_from_thread(
                self._populate_stats,
                total_thoughts=total_thoughts,
                needs_review=thoughts_data.get("needs_review", 0),
                total_entities=db_stats.get("entities", {}).get("total", 0),
                total_digests=db_stats.get("digests", {}).get("total", 0),
                db_size=db_size,
                last_capture=_relative_date(thoughts_data.get("last")),
                first_capture=_relative_date(thoughts_data.get("first")),
                top_entities=top_entities_with_type,
                classified_count=classified_count,
                avg_confidence=avg_confidence,
                by_category=by_category,
                embedding_model=embedding_model,
                embedding_dimensions=embedding_dimensions,
                llm_model=llm_model,
                search_weights=search_weights,
                db_path=str(svc.config.db_path),
                confidence_threshold=confidence_threshold,
                version=__version__,
            )
        except Exception as exc:
            logger.warning("Failed to load stats: %s", exc)

    def _populate_stats(
        self,
        total_thoughts: int,
        needs_review: int,
        total_entities: int,
        total_digests: int,
        db_size: str,
        last_capture: str,
        first_capture: str,
        top_entities: list[dict],
        classified_count: int,
        avg_confidence: float,
        by_category: dict[str, int],
        embedding_model: str,
        embedding_dimensions: int,
        llm_model: str,
        search_weights: dict,
        db_path: str,
        confidence_threshold: float,
        version: str,
    ) -> None:
        """Populate all stats sections (must be called from main thread)."""
        try:
            library = self.query_one("#library-stats", LibraryStats)
            library.set_data(
                total_thoughts=total_thoughts,
                needs_review=needs_review,
                total_entities=total_entities,
                total_digests=total_digests,
                db_size=db_size,
                last_capture=last_capture,
                first_capture=first_capture,
                top_entities=top_entities,
            )
        except Exception:
            pass

        try:
            classification = self.query_one(
                "#classification-stats", ClassificationStats
            )
            classification.set_data(
                total_thoughts=total_thoughts,
                classified_count=classified_count,
                avg_confidence=avg_confidence,
                by_category=by_category,
                embedding_model=embedding_model,
                embedding_dimensions=embedding_dimensions,
                llm_model=llm_model,
                search_weights=search_weights,
                db_path=db_path,
                confidence_threshold=confidence_threshold,
                version=version,
            )
        except Exception:
            pass

        try:
            data_flow = self.query_one("#data-flow-section", DataFlowSection)
            data_flow.set_data(
                embedding_model=embedding_model,
                db_path=db_path,
            )
        except Exception:
            pass
