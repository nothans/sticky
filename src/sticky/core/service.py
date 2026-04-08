"""Service orchestration layer for sticky.

ALL interfaces (MCP, CLI, TUI) call StickyService methods.
Coordinates the full pipeline: embedding, classification, entity
resolution, search, digest, import/export.
"""

from __future__ import annotations

import json
import logging
import platform
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sticky.core.classify import Classifier
from sticky.core.config import StickyConfig, get_config
from sticky.core.db import Database
from sticky.core.digest import DigestGenerator
from sticky.core.embeddings import EmbeddingEngine
from sticky.core.entities import EntityResolver
from sticky.core.models import (
    ActionItem,
    ClassificationResult,
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


class StickyService:
    """Central orchestration service for sticky.

    Coordinates embedding, classification, entity resolution, search,
    digest generation, and data import/export.
    """

    def __init__(
        self,
        data_dir: Path | str | None = None,
        config: StickyConfig | None = None,
    ) -> None:
        if config is not None:
            self.config = config
        else:
            self.config = get_config(force_reload=True)

        if data_dir is not None:
            self.config.data_dir = Path(data_dir)

        self.db = Database(self.config.db_path)
        self.embedding_engine = EmbeddingEngine(self.config.embedding_model)
        self.classifier = Classifier(
            api_key=self.config.openrouter_api_key,
            model=self.config.openrouter_model,
        )
        self.entity_resolver = EntityResolver(self.db)
        self.digest_generator = DigestGenerator(
            api_key=self.config.openrouter_api_key,
            model=self.config.openrouter_model,
        )
        self.search_engine: HybridSearch | None = None

    def initialize(self) -> None:
        """Initialize database, directories, and search engine.

        Also auto-resolves review items older than review_auto_resolve_days.
        """
        self.config.ensure_dirs()
        self.db.initialize()
        self.search_engine = HybridSearch(
            db=self.db,
            engine=self.embedding_engine,
            vector_weight=self.config.search_vector_weight,
            fts_weight=self.config.search_fts_weight,
        )

        # Auto-resolve old review items
        try:
            self.db.execute(
                """UPDATE thoughts SET needs_review = 0
                   WHERE needs_review = 1
                   AND updated_at < datetime('now', '-' || ? || ' days')""",
                (str(self.config.review_auto_resolve_days),),
            )
            self.db._get_conn().commit()
        except Exception as exc:
            logger.warning("Failed to auto-resolve review items: %s", exc)

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    def capture(
        self,
        content: str,
        template: str | None = None,
        source: str = "cli",
        source_url: str | None = None,
        thread: str | None = None,
    ) -> dict:
        """Capture a new thought.

        1. Generate embedding (sync, local)
        2. Classify via LLM (graceful degradation)
        3. Resolve entities
        4. Store thought
        5. Extract and store action items

        Capture NEVER fails -- if classification fails, defaults are used.

        Returns:
            Dict with id, content, category, confidence, needs_review,
            entities, created_at.
        """
        # 1. Generate embedding
        try:
            embedding = self.embedding_engine.embed(content)
        except Exception as exc:
            logger.warning("Embedding failed: %s", exc)
            embedding = None

        # 1b. Dedup check — reject near-identical content (cosine > 0.95)
        if embedding is not None:
            if self.db.has_vec:
                dup_rows = self.db.execute(
                    """SELECT thought_id, distance FROM vec_thoughts
                       WHERE embedding MATCH ? AND k = 1
                       ORDER BY distance""",
                    (embedding,),
                ).fetchall()
                if dup_rows and (1.0 - dup_rows[0]["distance"]) > 0.95:
                    existing_id = dup_rows[0]["thought_id"]
                    existing = self.db.get_thought(existing_id)
                    if existing:
                        sim = round(1.0 - dup_rows[0]["distance"], 3)
                        logger.info("Duplicate detected (sim=%.3f): %s", sim, existing_id)
                        return {
                            "id": existing_id,
                            "content": existing.content,
                            "duplicate": True,
                            "similarity": sim,
                            "message": "Near-duplicate thought already exists.",
                        }
            else:
                rows = self.db.execute(
                    "SELECT id, content, embedding FROM thoughts WHERE embedding IS NOT NULL"
                ).fetchall()
                for row in rows:
                    sim = self.embedding_engine.cosine_similarity(embedding, row["embedding"])
                    if sim > 0.95:
                        logger.info("Duplicate detected (sim=%.3f): %s", sim, row["id"])
                        return {
                            "id": row["id"],
                            "content": row["content"],
                            "duplicate": True,
                            "similarity": round(sim, 3),
                            "message": "Near-duplicate thought already exists.",
                        }

        # 2. Classify via LLM (graceful degradation)
        classification: ClassificationResult | None = None
        try:
            classification = self.classifier.classify_sync(content, template)
        except Exception as exc:
            logger.warning("Classification failed: %s", exc)

        # Determine category, confidence, needs_review
        if classification is not None:
            category = classification.category
            confidence = classification.confidence
            needs_review = confidence < self.config.confidence_threshold
        else:
            category = None
            confidence = 0.0
            needs_review = True

        # Use classifier-detected URL if none provided explicitly
        if source_url is None and classification is not None and getattr(classification, 'source_url', None):
            source_url = classification.source_url

        # 3. Create and store thought
        thought = Thought.create(
            content=content,
            embedding=embedding,
            embedding_model=self.config.embedding_model if embedding is not None else None,
            source_url=source_url,
            category=category,
            confidence=confidence,
            needs_review=needs_review,
            source=source,
        )
        if template:
            thought.metadata["template"] = template
        if thread:
            thought.metadata["thread"] = thread
        self.db.insert_thought(thought)

        # 4. Resolve entities
        entities: list[dict] = []
        if classification is not None:
            try:
                entities = self.entity_resolver.resolve_entities(
                    classification, thought.id
                )
            except Exception as exc:
                logger.warning("Entity resolution failed: %s", exc)

        # 5. Extract and store action items (max 2 per thought)
        if classification is not None and classification.actions:
            people_lower = {p.lower(): p for p in (classification.people or [])}
            for action_text in classification.actions[:2]:
                try:
                    # Match person by name appearing in the action text
                    person = None
                    action_lower = action_text.lower()
                    for name_lower, name in people_lower.items():
                        if name_lower in action_lower:
                            person = name
                            break
                    action_item = ActionItem(
                        content=action_text,
                        person=person,
                        source_thought_id=thought.id,
                    )
                    self.db.insert_action_item(action_item)
                except Exception as exc:
                    logger.warning("Failed to store action item: %s", exc)

        return {
            "id": thought.id,
            "content": thought.content,
            "category": category,
            "confidence": confidence,
            "needs_review": needs_review,
            "entities": entities,
            "source_url": thought.source_url,
            "created_at": thought.created_at,
            "thread": thought.metadata.get("thread"),
        }

    def search(
        self,
        query: str,
        limit: int = 10,
        mode: str = "hybrid",
        **filters: Any,
    ) -> list[dict]:
        """Perform hybrid search.

        Returns results with scores and timing.
        """
        if self.search_engine is None:
            raise RuntimeError("Service not initialized. Call initialize() first.")

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
        """Paginated listing of thoughts.

        Returns dict with thoughts, total, has_more, next_cursor.
        """
        limit = kwargs.get("limit", self.config.default_list_limit)
        cursor = kwargs.get("cursor")
        category = kwargs.get("category")
        entity = kwargs.get("entity")
        after = kwargs.get("after")
        before = kwargs.get("before")
        needs_review = kwargs.get("needs_review")
        sort = kwargs.get("sort", "created_at_desc")
        thread = kwargs.get("thread")

        thoughts, total = self.db.list_thoughts(
            limit=limit,
            cursor=cursor,
            category=category,
            entity=entity,
            after=after,
            before=before,
            needs_review=needs_review,
            sort=sort,
            thread=thread,
        )

        thought_dicts = [t.to_display() for t in thoughts]
        has_more = len(thoughts) == limit and len(thoughts) < total

        result: dict[str, Any] = {
            "thoughts": thought_dicts,
            "total": total,
            "has_more": has_more,
        }

        if thoughts and has_more:
            result["next_cursor"] = thoughts[-1].created_at

        return result

    def get_review_items(self, limit: int = 10) -> dict:
        """Get thoughts needing review.

        Returns dict with items and total count.
        """
        thoughts, total = self.db.list_thoughts(
            limit=limit,
            needs_review=True,
        )

        return {
            "items": [t.to_display() for t in thoughts],
            "total": total,
        }

    def classify_thought(self, thought_id: str, category: str) -> dict:
        """Manually reclassify a thought.

        Sets confidence=1.0 and needs_review=False.

        Raises:
            ValueError: If thought_id not found.
        """
        thought = self.db.get_thought(thought_id)
        if thought is None:
            raise ValueError(f"Thought {thought_id} not found")

        self.db.update_thought(
            thought_id,
            category=category,
            confidence=1.0,
            needs_review=False,
        )

        updated = self.db.get_thought(thought_id)
        return updated.to_display()

    def list_entities(self, **kwargs: Any) -> dict:
        """List entities with recent_thoughts per entity.

        Returns dict with entities and total count.
        """
        entity_type = kwargs.get("entity_type")
        query = kwargs.get("query")
        limit = kwargs.get("limit", self.config.default_list_limit)
        sort = kwargs.get("sort", "last_seen")

        entities, total = self.db.list_entities(
            entity_type=entity_type,
            query=query,
            limit=limit,
            sort=sort,
        )

        entity_dicts = []
        for entity in entities:
            ed = entity.model_dump()
            # Get recent thoughts for this entity
            recent = self.db.get_thoughts_for_entity(entity.id, limit=3)
            ed["recent_thoughts"] = [t.to_display() for t in recent]
            entity_dicts.append(ed)

        return {
            "entities": entity_dicts,
            "total": total,
        }

    def digest(
        self,
        period: str = "day",
        since: str | None = None,
    ) -> dict:
        """Generate a digest for the given period.

        Collects thoughts, finds resurface candidates, carries forward
        uncompleted action items, stores digest, and returns structured result.

        Returns:
            Dict with digest, period, thought_count, action_items,
            people_mentioned, resurfaced, digest_id, source_map.
        """
        # Determine time range
        now = datetime.now(timezone.utc)
        if since is not None:
            period_start = since
        else:
            period_deltas = {
                "day": timedelta(days=1),
                "week": timedelta(weeks=1),
                "month": timedelta(days=30),
            }
            delta = period_deltas.get(period, timedelta(days=1))
            period_start = (now - delta).isoformat()

        period_end = now.isoformat()

        # Get thoughts for the period
        thoughts, _ = self.db.list_thoughts(
            after=period_start,
            limit=1000,
            sort="created_at_asc",
        )

        # Find resurface candidate using DigestGenerator helper
        resurfaced_thought: Thought | None = None
        resurfaced_dict: dict | None = None
        if thoughts:
            try:
                resurfaced_thought = self.digest_generator.find_resurface_candidate(
                    db=self.db,
                    engine=self.embedding_engine,
                    recent_thoughts=thoughts,
                    min_age_days=7,
                )
                if resurfaced_thought is not None:
                    resurfaced_dict = resurfaced_thought.to_display()
            except Exception as exc:
                logger.warning("Resurface candidate search failed: %s", exc)

        # Carry forward uncompleted, non-expired action items
        active_actions = self.db.list_action_items(completed=False, limit=50)
        action_dicts = []
        for action in active_actions:
            # Check if expired
            try:
                expires = datetime.fromisoformat(action.expires_at)
                if expires > now:
                    action_dicts.append({
                        "id": action.id,
                        "content": action.content,
                        "person": action.person,
                        "created_at": action.created_at,
                        "expires_at": action.expires_at,
                    })
            except (ValueError, TypeError):
                action_dicts.append({
                    "id": action.id,
                    "content": action.content,
                    "person": action.person,
                    "created_at": action.created_at,
                    "expires_at": action.expires_at,
                })

        # Collect people mentioned
        people_mentioned: list[str] = []
        for t in thoughts:
            rows = self.db.execute(
                """SELECT DISTINCT e.name FROM entities e
                   JOIN entity_mentions em ON em.entity_id = e.id
                   WHERE em.thought_id = ? AND e.entity_type = 'person'""",
                (t.id,),
            ).fetchall()
            for row in rows:
                name = row["name"]
                if name not in people_mentioned:
                    people_mentioned.append(name)

        # Generate digest content via DigestGenerator
        digest_result = self.digest_generator.generate_sync(
            thoughts=thoughts,
            resurfaced=resurfaced_thought,
        )

        # digest_result has keys: text, source_map, action_items, people
        digest_content = digest_result.get("text", "")
        source_map = digest_result.get("source_map", {})

        if not digest_content:
            digest_content = f"No thoughts found for {period} period."

        # Store digest
        thought_ids = [t.id for t in thoughts]
        digest_model = Digest(
            content=digest_content,
            thought_ids=thought_ids,
            period_start=period_start,
            period_end=period_end,
        )
        self.db.insert_digest(digest_model)

        return {
            "digest": digest_content,
            "period": period,
            "thought_count": len(thoughts),
            "action_items": action_dicts,
            "people_mentioned": people_mentioned,
            "resurfaced": resurfaced_dict,
            "digest_id": digest_model.id,
            "source_map": source_map,
        }

    def update(self, thought_id: str, content: str) -> dict:
        """Update a thought: re-embed, re-classify, re-resolve entities.

        Raises:
            ValueError: If thought_id not found.
        """
        thought = self.db.get_thought(thought_id)
        if thought is None:
            raise ValueError(f"Thought {thought_id} not found")

        # Re-embed
        try:
            embedding = self.embedding_engine.embed(content)
        except Exception as exc:
            logger.warning("Re-embedding failed: %s", exc)
            embedding = thought.embedding

        # Re-classify
        classification: ClassificationResult | None = None
        try:
            classification = self.classifier.classify_sync(content)
        except Exception as exc:
            logger.warning("Re-classification failed: %s", exc)

        if classification is not None:
            category = classification.category
            confidence = classification.confidence
            needs_review = confidence < self.config.confidence_threshold
        else:
            category = thought.category
            confidence = thought.confidence
            needs_review = thought.needs_review

        # Update in DB
        self.db.update_thought(
            thought_id,
            content=content,
            embedding=embedding,
            category=category,
            confidence=confidence,
            needs_review=needs_review,
        )

        # Re-resolve entities
        if classification is not None:
            try:
                self.db.delete_mentions_for_thought(thought_id)
                self.entity_resolver.resolve_entities(classification, thought_id)
            except Exception as exc:
                logger.warning("Entity re-resolution failed: %s", exc)

        updated = self.db.get_thought(thought_id)
        return updated.to_display()

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
        """System stats including system info."""
        db_stats = self.db.get_stats()

        # Add system info
        db_stats["system"] = {
            "db_path": str(self.config.db_path),
            "data_dir": str(self.config.data_dir),
            "embedding_model": self.config.embedding_model,
            "search_weights": {
                "vector": self.config.search_vector_weight,
                "fts": self.config.search_fts_weight,
            },
            "confidence_threshold": self.config.confidence_threshold,
            "llm_model": self.config.openrouter_model,
            "platform": platform.system(),
            "python_version": platform.python_version(),
        }

        return db_stats

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
            limit=100000,
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
        # Get all entities
        entities, _ = self.db.list_entities(limit=100000)

        data = {
            "exported_at": _now_iso(),
            "version": "1.0",
            "thoughts": [t.to_display() for t in thoughts],
            "entities": [e.model_dump() for e in entities],
        }

        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)

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

        files: list[str] = []
        index_entries: list[str] = []

        for thought in thoughts:
            # Build filename: {date}_{id[:8]}_{slug}.md
            try:
                dt = datetime.fromisoformat(thought.created_at)
                date_str = dt.strftime("%Y-%m-%d")
            except (ValueError, TypeError):
                date_str = "unknown"

            slug = _slugify(thought.content)
            short_id = thought.id[:8]
            filename = f"{date_str}_{short_id}_{slug}.md"

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
            with open(filepath, "w", encoding="utf-8") as f:
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

        with open(out_dir / "_index.md", "w", encoding="utf-8") as f:
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
            elif path.suffix == ".json":
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
        """Import from a JSON export file."""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        thoughts_data = data.get("thoughts", [])
        imported = 0
        skipped = 0
        errors: list[str] = []

        for td in thoughts_data:
            thought_id = td.get("id")

            # Check for duplicate
            if thought_id and self.db.get_thought(thought_id) is not None:
                skipped += 1
                continue

            if dry_run:
                imported += 1
                continue

            try:
                content = td.get("content", "")
                # Run full capture pipeline for imported thoughts
                self.capture(
                    content=content,
                    source=td.get("source", "import"),
                )
                imported += 1
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

    def related_thoughts(
        self, thought_id: str, limit: int = 3
    ) -> list[dict]:
        """Find top N related thoughts with boosted scoring.

        Uses sqlite-vec for initial candidates, then applies:
        - Entity co-occurrence boost (+0.10 for 1 shared entity, +0.15 for 2+)
        - Recency boost (+0.05 within 7 days, +0.03 within 30 days)
        - Minimum score threshold (0.45) to filter low-quality matches
        """
        thought = self.db.get_thought(thought_id)
        if thought is None or thought.embedding is None:
            return []

        # Fetch more candidates than needed for re-ranking
        fetch_limit = max(limit * 4, 20)
        if self.db.has_vec:
            rows = self.db.execute(
                """SELECT v.thought_id, v.distance, t.*
                   FROM vec_thoughts v
                   JOIN thoughts t ON t.id = v.thought_id
                   WHERE v.embedding MATCH ? AND v.k = ?
                   ORDER BY v.distance""",
                (thought.embedding, fetch_limit),
            ).fetchall()
        else:
            # Fallback: Python cosine similarity
            all_rows = self.db.execute(
                "SELECT * FROM thoughts WHERE id != ? AND embedding IS NOT NULL",
                (thought_id,),
            ).fetchall()
            # Add a synthetic "distance" key
            rows = []
            for r in all_rows:
                other = Thought.from_row(dict(r))
                sim = self.embedding_engine.cosine_similarity(thought.embedding, other.embedding)
                row_dict = dict(r)
                row_dict["thought_id"] = r["id"]
                row_dict["distance"] = 1.0 - sim
                rows.append(row_dict)
            rows.sort(key=lambda x: x["distance"])
            rows = rows[:fetch_limit]

        # Get entity IDs for the source thought
        source_entity_rows = self.db.execute(
            "SELECT entity_id FROM entity_mentions WHERE thought_id = ?",
            (thought_id,),
        ).fetchall()
        source_entity_ids = {r["entity_id"] for r in source_entity_rows}

        # Parse source thought's created_at for recency comparison
        source_created = datetime.fromisoformat(thought.created_at)

        scored_results = []
        for row in rows:
            if row["thought_id"] == thought_id:
                continue

            # Base score: cosine similarity
            cosine_sim = max(0.0, 1.0 - row["distance"])

            # Skip very dissimilar candidates early
            if cosine_sim < 0.20:
                continue

            # Entity co-occurrence boost
            entity_boost = 0.0
            if source_entity_ids:
                candidate_entity_rows = self.db.execute(
                    "SELECT entity_id FROM entity_mentions WHERE thought_id = ?",
                    (row["thought_id"],),
                ).fetchall()
                candidate_entity_ids = {r["entity_id"] for r in candidate_entity_rows}
                shared_count = len(source_entity_ids & candidate_entity_ids)
                if shared_count >= 2:
                    entity_boost = 0.15
                elif shared_count == 1:
                    entity_boost = 0.10

            # Recency boost
            recency_boost = 0.0
            try:
                candidate_created = datetime.fromisoformat(row["created_at"])
                days_apart = abs((source_created - candidate_created).days)
                if days_apart <= 7:
                    recency_boost = 0.05
                elif days_apart <= 30:
                    recency_boost = 0.03
            except (ValueError, TypeError):
                pass  # skip recency boost if date parsing fails

            final_score = min(1.0, cosine_sim + entity_boost + recency_boost)

            # Filter out low-quality matches
            if final_score < 0.45:
                continue

            scored_results.append(
                (final_score, Thought.from_row(dict(row)))
            )

        # Sort by final score descending, return top `limit`
        scored_results.sort(key=lambda x: x[0], reverse=True)
        return [
            t.to_display(score=round(score, 6))
            for score, t in scored_results[:limit]
        ]

    def privacy_info(self) -> dict:
        """Return privacy/data flow information."""
        return {
            "data_flow": {
                "embeddings": "LOCAL",
                "classification": "CLOUD",
                "digest": "CLOUD",
                "storage": "LOCAL",
            },
            "description": (
                "Embeddings are generated locally using sentence-transformers. "
                "Classification and digest generation use OpenRouter API (cloud). "
                "All data is stored locally in SQLite."
            ),
            "data_dir": str(self.config.data_dir),
            "db_path": str(self.config.db_path),
        }

    def get_config_display(self) -> dict:
        """Return config with source information."""
        return self.config.to_display_dict()

    def set_config_value(self, key: str, value: Any) -> dict:
        """Set a config value, returning previous value.

        Also persists to config file.
        """
        previous = getattr(self.config, key)
        self.config.set(key, value)

        # Persist to config file
        try:
            self.config.save_to_file()
        except Exception as exc:
            logger.warning("Failed to save config to file: %s", exc)

        return {
            "key": key,
            "previous": previous,
            "new": value,
        }

    # ------------------------------------------------------------------
    # Action items
    # ------------------------------------------------------------------

    def list_actions(self, completed: bool = False) -> dict:
        """List action items.

        Returns dict with actions list and total count.
        """
        items = self.db.list_action_items(completed=completed)
        return {
            "actions": [
                {
                    "id": item.id,
                    "content": item.content,
                    "person": item.person,
                    "completed": item.completed,
                    "created_at": item.created_at,
                    "completed_at": item.completed_at,
                    "expires_at": item.expires_at,
                    "source_thought_id": item.source_thought_id,
                }
                for item in items
            ],
            "total": len(items),
        }

    def complete_action(self, action_id: str) -> dict:
        """Mark an action item as complete.

        Raises:
            ValueError: If action_id not found.
        """
        # Verify it exists
        rows = self.db.execute(
            "SELECT * FROM action_items WHERE id = ?", (action_id,)
        ).fetchall()

        if not rows:
            raise ValueError(f"Action item {action_id} not found")

        self.db.complete_action_item(action_id)

        return {
            "id": action_id,
            "completed": True,
            "completed_at": _now_iso(),
        }

    def entity_context_summary(self, entity_id: str) -> str:
        """Generate a 2-3 line summary for a pinned person context.

        Uses recent thoughts mentioning this entity.
        """
        entity = self.db.get_entity(entity_id)
        if entity is None:
            return "No entity found with this ID."

        thoughts = self.db.get_thoughts_for_entity(entity_id, limit=5)
        if not thoughts:
            return f"{entity.name}: No recent thoughts found."

        # Build a simple summary from recent thoughts
        snippets = []
        for t in thoughts[:3]:
            snippet = t.content[:100].replace("\n", " ")
            snippets.append(snippet)

        summary_parts = [
            f"{entity.name} ({entity.entity_type}) - "
            f"mentioned {entity.mention_count} time(s).",
        ]
        summary_parts.append(
            "Recent context: " + "; ".join(snippets) + "."
        )

        return " ".join(summary_parts)

    def reclassify_batch(
        self,
        confidence_threshold: float | None = None,
    ) -> dict:
        """Re-run LLM classifier on thoughts with null category or low confidence.

        Queries thoughts where category IS NULL or confidence < threshold,
        re-classifies each via the LLM, updates the DB, and resolves entities.

        Args:
            confidence_threshold: Minimum confidence to skip reclassification.
                Defaults to ``self.config.confidence_threshold``.

        Returns:
            Dict with reclassified, total_candidates, errors, and results.
        """
        threshold = (
            confidence_threshold
            if confidence_threshold is not None
            else self.config.confidence_threshold
        )

        rows = self.db.execute(
            "SELECT * FROM thoughts WHERE category IS NULL OR confidence < ?",
            (threshold,),
        ).fetchall()

        candidates = [Thought.from_row(dict(r)) for r in rows]
        total_candidates = len(candidates)

        reclassified = 0
        errors: list[str] = []
        results: list[dict] = []

        for thought in candidates:
            old_category = thought.category
            try:
                classification = self.classifier.classify_sync(thought.content)
            except Exception as exc:
                logger.warning(
                    "Reclassify failed for thought %s: %s", thought.id, exc
                )
                errors.append(f"{thought.id}: {exc}")
                continue

            new_category = classification.category
            new_confidence = classification.confidence
            new_needs_review = new_confidence < threshold

            self.db.update_thought(
                thought.id,
                category=new_category,
                confidence=new_confidence,
                needs_review=new_needs_review,
            )

            # Resolve entities from the new classification
            try:
                self.entity_resolver.resolve_entities(classification, thought.id)
            except Exception as exc:
                logger.warning(
                    "Entity resolution failed for thought %s: %s",
                    thought.id,
                    exc,
                )

            reclassified += 1
            preview = thought.content[:80].replace("\n", " ")
            results.append({
                "id": thought.id,
                "content_preview": preview,
                "old_category": old_category,
                "new_category": new_category,
                "confidence": new_confidence,
            })

        return {
            "reclassified": reclassified,
            "total_candidates": total_candidates,
            "errors": errors,
            "results": results,
        }

    def brief(self) -> dict:
        """Generate a fast, local-only morning briefing. No LLM calls."""
        # 1. Count new thoughts since last digest
        rows = self.db.execute(
            "SELECT period_end FROM digests ORDER BY created_at DESC LIMIT 1"
        ).fetchall()
        if rows:
            last_digest_end = rows[0]["period_end"]
            count_row = self.db.execute(
                "SELECT COUNT(*) FROM thoughts WHERE created_at > ?",
                (last_digest_end,),
            ).fetchone()
            new_thoughts = count_row[0] if count_row else 0
        else:
            count_row = self.db.execute("SELECT COUNT(*) FROM thoughts").fetchone()
            new_thoughts = count_row[0] if count_row else 0

        # 2. Open action items (top 5, non-expired)
        now_iso = datetime.now(timezone.utc).isoformat()
        actions = self.db.list_action_items(completed=False, limit=10)
        action_dicts = [
            {"content": a.content, "person": a.person, "created_at": a.created_at}
            for a in actions
            if not (a.expires_at and a.expires_at < now_iso)
        ][:5]

        # 3. Entity pulse — most mentioned in last 7 days
        week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        pulse_rows = self.db.execute(
            """SELECT e.name, e.entity_type, COUNT(*) as recent_mentions
               FROM entity_mentions em
               JOIN entities e ON e.id = em.entity_id
               WHERE em.created_at > ?
               GROUP BY e.id
               ORDER BY recent_mentions DESC
               LIMIT 5""",
            (week_ago,),
        ).fetchall()
        entity_pulse = [
            {"name": r["name"], "type": r["entity_type"], "mentions": r["recent_mentions"]}
            for r in pulse_rows
        ]

        # 4. Resurface one older thought connected to recent activity
        resurfaced = None
        if entity_pulse:
            top_entity_name = entity_pulse[0]["name"]
            resurface_rows = self.db.execute(
                """SELECT t.* FROM thoughts t
                   JOIN entity_mentions em ON em.thought_id = t.id
                   JOIN entities e ON e.id = em.entity_id
                   WHERE e.name = ? COLLATE NOCASE
                     AND t.created_at < ?
                   ORDER BY t.created_at ASC
                   LIMIT 1""",
                (top_entity_name, week_ago),
            ).fetchall()
            if resurface_rows:
                t = Thought.from_row(dict(resurface_rows[0]))
                resurfaced = {
                    "id": t.id,
                    "content": t.content,
                    "category": t.category,
                    "created_at": t.created_at,
                }

        # 5. Review count
        review_row = self.db.execute(
            "SELECT COUNT(*) FROM thoughts WHERE needs_review = 1"
        ).fetchone()
        review_count = review_row[0] if review_row else 0

        return {
            "new_thoughts": new_thoughts,
            "action_items": action_dicts,
            "entity_pulse": entity_pulse,
            "resurfaced": resurfaced,
            "review_count": review_count,
        }

    def synthesize(self, entity_name: str) -> dict:
        """Synthesize everything known about an entity via LLM."""
        # Find entity
        entity = self.db.get_entity_by_name(entity_name)
        if entity is None:
            return {
                "entity": entity_name,
                "synthesis": f"No entity found matching '{entity_name}'.",
                "thought_count": 0,
                "thought_ids": [],
            }

        # Get all linked thoughts
        rows = self.db.execute(
            """SELECT t.* FROM thoughts t
               JOIN entity_mentions em ON em.thought_id = t.id
               WHERE em.entity_id = ?
               ORDER BY t.created_at ASC""",
            (entity.id,),
        ).fetchall()

        if not rows:
            return {
                "entity": entity_name,
                "entity_type": entity.entity_type,
                "synthesis": f"No thoughts found linked to '{entity_name}'.",
                "thought_count": 0,
                "thought_ids": [],
            }

        thoughts = [Thought.from_row(dict(r)) for r in rows]

        # Build prompt
        thought_text = "\n\n".join(
            f"[{i+1}] ({t.created_at[:10]}) {t.content}"
            for i, t in enumerate(thoughts)
        )

        synthesis_prompt = (
            f'Summarize everything the user knows about "{entity_name}" '
            f"based on their captured thoughts.\n\n"
            f"Thoughts (chronological):\n{thought_text}\n\n"
            "Write a concise synthesis (2-4 paragraphs) that:\n"
            "1. Summarizes the key facts and insights\n"
            "2. Notes any open questions or contradictions\n"
            "3. Highlights action items if any\n"
            "4. References thought numbers [N] for provenance\n\n"
            "Do not add information not present in the thoughts."
        )

        # Call LLM
        try:
            import asyncio

            import httpx

            async def _call_llm():
                headers = {
                    "Authorization": f"Bearer {self.classifier.api_key}",
                    "Content-Type": "application/json",
                }
                body = {
                    "model": self.classifier.model,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "You are a knowledge synthesis assistant. "
                                "Summarize the user's captured thoughts into "
                                "a coherent narrative."
                            ),
                        },
                        {"role": "user", "content": synthesis_prompt},
                    ],
                    "temperature": 0.3,
                    "max_tokens": 1000,
                }
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.post(
                        "https://openrouter.ai/api/v1/chat/completions",
                        headers=headers,
                        json=body,
                    )
                    response.raise_for_status()
                    data = response.json()
                    return data["choices"][0]["message"]["content"]

            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop is None:
                synthesis_text = asyncio.run(_call_llm())
            else:
                from concurrent.futures import ThreadPoolExecutor

                with ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(asyncio.run, _call_llm())
                    synthesis_text = future.result()

        except Exception as exc:
            logger.warning("Synthesis LLM call failed: %s", exc)
            synthesis_text = (
                "LLM synthesis unavailable. Raw thoughts:\n\n" + thought_text
            )

        return {
            "entity": entity_name,
            "entity_type": entity.entity_type,
            "synthesis": synthesis_text,
            "thought_count": len(thoughts),
            "thought_ids": [t.id for t in thoughts],
        }

    @property
    def conn(self):
        """Expose database connection for advanced operations."""
        return self.db._get_conn()
