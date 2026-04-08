# Sticky — Personal Memory System

A second-brain / personal memory system designed to capture, organize, retrieve, and surface knowledge with zero friction. AI handles the organization; humans provide the knowledge.

## Project State

Early design phase. Research is complete (`.meta/research/`), agents and skills are in place, architecture is proposed but not yet implemented.

## Key Directories

```
.meta/research/          # 14 research files — the knowledge base for all design decisions
.claude/agents/          # 9 agents across 3 teams
.claude/skills/          # 7 shared workflow skills
docs/plans/              # Design docs and implementation plans
docs/decisions/          # Architecture Decision Records (ADRs)
```

## Agent Teams

### Leadership (Opus) — Strategic decisions
- **cto** — Architecture, tech stack, scalability. Opinionated, anti-over-engineering.
- **technical-cofounder** — "What can we ship this weekend?" MVP scoping, build-vs-buy.
- **product-manager** — User needs, prioritization, adoption strategy. Thinks in personas.

### UX (Mixed) — Design and research
- **ux-researcher** (Opus) — Pain points, behavioral patterns, Jobs-to-be-Done. Questions assumptions.
- **ux-designer** (Sonnet) — Flows, not screens. Friction reduction. Progressive disclosure.

### User Personas (Sonnet) — Validation
- **maya-chen** `casual` — ADHD product designer, 47 tabs, zero discipline for organization
- **jordan-reeves** `power-user` — Indie dev, terminal-native, wants MCP + CLI + extensibility
- **alex-drummond** `knowledge-worker` — Eng manager drowning in meetings, needs daily digests
- **priya-kapoor** `skeptic` — Senior data scientist, abandoned 6 PKM tools, needs week-1 value

## Skills

- **product-review** — Evaluate features against user needs and research
- **user-story-workshop** — Generate stories + acceptance criteria via personas
- **architecture-decision-record** — Document technical decisions (ADR format)
- **competitive-analysis** — Analyze competitors against sticky's approach
- **persona-gauntlet** — Run a feature through all 4 personas for validation
- **research-synthesis** — Pull and synthesize insights from .meta/research/
- **friction-audit** — Count decisions/steps in a flow, flag over-budget friction

## Conventions

- Opus for strategic/leadership agents; Sonnet for personas and lighter work
- All design decisions should reference `.meta/research/` findings as evidence
- Use ADRs for significant technical choices (`docs/decisions/NNNN-title.md`)
- Run the persona gauntlet before finalizing any user-facing feature

## Living Documentation

Plans and docs MUST be kept up to date as the design evolves. When a decision changes, an approach shifts, or new research invalidates an assumption:
1. Update the relevant design doc in `docs/plans/`
2. If a technical decision changed, create a new ADR that supersedes the old one (update the old ADR's status to "Superseded by NNNN")
3. Update `.meta/research/` files if new findings emerge
4. Never let docs drift from reality — stale docs are worse than no docs

## Research Foundation

The `.meta/research/` directory contains 14 files covering memory tiers, storage backends, retrieval patterns, knowledge graphs, infinite context, capture UX, forgetting, zettelkasten+AI, notable systems, competitive analysis, and a synthesized architecture blueprint. All agents should reference these files to ground recommendations in evidence.

## Commit Style
- lowercase first letter
- short, direct descriptions (no fluff)
- use a line for each change
- no conventional commit prefixes (no "feat:", "fix:", etc.)
- imperative/present tense ("fix X", "update Y", "remove Z")
- no trailing period
- no Co-Authored-By lines
