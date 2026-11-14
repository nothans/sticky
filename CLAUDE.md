# Sticky

A small personal memory tool: capture, find, edit/delete, recover. Python 3.11+;
package `sticky-brain`, import `sticky`, command `sticky`. Alpha.

## Development direction

Err on the side of reducing complexity and features. Preserve the original note
before enriching it. Avoid new integrations, dashboards, queues, settings, or
derived memory systems without evidence from daily use.

Capture and editing are local, with best-effort fixed MiniLM 384-dimensional
embeddings and keyword fallback. Each capture is a separate record; JSON restore
is idempotent by ID. A changed note cannot keep the previous note's vector or
classification. SQLite text/FTS/vector mutations are atomic.

Automatic classification, entity/action features, review queues, resurfacing,
synthesis, scheduling, and search/model selectors are removed from active
interfaces. Legacy SQLite tables and stored history remain for recovery. Optional
cloud digests require an explicit request and configured key. No startup cloud
calls or automatic generation.

## Layout

- `src/sticky/core/service.py`: shared facade for all interfaces.
- `core/db.py`: SQLite schema, legacy storage, atomic text/FTS/vector mutations,
  stable versioned timestamp/ID/direction cursors.
- `core/search.py`, `embeddings.py`: fixed local hybrid retrieval and literal
  keyword fallback. Apply filters before candidate limits.
- `core/digest.py`: optional explicit cloud summaries.
- `core/config.py`: five startup settings; environment > TOML > defaults.
- `cli/app.py`: core note commands, local settings, optional digest, setup.
- `mcp/server.py`: 13 tools, two resources, one research prompt; settings read-only.
- `tui/`: Home, Search, About and note detail; retain drafts until writes succeed.
- `tests/`: preservation/failure, retrieval, schemas, complete recovery, and Pilot.

## Commands

```powershell
uv sync --locked --extra dev
uv run --locked --extra dev pytest
uv run sticky setup
uv run sticky add "a thought"
uv run sticky search "a thought"
uv run sticky tui
uv run sticky mcp-serve
```

Default database: `%LOCALAPPDATA%\sticky\sticky.db` on Windows,
`~/.local/share/sticky/sticky.db` elsewhere. `STICKY_DATA_DIR` overrides it.
Tests must isolate data and configuration and mock optional HTTP calls.
The tracked `uv.lock` defines the tested development/CI environment. Keep Python
3.11 compatibility when refreshing it. MCP uses the v2 SDK; async protocol
handlers keep the shared SQLite service on its event-loop thread. Read-only and
write annotations and structured results describe the existing small surface.

## Conventions

- Business logic belongs in the core service; interfaces remain thin.
- Update tests and README when behavior changes. Keep command/tool lists accurate.
- Mask API keys in reads, writes, errors, and setup receipts.
- Local configuration writes validate retained fields and apply next session.
- JSON restores thought records; SQLite backup is complete database recovery.
  Use the executable README procedure; preserve legacy history.
- Internal working context lives in ignored `.meta/`.
- Commit messages: lowercase, short, imperative, no prefix or co-author trailers.
  Commit or push only when requested.
