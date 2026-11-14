"""Tests for the sticky database layer."""

import json
import base64
import sqlite3
import struct
import time
from datetime import datetime, timezone

import pytest

from sticky.core.db import Database, encode_cursor
from sticky.core.models import (
    ActionItem,
    Digest,
    Entity,
    EntityMention,
    Thought,
)


def _has_vec():
    try:
        conn = sqlite3.connect(":memory:")
        conn.enable_load_extension(True)
        import sqlite_vec
        sqlite_vec.load(conn)
        conn.close()
        return True
    except Exception:
        return False


_VEC_AVAILABLE = _has_vec()
requires_vec = pytest.mark.skipif(not _VEC_AVAILABLE, reason="sqlite-vec not available")


@pytest.fixture
def db(tmp_data_dir):
    """Provide an initialized Database instance for tests."""
    database = Database(tmp_data_dir / "test.db")
    database.initialize()
    yield database
    database.close()


def _make_thought(**kwargs) -> Thought:
    """Helper to create a Thought with sensible defaults."""
    defaults = {"content": "Test thought content"}
    defaults.update(kwargs)
    return Thought(**defaults)


def _make_entity(**kwargs) -> Entity:
    """Helper to create an Entity with sensible defaults."""
    defaults = {"name": "Alice", "entity_type": "person"}
    defaults.update(kwargs)
    return Entity(**defaults)


DUMMY_EMBEDDING = b"\x00" * (384 * 4)


# ---------------------------------------------------------------------------
# Schema / Initialization
# ---------------------------------------------------------------------------


def test_initialize_creates_tables(db):
    """initialize() should create all expected tables."""
    cursor = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    tables = {row["name"] for row in cursor.fetchall()}
    expected = {
        "thoughts",
        "thoughts_fts",
        "thoughts_fts_config",
        "thoughts_fts_data",
        "thoughts_fts_docsize",
        "thoughts_fts_idx",
        "entities",
        "entity_mentions",
        "config",
        "digests",
        "action_items",
    }
    assert expected.issubset(tables), f"Missing tables: {expected - tables}"


def test_initialize_idempotent(db):
    """Calling initialize() twice should not raise."""
    db.initialize()


@requires_vec
def test_vector_availability_is_per_connection(tmp_data_dir, monkeypatch):
    import sqlite_vec

    load = sqlite_vec.load
    fallback = Database(tmp_data_dir / "fallback.db")
    indexed = Database(tmp_data_dir / "indexed.db")
    try:
        monkeypatch.setattr(sqlite_vec, "load", lambda conn: (_ for _ in ()).throw(OSError("Unavailable")))
        fallback.initialize()
        assert not fallback.has_vec
        monkeypatch.setattr(sqlite_vec, "load", load)
        indexed.initialize()
        assert indexed.has_vec and not fallback.has_vec
        note = _make_thought(embedding=DUMMY_EMBEDDING)
        fallback.insert_thought(note)
        assert fallback.get_thought(note.id).content == note.content
        assert fallback.fts_search("thought")[0]["id"] == note.id
        fallback.update_thought(note.id, content="Edited fallback", embedding=None)
        assert fallback.fts_search("Edited")[0]["id"] == note.id
        fallback.delete_thought(note.id)
        assert fallback.get_thought(note.id) is None
        fallback.close()
        fallback.initialize()
        assert fallback.has_vec
    finally:
        fallback.close()
        indexed.close()


@requires_vec
def test_vector_index_recovers_edits_and_deletes_during_outage(tmp_data_dir, monkeypatch):
    import sqlite_vec

    database = Database(tmp_data_dir / "outage.db")
    old = struct.pack("384f", 1.0, *([0.0] * 383))
    replacement = struct.pack("384f", 0.0, 1.0, *([0.0] * 382))
    try:
        database.initialize()
        for note_id in ("edited", "deleted", "cleared"):
            database.insert_thought(_make_thought(id=note_id, content="Old bananas", embedding=old))
        database.close()
        with monkeypatch.context() as unavailable:
            unavailable.setattr(sqlite_vec, "load", lambda conn: (_ for _ in ()).throw(OSError("Unavailable")))
            database.initialize()
            assert not database.has_vec
            database.update_thought("edited", content="Passport renewal", embedding=replacement)
            database.update_thought("cleared", embedding=None)
            database.delete_thought("deleted")
            database.insert_thought(_make_thought(id="legacy", embedding=b"old unsupported bytes"))
            database.close()
        database.initialize()
        rows = database.execute("SELECT thought_id, embedding FROM vec_thoughts").fetchall()
        assert {row["thought_id"]: row["embedding"] for row in rows} == {"edited": replacement}
        assert database.fts_search("Passport")[0]["id"] == "edited"
        assert database.get_thought("cleared").embedding is None
        assert database.get_thought("legacy").embedding == b"old unsupported bytes"
    finally:
        database.close()


