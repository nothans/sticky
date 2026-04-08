"""Tests for the hybrid search engine."""

import pytest

from sticky.core.db import Database
from sticky.core.embeddings import EmbeddingEngine
from sticky.core.models import Thought, SearchResult
from sticky.core.search import HybridSearch


@pytest.fixture
def engine():
    return EmbeddingEngine()


@pytest.fixture
def db(tmp_data_dir):
    database = Database(tmp_data_dir / "search_test.db")
    database.initialize()
    yield database
    database.close()


@pytest.fixture
def search(db, engine):
    return HybridSearch(db, engine)


@pytest.fixture
def populated_db(db, engine):
    texts = [
        "Sarah is thinking about leaving the team for the platform group",
        "Auth migration delayed by two weeks due to dependency issues",
        "Meeting with Marcus about API backward compatibility",
        "ULID is better than UUID for time-sortable identifiers",
        "Weekly standup: discussed roadmap for Q3",
    ]
    for text in texts:
        t = Thought.create(text)
        t.embedding = engine.embed(text)
        t.category = "idea"
        db.insert_thought(t)
    return db


def test_vector_search_semantic(populated_db, search):
    """Vector search should find semantically similar thoughts."""
    results = search.search("team changes and personnel", mode="semantic")
    assert len(results) > 0
    assert all(isinstance(r, SearchResult) for r in results)
    # The Sarah thought about leaving the team should rank highly
    top_contents = [r.thought.content for r in results[:3]]
    assert any("Sarah" in c for c in top_contents)
    # All results should have match_type "vector"
    assert all(r.match_type == "vector" for r in results)


def test_fts_search_keyword(populated_db, search):
    """FTS search should find exact keyword matches."""
    results = search.search("API", mode="keyword")
    assert len(results) > 0
    assert any("API" in r.thought.content for r in results)
    # All results should have match_type "fts"
    assert all(r.match_type == "fts" for r in results)


def test_hybrid_combines_results(populated_db, search):
    """Hybrid mode should combine vector and FTS results."""
    results = search.search("API compatibility", mode="hybrid")
    assert len(results) > 0
    # Should find the API backward compatibility thought
    top_contents = [r.thought.content for r in results[:3]]
    assert any("API" in c or "compatibility" in c for c in top_contents)
    # Results should be sorted by score descending
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True)


def test_search_with_category_filter(populated_db, search):
    """Search should respect category filter."""
    results = search.search("team", mode="semantic", category="idea")
    assert len(results) > 0
    assert all(r.thought.category == "idea" for r in results)

    # Non-existent category should return empty
    results = search.search("team", mode="semantic", category="nonexistent")
    assert len(results) == 0


def test_search_empty_query(populated_db, search):
    """Empty query should return empty results."""
    results = search.search("")
    assert results == []


def test_search_mode_semantic(populated_db, search):
    """Semantic mode should only use vector search."""
    results = search.search("unique identifiers", mode="semantic")
    assert len(results) > 0
    assert all(r.match_type == "vector" for r in results)
    # Should find the ULID/UUID thought
    top_contents = [r.thought.content for r in results[:3]]
    assert any("ULID" in c or "UUID" in c for c in top_contents)


def test_search_mode_keyword(populated_db, search):
    """Keyword mode should only use FTS search."""
    results = search.search("ULID", mode="keyword")
    assert len(results) > 0
    assert all(r.match_type == "fts" for r in results)
    assert any("ULID" in r.thought.content for r in results)


def test_search_limit(populated_db, search):
    """Search should respect the limit parameter."""
    results = search.search("team", mode="semantic", limit=2)
    assert len(results) <= 2


def test_search_scores_normalized(populated_db, search):
    """All scores should be between 0 and 1."""
    results = search.search("API backward compatibility", mode="hybrid")
    for r in results:
        assert 0.0 <= r.score <= 1.0, f"Score {r.score} out of range"


def test_search_date_filter(populated_db, search):
    """Search should filter by date range."""
    # All test thoughts have recent timestamps, using a future date should include all
    results = search.search(
        "team", mode="semantic", before="2099-01-01T00:00:00+00:00"
    )
    assert len(results) > 0

    # Using a past date should exclude all
    results = search.search(
        "team", mode="semantic", after="2099-01-01T00:00:00+00:00"
    )
    assert len(results) == 0


def test_search_needs_review_filter(db, engine, search):
    """Search should filter by needs_review flag."""
    t1 = Thought.create("Review this important thought")
    t1.embedding = engine.embed(t1.content)
    t1.needs_review = True
    db.insert_thought(t1)

    t2 = Thought.create("This thought is already reviewed")
    t2.embedding = engine.embed(t2.content)
    t2.needs_review = False
    db.insert_thought(t2)

    results = search.search("thought", mode="semantic", needs_review=True)
    assert len(results) == 1
    assert results[0].thought.needs_review is True


def test_search_no_embeddings_graceful(db, search):
    """Vector search should handle thoughts without embeddings gracefully."""
    t = Thought.create("A thought without embedding")
    db.insert_thought(t)

    # Should not crash, may return empty or FTS-only results
    results = search.search("thought", mode="semantic")
    assert isinstance(results, list)


def test_hybrid_deduplicates(populated_db, search):
    """Hybrid search should not return duplicate thoughts."""
    results = search.search("API", mode="hybrid")
    thought_ids = [r.thought.id for r in results]
    assert len(thought_ids) == len(set(thought_ids)), "Duplicate thought IDs found"
