"""Typer commands for local capture, retrieval, editing, and recovery."""

from __future__ import annotations

import json
import shutil
import sys
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text

app = typer.Typer(name="sticky", help="Capture, find, and keep your notes locally.")
console = Console()
_service = None


def _json_out(data: object) -> None:
    print(json.dumps(data, indent=2, default=str))


def get_service():
    global _service
    if _service is None:
        from sticky.core.service import StickyService

        service = _call(StickyService)
        _call(service.initialize)
        _service = service
    return _service


def _call(method, *args, **kwargs):
    try:
        return method(*args, **kwargs)
    except (ValueError, OSError) as exc:
        console.print(f"Error: {exc}", style="red", markup=False)
        raise typer.Exit(code=1) from exc


def _truncate(content: str, limit: int = 72) -> str:
    text = content.replace("\n", " ")
    return text if len(text) <= limit else text[:limit - 3] + "..."


def _filters(**values) -> dict:
    return {key: value for key, value in values.items() if value is not None}


@app.command()
def add(
    content: str = typer.Argument(..., help="The thought to capture"),
    source: str = typer.Option("cli", "--source", "-s", help="Source identifier"),
    url: Optional[str] = typer.Option(None, "--url", "-u", help="Source URL"),
    thread: Optional[str] = typer.Option(None, "--thread", help="Thread name"),
    quiet: bool = typer.Option(False, "--quiet", "-q"),
    output_json: bool = typer.Option(False, "--json"),
):
    """Capture a new thought locally. Each submission is saved."""
    result = _call(get_service().capture, content, source=source, source_url=url, thread=thread)
    if output_json:
        _json_out(result)
    elif quiet:
        console.print(result["id"], markup=False)
    else:
        console.print(f"Captured {result['id']}", style="green", markup=False)


@app.command()
def search(
    query: str = typer.Argument(..., help="Search query"),
    limit: Optional[int] = typer.Option(None, "--limit", "-n", min=1),
    category: Optional[str] = typer.Option(None, "--category", "-c"),
    after: Optional[str] = typer.Option(None, "--after", "-a"),
    before: Optional[str] = typer.Option(None, "--before", "-b"),
    output_json: bool = typer.Option(False, "--json"),
):
    """Find notes with local semantic and keyword matching."""
    results = _call(get_service().search, query, **_filters(
        limit=limit, category=category, after=after, before=before
    ))
    if output_json:
        _json_out(results)
        return
    if not results:
        console.print(f'No results for "{query}"', markup=False)
        return
    for result in results:
        console.print(
            f"{result['id']} [{result.get('score', 0):.2f}] "
            f"{result.get('category') or 'uncategorized'} | {result.get('created_at', '')[:10]}",
            markup=False,
        )
        console.print(f"  {_truncate(result.get('content', ''))}", markup=False)


@app.command(name="list")
def list_cmd(
    limit: Optional[int] = typer.Option(None, "--limit", "-n", min=1),
    category: Optional[str] = typer.Option(None, "--category", "-c"),
    after: Optional[str] = typer.Option(None, "--after", "-a"),
    before: Optional[str] = typer.Option(None, "--before", "-b"),
    thread: Optional[str] = typer.Option(None, "--thread"),
    cursor: Optional[str] = typer.Option(None, "--cursor"),
    sort: str = typer.Option("created_at_desc", "--sort", help="created_at_desc or created_at_asc"),
    output_json: bool = typer.Option(False, "--json"),
):
    """Browse notes with a stable pagination cursor."""
    result = _call(get_service().list_thoughts, **_filters(
        limit=limit, category=category, after=after, before=before,
        thread=thread, cursor=cursor, sort=sort,
    ))
    if output_json:
        _json_out(result)
        return
    if not result["thoughts"]:
        console.print("No thoughts found.")
        return
    table = Table(title=f"Thoughts ({result['total']} total)")
    table.add_column("ID")
    table.add_column("Category")
    table.add_column("Content")
    table.add_column("Created")
    for thought in result["thoughts"]:
        table.add_row(Text(thought["id"]), Text(thought.get("category") or "uncategorized"),
                      Text(_truncate(thought["content"])), Text(thought.get("created_at", "")[:10]))
    console.print(table)
    if result.get("has_more"):
        console.print(f"Next cursor: {result['next_cursor']}", markup=False)