@requires_vec
def test_failed_vector_rebuild_retains_previous_index(db):
    original = _make_thought(embedding=DUMMY_EMBEDDING)
    db.insert_thought(original)
    conn = db._get_conn()
    conn.set_authorizer(lambda action, table, *args: sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_INSERT and table == "vec_thoughts" else sqlite3.SQLITE_OK)
    try:
        with pytest.raises(sqlite3.DatabaseError):
            db.initialize()
    finally:
        conn.set_authorizer(None)
    assert not conn.in_transaction
    assert db.execute("SELECT thought_id FROM vec_thoughts").fetchone()[0] == original.id


@pytest.mark.parametrize("sort", ["created_at_asc", "created_at_desc"])
def test_offset_and_microsecond_timestamps_page_chronologically(db, sort):
    timestamps = {
        "a": "2026-10-07T12:00:00.000001+02:00",
        "b": "2026-10-07T10:00:00.000002Z",
        "c": "2026-10-07T06:00:00.000002-04:00",
        "d": "2026-10-07T11:00:00+00:00",
        "e": "2026-10-07",
    }
    for note_id, timestamp in timestamps.items():
        db.insert_thought(_make_thought(id=note_id, created_at=timestamp))
    expected = ["e", "a", "b", "c", "d"]
    if sort == "created_at_desc":
        expected.reverse()
    found = []
    cursor = None
    while True:
        page, total = db.list_thoughts(limit=2, sort=sort, cursor=cursor)
        assert total == len(timestamps)
        if not page:
            break
        found.extend(note.id for note in page)
        assert all(note.created_at == timestamps[note.id] for note in page)
        cursor = encode_cursor(page[-1], sort)
    assert found == expected
    later, _ = db.list_thoughts(after=timestamps["a"])
    assert {note.id for note in later} == {"b", "c", "d"}
    earlier, _ = db.list_thoughts(before=timestamps["c"])
    assert {note.id for note in earlier} == {"e", "a"}


@pytest.mark.parametrize("field", ["after", "before"])
def test_invalid_date_filter_fails_clearly(db, field):
    with pytest.raises(ValueError, match=field):
        db.list_thoughts(**{field: "not-a-date"})


@pytest.mark.parametrize("sort", ["created_at_asc", "created_at_desc"])
@pytest.mark.parametrize("legacy_timestamp", ["banana", ""])
def test_legacy_invalid_timestamp_remains_pageable(db, sort, legacy_timestamp):
    for note_id, timestamp in (("valid", "2026-10-07"), ("legacy", legacy_timestamp)):
        db.insert_thought(_make_thought(id=note_id, created_at=timestamp))
    first, _ = db.list_thoughts(limit=1, sort=sort)
    second, _ = db.list_thoughts(limit=1, sort=sort, cursor=encode_cursor(first[0], sort))
    assert {first[0].id, second[0].id} == {"valid", "legacy"}
    future, _ = db.list_thoughts(after="2099-01-01")
    assert future == []
    bounded, _ = db.list_thoughts(before="2099-01-01")
    assert [note.id for note in bounded] == ["valid"]


# ---------------------------------------------------------------------------
# Thoughts CRUD
# ---------------------------------------------------------------------------


def test_insert_and_get_thought(db):
    """Insert a thought and retrieve it by ID."""
    thought = _make_thought(content="Hello world", embedding=DUMMY_EMBEDDING)
    db.insert_thought(thought)

    retrieved = db.get_thought(thought.id)
    assert retrieved is not None
    assert retrieved.id == thought.id
    assert retrieved.content == "Hello world"
    assert retrieved.embedding == DUMMY_EMBEDDING
    assert retrieved.source == "cli"
    assert retrieved.needs_review is False


