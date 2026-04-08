"""Tests for the sticky entity resolution module."""

import pytest

from sticky.core.db import Database
from sticky.core.entities import EntityResolver
from sticky.core.models import ClassificationResult, Entity, Thought


@pytest.fixture
def db(tmp_data_dir):
    """Provide an initialized Database instance for tests."""
    database = Database(tmp_data_dir / "entity_test.db")
    database.initialize()
    yield database
    database.close()


@pytest.fixture
def resolver(db):
    """Provide an EntityResolver backed by a test database."""
    return EntityResolver(db)


def _make_thought(db, content="Test thought") -> Thought:
    """Insert a thought into the DB and return it."""
    thought = Thought(content=content)
    db.insert_thought(thought)
    return thought


# ---------------------------------------------------------------------------
# resolve_entities
# ---------------------------------------------------------------------------


def test_resolve_new_entity(resolver, db):
    """Resolving a name not in the DB creates a new entity."""
    thought = _make_thought(db, "Met with Sarah today")
    classification = ClassificationResult(
        category="person",
        confidence=0.9,
        people=["Sarah"],
        projects=[],
    )

    results = resolver.resolve_entities(classification, thought.id)
    assert len(results) == 1
    assert results[0]["name"] == "Sarah"
    assert results[0]["type"] == "person"
    assert results[0]["is_new"] is True
    assert results[0]["id"] is not None

    # Entity should exist in DB
    entity = db.get_entity_by_name("Sarah")
    assert entity is not None
    assert entity.entity_type == "person"


def test_resolve_existing_entity(resolver, db):
    """Resolving a name already in the DB links to the existing entity."""
    # Pre-create entity
    existing = Entity.create(name="Sarah", entity_type="person")
    db.insert_entity(existing)

    thought = _make_thought(db, "Sarah called again")
    classification = ClassificationResult(
        category="person",
        confidence=0.9,
        people=["Sarah"],
        projects=[],
    )

    results = resolver.resolve_entities(classification, thought.id)
    assert len(results) == 1
    assert results[0]["name"] == "Sarah"
    assert results[0]["is_new"] is False
    assert results[0]["id"] == existing.id

    # mention_count should have been incremented
    updated = db.get_entity(existing.id)
    assert updated.mention_count == 2


def test_resolve_multiple_types(resolver, db):
    """Resolving classification with both people and projects."""
    thought = _make_thought(db, "Sarah is working on ProjectX")
    classification = ClassificationResult(
        category="meeting",
        confidence=0.8,
        people=["Sarah"],
        projects=["ProjectX"],
    )

    results = resolver.resolve_entities(classification, thought.id)
    assert len(results) == 2

    names = {r["name"] for r in results}
    assert names == {"Sarah", "ProjectX"}

    types = {r["name"]: r["type"] for r in results}
    assert types["Sarah"] == "person"
    assert types["ProjectX"] == "project"


def test_resolve_empty_names_skipped(resolver, db):
    """Empty or whitespace-only names are skipped."""
    thought = _make_thought(db)
    classification = ClassificationResult(
        category="idea",
        confidence=0.5,
        people=["", "  ", "Alice"],
        projects=[],
    )

    results = resolver.resolve_entities(classification, thought.id)
    assert len(results) == 1
    assert results[0]["name"] == "Alice"


def test_resolve_no_entities(resolver, db):
    """Classification with no people or projects returns empty list."""
    thought = _make_thought(db)
    classification = ClassificationResult(
        category="journal",
        confidence=0.9,
        people=[],
        projects=[],
    )

    results = resolver.resolve_entities(classification, thought.id)
    assert results == []


def test_resolve_concept_entities(resolver, db):
    """Concepts from classification should be resolved as concept entities."""
    thought = _make_thought(db, "Test thought for concept entities")
    classification = ClassificationResult(
        category="reference",
        confidence=0.95,
        topics=["knowledge management"],
        people=[],
        projects=[],
        concepts=["Zettelkasten", "PARA method"],
        actions=[],
    )

    results = resolver.resolve_entities(classification, thought.id)
    assert len(results) == 2
    assert results[0]["type"] == "concept"
    assert results[0]["name"] == "Zettelkasten"
    assert results[1]["type"] == "concept"
    assert results[1]["name"] == "PARA method"


# ---------------------------------------------------------------------------
# add_alias
# ---------------------------------------------------------------------------


def test_add_alias(resolver, db):
    """add_alias adds an alias to an entity for future matching."""
    entity = Entity.create(name="Robert Smith", entity_type="person")
    db.insert_entity(entity)

    updated = resolver.add_alias(entity.id, "Bob")
    assert updated is not None
    assert "Bob" in updated.aliases

    # Should be findable by alias now
    found = db.get_entity_by_name("Bob")
    assert found is not None
    assert found.id == entity.id


def test_add_alias_nonexistent_entity(resolver):
    """add_alias returns None for a nonexistent entity."""
    result = resolver.add_alias("nonexistent-id", "Bob")
    assert result is None


def test_add_alias_duplicate_skipped(resolver, db):
    """add_alias does not add duplicate aliases."""
    entity = Entity.create(
        name="Robert Smith", entity_type="person", aliases=["Bob"]
    )
    db.insert_entity(entity)

    updated = resolver.add_alias(entity.id, "Bob")
    assert updated is not None
    assert updated.aliases.count("Bob") == 1


# ---------------------------------------------------------------------------
# merge_entities
# ---------------------------------------------------------------------------


def test_merge_entities(resolver, db):
    """merge_entities moves mentions from source to target and deletes source."""
    # Create two entities
    source = Entity.create(name="Bob", entity_type="person")
    target = Entity.create(name="Robert Smith", entity_type="person")
    db.insert_entity(source)
    db.insert_entity(target)

    # Create a thought linked to source
    thought = _make_thought(db, "Bob mentioned something")
    from sticky.core.models import EntityMention

    db.insert_entity_mention(
        EntityMention(entity_id=source.id, thought_id=thought.id)
    )

    merged = resolver.merge_entities(source.id, target.id)
    assert merged is not None
    assert merged.id == target.id
    assert "Bob" in merged.aliases

    # Source should be deleted
    assert db.get_entity(source.id) is None

    # Target should now have the thought linked
    thoughts = db.get_thoughts_for_entity(target.id)
    assert len(thoughts) == 1
    assert thoughts[0].id == thought.id


def test_merge_entities_nonexistent_source(resolver, db):
    """merge_entities returns None if source doesn't exist."""
    target = Entity.create(name="Alice", entity_type="person")
    db.insert_entity(target)

    result = resolver.merge_entities("nonexistent", target.id)
    assert result is None


def test_merge_entities_nonexistent_target(resolver, db):
    """merge_entities returns None if target doesn't exist."""
    source = Entity.create(name="Bob", entity_type="person")
    db.insert_entity(source)

    result = resolver.merge_entities(source.id, "nonexistent")
    assert result is None
