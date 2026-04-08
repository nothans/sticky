"""Tests for sticky.core.models data models."""

import json
from datetime import datetime, timezone

from sticky.core.models import (
    ActionItem,
    ClassificationResult,
    Digest,
    Entity,
    EntityMention,
    SearchResult,
    Thought,
    ThoughtCreate,
)


def test_thought_create_generates_ulid():
    tc = ThoughtCreate(content="test thought")
    assert tc.content == "test thought"
    assert tc.template is None
    assert tc.source == "cli"


def test_thought_create_with_template():
    tc = ThoughtCreate(content="test", template="standup", source="mcp")
    assert tc.template == "standup"
    assert tc.source == "mcp"


def test_thought_model():
    t = Thought.create("hello world")
    assert len(t.id) == 26  # ULID length
    assert t.content == "hello world"
    assert t.created_at is not None
    assert t.needs_review is False


def test_thought_defaults():
    t = Thought.create("test")
    assert t.embedding is None
    assert t.category is None
    assert t.confidence is None
    assert t.needs_review is False
    assert t.source == "cli"
    assert t.metadata == {}
    assert t.updated_at is not None


def test_thought_create_with_kwargs():
    t = Thought.create("test", source="mcp", category="work")
    assert t.source == "mcp"
    assert t.category == "work"


def test_thought_metadata_parsing():
    t = Thought.create("test", metadata={"topics": ["auth"], "people": ["Sarah"]})
    assert t.metadata["topics"] == ["auth"]
    assert t.metadata["people"] == ["Sarah"]


def test_thought_metadata_json_property():
    t = Thought.create("test", metadata={"topics": ["auth"]})
    parsed = json.loads(t.metadata_json)
    assert parsed["topics"] == ["auth"]