def test_insert_thought_with_embedding_model(db):
    """Insert a thought with embedding_model and retrieve it."""
    thought = _make_thought(
        content="Versioned embedding",
        embedding=DUMMY_EMBEDDING,
        embedding_model="all-MiniLM-L6-v2",
    )
    db.insert_thought(thought)

    retrieved = db.get_thought(thought.id)
    assert retrieved is not None
    assert retrieved.embedding_model == "all-MiniLM-L6-v2"


def test_insert_thought_without_embedding_model(db):
    """Insert a thought without embedding_model; field should be None."""
    thought = _make_thought(content="No embedding model set")
    db.insert_thought(thought)

    retrieved = db.get_thought(thought.id)
    assert retrieved is not None
    assert retrieved.embedding_model is None


def test_get_thought_not_found(db):
    """get_thought returns None for nonexistent ID."""
    assert db.get_thought("nonexistent-id") is None


def test_list_thoughts(db):
    """list_thoughts returns inserted thoughts with total count."""
    for i in range(5):
        db.insert_thought(_make_thought(content=f"Thought {i}"))

    thoughts, total = db.list_thoughts(limit=3)
    assert len(thoughts) == 3
    assert total == 5


def test_list_thoughts_default_sort(db):
    """list_thoughts returns thoughts sorted by created_at desc by default."""
    t1 = _make_thought(content="First", created_at="2024-01-01T00:00:00+00:00")
    t2 = _make_thought(content="Second", created_at="2024-01-02T00:00:00+00:00")
    t3 = _make_thought(content="Third", created_at="2024-01-03T00:00:00+00:00")
    for t in [t1, t2, t3]:
        db.insert_thought(t)

    thoughts, _ = db.list_thoughts()
    assert thoughts[0].content == "Third"
    assert thoughts[2].content == "First"


def test_list_thoughts_with_category_filter(db):
    """list_thoughts can filter by category."""
    db.insert_thought(_make_thought(content="Work thing", category="work"))
    db.insert_thought(_make_thought(content="Personal note", category="personal"))
    db.insert_thought(_make_thought(content="Another work", category="work"))

    thoughts, total = db.list_thoughts(category="work")
    assert total == 2
    assert all(t.category == "work" for t in thoughts)


def test_list_thoughts_with_needs_review_filter(db):
    """list_thoughts can filter by needs_review."""
    db.insert_thought(_make_thought(content="Reviewed", needs_review=False))
    db.insert_thought(_make_thought(content="Needs review", needs_review=True))

    thoughts, total = db.list_thoughts(needs_review=True)
    assert total == 1
    assert thoughts[0].needs_review is True


def test_list_thoughts_with_cursor(db):
    """list_thoughts supports cursor-based pagination."""
    for i in range(5):
        db.insert_thought(
            _make_thought(
                content=f"Thought {i}",
                created_at=f"2024-01-0{i + 1}T00:00:00+00:00",
            )
        )

    page1, total = db.list_thoughts(limit=2)
    assert len(page1) == 2
    assert total == 5

    # Use last item's created_at as cursor
    page2, _ = db.list_thoughts(limit=2, cursor=page1[-1].created_at)
    assert len(page2) == 2
    # page2 should not overlap with page1
    page1_ids = {t.id for t in page1}
    page2_ids = {t.id for t in page2}
    assert page1_ids.isdisjoint(page2_ids)


@pytest.mark.parametrize("sort", ["created_at_asc", "created_at_desc"])
def test_cursor_pages_timestamp_ties_without_losing_records(db, sort):
    """The ID tie-breaker works in both directions, including filtered pages."""
    for i in range(7):
        db.insert_thought(_make_thought(
            id=f"note-{i}", content=f"Note {i}", category="work",
            created_at="2024-01-01T00:00:00+00:00",
        ))
    db.insert_thought(_make_thought(category="other"))

    cursor = None
    seen = []
    while True:
        page, total = db.list_thoughts(
            limit=2, cursor=cursor, category="work", sort=sort,
        )
        assert total == 7
        if not page:
            break
        seen.extend(thought.id for thought in page)
        cursor = encode_cursor(page[-1], sort)

    assert seen == sorted(
        [f"note-{i}" for i in range(7)], reverse=sort.endswith("desc"),
    )


