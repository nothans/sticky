"""Hybrid search engine combining vector similarity and FTS5 keyword matching."""

from __future__ import annotations

from sticky.core.db import Database
from sticky.core.embeddings import EmbeddingEngine
from sticky.core.models import SearchResult, Thought


class HybridSearch:
    """Combines vector similarity and FTS5 keyword matching with configurable weights.

    Supports three modes:
    - ``hybrid`` (default): weighted combination of vector + FTS5
    - ``semantic``: vector similarity only
    - ``keyword``: FTS5 keyword matching only
    """

    def __init__(
        self,
        db: Database,
        engine: EmbeddingEngine,
        vector_weight: float = 0.6,
        fts_weight: float = 0.4,
    ) -> None:
        self.db = db
        self.engine = engine
        self.vector_weight = vector_weight
        self.fts_weight = fts_weight

    def search(
        self,
        query: str,
        limit: int = 10,
        mode: str = "hybrid",
        category: str | None = None,
        entity: str | None = None,
        after: str | None = None,
        before: str | None = None,
        needs_review: bool | None = None,
    ) -> list[SearchResult]:
        """Perform search and return ranked results.

        Args:
            query: The search query string.
            limit: Maximum number of results to return.
            mode: Search mode -- ``hybrid``, ``semantic``, or ``keyword``.
            category: Filter results by category.
            entity: Filter results by entity name.
            after: Only include thoughts created after this ISO timestamp.
            before: Only include thoughts created before this ISO timestamp.
            needs_review: Filter by needs_review flag.

        Returns:
            List of SearchResult objects sorted by score descending.
        """
        if not query or not query.strip():
            return []

        # Determine effective weights based on mode
        v_weight, f_weight = self._effective_weights(mode)

        # Collect scored results keyed by thought ID
        # Each entry: {thought_id: (thought, score, match_type)}
        scored: dict[str, tuple[Thought, float, str]] = {}

        # --- Vector search ---
        if v_weight > 0:
            vector_results = self._vector_search(query)
            for thought, v_score in vector_results:
                scored[thought.id] = (thought, v_score * v_weight, "vector")

        # --- FTS search ---
        if f_weight > 0:
            fts_results = self._fts_search(query)
            for thought, f_score in fts_results:
                if thought.id in scored:
                    # Merge: add FTS score to existing vector score
                    existing_thought, existing_score, _ = scored[thought.id]
                    scored[thought.id] = (
                        existing_thought,
                        existing_score + f_score * f_weight,
                        "hybrid",
                    )
                else:
                    scored[thought.id] = (thought, f_score * f_weight, "fts")

        # If both sources contributed but mode is not hybrid, set match_type
        if mode == "semantic":
            scored = {
                tid: (t, s, "vector") for tid, (t, s, _) in scored.items()
            }
        elif mode == "keyword":
            scored = {
                tid: (t, s, "fts") for tid, (t, s, _) in scored.items()
            }

        # --- Apply post-search filters ---
        filtered = self._apply_filters(
            scored, category=category, entity=entity,
            after=after, before=before, needs_review=needs_review,
        )

        # --- Sort by score descending and apply limit ---
        filtered.sort(key=lambda x: x[1], reverse=True)
        top = filtered[:limit]

        return [
            SearchResult(thought=thought, score=round(score, 6), match_type=match_type)
            for thought, score, match_type in top
        ]

    def _effective_weights(self, mode: str) -> tuple[float, float]:
        """Return (vector_weight, fts_weight) based on mode."""
        if mode == "semantic":
            return (1.0, 0.0)
        elif mode == "keyword":
            return (0.0, 1.0)
        else:  # hybrid
            return (self.vector_weight, self.fts_weight)

    def _vector_search(self, query: str) -> list[tuple[Thought, float]]:
        """KNN search using sqlite-vec (or Python fallback).

        Returns list of (Thought, normalized_score) pairs.
        """
        query_embedding = self.engine.embed(query)

        if self.db.has_vec:
            rows = self.db.execute(
                """SELECT v.thought_id, v.distance, t.*
                   FROM vec_thoughts v
                   JOIN thoughts t ON t.id = v.thought_id
                   WHERE v.embedding MATCH ?
                     AND v.k = 200
                   ORDER BY v.distance""",
                (query_embedding,),
            ).fetchall()

            if not rows:
                return []

            return [
                (Thought.from_row(dict(row)), max(0.0, 1.0 - row["distance"]))
                for row in rows
            ]

        # Fallback: Python-loop cosine similarity
        rows = self.db.execute(
            "SELECT * FROM thoughts WHERE embedding IS NOT NULL"
        ).fetchall()
        if not rows:
            return []

        results: list[tuple[Thought, float]] = []
        for row in rows:
            thought = Thought.from_row(dict(row))
            sim = self.engine.cosine_similarity(query_embedding, thought.embedding)
            results.append((thought, max(0.0, sim)))
        return results

    def _fts_search(self, query: str) -> list[tuple[Thought, float]]:
        """Perform FTS5 keyword search and normalize BM25 scores to 0-1.

        Returns list of (Thought, normalized_score) pairs.
        """
        try:
            fts_rows = self.db.fts_search(query, limit=100)
        except Exception:
            # FTS can fail on malformed queries; return empty gracefully
            return []

        if not fts_rows:
            return []

        # BM25 rank values are negative (lower = better match).
        # Extract ranks and normalize to [0, 1].
        ranks = [row["rank"] for row in fts_rows]
        # Invert: best rank (most negative) gets highest score
        # rank is negative, so -rank is positive; higher -rank = better match
        inverted = [-r for r in ranks]
        max_inv = max(inverted) if inverted else 1.0
        min_inv = min(inverted) if inverted else 0.0

        results: list[tuple[Thought, float]] = []
        for row, inv in zip(fts_rows, inverted):
            if max_inv == min_inv:
                # All scores are the same
                normalized = 1.0
            else:
                normalized = (inv - min_inv) / (max_inv - min_inv)
            # Ensure at least a small score for FTS matches
            normalized = max(normalized, 0.1)
            thought = Thought.from_row(
                {k: v for k, v in row.items() if k != "rank"}
            )
            results.append((thought, normalized))

        return results

    def _apply_filters(
        self,
        scored: dict[str, tuple[Thought, float, str]],
        category: str | None = None,
        entity: str | None = None,
        after: str | None = None,
        before: str | None = None,
        needs_review: bool | None = None,
    ) -> list[tuple[Thought, float, str]]:
        """Apply post-search filters to scored results.

        Returns filtered list of (thought, score, match_type) tuples.
        """
        # If entity filter is set, get the set of thought IDs for that entity
        entity_thought_ids: set[str] | None = None
        if entity is not None:
            rows = self.db.execute(
                """SELECT em.thought_id FROM entity_mentions em
                   JOIN entities e ON e.id = em.entity_id
                   WHERE e.name = ? COLLATE NOCASE""",
                (entity,),
            ).fetchall()
            entity_thought_ids = {row["thought_id"] for row in rows}

        filtered: list[tuple[Thought, float, str]] = []
        for thought, score, match_type in scored.values():
            if category is not None and thought.category != category:
                continue
            if entity_thought_ids is not None and thought.id not in entity_thought_ids:
                continue
            if after is not None and thought.created_at <= after:
                continue
            if before is not None and thought.created_at >= before:
                continue
            if needs_review is not None and thought.needs_review != needs_review:
                continue
            filtered.append((thought, score, match_type))

        return filtered
