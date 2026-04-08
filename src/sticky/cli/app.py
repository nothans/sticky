"""Typer CLI for sticky — a local-first personal memory system.

Provides 20 commands wrapping StickyService with Rich-formatted output.
Every command (except tui) supports --json for machine-readable output.
"""

from __future__ import annotations

import json
import sys
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(name="sticky", help="A local-first personal memory system.")
console = Console()

_service = None


def _json_out(data: object) -> None:
    """Print JSON to stdout without Rich formatting.

    Uses print() instead of console.print() so library warnings
    written to stderr don't contaminate the JSON output.
    """
    print(json.dumps(data, indent=2, default=str))


def get_service():
    """Lazily initialize and return the StickyService singleton."""
    global _service
    if _service is None:
        from sticky.core.service import StickyService

        _service = StickyService()
        _service.initialize()
    return _service


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CATEGORY_COLORS = {
    "idea": "cyan",
    "person": "magenta",
    "meeting": "blue",
    "action": "yellow",
    "reflection": "green",
    "reference": "white",
}


def _score_color(score: float) -> str:
    if score >= 0.7:
        return "green"
    if score >= 0.4:
        return "yellow"
    return "red"


def _category_badge(category: str | None) -> str:
    if category is None:
        return "[dim]uncategorized[/]"
    color = _CATEGORY_COLORS.get(category, "white")
    return f"[{color}]{category}[/]"