@pytest.mark.parametrize("cursor", [
    "garbage", "", "2024-99-99",
    base64.urlsafe_b64encode(b'[]').decode(),
    base64.urlsafe_b64encode(b'["not a date","note-id"]').decode(),
    base64.urlsafe_b64encode(b'["2024-01-01",null]').decode(),
])
def test_cursor_rejects_invalid_payloads(db, cursor):
    with pytest.raises(ValueError, match="Invalid pagination cursor"):
        db.list_thoughts(cursor=cursor)


def test_list_thoughts_with_date_filter(db):
    """list_thoughts can filter by after/before dates."""
    db.insert_thought(
        _make_thought(content="Old", created_at="2024-01-01T00:00:00+00:00")
    )
    db.insert_thought(
        _make_thought(content="Middle", created_at="2024-06-15T00:00:00+00:00")
    )
    db.insert_thought(
        _make_thought(content="New", created_at="2024-12-31T00:00:00+00:00")
    )

    thoughts, total = db.list_thoughts(after="2024-06-01T00:00:00+00:00")
    assert total == 2

    thoughts, total = db.list_thoughts(before="2024-06-30T00:00:00+00:00")
    assert total == 2

    thoughts, total = db.list_thoughts(
        after="2024-06-01T00:00:00+00:00", before="2024-06-30T00:00:00+00:00"
    )
    assert total == 1
    assert thoughts[0].content == "Middle"


def test_list_thoughts_with_entity_filter(db):
    """list_thoughts can filter by entity name."""
    t1 = _make_thought(content="Met with Alice today")
    t2 = _make_thought(content="Unrelated thought")
    db.insert_thought(t1)
    db.insert_thought(t2)

    entity = _make_entity(name="Alice", entity_type="person")
    db.insert_entity(entity)
    db.insert_entity_mention(
        EntityMention(entity_id=entity.id, thought_id=t1.id, context="Met with Alice")
    )

    thoughts, total = db.list_thoughts(entity="Alice")
    assert total == 1
    assert thoughts[0].id == t1.id


def test_update_thought(db):
    """update_thought modifies specific fields."""
    thought = _make_thought(content="Original")
    db.insert_thought(thought)

    db.update_thought(
        thought.id,
        content="Updated content",
        category="work",
        confidence=0.95,
        needs_review=True,
    )

    updated = db.get_thought(thought.id)
    assert updated.content == "Updated content"
    assert updated.category == "work"
    assert updated.confidence == 0.95
    assert updated.needs_review is True


def test_update_thought_metadata(db):
    """update_thought can update metadata dict."""
    thought = _make_thought(content="Test", metadata={"key1": "val1"})
    db.insert_thought(thought)

    db.update_thought(thought.id, metadata={"key1": "val1", "key2": "val2"})
    updated = db.get_thought(thought.id)
    assert updated.metadata == {"key1": "val1", "key2": "val2"}


def test_update_thought_embedding(db):
    """update_thought can set embedding blob."""
    thought = _make_thought(content="Test")
    db.insert_thought(thought)
    assert db.get_thought(thought.id).embedding is None

    db.update_thought(thought.id, embedding=DUMMY_EMBEDDING)
    updated = db.get_thought(thought.id)
    assert updated.embedding == DUMMY_EMBEDDING


def test_nullable_fields_are_preserved_when_omitted(db):
    thought = _make_thought(
        embedding=DUMMY_EMBEDDING, embedding_model="all-MiniLM-L6-v2",
        category="work", confidence=0.9,
    )
    db.insert_thought(thought)
    db.update_thought(thought.id, metadata={"thread": "work"})
    updated = db.get_thought(thought.id)
    assert updated.embedding == thought.embedding
    assert updated.embedding_model == thought.embedding_model
    assert updated.category == thought.category
    assert updated.confidence == thought.confidence


def test_nullable_fields_can_be_cleared_with_none(db):
    thought = _make_thought(
        content="bananas", embedding=DUMMY_EMBEDDING,
        embedding_model="all-MiniLM-L6-v2", category="work", confidence=0.9,
    )
    db.insert_thought(thought)
    db.update_thought(
        thought.id, content="passport", embedding=None, embedding_model=None,
        category=None, confidence=None,
    )
    updated = db.get_thought(thought.id)
    assert updated.content == "passport"
    assert updated.embedding is None
    assert updated.embedding_model is None
    assert updated.category is None
    assert updated.confidence is None
    assert db.fts_search("bananas") == []
    assert db.fts_search("passport")[0]["id"] == thought.id
    if db.has_vec:
        assert not db.execute(
            "SELECT thought_id FROM vec_thoughts WHERE thought_id = ?",
            (thought.id,),
        ).fetchall()


