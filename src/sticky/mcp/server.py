"""A small MCP surface over Sticky's local note service."""

from __future__ import annotations

import importlib
import json
import logging
from functools import wraps
from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from sticky import __version__
from sticky.core.service import StickyService

mcp = MCPServer(
    "sticky", version=__version__,
    description="Local note capture, search, editing, and recovery. Cloud digests only on request.",
)
_service: StickyService | None = None
logger = logging.getLogger(__name__)

_READ_LOCAL = ToolAnnotations(read_only_hint=True, open_world_hint=False)
_CREATE_LOCAL = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False,
)
_EDIT_LOCAL = ToolAnnotations(
    read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=False,
)

# V2 runs sync handlers in worker threads. These async handlers intentionally
# call the sync core without yielding, keeping the one SQLite connection on the
# event-loop thread and serializing each operation. Slow local work blocks other
# requests, as it did in v1; the small stdio server has no background workers.


def _tool(*, annotations: ToolAnnotations):
    """Keep expected input/storage errors useful to the calling agent."""
    def register(handler):
        @wraps(handler)
        async def invoke(*args, **kwargs):
            try:
                return await handler(*args, **kwargs)
            except (ValueError, FileNotFoundError, FileExistsError) as exc:
                raise ToolError(str(exc)) from exc

        return mcp.tool(annotations=annotations)(invoke)

    return register


def run_server() -> None:
    global _service
    # On Windows, first importing SciPy inside a request can stall after the
    # stdio reader threads start. Load libraries first; model weights stay lazy.
    try:
        importlib.import_module("sentence_transformers")
    except Exception:
        logger.warning("Embedding libraries unavailable; notes remain usable with keyword search")
    try:
        mcp.run(transport="stdio")
    finally:
        if _service is not None:
            _service.db.close()
            _service = None


def get_service() -> StickyService:
    global _service
    if _service is None:
        service = StickyService()
        try:
            service.initialize()
        except Exception:
            service.db.close()
            raise
        _service = service
    return _service


def _filters(**values: Any) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


@_tool(annotations=_CREATE_LOCAL)
async def sticky_capture(content: str, source_url: str | None = None, thread: str | None = None) -> dict[str, Any]:
    """Save a new local note. Each submission is a distinct record; no cloud request.

    content: Note text. source_url: Optional attribution URL. thread: Optional grouping name.
    """
    return get_service().capture(content, source="mcp", source_url=source_url, thread=thread)


@_tool(annotations=_READ_LOCAL)
async def sticky_search(
    query: str, limit: int | None = None, category: str | None = None,
    after: str | None = None, before: str | None = None,
) -> dict[str, Any]:
    """Find notes using local semantic and keyword matching.

    limit: Positive result count (defaults to configured limit).
    category: Optional manual category. after/before: ISO date or datetime filters.
    """
    results = get_service().search(query, **_filters(
        limit=limit, category=category, after=after, before=before
    ))
    return {"query": query, "results": results, "total_results": len(results)}


@_tool(annotations=_READ_LOCAL)
async def sticky_list(
    limit: int | None = None, cursor: str | None = None,
    category: str | None = None, after: str | None = None, before: str | None = None,
    sort: str = "created_at_desc", thread: str | None = None,
) -> dict[str, Any]:
    """Browse local notes. Pass next_cursor from the previous result to continue.

    limit: Positive page size. sort: created_at_desc or created_at_asc.
    category/thread: Optional filters. after/before: ISO date or datetime filters.
    """
    return get_service().list_thoughts(**_filters(
        limit=limit, cursor=cursor, category=category, after=after,
        before=before, sort=sort, thread=thread,
    ))


@_tool(annotations=_READ_LOCAL)
async def sticky_get(thought_id: str) -> dict[str, Any]:
    """Read a complete note by its full ID, including source attribution."""
    return get_service().get_thought(thought_id)


@_tool(annotations=_EDIT_LOCAL)
async def sticky_classify(thought_id: str, category: str) -> dict[str, Any]:
    """Assign a manual category locally: idea, project, person, meeting, action, reference, journal."""
    return get_service().classify_thought(thought_id, category)


@_tool(annotations=_EDIT_LOCAL)
async def sticky_update(thought_id: str, content: str) -> dict[str, Any]:
    """Replace a note's text and update local search data; no cloud request."""
    return get_service().update(thought_id, content)


@_tool(annotations=_EDIT_LOCAL)
async def sticky_delete(thought_id: str) -> dict[str, Any]:
    """Permanently delete one note by its full ID."""
    return get_service().delete(thought_id)


@_tool(annotations=ToolAnnotations(
    read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=False,
))
async def sticky_export(
    format: str, output_path: str | None = None, category: str | None = None,
    after: str | None = None, before: str | None = None,
) -> dict[str, Any]:
    """Export notes as JSON for restore, or readable Markdown. This writes local files."""
    output_path = output_path or ("sticky_export.json" if format == "json" else "sticky_export")
    return get_service().export_data(format, output_path, **_filters(
        category=category, after=after, before=before
    ))


@_tool(annotations=_CREATE_LOCAL)
async def sticky_import(source_path: str, format: str = "auto", dry_run: bool = False) -> dict[str, Any]:
    """Restore JSON note records or capture text from local files.

    format: auto, json, markdown, or text. dry_run: Preview without writing records.
    JSON restores skip existing IDs; Markdown/text imports create new notes.
    """
    return get_service().import_data(source_path, format=format, dry_run=dry_run)


@_tool(annotations=ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True,
))
async def sticky_digest(period: str = "day", since: str | None = None) -> dict[str, Any]:
    """Send selected note text to OpenRouter for an explicit digest. Requires a configured API key.

    period: day, week, or month. since: ISO start datetime overriding period.
    Provider retention policies vary; this operation sends data off the device.
    """
    return get_service().digest(period=period, since=since)


@_tool(annotations=_READ_LOCAL)
async def sticky_stats() -> dict[str, Any]:
    """Read local note count and storage paths."""
    return get_service().stats()


@_tool(annotations=_READ_LOCAL)
async def sticky_config(key: str | None = None) -> dict[str, Any]:
    """Read startup settings. API keys are masked. Settings cannot be changed through MCP."""
    values = get_service().get_config_display()
    if key is not None and key not in values:
        raise ValueError(f"Unknown config key: {key}")
    return values if key is None else values[key]


@_tool(annotations=_READ_LOCAL)
async def sticky_privacy() -> dict[str, Any]:
    """Read data flow information for local notes and explicit cloud digests."""
    return get_service().privacy_info()


@mcp.resource("sticky://stats")
async def resource_stats() -> str:
    """Local note count and storage paths."""
    return json.dumps(get_service().stats(), indent=2, default=str)


@mcp.resource("sticky://privacy")
async def resource_privacy() -> str:
    """Local and explicit cloud data flow."""
    return json.dumps(get_service().privacy_info(), indent=2, default=str)


@mcp.prompt()
async def research_session(topic: str, source: str = "") -> str:
    """Capture attributed research notes in a thread, then retrieve the original notes."""
    return (
        f'Help the user research "{topic}"' + (f' from "{source}".' if source else ".")
        + f' Save ideas with sticky_capture, thread="{topic.lower().replace(" ", "-")}-research".'
        + " Include source_url when available. Use sticky_list with that thread to review the saved notes."
        + " Use sticky_search to find relevant prior notes; cite their IDs and preserve source attribution."
    )


if __name__ == "__main__":
    run_server()
