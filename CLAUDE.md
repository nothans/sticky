# Sticky — Personal Memory System

A local-first personal memory system. Capture thoughts with zero friction, let AI
organize them, retrieve with semantic search, review with daily digests. Data stays
on the user's machine; embeddings run locally; classification/digests use OpenRouter.

Package: `sticky-brain` (PyPI name), import name `sticky`. Version 0.1.0. Python ≥3.11.

## Project State

Shipped and working (v0.1.0, Alpha). All three interfaces are implemented and tested
(343 tests). This is a real codebase now — not a design phase. If you touch behavior,
update tests and the README together.

## Layout

```
src/sticky/
  cli/app.py          # Typer + Rich CLI — all subcommands + `mcp-serve`, `tui`, `setup`
  core/               # engine (interface-agnostic)
    service.py        #   facade the CLI/MCP/TUI all call into
    db.py             #   SQLite schema, sqlite-vec KNN + FTS5 keyword index
    models.py         #   Pydantic v2 data models
    embeddings.py     #   local sentence-transformers (all-MiniLM-L6-v2), lazy-loaded
    classify.py       #   OpenRouter classification + entity extraction
    entities.py       #   entity tracking / co-occurrence
    search.py         #   hybrid search (0.6 vector + 0.4 FTS)
    digest.py         #   daily digest, morning brief, entity synthesis
    scheduler.py      #   scheduled digests
    config.py         #   config precedence: env > config.toml > defaults
  mcp/server.py       # FastMCP server — 19 tools, 5 resources, 4 prompts
  mcp/__main__.py     # `python -m sticky.mcp` entry
  tui/                # Textual TUI — 8 views (home, search, detail, review,
                      #   entities, digest, stats, about) + widgets
tests/                # 343 tests, pytest + pytest-asyncio (asyncio_mode=auto)
config.example.toml   # copy to the OS config dir to configure
```

## Architecture

`core/service.py` is the single facade; the CLI, MCP server, and TUI are thin
presentation layers over it. Put business logic in `core/`, never in an interface
layer. When adding a capability, wire it through the service so all three interfaces
get it.

Data flow: capture → local embedding (~100ms) → OpenRouter classification + entity
extraction (~1s) → SQLite (`thoughts`, `entities`, `entity_mentions`, `action_items`,
`digests`) with sqlite-vec KNN + FTS5 → hybrid search → digest/brief/synthesis on demand.

Storage: single SQLite file. `%LOCALAPPDATA%\sticky\sticky.db` on Windows,
`~/.local/share/sticky/sticky.db` on Linux/macOS.

LLM: `anthropic/claude-sonnet-4.6` via OpenRouter (configurable). Works without an API
key — capture and search still function; classification, entity extraction, and
digests require one.

## Tech Stack

Python 3.11+ (uv) · SQLite + sqlite-vec + FTS5 · sentence-transformers (all-MiniLM-L6-v2)
· OpenRouter · Typer + Rich (CLI) · Textual (TUI) · FastMCP (MCP) · Pydantic v2 · httpx.

## Common Commands

```bash
uv sync --dev                      # install with dev deps
uv run pytest                      # run all 343 tests
uv run pytest tests/test_search.py -v
uv run pytest --cov=sticky
uv run sticky setup                # create data dir, print MCP config
uv run sticky add "a thought"      # capture
uv run sticky mcp-serve            # start MCP server (same as `python -m sticky.mcp`)
uv run sticky tui                  # terminal UI
```

## MCP Server

Exposes 19 tools, 5 resources, 4 prompts (see README for the full list). Configured in
the user's Claude config as `python -m sticky.mcp` (stdio, 30s timeout). The heavy deps
(torch/transformers) are lazy-loaded, so cold import is ~1s — well under the timeout.
When adding an MCP tool, back it with a `service.py` method rather than duplicating logic.

## Conventions

- All logic lives in `core/`; interfaces stay thin.
- Every CLI command supports `--json` for scripting — preserve that when editing commands.
- Keep the README's command/tool lists in sync with the code (three interfaces + tool count).
- Add tests for new behavior; match existing test-file naming (`tests/test_<module>.py`).

## Commit Style
- lowercase first letter
- short, direct descriptions (no fluff)
- use a line for each change
- no conventional commit prefixes (no "feat:", "fix:", etc.)
- imperative/present tense ("fix X", "update Y", "remove Z")
- no trailing period
- no Co-Authored-By lines