def test_delete_thought(db):
    """delete_thought removes a thought and returns content preview."""
    thought = _make_thought(content="Some content to delete")
    db.insert_thought(thought)

    preview = db.delete_thought(thought.id)
    assert preview is not None
    assert "Some content" in preview
    assert db.get_thought(thought.id) is None


def test_delete_thought_not_found(db):
    """delete_thought returns None for nonexistent ID."""
    assert db.delete_thought("nonexistent-id") is None


def test_edit_and_delete_keep_legacy_history_coherent(db):
    """Edits preserve archived enrichment; deletion removes links but keeps history."""
    thought = _make_thought(content="bananas", embedding=DUMMY_EMBEDDING)
    db.insert_thought(thought)
    entity = _make_entity()
    db.insert_entity(entity)
    db.insert_entity_mention(EntityMention(entity_id=entity.id, thought_id=thought.id))
    action = ActionItem(content="historical bananas task", source_thought_id=thought.id)
    db.insert_action_item(action)
    db.complete_action_item(action.id)

    db.update_thought(thought.id, content="passport", embedding=None)
    assert db.get_thoughts_for_entity(entity.id)[0].id == thought.id
    assert db.list_action_items(completed=True)[0].source_thought_id == thought.id

    db.delete_thought(thought.id)
    assert db.fts_search("passport") == []
    assert db.get_thoughts_for_entity(entity.id) == []
    assert db.get_entity(entity.id) is not None
    archived = db.list_action_items(completed=True)[0]
    assert archived.content == action.content
    assert archived.source_thought_id is None
    assert archived.completed is True


# ---------------------------------------------------------------------------
# FTS
# ---------------------------------------------------------------------------


def test_fts_search(db):
    """fts_search finds thoughts by keyword."""
    db.insert_thought(_make_thought(content="The quick brown fox jumps"))
    db.insert_thought(_make_thought(content="The lazy dog sleeps"))
    db.insert_thought(_make_thought(content="Python programming is fun"))

    results = db.fts_search("fox")
    assert len(results) == 1
    assert "fox" in results[0]["content"]


def test_fts_search_no_results(db):
    """fts_search returns empty list when no match."""
    db.insert_thought(_make_thought(content="Something unrelated"))
    results = db.fts_search("nonexistent")
    assert results == []


def test_fts_search_multiple_matches(db):
    """fts_search returns multiple matches."""
    db.insert_thought(_make_thought(content="Python is great"))
    db.insert_thought(_make_thought(content="Python for data science"))
    db.insert_thought(_make_thought(content="Java is different"))

    results = db.fts_search("Python")
    assert len(results) == 2


def test_fts_syncs_on_update(db):
    """FTS index should update when thought content is updated."""
    thought = _make_thought(content="Original unique keyword alpha")
    db.insert_thought(thought)

    assert len(db.fts_search("alpha")) == 1

    db.update_thought(thought.id, content="Updated unique keyword beta")
    assert len(db.fts_search("alpha")) == 0
    assert len(db.fts_search("beta")) == 1


def test_fts_syncs_on_delete(db):
    """FTS index should update when thought is deleted."""
    thought = _make_thought(content="Searchable keyword gamma")
    db.insert_thought(thought)

    assert len(db.fts_search("gamma")) == 1

    db.delete_thought(thought.id)
    assert len(db.fts_search("gamma")) == 0


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------


def test_insert_and_get_entity(db):
    """Insert an entity and retrieve it by ID."""
    entity = _make_entity(name="Bob", entity_type="person")
    db.insert_entity(entity)

    retrieved = db.get_entity(entity.id)
    assert retrieved is not None
    assert retrieved.name == "Bob"
    assert retrieved.entity_type == "person"
    assert retrieved.mention_count == 1


def test_get_entity_by_name(db):
    """get_entity_by_name is case-insensitive."""
    entity = _make_entity(name="Alice", entity_type="person")
    db.insert_entity(entity)

    retrieved = db.get_entity_by_name("alice")
    assert retrieved is not None
    assert retrieved.id == entity.id

    retrieved_upper = db.get_entity_by_name("ALICE")
    assert retrieved_upper is not None
    assert retrieved_upper.id == entity.id