@app.command()
def get(
    thought_id: str = typer.Argument(..., help="Full thought ID"),
    output_json: bool = typer.Option(False, "--json"),
):
    """Read a complete note by ID."""
    result = _call(get_service().get_thought, thought_id)
    if output_json:
        _json_out(result)
    else:
        console.print(result["content"], markup=False)


@app.command()
def classify(
    thought_id: str = typer.Argument(...),
    category: str = typer.Option(..., "--category", "-c"),
    output_json: bool = typer.Option(False, "--json"),
):
    """Assign a category manually; no cloud request."""
    result = _call(get_service().classify_thought, thought_id, category)
    if output_json:
        _json_out(result)
    else:
        console.print(f"Categorized {thought_id} as {category}", markup=False)


@app.command()
def update(
    thought_id: str = typer.Argument(...),
    content: str = typer.Argument(..., help="Replacement note text"),
    output_json: bool = typer.Option(False, "--json"),
):
    """Edit note text and update its local search representation."""
    result = _call(get_service().update, thought_id, content)
    if output_json:
        _json_out(result)
    else:
        console.print(f"Updated {thought_id}", style="green", markup=False)


@app.command()
def delete(
    thought_id: str = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
    output_json: bool = typer.Option(False, "--json"),
):
    """Delete a note permanently."""
    if not yes and not typer.confirm(f"Delete thought {thought_id}?"):
        raise typer.Abort()
    result = _call(get_service().delete, thought_id)
    if output_json:
        _json_out(result)
    else:
        console.print(f"Deleted {thought_id}", style="red", markup=False)


@app.command(name="export")
def export_cmd(
    format: str = typer.Argument(..., help="json or markdown"),
    output: Optional[str] = typer.Option(None, "--output", "-o"),
    category: Optional[str] = typer.Option(None, "--category", "-c"),
    after: Optional[str] = typer.Option(None, "--after", "-a"),
    before: Optional[str] = typer.Option(None, "--before", "-b"),
    output_json: bool = typer.Option(False, "--json"),
):
    """Export notes as JSON for restore, or readable Markdown."""
    output = output or ("sticky_export.json" if format == "json" else "sticky_export")
    result = _call(get_service().export_data, format, output,
                   **_filters(category=category, after=after, before=before))
    if output_json:
        _json_out(result)
    else:
        console.print(f"Exported {result['count']} thoughts to {result['path']}", markup=False)


