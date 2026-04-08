# sticky

A local-first personal memory system. Capture thoughts with zero friction, let AI organize them, retrieve with semantic search, review with daily digests.

Your data stays on your machine. Embeddings run locally. Classification and digests use OpenRouter (configurable).

## Install

```bash
# With pip (any OS)
pip install sticky-brain

# Or with uv (faster)
uv tool install sticky-brain

# Or from source
git clone https://github.com/nothans/sticky.git
cd sticky
pip install -e .
```

After install, the `sticky` command is available globally.

## Quick Start

```bash
# 1. Run setup (creates data directory, shows MCP config)
sticky setup

# 2. Set your OpenRouter API key (for classification + digests)
sticky setup --api-key sk-or-v1-your-key

# 3. Capture your first thought
sticky add "Sarah mentioned she wants to transfer to the platform team"

# 4. Search semantically
sticky search "career changes"

# 5. See what you've captured
sticky stats

# 6. Generate a daily digest
sticky digest

# 7. Get a fast morning briefing (no LLM, instant)
sticky brief
```

Get an OpenRouter key at [openrouter.ai/keys](https://openrouter.ai/keys). Sticky works without a key (capture + search still function), but classification, entity extraction, and digest generation require it.

### Connect to Claude Code

```bash
# Print MCP config JSON
sticky setup --mcp

# Or manually add to your Claude Code MCP settings:
# Command: sticky
# Args: mcp-serve
```

## What It Does

**Capture** a thought in natural language. No categories, no tags, no folders to choose.

**AI classifies** it automatically (idea, meeting, person, action, project, reference, journal) with a confidence score. Low-confidence items are flagged for your review.

**Entities are extracted** (people, projects, concepts) and tracked across all your thoughts. Search by person to see everything you know about them.

**Semantic search** finds thoughts by meaning, not just keywords. "career changes" finds "Sarah is thinking about leaving the team" even though they share no words.

**Daily digest** summarizes your captures by topic, extracts action items, lists people mentioned, and resurfaces a related older thought you might have forgotten.

**Synthesize** everything you know about a person, project, or concept on demand. "What do I know about Sarah?" returns a coherent narrative across all linked thoughts.

## Usage Examples

### Capture a meeting note
```bash
sticky add "1:1 with Sarah — she confirmed she wants to move to platform team. Asked about timeline. I said I'd talk to the lead this week."
```
AI classifies as "meeting", extracts Sarah as a person entity, and creates action items.

### Research a topic
```bash
sticky add "Nate Jones says zero-friction capture is essential. If it takes more than 5 seconds, you won't do it." \
  --url "https://youtube.com/watch?v=..." \
  --thread "second-brain-research"
```
Tags the source URL for attribution and groups captures into a research thread.

### Search by meaning
```bash
sticky search "who is thinking about leaving?"
# Finds: "Sarah mentioned she's thinking about leaving the team..."

sticky search "API compatibility" --mode keyword
# Fast exact-match search via FTS5

sticky search "architecture decisions" --category meeting
# Semantic search filtered to meeting notes only
```

### Synthesize what you know
```bash
sticky synthesize "Sarah"
# Returns a multi-paragraph narrative of everything you know about Sarah,
# with numbered references to source thoughts

sticky synthesize "event sourcing"
# Works for concepts too
```

### Morning routine
```bash
sticky brief
# Instant (no LLM): new thought count, action items, entity pulse,
# resurfaced older thought — under 2 seconds

sticky digest
# AI-generated summary: topics, action items, people mentioned
```

### Track action items
```bash
sticky actions              # list open actions
sticky actions complete ID  # mark one done
```

### Research threads
```bash
sticky add "PARA organizes by actionability, not topic" --thread "pkm-research"
sticky add "Zettelkasten is link-forward vs search-forward" --thread "pkm-research"
sticky list --thread "pkm-research"  # see just this thread
```

### Batch operations
```bash
sticky reclassify --unclassified    # re-run AI on unclassified thoughts
sticky reclassify --below 0.5       # re-run on low-confidence thoughts
```

## Three Interfaces

### CLI

Every command supports `--json` for scripting.

```bash
sticky add "thought text"                # capture
sticky add "notes" -t meeting --url URL  # with template + source URL
sticky add "idea" --thread "research"    # with research thread
sticky search "query"                    # hybrid semantic + keyword
sticky search "term" --mode keyword      # keyword-only (fastest)
sticky list                              # browse all thoughts
sticky list --category meeting           # filter by category
sticky list --thread "research"          # filter by thread
sticky entities --type person            # browse tracked people
sticky entities --type concept           # browse tracked concepts
sticky digest                            # daily summary
sticky digest --period week              # weekly summary
sticky brief                             # fast morning briefing
sticky synthesize "Sarah"                # synthesize entity knowledge
sticky review                            # low-confidence items
sticky reclassify --unclassified         # batch re-classify
sticky classify <id> -c action           # manually reclassify
sticky related <id>                      # find similar thoughts
sticky actions                           # list action items
sticky actions complete <id>             # mark action done
sticky export markdown                   # export to markdown files
sticky export json                       # export to JSON
sticky import ./notes/                   # import from files
sticky stats                             # system info + counts
sticky privacy                           # what's local vs cloud
sticky config                            # view all settings
sticky config set key value              # change a setting
sticky schedule digest --time 08:30      # schedule daily digest
sticky schedule list                     # show active schedules
sticky tui                               # terminal UI
```

### MCP Server

Sticky exposes 19 tools, 5 resources, and 4 prompts for use with Claude Code, Cursor, or any MCP-compatible AI tool.

Configure in `.mcp.json`:

```json
{
  "mcpServers": {
    "sticky": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/sticky", "python", "-m", "sticky.mcp.server"]
    }
  }
}
```

**Tools (19):** `sticky_capture`, `sticky_search`, `sticky_list`, `sticky_review`, `sticky_classify`, `sticky_entities`, `sticky_digest`, `sticky_export`, `sticky_import`, `sticky_stats`, `sticky_update`, `sticky_delete`, `sticky_config`, `sticky_related`, `sticky_privacy`, `sticky_actions`, `sticky_reclassify`, `sticky_brief`, `sticky_synthesize`

**Resources (5):** `sticky://stats`, `sticky://brief`, `sticky://entities/people`, `sticky://entities/concepts`, `sticky://privacy`

**Prompts (4):** `research_session`, `daily_review`, `prepare_for_meeting`, `weekly_review`

#### MCP Usage Examples

With Claude Code or any MCP client:

```
"Capture this: Sarah mentioned she's blocked on the auth PR"
→ sticky_capture

"What do I know about Marcus?"
→ sticky_synthesize("Marcus")

"Find my notes about API compatibility"
→ sticky_search("API compatibility")

"Prepare me for my meeting with Sarah"
→ prepare_for_meeting("Sarah") prompt

"Start a research session on knowledge graphs"
→ research_session("knowledge graphs") prompt

"Give me my morning brief"
→ sticky_brief
```

### TUI

Terminal UI built with [Textual](https://textual.textualize.io/). 8 views: Home, Search, Detail, Review, Entities, Digest, Stats, About.

```bash
sticky tui
```

**Key bindings:** `/` Search, `G` Digest, `R` Review, `F` Filter, `Ctrl+R` Refresh, `Ctrl+K` Command Palette, `Q` Quit

## How It Works

```
Capture Input
    |
    v
Embedding (local, sentence-transformers, ~100ms)
    |
    v
Classification + Entity Extraction (OpenRouter, ~1s)
    |     Extracts: category, people, projects, concepts, actions, source_url
    v
sqlite-vec KNN Index + FTS5 Keyword Index
    |
    v
SQLite (thoughts + entities + entity_mentions + action_items + digests)
    |
    v
Hybrid Search (0.6 vector + 0.4 keyword, sqlite-vec native)
    |
    v
Related Thoughts (entity co-occurrence + recency boost + cosine similarity)
    |
    v
Daily Digest / Morning Brief / Entity Synthesis (on demand)
```

**Storage:** Single SQLite file at `~/.local/share/sticky/sticky.db` (Linux/macOS) or `%LOCALAPPDATA%\sticky\sticky.db` (Windows).

**Embeddings:** `all-MiniLM-L6-v2` runs entirely on your machine. 384-dimensional vectors, ~80MB model downloaded on first use.

**Vector Search:** sqlite-vec handles KNN queries natively with cosine distance. No Python loops — searches stay fast at any scale.

**LLM:** `anthropic/claude-sonnet-4.6` via OpenRouter for classification, digest generation, and entity synthesis. Stateless API calls — OpenRouter does not store your data.

**Search:** Hybrid of sqlite-vec cosine similarity (semantic meaning) and FTS5 (exact keywords). Configurable weights (default 0.6/0.4).

## Configuration

Copy `config.example.toml` to your config directory:

```bash
# Linux/macOS
cp config.example.toml ~/.config/sticky/config.toml

# Windows
copy config.example.toml %APPDATA%\sticky\config.toml
```

Or configure via CLI:

```bash
sticky config set openrouter_model anthropic/claude-sonnet-4.6
sticky config set confidence_threshold 0.6
sticky config set search_mode hybrid
```

Or via environment variables (highest precedence):

```bash
export STICKY_OPENROUTER_API_KEY=sk-or-v1-...
export STICKY_OPENROUTER_MODEL=anthropic/claude-sonnet-4.6
```

## Privacy

| Component | Location | Details |
|-----------|----------|---------|
| Embeddings | **LOCAL** | sentence-transformers runs on your machine |
| Vector Search | **LOCAL** | sqlite-vec runs native KNN queries |
| Classification | **CLOUD** | Thought text sent to OpenRouter for categorization |
| Digest/Synthesis | **CLOUD** | Thought text sent to OpenRouter for summarization |
| Storage | **LOCAL** | SQLite file on your machine |

Run `sticky privacy` for full details. OpenRouter API calls are stateless — your data is not stored by the provider.

## Data Portability

Your data is never locked in:

```bash
# Export everything to markdown (one file per thought, YAML frontmatter)
sticky export markdown -o ./my-backup/

# Export to JSON
sticky export json -o ./backup.json

# Import from any format
sticky import ./notes/          # auto-detect format
sticky import backup.json       # JSON
sticky import ./obsidian-vault/  # markdown files
```

The SQLite database is a single file you can inspect directly:

```bash
sqlite3 ~/.local/share/sticky/sticky.db "SELECT content FROM thoughts ORDER BY created_at DESC LIMIT 5"
```

## Development

```bash
# Install with dev dependencies
uv sync --dev

# Run tests (340+ tests)
uv run pytest

# Run specific test file
uv run pytest tests/test_search.py -v

# Run with coverage
uv run pytest --cov=sticky
```

## Tech Stack

- **Python 3.11+** with [uv](https://docs.astral.sh/uv/)
- **SQLite** + [sqlite-vec](https://github.com/asg017/sqlite-vec) for native vector KNN search + FTS5 for keyword search
- **sentence-transformers** (all-MiniLM-L6-v2) for local embeddings
- **OpenRouter** for LLM classification, digest generation, and entity synthesis
- **Typer** + **Rich** for CLI
- **Textual** for TUI
- **FastMCP** for MCP server (tools, resources, prompts)
- **Pydantic v2** for data models