def test_get_entity_by_alias(db):
    """get_entity_by_name should check aliases too."""
    entity = _make_entity(
        name="Robert Smith", entity_type="person", aliases=["Bob", "Bobby"]
    )
    db.insert_entity(entity)

    retrieved = db.get_entity_by_name("bob")
    assert retrieved is not None
    assert retrieved.id == entity.id


def test_get_entity_by_name_not_found(db):
    """get_entity_by_name returns None when not found."""
    assert db.get_entity_by_name("nobody") is None


def test_list_entities(db):
    """list_entities returns entities with count."""
    db.insert_entity(_make_entity(name="Alice", entity_type="person"))
    db.insert_entity(_make_entity(name="ProjectX", entity_type="project"))
    db.insert_entity(_make_entity(name="Bob", entity_type="person"))

    entities, total = db.list_entities()
    assert total == 3
    assert len(entities) == 3


def test_list_entities_type_filter(db):
    """list_entities can filter by entity_type."""
    db.insert_entity(_make_entity(name="Alice", entity_type="person"))
    db.insert_entity(_make_entity(name="ProjectX", entity_type="project"))
    db.insert_entity(_make_entity(name="Bob", entity_type="person"))

    entities, total = db.list_entities(entity_type="person")
    assert total == 2
    assert all(e.entity_type == "person" for e in entities)


def test_update_entity_seen(db):
    """update_entity_seen increments mention_count and updates last_seen."""
    entity = _make_entity(name="Alice")
    db.insert_entity(entity)

    db.update_entity_seen(entity.id)

    updated = db.get_entity(entity.id)
    assert updated.mention_count == 2


# ---------------------------------------------------------------------------
# Entity Mentions
# ---------------------------------------------------------------------------


def test_entity_mention(db):
    """Insert entity mention and retrieve thoughts for entity."""
    thought = _make_thought(content="Met with Alice today")
    db.insert_thought(thought)

    entity = _make_entity(name="Alice", entity_type="person")
    db.insert_entity(entity)

    mention = EntityMention(
        entity_id=entity.id, thought_id=thought.id, context="Met with Alice"
    )
    db.insert_entity_mention(mention)

    thoughts = db.get_thoughts_for_entity(entity.id)
    assert len(thoughts) == 1
    assert thoughts[0].id == thought.id


def test_delete_mentions_for_thought(db):
    """delete_mentions_for_thought removes all mentions for a thought."""
    thought = _make_thought(content="Met with Alice and Bob")
    db.insert_thought(thought)

    alice = _make_entity(name="Alice", entity_type="person")
    bob = _make_entity(name="Bob", entity_type="person")
    db.insert_entity(alice)
    db.insert_entity(bob)

    db.insert_entity_mention(
        EntityMention(entity_id=alice.id, thought_id=thought.id)
    )
    db.insert_entity_mention(
        EntityMention(entity_id=bob.id, thought_id=thought.id)
    )

    db.delete_mentions_for_thought(thought.id)

    assert db.get_thoughts_for_entity(alice.id) == []
    assert db.get_thoughts_for_entity(bob.id) == []


# ---------------------------------------------------------------------------
# Digests
# ---------------------------------------------------------------------------


def test_insert_and_list_digests(db):
    """Insert a digest and list it."""
    digest = Digest(
        content="Daily summary",
        thought_ids=["id1", "id2"],
        period_start="2024-01-01T00:00:00+00:00",
        period_end="2024-01-01T23:59:59+00:00",
    )
    db.insert_digest(digest)

    digests = db.list_digests()
    assert len(digests) == 1
    assert digests[0].content == "Daily summary"
    assert digests[0].thought_ids == ["id1", "id2"]


# ---------------------------------------------------------------------------
# Action Items
# ---------------------------------------------------------------------------


def test_action_items(db):
    """Insert action items and list them."""
    item1 = ActionItem(content="Review PR", person="Alice")
    item2 = ActionItem(content="Write docs", person="Bob")
    db.insert_action_item(item1)
    db.insert_action_item(item2)

    items = db.list_action_items(completed=False)
    assert len(items) == 2


