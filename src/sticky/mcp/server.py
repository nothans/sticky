"""FastMCP server for sticky.

Exposes 19 tools that delegate to StickyService.
Each tool has type-annotated parameters and a descriptive docstring
that becomes the tool description in MCP.

Run directly with: python -m sticky.mcp.server
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from sticky.core.service import StickyService

mcp = FastMCP("sticky")

_service: StickyService | None = None


def get_service() -> StickyService:
    """Return the singleton StickyService, initializing on first call."""
    global _service
    if _service is None:
        _service = StickyService()
        _service.initialize()
    return _service


# ------------------------------------------------------------------
# Tool 1: sticky_capture
# ------------------------------------------------------------------


@mcp.tool()
def sticky_capture(
    content: str,
    template: str | None = None,
    source_url: str | None = None,
    thread: str | None = None,
) -> dict:
    """Capture a thought. AI classifies into a category (idea, project, person, meeting, action, reference, journal) and extracts entities (people, projects, concepts) automatically.

    Args:
        content: The thought text to capture.
        template: Optional template hint (e.g. "meeting", "idea", "task") to guide classification.
        source_url: Optional URL where this thought originated (for source attribution).
        thread: Optional research thread name for grouping related thoughts (e.g. "second-brain-research").
    """
    return get_service().capture(
        content, template=template, source="mcp", source_url=source_url, thread=thread
    )


# ------------------------------------------------------------------
# Tool 2: sticky_search
# ------------------------------------------------------------------


@mcp.tool()
def sticky_search(
    query: str,
    limit: int = 10,
    category: str | None = None,
    entity: str | None = None,
    after: str | None = None,
    before: str | None = None,
    needs_review: bool | None = None,
    mode: str = "hybrid",
) -> dict:
    """Search thoughts using hybrid semantic + keyword matching. Returns results ranked by relevance score.

    Args:
        query: Search query text (natural language works best for hybrid/vector modes).
        limit: Maximum number of results.
        category: Filter by category (idea, project, person, meeting, action, reference, journal).
        entity: Filter by entity name (e.g. "Sarah", "auth migration").
        after: Only thoughts after this ISO datetime (e.g. "2026-04-01").
        before: Only thoughts before this ISO datetime.
        needs_review: Filter by review status.
        mode: Search mode — "hybrid" (default, best quality), "vector" (semantic only), or "keyword" (exact match, fastest).
    """
    results = get_service().search(
        query,
        limit=limit,
        mode=mode,
        category=category,
        entity=entity,
        after=after,
        before=before,
        needs_review=needs_review,
    )
    return {"query": query, "results": results, "total_results": len(results)}


# ------------------------------------------------------------------
# Tool 3: sticky_list
# ------------------------------------------------------------------


@mcp.tool()
def sticky_list(
    limit: int = 20,
    cursor: str | None = None,
    category: str | None = None,
    entity: str | None = None,
    after: str | None = None,
    before: str | None = None,
    needs_review: bool | None = None,
    sort: str | None = None,
    thread: str | None = None,
) -> dict:
    """List thoughts with pagination and optional filters.

    Args:
        limit: Maximum number of results per page.
        cursor: Pagination cursor from a previous response.
        category: Filter by category.
        entity: Filter by entity name.
        after: Only thoughts after this ISO datetime.
        before: Only thoughts before this ISO datetime.
        needs_review: Filter by review status.
        sort: Sort order (e.g. "created_at_desc", "created_at_asc").
        thread: Filter by research thread name.
    """
    kwargs: dict = {"limit": limit}
    if cursor is not None:
        kwargs["cursor"] = cursor
    if category is not None:
        kwargs["category"] = category
    if entity is not None:
        kwargs["entity"] = entity
    if after is not None:
        kwargs["after"] = after
    if before is not None:
        kwargs["before"] = before
    if needs_review is not None:
        kwargs["needs_review"] = needs_review
    if sort is not None:
        kwargs["sort"] = sort
    if thread is not None:
        kwargs["thread"] = thread
    return get_service().list_thoughts(**kwargs)


# ------------------------------------------------------------------
# Tool 4: sticky_review
# ------------------------------------------------------------------


@mcp.tool()
def sticky_review(limit: int = 10) -> dict:
    """Get thoughts that need human review (low-confidence classification).

    Args:
        limit: Maximum number of review items to return.
    """
    return get_service().get_review_items(limit)


# ------------------------------------------------------------------
# Tool 5: sticky_classify
# ------------------------------------------------------------------


@mcp.tool()
def sticky_classify(thought_id: str, category: str) -> dict:
    """Manually classify a thought, setting confidence to 1.0 (overrides AI classification).

    Args:
        thought_id: ID of the thought to classify.
        category: The category to assign. Must be one of: idea, project, person, meeting, action, reference, journal.
    """
    return get_service().classify_thought(thought_id, category)


# ------------------------------------------------------------------
# Tool 6: sticky_entities
# ------------------------------------------------------------------


@mcp.tool()
def sticky_entities(
    entity_type: str | None = None,
    query: str | None = None,
    limit: int = 20,
    sort: str = "last_seen",
) -> dict:
    """List tracked entities — people, projects, and concepts extracted from your thoughts. Each entity shows mention count and recent thoughts.

    Args:
        entity_type: Filter by type — "person", "project", or "concept".
        query: Search entities by name (e.g. "Sarah", "Zettelkasten").
        limit: Maximum number of entities to return.
        sort: Sort order — "last_seen" (default) or "mention_count".
    """
    return get_service().list_entities(
        entity_type=entity_type, query=query, limit=limit, sort=sort
    )


# ------------------------------------------------------------------
# Tool 7: sticky_digest
# ------------------------------------------------------------------


@mcp.tool()
def sticky_digest(period: str = "day", since: str | None = None) -> dict:
    """Generate a digest summarizing recent thoughts.

    Args:
        period: Time period — "day", "week", or "month".
        since: Custom start time as ISO datetime (overrides period).
    """
    return get_service().digest(period=period, since=since)


# ------------------------------------------------------------------
# Tool 8: sticky_export
# ------------------------------------------------------------------


@mcp.tool()
def sticky_export(
    format: str,
    output_path: str | None = None,
    category: str | None = None,
    entity: str | None = None,
    after: str | None = None,
    before: str | None = None,
) -> dict:
    """Export thoughts to JSON or Markdown files.

    Args:
        format: Export format — "json" or "markdown".
        output_path: Destination file or directory path.
        category: Filter by category.
        entity: Filter by entity name.
        after: Only thoughts after this ISO datetime.
        before: Only thoughts before this ISO datetime.
    """
    if output_path is None:
        output_path = f"sticky_export.{format}" if format == "json" else "sticky_export"

    filters: dict = {}
    if category is not None:
        filters["category"] = category
    if entity is not None:
        filters["entity"] = entity
    if after is not None:
        filters["after"] = after
    if before is not None:
        filters["before"] = before

    return get_service().export_data(format, output_path, **filters)


# ------------------------------------------------------------------
# Tool 9: sticky_import
# ------------------------------------------------------------------


@mcp.tool()
def sticky_import(
    source_path: str,
    format: str = "auto",
    dry_run: bool = False,
) -> dict:
    """Import thoughts from JSON, Markdown, or text files.

    Args:
        source_path: Path to the file or directory to import.
        format: Import format — "auto", "json", "markdown", or "text".
        dry_run: If True, preview import without writing data.
    """
    return get_service().import_data(source_path, format=format, dry_run=dry_run)


# ------------------------------------------------------------------
# Tool 10: sticky_stats
# ------------------------------------------------------------------


@mcp.tool()
def sticky_stats() -> dict:
    """Show system statistics: thought counts, storage info, and configuration."""
    return get_service().stats()


# ------------------------------------------------------------------
# Tool 11: sticky_update
# ------------------------------------------------------------------


@mcp.tool()
def sticky_update(thought_id: str, content: str) -> dict:
    """Update a thought's content. Re-embeds and re-classifies automatically.

    Args:
        thought_id: ID of the thought to update.
        content: The new content text.
    """
    return get_service().update(thought_id, content)


# ------------------------------------------------------------------
# Tool 12: sticky_delete
# ------------------------------------------------------------------


@mcp.tool()
def sticky_delete(thought_id: str) -> dict:
    """Permanently delete a thought.

    Args:
        thought_id: ID of the thought to delete.
    """
    return get_service().delete(thought_id)


# ------------------------------------------------------------------
# Tool 13: sticky_config
# ------------------------------------------------------------------


@mcp.tool()
def sticky_config(
    action: str,
    key: str | None = None,
    value: str | None = None,
) -> dict:
    """View or modify sticky configuration.

    Args:
        action: "get" to read config, "set" to change a value.
        key: Config key to get or set (omit for full config dump).
        value: New value when action is "set".
    """
    svc = get_service()
    if action == "set" and key and value:
        return svc.set_config_value(key, value)
    elif action == "get" and key:
        config = svc.get_config_display()
        return config.get(key, {"error": f"Unknown key: {key}"})
    else:
        return svc.get_config_display()


# ------------------------------------------------------------------
# Tool 14: sticky_related
# ------------------------------------------------------------------


@mcp.tool()
def sticky_related(thought_id: str, limit: int = 3) -> list[dict]:
    """Find related thoughts using semantic similarity, entity co-occurrence, and recency. Results are filtered by a quality threshold — only meaningful connections are returned.

    Args:
        thought_id: ID of the thought to find related items for.
        limit: Maximum number of related thoughts to return.
    """
    return get_service().related_thoughts(thought_id, limit=limit)


# ------------------------------------------------------------------
# Tool 15: sticky_privacy
# ------------------------------------------------------------------


@mcp.tool()
def sticky_privacy() -> dict:
    """Show privacy and data flow information.

    Explains what data stays local vs. what is sent to cloud services.
    """
    return get_service().privacy_info()


# ------------------------------------------------------------------
# Tool 16: sticky_actions
# ------------------------------------------------------------------


@mcp.tool()
def sticky_actions(
    action: str = "list",
    action_id: str | None = None,
) -> dict:
    """List or complete action items extracted from thoughts.

    Args:
        action: "list" to show action items, "complete" to mark one done.
        action_id: ID of the action item to complete (required when action="complete").
    """
    svc = get_service()
    if action == "complete" and action_id:
        return svc.complete_action(action_id)
    else:
        return svc.list_actions()


# ------------------------------------------------------------------
# Tool 17: sticky_reclassify
# ------------------------------------------------------------------


@mcp.tool()
def sticky_reclassify(
    unclassified_only: bool = True,
    confidence_threshold: float = 0.0,
) -> dict:
    """Re-run AI classification on thoughts that have no category or low confidence. Also extracts new entities and concepts. Use after improving the classifier or to backfill old thoughts.

    Args:
        unclassified_only: If True, only reclassify thoughts with no category (default). If False, reclassify all below the confidence threshold.
        confidence_threshold: Reclassify thoughts below this confidence level (0.0-1.0). Use 1.0 to reclassify everything.
    """
    svc = get_service()
    threshold = confidence_threshold if confidence_threshold > 0 else (
        0.01 if unclassified_only else svc.config.confidence_threshold
    )
    return svc.reclassify_batch(confidence_threshold=threshold)


# ------------------------------------------------------------------
# Tool 18: sticky_brief
# ------------------------------------------------------------------


@mcp.tool()
def sticky_brief() -> dict:
    """Fast morning briefing — no LLM, under 2 seconds.

    Shows: new thought count, open action items, entity pulse
    (most active people/projects this week), and one resurfaced
    older thought connected to recent activity.
    """
    return get_service().brief()


# ------------------------------------------------------------------
# Tool 19: sticky_synthesize
# ------------------------------------------------------------------


@mcp.tool()
def sticky_synthesize(entity_name: str) -> dict:
    """Synthesize everything known about a person, project, or concept.

    Args:
        entity_name: Name of the entity to synthesize (e.g. "Nate Jones", "PARA method").
    """
    return get_service().synthesize(entity_name)


# ------------------------------------------------------------------
# MCP Resources (read-only data endpoints)
# ------------------------------------------------------------------


@mcp.resource("sticky://stats")
def resource_stats() -> str:
    """Current system statistics — thought counts, entity counts, storage info."""
    import json
    return json.dumps(get_service().stats(), indent=2, default=str)


@mcp.resource("sticky://brief")
def resource_brief() -> str:
    """Morning briefing — new thoughts, action items, entity pulse, resurfaced thought."""
    import json
    return json.dumps(get_service().brief(), indent=2, default=str)


@mcp.resource("sticky://entities/people")
def resource_people() -> str:
    """All tracked people with mention counts and recent context."""
    import json
    return json.dumps(
        get_service().list_entities(entity_type="person", limit=50, sort="mention_count"),
        indent=2, default=str,
    )


@mcp.resource("sticky://entities/concepts")
def resource_concepts() -> str:
    """All tracked concepts (methods, frameworks, theories) with mention counts."""
    import json
    return json.dumps(
        get_service().list_entities(entity_type="concept", limit=50, sort="mention_count"),
        indent=2, default=str,
    )


@mcp.resource("sticky://privacy")
def resource_privacy() -> str:
    """Data flow and privacy information — what stays local vs. cloud."""
    import json
    return json.dumps(get_service().privacy_info(), indent=2, default=str)


# ------------------------------------------------------------------
# MCP Prompts (reusable prompt templates)
# ------------------------------------------------------------------


@mcp.prompt()
def research_session(topic: str, source: str = "") -> str:
    """Start a structured research session. Guides you through capturing notes from a source about a specific topic.

    Args:
        topic: The research topic (e.g. "second brain", "gradient boosting").
        source: Optional source being researched (e.g. "Nate Jones YouTube", "SHAP paper").
    """
    source_line = f' from "{source}"' if source else ""
    return f"""You are helping the user research "{topic}"{source_line}.

