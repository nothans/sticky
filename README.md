# Sticky

A small personal memory tool. Capture notes, find them with local semantic and
keyword search, edit or delete them, and recover them from a backup.

Python 3.11+. Package: `sticky-brain`; command: `sticky`. Alpha, under active development.

## Start

```powershell
uv sync --locked
uv run --locked sticky setup
uv run --locked sticky add "Meeting with Sarah at 2 pm tomorrow"
uv run --locked sticky add "Meeting moved to 3 pm tomorrow"
uv run --locked sticky search "meeting with Sarah"
uv run --locked sticky tui
```

Capture, editing, and search work without an API key. Every capture is a separate
record, including identical text. Semantic similarity never discards a new note.
The fixed local model is `all-MiniLM-L6-v2` (384 dimensions). Its files download on
first use; later runs use the local cache. If embedding is unavailable, raw notes
still save and remain searchable by keyword. If embedding fails during an edit,
the saved text has no stale vector or classification. A database write failure
rolls the edit back, preserving the original note and its indexes.
For offline use after the model has downloaded, set `HF_HUB_OFFLINE=1`.
Sticky then loads model files from the cache only.
If sqlite-vec is unavailable, keyword search remains usable. When it becomes
available again, the vector index rebuilds from the current notes so edits and
deletions made during the outage cannot leave stale semantic results.

## Commands

All data commands support `--json`. Use `sticky COMMAND --help` for options.

| Command | Purpose |
| --- | --- |
| `add "text"` | Capture locally; optional `--url`, `--source`, and `--thread` |
| `search "query"` | One default semantic/keyword search; optional category/date filters |
| `list` | List notes; optional category/date/thread filters and cursor pagination |
| `get ID` | Read a complete note |
| `update ID "text"` | Replace text locally |
| `delete ID` | Delete a note, with confirmation; `--yes` skips it |
| `classify ID --category CATEGORY` | Set a category manually; category is free text |
| `export json --output notes.json` | Export thought records for JSON restoration |
| `export markdown --output notes` | Write readable files to a new or empty directory |
| `import notes.json` | Restore thought IDs and original metadata |
| `stats` | Local note count and paths |
| `privacy` | Describe the active data flow |
| `config` | View masked settings; local `get`, `set`, and `path` subcommands |
| `digest` | Optional, explicitly requested cloud summary |
| `setup` | Prepare local storage; `--mcp` prints client configuration |
| `tui` | Open the terminal interface |
| `mcp-serve` | Run the MCP server over stdio |

Search treats query text literally for keyword matching, including punctuation
such as `C++` and `alpha-beta`. List cursors are opaque: pass `next_cursor` back
with the same filters and sort direction. Timestamps tied across restored notes
are ordered by ID; the final page has `has_more: false`.
Date filters compare instants across timezone offsets and use exclusive bounds.
Date-only values and datetimes without a timezone mean UTC. Invalid bounds fail
clearly. Legacy notes with malformed dates remain visible without date filters.

The TUI has Home, Search, and About. Capture keeps the draft until saving succeeds.
Open a note to edit, delete, or set its category. Escape returns from Search to
Home, and from a note to its previous view. Displayed shortcuts describe available
actions; no cloud work runs on launch.

## Smaller Alpha surface

Automatic classification, entity/action tracking, review queues, resurfacing,
synthesis, scheduling, model selection, and search-mode selectors have been
removed from the active interfaces. Existing SQLite history remains stored and
can be recovered through a full database backup. No automatic review expiry runs
on startup. Older configuration keys for these features are ignored.

## Settings and optional cloud digests

Settings load at startup: `STICKY_` environment variables override local
`config.toml`, which overrides defaults. `sticky config path` locates the file.
Only these settings remain:

| Key | Default |
| --- | --- |
| `data_dir` | `%LOCALAPPDATA%\sticky` on Windows; `~/.local/share/sticky` elsewhere |
| `default_search_limit` | 10 |
| `default_list_limit` | 20 |
| `openrouter_api_key` | Empty; optional |
| `openrouter_model` | `anthropic/claude-sonnet-4.6` |

Local setting changes apply to the next session. Numeric limits must be positive.
API keys are masked in displayed settings and mutation receipts; a configured key
is stored in your local TOML file unless supplied through the environment.

```powershell
uv run sticky config set default_search_limit 5
uv run sticky config set openrouter_api_key YOUR_KEY
uv run sticky digest --period week
```

