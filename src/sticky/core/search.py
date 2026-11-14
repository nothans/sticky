"""Hybrid search engine combining vector similarity and FTS5 keyword matching."""

from __future__ import annotations

from sticky.core.db import Database, _thought_filters
from sticky.core.embeddings import EmbeddingEngine
from sticky.core.models import SearchResult, Thought


class HybridSearch:
    """Fixed hybrid ranking, with component modes available for internal checks.

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
        if not query or not query.strip() or limit <= 0:
            return []

        # Determine effective weights based on mode
        v_weight, f_weight = self._effective_weights(mode)
        filters = {
            "category": category, "entity": entity, "after": after,
            "before": before, "needs_review": needs_review,
        }
        # Invalid filters are caller errors, even if an embedding or FTS fails.
        _thought_filters(**filters)

        # Collect scored results keyed by thought ID
        # Each entry: {thought_id: (thought, score, match_type)}
        scored: dict[str, tuple[Thought, float, str]] = {}

        # --- Vector search ---
        if v_weight > 0:
            try:
                vector_results = self._vector_search(query, limit=limit, **filters)
            except Exception:
                # Search remains usable when the local model cannot load/embed.
                vector_results = []
                f_weight = 1.0
            for thought, v_score in vector_results:
                scored[thought.id] = (thought, v_score * v_weight, "vector")

        # --- FTS search ---
        if f_weight > 0:
            fts_results = self._fts_search(query, limit=limit, **filters)
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

        # --- Sort by score descending and apply limit ---
        ranked = sorted(scored.values(), key=lambda x: (x[1], x[0].id), reverse=True)
        top = ranked[:limit]

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

    def _vector_search(
        self, query: str, limit: int = 10, **filters,
    ) -> list[tuple[Thought, float]]:
        """KNN search using sqlite-vec (or Python fallback).

        Returns list of (Thought, normalized_score) pairs.
        """
        query_embedding = self.engine.embed(query)
        if not isinstance(query_embedding, bytes) or len(query_embedding) != 384 * 4:
            raise ValueError("Search requires a 384-dimensional MiniLM embedding")
        conditions, params = _thought_filters(**filters)
        candidate_limit = max(200, limit)

        # sqlite-vec's fast KNN query ranks globally. For filtered searches,
        # score only eligible rows so unrelated global hits cannot hide them.
        if self.db.has_vec and not conditions and candidate_limit < 4096:
            rows = self.db.execute(
                """SELECT v.thought_id, v.distance, t.*
                   FROM vec_thoughts v
                   JOIN thoughts t ON t.id = v.thought_id
                   WHERE v.embedding MATCH ?
                     AND v.k = ?
                   ORDER BY v.distance""",
                (query_embedding, candidate_limit + 1),
            ).fetchall()

            if not rows:
                return []

            def score(row):
                return min(1.0, max(0.0, 1.0 - row["distance"]))
            # A tied cutoff can hide higher IDs in the same relevance group.
            # Reuse complete scoring only for those ties; otherwise KNN is enough.
            if len(rows) <= candidate_limit or score(rows[-2]) > score(rows[-1]):
                return [(Thought.from_row(dict(row)), score(row)) for row in rows[:candidate_limit]]

        # No ID IN list: the number of eligible rows never consumes SQL parameters.
        where = " AND " + " AND ".join(conditions) if conditions else ""
        rows = self.db.execute(
            f"SELECT t.* FROM thoughts t WHERE t.embedding IS NOT NULL {where}",
            tuple(params),
        ).fetchall()
        if not rows:
            return []

        results: list[tuple[Thought, float]] = []
        for row in rows:
            thought = Thought.from_row(dict(row))
            if len(thought.embedding) != len(query_embedding):
                continue  # Keep older incompatible embeddings available through FTS.
            sim = self.engine.cosine_similarity(query_embedding, thought.embedding)
            results.append((thought, min(1.0, max(0.0, sim))))
        return results

    def _fts_search(
        self, query: str, limit: int = 10, **filters,
    ) -> list[tuple[Thought, float]]:
        """Perform FTS5 keyword search and normalize BM25 scores to 0-1.

        Returns list of (Thought, normalized_score) pairs.
        """
        try:
            # Public search accepts ordinary text, not the FTS expression language.
            literal_query = " AND ".join('"' + term.replace('"', '""') + '"' for term in query.split())
            fts_rows = self.db.fts_search(literal_query, limit=max(100, limit), **filters)
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