For each key idea, use sticky_capture to save it with:
- template: "reference" for facts/quotes from the source, "idea" for the user's own synthesis
- thread: "{topic.lower().replace(' ', '-')}-research"
{f'- source_url: include if a URL is available' if source else ''}

After capturing 5-8 notes, use sticky_synthesize to generate a synthesis of everything captured about this topic.

Start by asking: What's the first key idea or insight you want to capture about "{topic}"?"""


@mcp.prompt()
def daily_review() -> str:
    """Run a daily review workflow. Checks your morning brief, reviews action items, and suggests what to focus on."""
    return """Run a daily review for the user:

1. Call sticky_brief to get the morning briefing
2. Show: new thought count, open action items, entity pulse, and any resurfaced thought
3. Call sticky_actions to show open action items
4. Highlight any action items expiring soon
5. Suggest which items to focus on today based on the entity pulse (most active people/projects)

Keep it concise — this should take under 2 minutes to read."""


@mcp.prompt()
def prepare_for_meeting(person: str) -> str:
    """Prepare context for a meeting with someone. Pulls everything you know about them.

    Args:
        person: Name of the person you're meeting with (e.g. "Sarah", "Marcus").
    """
    return f"""Help the user prepare for a meeting with {person}:

1. Call sticky_synthesize with entity_name="{person}" to get a full synthesis
2. Call sticky_search with entity="{person}" to find recent thoughts
3. Call sticky_actions to check for action items related to {person}
4. Summarize:
   - Key context about {person} (what you know, recent interactions)
   - Open action items involving {person}
   - Suggested talking points based on recent thoughts
   - Any unresolved questions or follow-ups

Present this as a concise meeting prep brief."""


@mcp.prompt()
def weekly_review() -> str:
    """Run a weekly review. Generates a digest, reviews entities, and identifies patterns."""
    return """Run a weekly review for the user:

1. Call sticky_digest with period="week" for the weekly summary
2. Call sticky_entities sorted by mention_count to see who/what was most active
3. Call sticky_actions to review all open action items
4. Identify:
   - Top themes of the week
   - People who came up most often (and why)
   - Action items that are overdue or expiring
   - Patterns or connections between this week's thoughts
5. Ask if there's anything the user wants to capture as a reflection before closing the week."""


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