def test_thought_from_row():
    row = {
        "id": "01ABCDEFGHIJKLMNOPQRSTUV01",
        "content": "from db",
        "embedding": None,
        "category": "work",
        "confidence": 0.95,
        "needs_review": 1,  # SQLite stores as int
        "source": "cli",
        "metadata": '{"topics": ["db"]}',
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    t = Thought.from_row(row)
    assert t.content == "from db"
    assert t.needs_review is True
    assert t.metadata["topics"] == ["db"]


def test_thought_from_row_needs_review_false():
    row = {
        "id": "01ABCDEFGHIJKLMNOPQRSTUV01",
        "content": "test",
        "embedding": None,
        "category": None,
        "confidence": None,
        "needs_review": 0,
        "source": "cli",
        "metadata": "{}",
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    t = Thought.from_row(row)
    assert t.needs_review is False


def test_thought_to_display():
    t = Thought.create("display test", category="work")
    d = t.to_display()
    assert "embedding" not in d
    assert d["content"] == "display test"
    assert d["category"] == "work"
    assert "score" not in d


def test_thought_to_display_with_score():
    t = Thought.create("scored")
    d = t.to_display(score=0.85)
    assert d["score"] == 0.85
    assert "embedding" not in d


def test_entity_model():
    e = Entity.create(name="Sarah", entity_type="person")
    assert e.name == "Sarah"
    assert e.entity_type == "person"
    assert e.aliases == []
    assert e.mention_count == 1


def test_entity_create_with_aliases():
    e = Entity.create(name="Sarah", entity_type="person", aliases=["S", "Sarah C"])
    assert e.aliases == ["S", "Sarah C"]


def test_entity_aliases_json():
    e = Entity.create(name="Sarah", entity_type="person", aliases=["S"])
    parsed = json.loads(e.aliases_json)
    assert parsed == ["S"]


def test_entity_from_row():
    row = {
        "id": "01ABCDEFGHIJKLMNOPQRSTUV01",
        "name": "Sarah",
        "entity_type": "person",
        "aliases": '["S", "Sarah C"]',
        "first_seen": "2026-01-01T00:00:00+00:00",
        "last_seen": "2026-01-01T00:00:00+00:00",
        "mention_count": 5,
    }
    e = Entity.from_row(row)
    assert e.name == "Sarah"
    assert e.aliases == ["S", "Sarah C"]
    assert e.mention_count == 5


def test_entity_mention():
    em = EntityMention(
        entity_id="01ABCDEFGHIJKLMNOPQRSTUV01",
        thought_id="01ABCDEFGHIJKLMNOPQRSTUV02",
        context="mentioned in standup",
    )
    assert em.entity_id == "01ABCDEFGHIJKLMNOPQRSTUV01"
    assert em.context == "mentioned in standup"
    assert em.created_at is not None


def test_entity_mention_no_context():
    em = EntityMention(
        entity_id="01ABCDEFGHIJKLMNOPQRSTUV01",
        thought_id="01ABCDEFGHIJKLMNOPQRSTUV02",
    )
    assert em.context is None


def test_digest_model():
    d = Digest(
        content="Weekly summary",
        thought_ids=["id1", "id2"],
        period_start="2026-01-01T00:00:00+00:00",
        period_end="2026-01-07T00:00:00+00:00",
    )
    assert d.content == "Weekly summary"
    assert len(d.id) == 26
    assert d.thought_ids == ["id1", "id2"]


def test_digest_thought_ids_json():
    d = Digest(
        content="summary",
        thought_ids=["a", "b"],
        period_start="2026-01-01T00:00:00+00:00",
        period_end="2026-01-07T00:00:00+00:00",
    )
    parsed = json.loads(d.thought_ids_json)
    assert parsed == ["a", "b"]


def test_digest_from_row():
    row = {
        "id": "01ABCDEFGHIJKLMNOPQRSTUV01",
        "content": "summary",
        "thought_ids": '["a", "b"]',
        "period_start": "2026-01-01T00:00:00+00:00",
        "period_end": "2026-01-07T00:00:00+00:00",
        "created_at": "2026-01-01T00:00:00+00:00",
    }
    d = Digest.from_row(row)
    assert d.thought_ids == ["a", "b"]


def test_action_item_defaults():
    a = ActionItem(content="follow up with Sarah", person="Sarah")
    assert a.completed is False
    assert a.expires_at is not None
    assert a.completed_at is None
    assert len(a.id) == 26


def test_action_item_expires_at_default():
    """expires_at should default to ~14 days after created_at."""
    a = ActionItem(content="test action")
    created = datetime.fromisoformat(a.created_at)
    expires = datetime.fromisoformat(a.expires_at)
    delta = expires - created
    assert delta.days == 14


def test_action_item_completed():
    a = ActionItem(content="done", completed=True, completed_at="2026-01-15T00:00:00+00:00")
    assert a.completed is True
    assert a.completed_at is not None


def test_classification_result():
    cr = ClassificationResult(
        category="work",
        confidence=0.92,
        topics=["auth", "api"],
        people=["Sarah"],
        projects=["sticky"],
        actions=["review PR"],
    )
    assert cr.category == "work"
    assert cr.confidence == 0.92
    assert cr.topics == ["auth", "api"]
    assert cr.people == ["Sarah"]


def test_classification_result_defaults():
    cr = ClassificationResult(category="personal", confidence=0.8)
    assert cr.topics == []
    assert cr.people == []
    assert cr.projects == []
    assert cr.actions == []


def test_search_result():
    t = Thought.create("searchable")
    sr = SearchResult(thought=t, score=0.95)
    assert sr.thought.content == "searchable"
    assert sr.score == 0.95
    assert sr.match_type == "hybrid"


def test_search_result_match_types():
    t = Thought.create("test")
    for mt in ["vector", "fts", "hybrid"]:
        sr = SearchResult(thought=t, score=0.5, match_type=mt)
        assert sr.match_type == mt


def test_thought_metadata_default_not_shared():
    """Ensure mutable default dict is not shared between instances."""
    t1 = Thought.create("one")
    t2 = Thought.create("two")
    t1.metadata["key"] = "value"
    assert "key" not in t2.metadata


def test_entity_aliases_default_not_shared():
    """Ensure mutable default list is not shared between instances."""
    e1 = Entity.create(name="A", entity_type="person")
    e2 = Entity.create(name="B", entity_type="person")
    e1.aliases.append("alias")
    assert "alias" not in e2.aliases