@app.command(name="import")
def import_cmd(
    source: str = typer.Argument(..., help="File or directory path"),
    format: str = typer.Option("auto", "--format", "-f", help="auto, json, markdown, or text"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    output_json: bool = typer.Option(False, "--json"),
):
    """Restore JSON note records or capture text from files."""
    result = _call(get_service().import_data, source, format=format, dry_run=dry_run)
    if output_json:
        _json_out(result)
        if result.get("errors"):
            raise typer.Exit(code=1)
        return
    console.print(f"Imported: {result.get('imported', 0)}; skipped: {result.get('skipped', 0)}")
    for error in result.get("errors", []):
        console.print(str(error), style="red", markup=False)
    if result.get("errors"):
        raise typer.Exit(code=1)


@app.command()
def digest(
    period: str = typer.Option("day", "--period", "-p", help="day, week, or month"),
    since: Optional[str] = typer.Option(None, "--since", "-s"),
    output_json: bool = typer.Option(False, "--json"),
):
    """Send selected notes to OpenRouter for a digest. Requires an API key."""
    result = _call(get_service().digest, period=period, since=since)
    if output_json:
        _json_out(result)
    else:
        console.print(f"Digest ({result['period']})", style="bold")
        console.print(result["digest"], markup=False)
        console.print(f"{result['thought_count']} thoughts summarized")


@app.command()
def stats(output_json: bool = typer.Option(False, "--json")):
    """Show local note count and storage paths."""
    result = get_service().stats()
    if output_json:
        _json_out(result)
    else:
        console.print("Sticky Stats", style="bold")
        console.print(f"Thoughts: {result['thoughts']['total']}")
        for key, value in result["system"].items():
            console.print(f"{key}: {value}", markup=False)


@app.command()
def privacy(output_json: bool = typer.Option(False, "--json")):
    """Show what stays local and what an explicit digest sends."""
    result = get_service().privacy_info()
    if output_json:
        _json_out(result)
    else:
        for component, location in result["data_flow"].items():
            console.print(f"{component}: {location}", markup=False)
        console.print(result["description"], markup=False)


config_app = typer.Typer(help="Read or save startup settings. Writes apply next session.")
app.add_typer(config_app, name="config")


@config_app.callback(invoke_without_command=True)
def config_default(ctx: typer.Context, output_json: bool = typer.Option(False, "--json")):
    if ctx.invoked_subcommand is not None:
        return
    result = get_service().get_config_display()
    if output_json:
        _json_out(result)
    else:
        console.print("Configuration", style="bold")
        for key, info in result.items():
            console.print(f"{key} = {info['value']} ({info['source']})", markup=False)


@config_app.command(name="get")
def config_get(key: str = typer.Argument(...), output_json: bool = typer.Option(False, "--json")):
    values = get_service().get_config_display()
    if key not in values:
        console.print(f"Unknown config key: {key}", style="red", markup=False)
        raise typer.Exit(code=1)
    if output_json:
        _json_out({key: values[key]})
    else:
        console.print(f"{key} = {values[key]['value']}", markup=False)


@config_app.command(name="set")
def config_set(
    key: str = typer.Argument(...), value: str = typer.Argument(...),
    output_json: bool = typer.Option(False, "--json"),
):
    """Validate and save a setting for the next process start."""
    result = _call(get_service().set_config_value, key, value)
    if output_json:
        _json_out(result)
    else:
        console.print(f"Set {key}: {result['previous']} -> {result['new']}; applies next session",
                      markup=False)


@config_app.command(name="path")
def config_path(output_json: bool = typer.Option(False, "--json")):
    path = str(get_service().config.config_file)
    _json_out({"path": path}) if output_json else console.print(path, markup=False)


@app.command()
def setup(
    api_key: Optional[str] = typer.Option(None, "--api-key", "-k"),
    mcp: bool = typer.Option(False, "--mcp", help="Print MCP client configuration"),
    output_json: bool = typer.Option(False, "--json"),
):
    """Prepare local storage and optionally configure explicit cloud digests."""
    svc = get_service()
    if api_key is not None:
        _call(svc.set_config_value, "openrouter_api_key", api_key)
    sticky_cmd = shutil.which("sticky")
    server = {"command": sticky_cmd, "args": ["mcp-serve"]} if sticky_cmd else {
        "command": sys.executable, "args": ["-m", "sticky.mcp.server"]
    }
    mcp_config = {"mcpServers": {"sticky": server}}
    result = {
        "data_dir": str(svc.config.data_dir), "db_path": str(svc.config.db_path),
        "api_key": ("configured" if api_key else "unset") if api_key is not None else (
            "configured" if svc.config.openrouter_api_key else "unset"
        ),
        "mcp_config": mcp_config,
    }
    if mcp:
        _json_out(mcp_config)
    elif output_json:
        _json_out(result)
    else:
        console.print(f"Data dir: {result['data_dir']}", markup=False)
        console.print(f"API key: {result['api_key']} (optional)")
        console.print(json.dumps(mcp_config, indent=2), markup=False)
        console.print('Quick test: sticky add "Hello from sticky!"; sticky search "Hello"', markup=False)


@app.command()
def tui():
    """Launch the local terminal UI."""
    from sticky.tui.app import StickyApp
    StickyApp().run()


@app.command(name="mcp-serve")
def mcp_serve():
    """Start the MCP server for agent clients."""
    from sticky.mcp.server import run_server
    run_server()


if __name__ == "__main__":
    app()