A digest requires a configured key and an explicit request. Without a key, it
makes no cloud request. Select at most 1,000 notes using the period or `--since`;
oversized selections fail clearly instead of silently omitting notes. A returned
digest includes the selected thought IDs. Cloud failure leaves notes unchanged.

Capture, editing, storage, and search keep note text on your machine. An explicit
digest sends the selected notes to OpenRouter and the chosen model provider.
Provider logging and retention policies vary; this app does not enforce Zero Data
Retention routing. See [OpenRouter provider logging](https://openrouter.ai/docs/guides/privacy/provider-logging)
and [Zero Data Retention](https://openrouter.ai/docs/guides/features/zdr).

## Recovery

JSON restores thought IDs, content, timestamps, category, source URL, and metadata;
embeddings regenerate locally. Existing IDs are skipped. Plain JSON notes without
IDs become new captures. Use `--dry-run` to validate without writing. Malformed
records are reported while valid records continue; CLI imports exit with status
1 if any records fail, including in previews. JSON exports replace an existing
backup only after a complete write and refuse Sticky's active database and
configuration paths. JSON does
not restore historical entity links, actions/completions, aliases, or digests.
Markdown is readable export; importing it captures text and is not a faithful
record restoration format. Export refuses a nonempty Markdown directory.

For complete database recovery, use SQLite's backup facility, which safely copies
a committed snapshot even while Sticky has the database open. From this checkout,
the following PowerShell example writes a new file in your home directory:

```powershell
@'
import sqlite3
from pathlib import Path
from sticky.core.config import get_config

source = get_config().db_path.resolve()
destination = Path.home() / "sticky-backup.db"
if destination.exists():
    raise SystemExit("Choose a new backup destination; that file already exists")
with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as src:
    with sqlite3.connect(destination) as dst:
        src.backup(dst)
print(destination)
'@ | uv run python -
```

To check recovery, stop Sticky and restore into a fresh directory. This preserves
the original database. Choose a different directory name if it already exists.

```powershell
$restoreDir = Join-Path $env:LOCALAPPDATA 'sticky-restored'
New-Item -ItemType Directory -Path $restoreDir -ErrorAction Stop
Copy-Item -LiteralPath (Join-Path $env:USERPROFILE 'sticky-backup.db') -Destination (Join-Path $restoreDir 'sticky.db')
$env:STICKY_DATA_DIR = $restoreDir
uv run sticky list --json
uv run sticky search "a note you expect"
```

The database backup retains notes, FTS/vector indexes, and existing aliases,
mentions, completed actions, and digest history. TOML settings are a separate file;
copy them separately if needed. Remove the temporary `STICKY_DATA_DIR` environment
override or set it back to your usual location after the check.

## MCP

Run `sticky setup --mcp` for a configuration using your installed executable.
The server exposes 13 tools: `sticky_capture`, `sticky_search`, `sticky_list`,
`sticky_get`, `sticky_classify`, `sticky_update`, `sticky_delete`, `sticky_export`,
`sticky_import`, `sticky_digest`, `sticky_stats`, `sticky_config`, `sticky_privacy`.
Configuration is read-only over MCP. Resources: `sticky://stats` and
`sticky://privacy`. The `research_session` prompt uses capture and search.
The server uses the current MCP v2 SDK, with structured JSON tool results and
annotations describing reads, writes, and cloud access. Expected input and file
errors return tool errors that clients can act on. Older MCP clients can still
use the same tool names over stdio.

## Development

```powershell
uv sync --locked --extra dev
uv run --locked --extra dev pytest
uv build --no-sources
```

The tracked `uv.lock` records the tested package versions for development and CI.
To deliberately refresh dependencies, run `uv lock --upgrade`, then
`uv sync --locked --extra dev` and the core tests. Major SDK versions are bounded
in `pyproject.toml` so upgrades happen deliberately. Python 3.11 remains supported;
the lock selects compatible NumPy/SciPy versions for that interpreter.

SQLite uses FTS5 and sqlite-vec. CLI (Typer), MCP (MCPServer), and TUI (Textual) call
the same service. Core preservation/failure regressions use isolated databases;
retrieval checks also use the real local model. Core feature tests cover capture,
search, editing, deletion, and recovery; they do not establish daily usefulness.

License: MIT.