def _truncate(text: str, max_len: int = 80) -> str:
    text = text.replace("\n", " ")
    if len(text) > max_len:
        return text[: max_len - 3] + "..."
    return text


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@app.command()
def add(
    content: str = typer.Argument(..., help="The thought to capture"),
    template: Optional[str] = typer.Option(None, "--template", "-t", help="Template hint"),
    source: str = typer.Option("cli", "--source", "-s", help="Source identifier"),
    url: Optional[str] = typer.Option(None, "--url", "-u", help="Source URL"),
    thread: Optional[str] = typer.Option(None, "--thread", help="Research thread name"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Minimal output"),
    output_json: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Capture a new thought."""
    svc = get_service()
    result = svc.capture(content=content, template=template, source=source, source_url=url, thread=thread)

    if output_json:
        _json_out(result)
        return

    if quiet:
        console.print(result["id"])
        return

    category = result.get("category")
    confidence = result.get("confidence", 0.0)
    review = result.get("needs_review", False)

    console.print(f"[bold green]Captured[/] {result['id'][:8]}")
    console.print(f"  Category: {_category_badge(category)} ({confidence:.0%})")
    if review:
        console.print("  [yellow]Needs review[/]")
    entities = result.get("entities", [])
    if entities:
        names = ", ".join(
            e.get("name", str(e)) if isinstance(e, dict) else str(e)
            for e in entities
        )
        console.print(f"  Entities: {names}")


@app.command()
def search(
    query: str = typer.Argument(..., help="Search query"),
    limit: int = typer.Option(10, "--limit", "-n", help="Max results"),
    category: Optional[str] = typer.Option(None, "--category", "-c"),
    entity: Optional[str] = typer.Option(None, "--entity", "-e"),
    after: Optional[str] = typer.Option(None, "--after", "-a"),
    before: Optional[str] = typer.Option(None, "--before", "-b"),
    needs_review: bool = typer.Option(False, "--needs-review"),
    mode: str = typer.Option("hybrid", "--mode", "-m"),
    output_json: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Search thoughts with hybrid vector + keyword search."""
    svc = get_service()
    filters = {}
    if category:
        filters["category"] = category
    if entity:
        filters["entity"] = entity
    if after:
        filters["after"] = after
    if before:
        filters["before"] = before
    if needs_review:
        filters["needs_review"] = True

    results = svc.search(query=query, limit=limit, mode=mode, **filters)

    if output_json:
        _json_out(results)
        return

    if not results:
        console.print(f'No results for "{query}"')
        return

    elapsed = results[0].get("search_time_ms", 0) if results else 0
    console.print(
        f'Found {len(results)} results for "{query}" ({elapsed:.0f}ms)\n'
    )

    for r in results:
        score = r.get("score", 0.0)
        cat = r.get("category")
        created = r.get("created_at", "")[:10]
        content = _truncate(r.get("content", ""), 72)
        color = _score_color(score)

        console.print(
            f"[{color}][{score:.2f}][/] {_category_badge(cat)} | {created}"
        )
        console.print(f"  {content}")

        # Show entities if present
        metadata = r.get("metadata", {})
        if isinstance(metadata, dict):
            ents = metadata.get("entities", [])
            if ents:
                console.print(f"  Entities: {', '.join(ents)}")
        console.print()


@app.command(name="list")
def list_cmd(
    limit: int = typer.Option(20, "--limit", "-n"),
    category: Optional[str] = typer.Option(None, "--category", "-c"),
    entity: Optional[str] = typer.Option(None, "--entity", "-e"),
    after: Optional[str] = typer.Option(None, "--after", "-a"),
    before: Optional[str] = typer.Option(None, "--before", "-b"),
    needs_review: bool = typer.Option(False, "--needs-review"),
    thread: Optional[str] = typer.Option(None, "--thread", help="Filter by thread"),
    cursor: Optional[str] = typer.Option(None, "--cursor"),
    output_json: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Browse thoughts."""
    svc = get_service()
    kwargs = {"limit": limit}
    if category:
        kwargs["category"] = category
    if entity:
        kwargs["entity"] = entity
    if after:
        kwargs["after"] = after
    if before:
        kwargs["before"] = before
    if needs_review:
        kwargs["needs_review"] = True
    if thread:
        kwargs["thread"] = thread
    if cursor:
        kwargs["cursor"] = cursor

    result = svc.list_thoughts(**kwargs)

    if output_json:
        _json_out(result)
        return

    thoughts = result.get("thoughts", [])
    total = result.get("total", 0)

    if not thoughts:
        console.print("No thoughts found.")
        return

    table = Table(title=f"Thoughts ({total} total)")
    table.add_column("ID", style="dim", width=10)
    table.add_column("Category", width=12)
    table.add_column("Content", min_width=30)
    table.add_column("Created", width=12)

    for t in thoughts:
        table.add_row(
            t["id"][:8],
            _category_badge(t.get("category")),
            _truncate(t.get("content", ""), 60),
            t.get("created_at", "")[:10],
        )

    console.print(table)

    if result.get("has_more"):
        console.print(
            f"\n[dim]More results available. Use --cursor to paginate.[/]"
        )


@app.command()
def review(
    limit: int = typer.Option(10, "--limit", "-n"),
    output_json: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Show thoughts needing review."""
    svc = get_service()
    result = svc.get_review_items(limit=limit)

    if output_json:
        _json_out(result)
        return

    items = result.get("items", [])
    total = result.get("total", 0)

    if not items:
        console.print("[green]No thoughts need review.[/]")
        return

    console.print(f"[yellow]{total} thought(s) need review[/]\n")

    for item in items:
        cat = item.get("category")
        confidence = item.get("confidence", 0.0)
        content = _truncate(item.get("content", ""), 72)
        console.print(
            f"  {item['id'][:8]} {_category_badge(cat)} ({confidence:.0%})"
        )
        console.print(f"    {content}\n")


@app.command()
def classify(
    thought_id: str = typer.Argument(..., help="Thought ID"),
    category: str = typer.Option(..., "--category", "-c", help="New category"),
    output_json: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Manually reclassify a thought."""
    svc = get_service()
    try:
        result = svc.classify_thought(thought_id, category)
    except ValueError as exc:
        console.print(f"[red]Error:[/] {exc}")
        raise typer.Exit(code=1)

    if output_json:
        _json_out(result)
        return

    console.print(
        f"[green]Reclassified[/] {thought_id[:8]} -> {_category_badge(category)}"
    )


@app.command()
def entities(
    entity_type: Optional[str] = typer.Option(None, "--type", "-t"),
    query: Optional[str] = typer.Option(None, "--query", "-q"),
    limit: int = typer.Option(20, "--limit", "-n"),
    output_json: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Browse tracked entities (people, projects, concepts)."""
    svc = get_service()
    kwargs = {"limit": limit}
    if entity_type:
        kwargs["entity_type"] = entity_type
    if query:
        kwargs["query"] = query

    result = svc.list_entities(**kwargs)

    if output_json:
        _json_out(result)
        return

    ents = result.get("entities", [])
    total = result.get("total", 0)

    if not ents:
        console.print("No entities found.")
        return

    table = Table(title=f"Entities ({total} total)")
    table.add_column("Name", min_width=15)
    table.add_column("Type", width=10)
    table.add_column("Mentions", width=10, justify="right")
    table.add_column("Last Seen", width=12)

    for e in ents:
        table.add_row(
            e.get("name", ""),
            e.get("entity_type", ""),
            str(e.get("mention_count", 0)),
            e.get("last_seen", "")[:10],
        )

    console.print(table)


@app.command()
def digest(
    period: str = typer.Option("day", "--period", "-p", help="day, week, or month"),
    since: Optional[str] = typer.Option(None, "--since", "-s"),
    output_json: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Generate a digest summarizing recent thoughts."""
    svc = get_service()
    result = svc.digest(period=period, since=since)

    if output_json:
        _json_out(result)
        return

    console.print(f"[bold]Digest ({result.get('period', period)})[/]\n")
    console.print(result.get("digest", ""))
    console.print(f"\n[dim]{result.get('thought_count', 0)} thoughts summarized[/]")

    actions = result.get("action_items", [])
    if actions:
        console.print(f"\n[yellow]Action items ({len(actions)}):[/]")
        for a in actions:
            console.print(f"  - {a.get('content', '')}")

    people = result.get("people_mentioned", [])
    if people:
        console.print(f"\n[magenta]People mentioned:[/] {', '.join(people)}")

    resurfaced = result.get("resurfaced")
    if resurfaced:
        console.print(f"\n[cyan]Resurfaced:[/] {_truncate(resurfaced.get('content', ''), 72)}")


@app.command(name="export")
def export_cmd(
    format: str = typer.Argument(..., help="Export format: markdown or json"),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Output path"),
    category: Optional[str] = typer.Option(None, "--category", "-c"),
    after: Optional[str] = typer.Option(None, "--after", "-a"),
    before: Optional[str] = typer.Option(None, "--before", "-b"),
    output_json: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Export thoughts to markdown or JSON."""
    svc = get_service()

    if output is None:
        if format == "json":
            output = "sticky_export.json"
        else:
            output = "sticky_export"

    filters = {}
    if category:
        filters["category"] = category
    if after:
        filters["after"] = after
    if before:
        filters["before"] = before

    result = svc.export_data(format=format, output_path=output, **filters)

    if output_json:
        _json_out(result)
        return

    console.print(
        f"[green]Exported[/] {result.get('count', 0)} thoughts as {format} to {result.get('path', output)}"
    )


@app.command(name="import")
def import_cmd(
    source: str = typer.Argument(..., help="Path to import file or directory"),
    format: str = typer.Option("auto", "--format", "-f", help="auto, json, markdown, or text"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without importing"),
    output_json: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Import thoughts from a file or directory."""
    svc = get_service()
    result = svc.import_data(source_path=source, format=format, dry_run=dry_run)

    if output_json:
        _json_out(result)
        return

    imported = result.get("imported", 0)
    skipped = result.get("skipped", 0)
    errors = result.get("errors", [])
    prefix = "[dim](dry run)[/] " if dry_run else ""

    console.print(f"{prefix}[green]Imported:[/] {imported}")
    if skipped:
        console.print(f"{prefix}[yellow]Skipped:[/] {skipped}")
    if errors:
        console.print(f"{prefix}[red]Errors:[/] {len(errors)}")
        for err in errors:
            console.print(f"  {err}")


@app.command()
def stats(
    output_json: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Show system statistics."""
    svc = get_service()
    result = svc.stats()

    if output_json:
        _json_out(result)
        return

    thoughts = result.get("thoughts", {})
    ent = result.get("entities", {})
    digests = result.get("digests", {})
    system = result.get("system", {})

    console.print("[bold]Sticky Stats[/]\n")

    table = Table(show_header=False, box=None)
    table.add_column("Key", style="dim", width=20)
    table.add_column("Value")

    table.add_row("Thoughts", str(thoughts.get("total", 0)))
    table.add_row("  Needs review", str(thoughts.get("needs_review", 0)))
    table.add_row("Entities", str(ent.get("total", 0)))
    table.add_row("Digests", str(digests.get("total", 0)))
    table.add_row("", "")
    table.add_row("Data dir", system.get("data_dir", ""))
    table.add_row("DB path", system.get("db_path", ""))
    table.add_row("Embedding model", system.get("embedding_model", ""))
    table.add_row("LLM model", system.get("llm_model", ""))

    console.print(table)


@app.command()
def update(
    thought_id: str = typer.Argument(..., help="Thought ID"),
    content: str = typer.Argument(..., help="New content"),
    output_json: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Update a thought's content."""
    svc = get_service()
    try:
        result = svc.update(thought_id, content)
    except ValueError as exc:
        console.print(f"[red]Error:[/] {exc}")
        raise typer.Exit(code=1)

    if output_json:
        _json_out(result)
        return

    console.print(f"[green]Updated[/] {thought_id[:8]}")
    console.print(f"  {_truncate(result.get('content', ''), 72)}")


@app.command()
def delete(
    thought_id: str = typer.Argument(..., help="Thought ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
    output_json: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Delete a thought permanently."""
    if not yes:
        confirm = typer.confirm(f"Delete thought {thought_id}?")
        if not confirm:
            raise typer.Abort()

    svc = get_service()
    try:
        result = svc.delete(thought_id)
    except ValueError as exc:
        console.print(f"[red]Error:[/] {exc}")
        raise typer.Exit(code=1)

    if output_json:
        _json_out(result)
        return

    console.print(
        f"[red]Deleted[/] {thought_id[:8]}: {result.get('preview', '')}"
    )


@app.command()
def reclassify(
    unclassified: bool = typer.Option(False, "--unclassified", help="Only reclassify null-category thoughts"),
    below: float = typer.Option(0.0, "--below", help="Reclassify thoughts below this confidence"),
    output_json: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Reclassify thoughts with missing or low-confidence categories."""
    svc = get_service()
    threshold = below if below > 0 else (0.01 if unclassified else svc.config.confidence_threshold)
    result = svc.reclassify_batch(confidence_threshold=threshold)

    if output_json:
        _json_out(result)
        return

    reclassified = result.get("reclassified", 0)
    total = result.get("total_candidates", 0)

    if reclassified == 0:
        console.print("[green]No thoughts need reclassification.[/]")
        return

    console.print(f"[green]Reclassified {reclassified}/{total} thoughts[/]\n")
    for r in result.get("results", []):
        old = r.get("old_category") or "none"
        new = r.get("new_category", "?")
        conf = r.get("confidence", 0)
        console.print(f"  {r['id'][:8]} {old} -> {_category_badge(new)} ({conf:.0%})")


# ---------------------------------------------------------------------------
# Config subcommand group
# ---------------------------------------------------------------------------

config_app = typer.Typer(help="View and modify configuration.")
app.add_typer(config_app, name="config")


@config_app.callback(invoke_without_command=True)
def config_default(
    ctx: typer.Context,
    output_json: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Show all configuration."""
    if ctx.invoked_subcommand is not None:
        return

    svc = get_service()
    result = svc.get_config_display()

    if output_json:
        _json_out(result)
        return

    table = Table(title="Configuration")
    table.add_column("Key", style="dim", min_width=20)
    table.add_column("Value", min_width=25)
    table.add_column("Source", width=12)

    for key, info in result.items():
        table.add_row(key, str(info.get("value", "")), info.get("source", ""))

    console.print(table)


@config_app.command(name="get")
def config_get(
    key: str = typer.Argument(..., help="Config key to read"),
    output_json: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Get a configuration value."""
    svc = get_service()
    config_data = svc.get_config_display()

    if key not in config_data:
        console.print(f"[red]Unknown config key:[/] {key}")
        raise typer.Exit(code=1)

    info = config_data[key]

    if output_json:
        _json_out({key: info})
        return

    console.print(f"{key} = {info.get('value', '')} [dim]({info.get('source', '')})[/]")


@config_app.command(name="set")
def config_set(
    key: str = typer.Argument(..., help="Config key to set"),
    value: str = typer.Argument(..., help="New value"),
    output_json: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Set a configuration value."""
    svc = get_service()

    # Try to convert value to the right type
    config_data = svc.get_config_display()
    if key not in config_data:
        console.print(f"[red]Unknown config key:[/] {key}")
        raise typer.Exit(code=1)

    # Coerce the value based on the current type
    current = config_data[key].get("value")
    coerced_value: object = value
    if isinstance(current, bool):
        coerced_value = value.lower() in ("true", "1", "yes")
    elif isinstance(current, int):
        try:
            coerced_value = int(value)
        except ValueError:
            pass
    elif isinstance(current, float):
        try:
            coerced_value = float(value)
        except ValueError:
            pass

    try:
        result = svc.set_config_value(key, coerced_value)
    except Exception as exc:
        console.print(f"[red]Error:[/] {exc}")
        raise typer.Exit(code=1)

    if output_json:
        _json_out(result)
        return

    console.print(f"[green]Set[/] {key}: {result.get('previous')} -> {result.get('new')}")


@config_app.command(name="path")
def config_path(
    output_json: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Show the config file path."""
    svc = get_service()
    path = str(svc.config.config_file)

    if output_json:
        _json_out({"path": path})
        return

    console.print(path)


# ---------------------------------------------------------------------------
# TUI
# ---------------------------------------------------------------------------


@app.command()
def tui():
    """Launch the terminal UI."""
    from sticky.tui.app import StickyApp

    tui_app = StickyApp()
    tui_app.run()


# ---------------------------------------------------------------------------
# Privacy
# ---------------------------------------------------------------------------


@app.command()
def privacy(
    output_json: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Show data flow and privacy information."""
    svc = get_service()
    result = svc.privacy_info()

    if output_json:
        _json_out(result)
        return

    console.print("[bold]Data Flow & Privacy[/]\n")

    flow = result.get("data_flow", {})
    for component, location in flow.items():
        if location == "LOCAL":
            badge = "[green]LOCAL[/]"
        else:
            badge = "[red]CLOUD[/]"
        console.print(f"  {component:<20} {badge}")

    console.print(f"\n{result.get('description', '')}")
    console.print(f"\n[dim]Data dir:[/] {result.get('data_dir', '')}")
    console.print(f"[dim]DB path:[/]  {result.get('db_path', '')}")


# ---------------------------------------------------------------------------
# Actions subcommand group
# ---------------------------------------------------------------------------

actions_app = typer.Typer(help="Manage action items.")
app.add_typer(actions_app, name="actions")


@actions_app.callback(invoke_without_command=True)
def actions_default(
    ctx: typer.Context,
    show_completed: bool = typer.Option(False, "--completed", help="Show completed actions"),
    output_json: bool = typer.Option(False, "--json", help="JSON output"),
):
    """List action items."""
    if ctx.invoked_subcommand is not None:
        return

    svc = get_service()
    result = svc.list_actions(completed=show_completed)

    if output_json:
        _json_out(result)
        return

    actions = result.get("actions", [])
    total = result.get("total", 0)

    if not actions:
        console.print("No action items found.")
        return

    table = Table(title=f"Action Items ({total})")
    table.add_column("ID", style="dim", width=10)
    table.add_column("Action", min_width=30)
    table.add_column("Person", width=15)
    table.add_column("Status", width=10)

    for a in actions:
        status = "[green]Done[/]" if a.get("completed") else "[yellow]Open[/]"
        table.add_row(
            a["id"][:8],
            _truncate(a.get("content", ""), 50),
            a.get("person", "") or "",
            status,
        )

    console.print(table)


@actions_app.command(name="complete")
def actions_complete(
    action_id: str = typer.Argument(..., help="Action item ID"),
    output_json: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Mark an action item as complete."""
    svc = get_service()
    try:
        result = svc.complete_action(action_id)
    except ValueError as exc:
        console.print(f"[red]Error:[/] {exc}")
        raise typer.Exit(code=1)

    if output_json:
        _json_out(result)
        return

    console.print(f"[green]Completed[/] action {action_id[:8]}")


# ---------------------------------------------------------------------------
# Schedule subcommand group
# ---------------------------------------------------------------------------

schedule_app = typer.Typer(help="Manage scheduled tasks.")
app.add_typer(schedule_app, name="schedule")


@schedule_app.command(name="digest")
def schedule_digest_cmd(
    time: str = typer.Option("09:00", "--time", "-t", help="Time in HH:MM (24h)"),
    output_json: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Schedule daily digest at a given time."""
    from sticky.core.scheduler import schedule_digest

    result = schedule_digest(time)
    if output_json:
        _json_out(result)
        return
    if result["status"] == "scheduled":
        console.print(
            f"[green]Digest scheduled[/] at {result['time']} daily via {result['method']}"
        )
    else:
        console.print(f"[red]Failed:[/] {result.get('message', 'Unknown error')}")


@schedule_app.command(name="list")
def schedule_list_cmd(
    output_json: bool = typer.Option(False, "--json", help="JSON output"),
):
    """List active schedules."""
    from sticky.core.scheduler import list_schedules

    schedules = list_schedules()
    if output_json:
        _json_out(schedules)
        return
    if not schedules:
        console.print("No active schedules.")
        return
    for s in schedules:
        console.print(
            f"  {s.get('name', 'unknown')}: {s.get('schedule', s.get('details', ''))}"
        )


@schedule_app.command(name="remove")
def schedule_remove_cmd(
    output_json: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Remove the digest schedule."""
    from sticky.core.scheduler import remove_schedule

    result = remove_schedule()
    if output_json:
        _json_out(result)
        return
    if result["status"] == "removed":
        console.print(f"[green]Schedule removed[/] ({result['method']})")
    else:
        console.print(f"[yellow]{result.get('status', 'unknown')}[/]")


# ---------------------------------------------------------------------------
# Brief
# ---------------------------------------------------------------------------


@app.command()
def brief(
    output_json: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Show a fast morning briefing — no LLM, under 2 seconds."""
    import datetime as dt

    svc = get_service()
    result = svc.brief()

    if output_json:
        _json_out(result)
        return

    today = dt.date.today().strftime("%A, %B %d")
    console.print(f"[bold]sticky brief[/] — {today}\n")

    new = result.get("new_thoughts", 0)
    if new > 0:
        console.print(f"  [cyan]{new}[/] new thoughts since last digest")
    else:
        console.print("  No new thoughts since last digest")

    review = result.get("review_count", 0)
    if review > 0:
        console.print(f"  [yellow]{review}[/] thoughts need review")

    actions = result.get("action_items", [])
    if actions:
        console.print(f"\n  [bold]Action Items ({len(actions)}):[/]")
        for a in actions:
            person = f" [dim]({a['person']})[/]" if a.get("person") else ""
            console.print(f"  [yellow]●[/] {a['content']}{person}")

    pulse = result.get("entity_pulse", [])
    if pulse:
        console.print(f"\n  [bold]Active This Week:[/]")
        parts = [f"{p['name']} ({p['mentions']})" for p in pulse]
        console.print(f"  {', '.join(parts)}")

    resurfaced = result.get("resurfaced")
    if resurfaced:
        date = resurfaced.get("created_at", "")[:10]
        content = _truncate(resurfaced.get("content", ""), 72)
        console.print(f"\n  [bold]Resurfaced:[/]")
        console.print(f"  [dim]\"{content}\"[/]")
        console.print(f"  [dim]— captured {date}[/]")

    console.print()


# ---------------------------------------------------------------------------
# Related
# ---------------------------------------------------------------------------


@app.command()
def related(
    thought_id: str = typer.Argument(..., help="Thought ID"),
    limit: int = typer.Option(3, "--limit", "-n"),
    output_json: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Show thoughts related to a given thought."""
    svc = get_service()
    results = svc.related_thoughts(thought_id, limit=limit)

    if output_json:
        _json_out(results)
        return

    if not results:
        console.print("No related thoughts found.")
        return

    console.print(f"[bold]Related to {thought_id[:8]}[/]\n")

    for r in results:
        score = r.get("score", 0.0)
        cat = r.get("category")
        content = _truncate(r.get("content", ""), 72)
        color = _score_color(score)

        console.print(
            f"[{color}][{score:.2f}][/] {_category_badge(cat)} | {r.get('created_at', '')[:10]}"
        )
        console.print(f"  {content}\n")


# ---------------------------------------------------------------------------
# Synthesize
# ---------------------------------------------------------------------------


@app.command()
def synthesize(
    entity_name: str = typer.Argument(..., help="Entity name to synthesize"),
    output_json: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Synthesize everything known about a person, project, or concept."""
    svc = get_service()
    result = svc.synthesize(entity_name)

    if output_json:
        _json_out(result)
        return

    entity_type = result.get("entity_type", "")
    count = result.get("thought_count", 0)
    synthesis = result.get("synthesis", "")

    console.print(f"[bold]Synthesis: {entity_name}[/] ({entity_type}, {count} thoughts)\n")
    console.print(synthesis)
    console.print()


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


@app.command()
def setup(
    api_key: Optional[str] = typer.Option(None, "--api-key", "-k", help="OpenRouter API key"),
    mcp: bool = typer.Option(False, "--mcp", help="Print MCP server config JSON for Claude Code"),
    output_json: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Set up sticky for first use. Configures API key and shows MCP config."""
    import shutil

    results: dict = {}

    # 1. Ensure data and config directories exist
    svc = get_service()
    data_dir = svc.config.data_dir
    config_dir = data_dir  # same on all platforms

    results["data_dir"] = str(data_dir)
    results["db_path"] = str(data_dir / "sticky.db")

    if not output_json and not mcp:
        console.print("[bold]sticky setup[/]\n")
        console.print(f"  Data dir: {data_dir}")
        console.print(f"  DB path:  {data_dir / 'sticky.db'}")

    # 2. Set API key if provided
    if api_key:
        svc.set_config_value("openrouter_api_key", api_key)
        results["api_key"] = "configured"
        if not output_json and not mcp:
            console.print(f"  API key:  [green]configured[/]")
    else:
        has_key = bool(svc.config.openrouter_api_key)
        results["api_key"] = "present" if has_key else "missing"
        if not output_json and not mcp:
            if has_key:
                console.print(f"  API key:  [green]present[/]")
            else:
                console.print(f"  API key:  [yellow]not set[/] (use --api-key or set STICKY_OPENROUTER_API_KEY)")

    # 3. Show MCP config
    sticky_cmd = shutil.which("sticky")
    if sticky_cmd:
        mcp_config = {
            "mcpServers": {
                "sticky": {
                    "command": sticky_cmd,
                    "args": ["mcp-serve"],
                }
            }
        }
    else:
        # Fallback to python -m
        mcp_config = {
            "mcpServers": {
                "sticky": {
                    "command": sys.executable,
                    "args": ["-m", "sticky.mcp.server"],
                }
            }
        }

    results["mcp_config"] = mcp_config

    if mcp:
        # Just print MCP config for piping
        print(json.dumps(mcp_config, indent=2))
        return

    if output_json:
        _json_out(results)
        return

    console.print(f"\n  [bold]MCP config[/] (add to .mcp.json or Claude Code settings):\n")
    console.print(json.dumps(mcp_config, indent=2))

    console.print(f"\n  [bold]Quick test:[/]")
    console.print(f'  sticky add "Hello from sticky!"')
    console.print(f"  sticky brief")
    console.print()


# ---------------------------------------------------------------------------
# MCP entry point (for `sticky mcp` shorthand)
# ---------------------------------------------------------------------------


@app.command(name="mcp-serve", hidden=True)
def mcp_serve():
    """Start the MCP server (used internally by MCP clients)."""
    from sticky.mcp.server import mcp as mcp_server
    mcp_server.run()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app()