def test_complete_action_item(db):
    """complete_action_item marks item as completed."""
    item = ActionItem(content="Fix bug")
    db.insert_action_item(item)

    db.complete_action_item(item.id)

    # Should not appear in uncompleted list
    items = db.list_action_items(completed=False)
    assert len(items) == 0

    # Should appear in completed list
    completed = db.list_action_items(completed=True)
    assert len(completed) == 1
    assert completed[0].completed is True
    assert completed[0].completed_at is not None


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def test_config_key_value(db):
    """set/get config key-value pairs."""
    assert db.get_config_value("test_key") is None

    db.set_config_value("test_key", "test_value")
    assert db.get_config_value("test_key") == "test_value"

    # Overwrite
    db.set_config_value("test_key", "new_value")
    assert db.get_config_value("test_key") == "new_value"


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


def test_stats(db):
    """get_stats returns summary statistics."""
    db.insert_thought(_make_thought(content="Work task 1", category="work"))
    db.insert_thought(_make_thought(content="Personal note", category="personal"))
    db.insert_thought(
        _make_thought(content="Needs review", category="work", needs_review=True)
    )

    entity = _make_entity(name="Alice", entity_type="person")
    db.insert_entity(entity)

    stats = db.get_stats()

    assert stats["thoughts"]["total"] == 3
    assert stats["thoughts"]["by_category"]["work"] == 2
    assert stats["thoughts"]["by_category"]["personal"] == 1
    assert stats["thoughts"]["needs_review"] == 1
    assert stats["entities"]["total"] == 1
    assert stats["entities"]["by_type"]["person"] == 1
    assert stats["digests"]["total"] == 0


def test_stats_empty_db(db):
    """get_stats works on empty database."""
    stats = db.get_stats()
    assert stats["thoughts"]["total"] == 0
    assert stats["thoughts"]["by_category"] == {}
    assert stats["thoughts"]["needs_review"] == 0
    assert stats["entities"]["total"] == 0
    assert stats["digests"]["total"] == 0


# ---------------------------------------------------------------------------
# sqlite-vec Virtual Table
# ---------------------------------------------------------------------------




@requires_vec
def test_vec_table_exists(db):
    """initialize() should create the vec_thoughts virtual table."""
    cursor = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='vec_thoughts'"
    )
    row = cursor.fetchone()
    assert row is not None, "vec_thoughts table should exist after initialize()"


@requires_vec
def test_vec_syncs_on_delete(db):
    """Deleting a thought should also remove its vec_thoughts entry."""
    dim = 384
    embedding = struct.pack(f"{dim}f", *([1.0] * dim))

    thought = _make_thought(content="Vec delete test", embedding=embedding)
    db.insert_thought(thought)

    # Verify vec_thoughts row exists
    rows = db.execute(
        "SELECT thought_id FROM vec_thoughts WHERE thought_id = ?",
        (thought.id,),
    ).fetchall()
    assert len(rows) == 1

    # Delete the thought
    db.delete_thought(thought.id)

    # vec_thoughts row should be gone
    rows = db.execute(
        "SELECT thought_id FROM vec_thoughts WHERE thought_id = ?",
        (thought.id,),
    ).fetchall()
    assert len(rows) == 0


