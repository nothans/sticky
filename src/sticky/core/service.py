"""Service orchestration layer for sticky.

ALL interfaces (MCP, CLI, TUI) call StickyService methods.
Capture, editing, and search are local. Cloud digests are explicit and optional.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from sticky.core.config import StickyConfig, get_config
from sticky.core.db import Database, _timestamp_key, encode_cursor
from sticky.core.digest import DigestGenerator
from sticky.core.embeddings import EmbeddingEngine
from sticky.core.models import (
    Digest,
    Thought,
)
from sticky.core.search import HybridSearch

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _slugify(text: str, max_len: int = 40) -> str:
    """Convert text into a URL/filename-friendly slug."""
    slug = text.lower()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug).strip("-")
    return slug[:max_len].rstrip("-")


def _markdown_filename(thought: Thought) -> str:
    try:
        date = datetime.fromisoformat(thought.created_at).strftime("%Y-%m-%d")
    except ValueError:
        date = "unknown"
    return f"{date}_{quote(thought.id, safe='')}_{_slugify(thought.content)}.md"


class StickyService:
    """Central orchestration service for sticky.

    Coordinates local notes/search, optional digests, and data import/export.
    """

    def __init__(self, data_dir: Path | str | None = None, config: StickyConfig | None = None) -> None:
        self.config = config if config is not None else get_config(force_reload=True)
        if data_dir is not None:
            self.config.data_dir = Path(data_dir)
        self.db = Database(self.config.db_path)
        self.embedding_engine = EmbeddingEngine()
        self.digest_generator = DigestGenerator(
            api_key=self.config.openrouter_api_key, model=self.config.openrouter_model,
        )
        self.search_engine: HybridSearch | None = None

    def initialize(self) -> None:
        """Initialize local storage and search; no cloud or review work on startup."""
        self.config.ensure_dirs()
        self.db.initialize()
        self.search_engine = HybridSearch(db=self.db, engine=self.embedding_engine)

    def _embed(self, content: str) -> bytes | None:
        """Best-effort local enrichment, shared by capture, edit, and restore."""
        try:
            embedding = self.embedding_engine.embed(content)
            if not isinstance(embedding, bytes) or len(embedding) != 384 * 4:
                raise ValueError("The supported model must produce 384 float32 values")
            return embedding
        except Exception as exc:
            logger.warning("Embedding unavailable; keeping raw note: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    def capture(
        self, content: str, template: str | None = None, source: str = "cli",
        source_url: str | None = None, thread: str | None = None,
    ) -> dict:
        """Save each capture locally. Embedding failure leaves a searchable raw note."""
        if not content.strip():
            raise ValueError("A thought must contain text")
        embedding = self._embed(content)
        thought = Thought.create(
            content=content, embedding=embedding,
            embedding_model=self.embedding_engine.model_name if embedding is not None else None,
            source_url=source_url, source=source,
        )
        if template:
            thought.metadata["template"] = template
        if thread:
            thought.metadata["thread"] = thread
        self.db.insert_thought(thought)
        return {**thought.to_display(), "thread": thought.metadata.get("thread")}

    def search(
        self,
        query: str,
        limit: int | None = None,
        mode: str = "hybrid",
        **filters: Any,
    ) -> list[dict]:
        """Perform hybrid search.

        Returns results with scores and timing.
        """
        if self.search_engine is None:
            raise RuntimeError("Service not initialized. Call initialize() first.")
        limit = self.config.default_search_limit if limit is None else limit
        if not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a positive integer")

        start = time.monotonic()
        results = self.search_engine.search(
            query=query,
            limit=limit,
            mode=mode,
            category=filters.get("category"),
            entity=filters.get("entity"),
            after=filters.get("after"),
            before=filters.get("before"),
            needs_review=filters.get("needs_review"),
        )
        elapsed = time.monotonic() - start

        return [
            {
                **r.thought.to_display(score=r.score),
                "match_type": r.match_type,
                "search_time_ms": round(elapsed * 1000, 2),
            }
            for r in results
        ]

    def list_thoughts(self, **kwargs: Any) -> dict:
        """List notes with a stable cursor and an exact next-page indication."""
        limit = kwargs.pop("limit", None)
        limit = self.config.default_list_limit if limit is None else limit
        if not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a positive integer")
        thoughts, total = self.db.list_thoughts(limit=limit + 1, **kwargs)
        has_more = len(thoughts) > limit
        thoughts = thoughts[:limit]
        result = {"thoughts": [t.to_display() for t in thoughts], "total": total, "has_more": has_more}
        if thoughts and has_more:
            result["next_cursor"] = encode_cursor(thoughts[-1], kwargs.get("sort", "created_at_desc"))
        return result

    def get_thought(self, thought_id: str) -> dict:
        """Read a complete note by its ID."""
        thought = self.db.get_thought(thought_id)
        if thought is None:
            raise ValueError(f"Thought {thought_id} not found")
        return thought.to_display()


    def classify_thought(self, thought_id: str, category: str) -> dict:
        """Manually reclassify a thought.

        Sets confidence=1.0 and needs_review=False.

        Raises:
            ValueError: If thought_id not found.
        """
        thought = self.db.get_thought(thought_id)
        if thought is None:
            raise ValueError(f"Thought {thought_id} not found")
        if not category.strip():
            raise ValueError("A category must contain text")

        self.db.update_thought(
            thought_id,
            category=category,
            confidence=1.0,
            needs_review=False,
        )

        updated = self.db.get_thought(thought_id)
        return updated.to_display()


    def digest(self, period: str = "day", since: str | None = None) -> dict:
        """Generate an optional digest only on explicit request with a configured key."""
        if not self.config.openrouter_api_key.strip():
            raise ValueError("A configured OpenRouter API key is required for a cloud digest")
        deltas = {"day": timedelta(days=1), "week": timedelta(weeks=1), "month": timedelta(days=30)}
        if period not in deltas:
            raise ValueError("period must be day, week, or month")
        now = datetime.now(timezone.utc)
        start = since if since is not None else (now - deltas[period]).isoformat()
        if since is not None:
            datetime.fromisoformat(since)
        thoughts, _ = self.db.list_thoughts(after=start, before=now.isoformat(), limit=1001, sort="created_at_asc")
        if len(thoughts) > 1000:
            raise ValueError("Digest period exceeds 1000 notes; choose a shorter period or a later since date")
        result = self.digest_generator.generate_sync(thoughts)
        digest = Digest(content=result["text"], thought_ids=[t.id for t in thoughts], period_start=start, period_end=now.isoformat())
        self.db.insert_digest(digest)
        return {"digest": digest.content, "period": period, "thought_count": len(thoughts), "digest_id": digest.id, "thought_ids": digest.thought_ids}

    def update(self, thought_id: str, content: str) -> dict:
        """Edit locally, replacing or clearing the old search representation."""
        thought = self.db.get_thought(thought_id)
        if thought is None:
            raise ValueError(f"Thought {thought_id} not found")
        if not content.strip():
            raise ValueError("A thought must contain text")
        embedding = self._embed(content)
        self.db.update_thought(
            thought_id, content=content, embedding=embedding,
            embedding_model=self.embedding_engine.model_name if embedding is not None else None,
            category=None, confidence=None, needs_review=False,
        )
        return self.db.get_thought(thought_id).to_display()

    def delete(self, thought_id: str) -> dict:
        """Permanently delete a thought.

        Raises:
            ValueError: If thought_id not found.
        """
        preview = self.db.delete_thought(thought_id)
        if preview is None:
            raise ValueError(f"Thought {thought_id} not found")

        return {
            "id": thought_id,
            "preview": preview,
            "deleted": True,
        }

    def stats(self) -> dict:
        """Return local note counts and paths."""
        total = self.db.execute("SELECT COUNT(*) FROM thoughts").fetchone()[0]
        return {"thoughts": {"total": total}, "system": {"db_path": str(self.config.db_path), "data_dir": str(self.config.data_dir), "embedding_model": self.embedding_engine.model_name}}

    def export_data(
        self,
        format: str,
        output_path: str,
        **filters: Any,
    ) -> dict:
        """Export data to markdown or JSON.

        Markdown: one .md per thought with YAML frontmatter + _index.md.
        JSON: single file with thoughts + entities.

        Returns dict with format, count, path.
        """
        # Get thoughts with optional filters
        category = filters.get("category")
        after = filters.get("after")
        before = filters.get("before")

        thoughts, total = self.db.list_thoughts(
            limit=None,
            category=category,
            after=after,
            before=before,
            sort="created_at_asc",
        )

        if format == "json":
            return self._export_json(thoughts, output_path)
        elif format == "markdown":
            return self._export_markdown(thoughts, output_path)
        else:
            raise ValueError(f"Unsupported export format: {format}")

    def _export_json(self, thoughts: list[Thought], output_path: str) -> dict:
        """Export thoughts and entities to a JSON file."""
        path = Path(output_path)
        protected = [self.config.db_path, self.config.config_file]
        protected.extend(Path(str(self.config.db_path) + suffix) for suffix in ("-wal", "-shm", "-journal"))
        for active in protected:
            if path.resolve() == active.resolve() or (path.exists() and active.exists() and path.samefile(active)):
                raise ValueError("Export cannot overwrite Sticky's active database or configuration files")
        # Get all entities
        entities, _ = self.db.list_entities(limit=None)

        data = {
            "exported_at": _now_iso(),
            "version": "1.0",
            "thoughts": [t.to_display() for t in thoughts],
            "entities": [e.model_dump() for e in entities],
        }

        path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as file:
                temporary = Path(file.name)
                json.dump(data, file, indent=2, default=str)
                file.flush()
                os.fsync(file.fileno())
            temporary.replace(path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

        return {
            "format": "json",
            "count": len(thoughts),
            "path": str(path),
        }

    def _export_markdown(
        self, thoughts: list[Thought], output_path: str
    ) -> dict:
        """Export thoughts as individual markdown files with YAML frontmatter."""
        out_dir = Path(output_path)
        out_dir.mkdir(parents=True, exist_ok=True)
        if any(out_dir.iterdir()):
            raise FileExistsError("Markdown export requires an empty directory")
        filenames = [_markdown_filename(thought) for thought in thoughts]
        if len({name.casefold() for name in filenames}) != len(filenames):
            raise ValueError("Markdown export filenames collide; use JSON to preserve these records")

        files: list[str] = []
        index_entries: list[str] = []

        for thought, filename in zip(thoughts, filenames):

            # Get entity mentions for frontmatter
            entity_rows = self.db.execute(
                """SELECT e.name FROM entities e
                   JOIN entity_mentions em ON em.entity_id = e.id
                   WHERE em.thought_id = ?""",
                (thought.id,),
            ).fetchall()
            entity_names = [row["name"] for row in entity_rows]

            # YAML frontmatter
            lines = [
                "---",
                f"id: {thought.id}",
                f"category: {thought.category or 'null'}",
                f"confidence: {thought.confidence}",
                f"source: {thought.source}",
                f"entities: {json.dumps(entity_names)}",
                f"created_at: {thought.created_at}",
                f"updated_at: {thought.updated_at}",
                "---",
                "",
                thought.content,
                "",
            ]

            filepath = out_dir / filename
            with open(filepath, "x", encoding="utf-8") as f:
                f.write("\n".join(lines))

            files.append(filename)
            preview = thought.content[:60].replace("\n", " ")
            index_entries.append(f"- [{preview}]({filename})")

        # Write _index.md
        index_content = [
            "# Sticky Thoughts Export",
            "",
            f"Exported: {_now_iso()}",
            f"Total: {len(thoughts)} thoughts",
            "",
            "## Thoughts",
            "",
        ] + index_entries + [""]

        with open(out_dir / "_index.md", "x", encoding="utf-8") as f:
            f.write("\n".join(index_content))

        return {
            "format": "markdown",
            "count": len(thoughts),
            "path": str(out_dir),
            "files": files,
        }

    def import_data(
        self,
        source_path: str,
        format: str = "auto",
        dry_run: bool = False,
    ) -> dict:
        """Import data from markdown, JSON, or text files.

        Auto-detects format from extension or directory contents.
        Skips duplicates (thoughts with matching IDs).

        Returns dict with imported count, skipped count, errors.
        """
        path = Path(source_path)

        # Auto-detect format
        if format == "auto":
            if path.is_dir():
                format = "markdown"
            elif path.suffix.lower() == ".json":
                format = "json"
            elif path.suffix in (".md", ".txt"):
                format = "text"
            else:
                format = "text"

        if format == "json":
            return self._import_json(path, dry_run)
        elif format == "markdown":
            return self._import_markdown_dir(path, dry_run)
        elif format == "text":
            return self._import_text(path, dry_run)
        else:
            raise ValueError(f"Unsupported import format: {format}")

    def _import_json(self, path: Path, dry_run: bool) -> dict:
        """Restore exported thoughts, or capture plain JSON notes without IDs."""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, dict) or not isinstance(data.get("thoughts"), list):
            raise ValueError("JSON import requires an object with a thoughts list")
        thoughts_data = data["thoughts"]
        imported = 0
        skipped = 0
        errors: list[str] = []
        seen_ids: set[str] = set()

        for td in thoughts_data:
            try:
                if not isinstance(td, dict):
                    raise ValueError("Each thought must be an object")
                thought_id = td.get("id")
                if thought_id and (thought_id in seen_ids or self.db.get_thought(thought_id) is not None):
                    skipped += 1
                    continue
                content = td.get("content")
                if not isinstance(content, str) or not content.strip():
                    raise ValueError("A thought must contain text")

                if thought_id:
                    # JSON exports contain the original record. Restore it without
                    # reclassifying, assigning a new ID, or merging similar notes.
                    thought = Thought.model_validate(
                        {key: value for key, value in td.items() if key != "embedding"}
                    )
                    for field in ("created_at", "updated_at"):
                        if _timestamp_key(getattr(thought, field))[0] != 0:
                            raise ValueError(f"{field} must be an ISO date or datetime")
                    if not dry_run:
                        thought.embedding = self._embed(thought.content)
                        thought.embedding_model = self.embedding_engine.model_name if thought.embedding is not None else None
                        self.db.insert_thought(thought)
                    seen_ids.add(thought_id)
                else:
                    # Plain JSON notes still use the normal capture pipeline.
                    metadata = td.get("metadata")
                    thread = metadata.get("thread") if isinstance(metadata, dict) else None
                    plain = Thought(content=content, source=td.get("source", "import"), source_url=td.get("source_url"))
                    if not dry_run:
                        self.capture(content=plain.content, source=plain.source, source_url=plain.source_url, thread=thread)
                imported += 1
            except sqlite3.IntegrityError as exc:
                # Another restore may commit the same ID after our initial read.
                if thought_id and self.db.get_thought(thought_id) is not None:
                    skipped += 1
                else:
                    errors.append(f"Failed to import thought: {exc}")
            except Exception as exc:
                errors.append(f"Failed to import thought: {exc}")

        return {
            "imported": imported,
            "skipped": skipped,
            "errors": errors,
            "dry_run": dry_run,
            "format": "json",
        }

    def _import_markdown_dir(self, path: Path, dry_run: bool) -> dict:
        """Import from a directory of markdown files."""
        if not path.is_dir():
            raise ValueError("Markdown import requires an existing directory")
        imported = 0
        skipped = 0
        errors: list[str] = []

        md_files = sorted(path.glob("*.md"))
        for md_file in md_files:
            if md_file.name == "_index.md":
                continue

            try:
                text = md_file.read_text(encoding="utf-8")

                # Try to parse YAML frontmatter
                thought_id = None
                content = text

                if text.startswith("---"):
                    parts = text.split("---", 2)
                    if len(parts) >= 3:
                        frontmatter = parts[1]
                        content = parts[2].strip()

                        # Extract id from frontmatter
                        for line in frontmatter.strip().split("\n"):
                            if line.startswith("id:"):
                                thought_id = line.split(":", 1)[1].strip()

                # Check for duplicate
                if thought_id and self.db.get_thought(thought_id) is not None:
                    skipped += 1
                    continue

                if not content.strip():
                    raise ValueError("A thought must contain text")
                if dry_run:
                    imported += 1
                    continue

                self.capture(content=content, source="import")
                imported += 1

            except Exception as exc:
                errors.append(f"Failed to import {md_file.name}: {exc}")

        return {
            "imported": imported,
            "skipped": skipped,
            "errors": errors,
            "dry_run": dry_run,
            "format": "markdown",
        }

    def _import_text(self, path: Path, dry_run: bool) -> dict:
        """Import from a plain text file (one thought per paragraph)."""
        text = path.read_text(encoding="utf-8")
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]

        imported = 0
        errors: list[str] = []

        for para in paragraphs:
            if dry_run:
                imported += 1
                continue

            try:
                self.capture(content=para, source="import")
                imported += 1
            except Exception as exc:
                errors.append(f"Failed to import paragraph: {exc}")

        return {
            "imported": imported,
            "skipped": 0,
            "errors": errors,
            "dry_run": dry_run,
            "format": "text",
        }


    def privacy_info(self) -> dict:
        """Describe the active data flow without promising provider non-retention."""
        return {
            "data_flow": {"capture": "LOCAL", "embeddings": "LOCAL", "search": "LOCAL", "storage": "LOCAL", "digest": "CLOUD_ON_REQUEST"},
            "description": "Capture, editing, embeddings, and search run locally. An explicitly requested digest sends selected notes to OpenRouter and its model provider. Provider logging and retention policies vary.",
            "data_dir": str(self.config.data_dir), "db_path": str(self.config.db_path),
        }

    def get_config_display(self) -> dict:
        """Return config with source information."""
        return self.config.to_display_dict()

    def set_config_value(self, key: str, value: Any) -> dict:
        """Save a validated setting for the next session; mask secret receipts."""
        updated = self.config.model_copy()
        previous = updated.set(key, value)
        updated.save_to_file()
        current = getattr(updated, key)
        if key == "openrouter_api_key":
            previous = "configured" if previous else "unset"
            current = "configured" if current else "unset"
        return {"key": key, "previous": previous, "new": str(current) if isinstance(current, Path) else current}

    @property
    def conn(self):
        """Expose database connection for advanced operations."""
        return self.db._get_conn()