@requires_vec
def test_vec_syncs_on_update(db):
    """Updating a thought's embedding should update vec_thoughts."""
    dim = 384
    embedding_v1 = struct.pack(f"{dim}f", *([1.0] * dim))
    embedding_v2 = struct.pack(f"{dim}f", *([0.5] * dim))

    thought = _make_thought(content="Vec update test", embedding=embedding_v1)
    db.insert_thought(thought)

    # Update with new embedding
    db.update_thought(thought.id, embedding=embedding_v2)

    # Query with embedding_v2 — should match with distance ~0
    rows = db.execute(
        """
        SELECT thought_id, distance
        FROM vec_thoughts
        WHERE embedding MATCH ?
        ORDER BY distance
        LIMIT 1
        """,
        (embedding_v2,),
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["thought_id"] == thought.id
    assert rows[0]["distance"] < 0.01


@requires_vec
def test_vec_insert_and_query(db):
    """Insert a thought with an embedding and KNN-query it back via vec_thoughts."""
    # Build a simple 384-dim embedding: all 1.0 values, packed as float32 bytes
    dim = 384
    embedding = struct.pack(f"{dim}f", *([1.0] * dim))

    thought = _make_thought(content="Vector test thought", embedding=embedding)
    db.insert_thought(thought)

    # Build a query vector (identical to the inserted one for a guaranteed match)
    query_vec = struct.pack(f"{dim}f", *([1.0] * dim))

    rows = db.execute(
        """
        SELECT thought_id, distance
        FROM vec_thoughts
        WHERE embedding MATCH ?
        ORDER BY distance
        LIMIT 5
        """,
        (query_vec,),
    ).fetchall()

    assert len(rows) >= 1, "KNN query should return at least one result"
    assert rows[0]["thought_id"] == thought.id
    # Cosine distance between identical vectors should be ~0
    assert rows[0]["distance"] < 0.01


@requires_vec
def test_vector_replacement_failure_rolls_back_text_and_indexes(db):
    embedding = struct.pack("384f", *([1.0] * 384))
    thought = _make_thought(
        content="original bananas", embedding=embedding,
        embedding_model="all-MiniLM-L6-v2", category="work", confidence=0.9,
    )
    db.insert_thought(thought)
    with pytest.raises(sqlite3.Error):
        db.update_thought(
            thought.id, content="changed passport", category=None,
            embedding=struct.pack("768f", *([1.0] * 768)),
        )
    assert db.get_thought(thought.id).model_dump() == thought.model_dump()
    assert db.fts_search("passport") == []
    assert db.fts_search("bananas")[0]["id"] == thought.id
    row = db.execute(
        "SELECT thought_id, distance FROM vec_thoughts WHERE embedding MATCH ? AND k=1",
        (embedding,),
    ).fetchone()
    assert row["thought_id"] == thought.id
    assert row["distance"] < 0.01
    assert not db._get_conn().in_transaction


@requires_vec
def test_vector_insert_failure_leaves_no_partial_record(db):
    thought = _make_thought(
        content="fresh bananas", embedding=struct.pack("768f", *([1.0] * 768)),
    )
    with pytest.raises(sqlite3.Error):
        db.insert_thought(thought)
    assert db.get_thought(thought.id) is None
    assert db.fts_search("bananas") == []
    assert not db._get_conn().in_transaction
    thought.embedding = struct.pack("384f", *([1.0] * 384))
    db.insert_thought(thought)
    assert db.get_thought(thought.id) is not None


def test_stdlib_backup_restores_live_database_and_legacy_history(db, tmp_path):
    """Documented sqlite3 backup works while Sticky's WAL connection is open."""
    thought = _make_thought(
        content="backup searchable bananas", embedding=struct.pack("384f", *([1.0] * 384)),
        metadata={"thread": "recovery"}, source_url="https://example.com/source",
    )
    db.insert_thought(thought)
    entity = _make_entity(aliases=["Ally"])
    db.insert_entity(entity)
    db.insert_entity_mention(EntityMention(
        entity_id=entity.id, thought_id=thought.id, context="historical link",
    ))
    action = ActionItem(content="Archived task", source_thought_id=thought.id)
    db.insert_action_item(action)
    db.complete_action_item(action.id)
    digest = Digest(
        content="Archived digest", thought_ids=[thought.id],
        period_start="2024-01-01", period_end="2024-01-02",
    )
    db.insert_digest(digest)
    db.set_config_value("history", "preserved")

    backup_path = tmp_path / "sticky-backup.db"
    # Exact documented route: ordinary sqlite3 connections, no extension needed.
    with sqlite3.connect(db._db_path) as source:
        with sqlite3.connect(backup_path) as target:
            source.backup(target)
    source.close()
    target.close()

    restored = Database(backup_path)
    try:
        restored.initialize()
        assert restored.get_thought(thought.id).model_dump() == thought.model_dump()
        assert restored.fts_search("bananas")[0]["id"] == thought.id
        assert restored.get_entity_by_name("Ally").id == entity.id
        assert restored.get_thoughts_for_entity(entity.id)[0].id == thought.id
        assert restored.list_action_items(completed=True)[0].source_thought_id == thought.id
        assert restored.list_action_items(completed=True)[0].completed_at is not None
        assert restored.list_digests()[0].model_dump() == digest.model_dump()
        assert restored.get_config_value("history") == "preserved"
        if restored.has_vec:
            row = restored.execute(
                "SELECT thought_id, distance FROM vec_thoughts WHERE embedding MATCH ? AND k=1",
                (thought.embedding,),
            ).fetchone()
            assert row["thought_id"] == thought.id
            assert row["distance"] < 0.01
    finally:
        restored.close()
