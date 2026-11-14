"""Regression tests for restoring thought records from JSON backups."""

import json
import sqlite3
import struct
from unittest.mock import Mock

import pytest

from sticky.core.config import StickyConfig
from sticky.core.models import Thought
from sticky.core.service import StickyService


@pytest.fixture
def service(tmp_data_dir, monkeypatch):
    svc = StickyService(config=StickyConfig(data_dir=tmp_data_dir))
    svc.initialize()
    # Exercise real storage and search without downloading a model or using an API.
    monkeypatch.setattr(svc.embedding_engine, "embed", Mock(return_value=struct.pack("384f", *([1.0] * 384))))
    yield svc
    svc.db.close()


def write_backup(tmp_path, thoughts):
    path = tmp_path / "backup.json"
    path.write_text(json.dumps({"thoughts": thoughts}), encoding="utf-8")
    return path


def test_export_restore_preserves_original_record(service, tmp_path):
    original = Thought(
        content="Sarah shared café notes on the platform migration.\nFollow up Friday.",
        category="meeting", confidence=0.45, needs_review=True, source="mcp",
        source_url="https://example.com/meeting",
        metadata={"thread": "platform", "template": "meeting", "topics": ["migration"]},
        created_at="2026-03-22T12:00:00+00:00",
        updated_at="2026-03-29T13:00:00+00:00",
    )
    service.db.insert_thought(original)
    path = tmp_path / "backup.json"
    service.export_data("json", str(path))
    service.delete(original.id)

    result = service.import_data(str(path))

    assert result["imported"] == 1
    assert result["skipped"] == 0
    assert result["errors"] == []
    restored = service.db.get_thought(original.id)
    assert restored.model_dump(exclude={"embedding", "embedding_model"}) == original.model_dump(exclude={"embedding", "embedding_model"})
    assert restored.embedding is not None
    assert restored.embedding_model == service.embedding_engine.model_name
    assert service.search("migration", mode="keyword")[0]["id"] == original.id
    assert service.search("migration", mode="semantic")[0]["id"] == original.id


def test_repeated_restore_skips_existing_ids(service, tmp_path):
    original = Thought(content="Remember the release checklist")
    path = write_backup(tmp_path, [original.to_display()])
    service.import_data(str(path))

    result = service.import_data(str(path))

    assert result["imported"] == 0
    assert result["skipped"] == 1
    assert service.list_thoughts()["total"] == 1
    assert service.embedding_engine.embed.call_count == 1


def test_restore_keeps_distinct_ids_with_identical_content(service, tmp_path):
    first = Thought(content="Daily standup")
    second = Thought(content="Daily standup")
    path = write_backup(tmp_path, [first.to_display(), second.to_display()])

    result = service.import_data(str(path))

    assert result["imported"] == 2
    assert service.db.get_thought(first.id) is not None
    assert service.db.get_thought(second.id) is not None


def test_restore_dry_run_validates_without_writing_or_embedding(service, tmp_path):
    original = Thought(content="Dry-run restore")
    path = write_backup(tmp_path, [original.to_display(), {"id": "invalid", "metadata": []}])

    result = service.import_data(str(path), dry_run=True)

    assert result["imported"] == 1
    assert len(result["errors"]) == 1
    assert service.list_thoughts()["total"] == 0
    service.embedding_engine.embed.assert_not_called()


def test_restore_survives_embedding_failure(service, tmp_path):
    original = Thought(content="Fallback keyword lookup", embedding_model="old-model")
    path = write_backup(tmp_path, [original.to_display()])
    service.embedding_engine.embed.side_effect = RuntimeError("Model unavailable")

    result = service.import_data(str(path))

    assert result["imported"] == 1
    assert result["errors"] == []
    restored = service.db.get_thought(original.id)
    assert restored.embedding is None
    assert restored.embedding_model is None
    assert service.search("Fallback", mode="keyword")[0]["id"] == original.id


def test_restore_survives_wrong_embedding_dimensions(service, tmp_path, monkeypatch):
    original = Thought(content="Restore my passport note")
    path = write_backup(tmp_path, [original.to_display()])
    monkeypatch.setattr(service.embedding_engine, "embed", Mock(return_value=struct.pack("768f", *([1.0] * 768))))
    result = service.import_data(str(path))
    assert result["imported"] == 1 and result["errors"] == []
    restored = service.db.get_thought(original.id)
    assert restored.embedding is None and restored.embedding_model is None
    assert service.search("passport", mode="keyword")[0]["id"] == original.id


def test_restore_reports_bad_row_and_continues(service, tmp_path):
    original = Thought(content="Valid note after malformed row")
    path = write_backup(tmp_path, [None, {"id": "bad"}, original.to_display()])

    result = service.import_data(str(path))

    assert result["imported"] == 1
    assert len(result["errors"]) == 2
    assert service.db.get_thought(original.id) is not None


def test_plain_json_notes_still_use_capture(service, tmp_path, monkeypatch):
    capture = Mock(return_value={"id": "new-id"})
    monkeypatch.setattr(service, "capture", capture)
    path = write_backup(tmp_path, [{"content": "New note", "source_url": "https://example.com", "metadata": {"thread": "research"}}])

    result = service.import_data(str(path))

    assert result["imported"] == 1
    capture.assert_called_once_with(content="New note", source="import", source_url="https://example.com", thread="research")


def test_plain_json_repeated_notes_are_distinct_captures(service, tmp_path):
    path = write_backup(tmp_path, [{"content": "Daily standup"}, {"content": "Daily standup"}])

    result = service.import_data(str(path))

    assert result["imported"] == 2
    assert result["skipped"] == 0
    assert service.list_thoughts()["total"] == 2


@pytest.mark.parametrize("metadata", [None, [], "research"])
def test_plain_json_notes_accept_non_object_metadata(service, tmp_path, monkeypatch, metadata):
    capture = Mock(return_value={"id": "new-id"})
    monkeypatch.setattr(service, "capture", capture)
    path = write_backup(tmp_path, [{"content": "New note", "metadata": metadata}])

    result = service.import_data(str(path))

    assert result["imported"] == 1
    assert result["errors"] == []
    assert capture.call_args.kwargs["thread"] is None


def test_failed_vector_insert_rolls_back_and_can_be_retried(service, tmp_path):
    if not service.db.has_vec:
        pytest.skip("Requires sqlite-vec to fail after inserting the thought row")
    original = Thought(content="Retry a failed restore")
    path = write_backup(tmp_path, [original.to_display()])
    conn = service.db._get_conn()
    conn.set_authorizer(lambda action, table, *args: sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_INSERT and table == "vec_thoughts" else sqlite3.SQLITE_OK)

    failed = service.import_data(str(path))

    assert failed["imported"] == 0
    assert len(failed["errors"]) == 1
    assert service.db.get_thought(original.id) is None
    assert service.search("Retry", mode="keyword") == []
    assert not service.db._get_conn().in_transaction

    conn.set_authorizer(None)
    retried = service.import_data(str(path))

    assert retried["imported"] == 1
    assert retried["skipped"] == 0
    assert retried["errors"] == []
    assert service.search("Retry", mode="semantic")[0]["id"] == original.id


@pytest.mark.parametrize("data", [[], None, {}, {"thoughts": None}, {"thoughts": {}}])
def test_malformed_backup_shape_is_refused(service, tmp_path, data):
    path = tmp_path / "malformed.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="thoughts list"):
        service.import_data(str(path))
    assert service.list_thoughts()["total"] == 0


def test_plain_dry_run_matches_record_validation_and_repeated_ids(service, tmp_path):
    path = write_backup(tmp_path, [
        {}, {"content": 23}, {"content": "  "},
        {"id": "repeated", "content": "Keep this"},
        {"id": "repeated", "content": "Same ID"},
        {"content": "New plain note"},
    ])
    preview = service.import_data(str(path), dry_run=True)
    assert preview["imported"] == 2 and preview["skipped"] == 1
    assert len(preview["errors"]) == 3
    service.embedding_engine.embed.assert_not_called()
    assert service.list_thoughts()["total"] == 0
    restored = service.import_data(str(path))
    assert restored == {**preview, "dry_run": False}
    assert service.list_thoughts()["total"] == 2


@pytest.mark.parametrize("field", ["created_at", "updated_at"])
def test_restore_rejects_malformed_timestamps_and_keeps_valid_offset(service, tmp_path, field):
    record = {"id": "valid-offset", "content": "Original timestamp", "created_at": "2026-10-07T12:00:00.123456+02:00"}
    path = write_backup(tmp_path, [{"id": "invalid", "content": "Bad timestamp", field: "banana"}, record])
    result = service.import_data(str(path))
    assert result["imported"] == 1 and len(result["errors"]) == 1
    assert field in result["errors"][0]
    assert service.get_thought("valid-offset")["created_at"] == record["created_at"]


def test_uppercase_json_extension_restores_record(service, tmp_path):
    path = tmp_path / "NOTES.JSON"
    path.write_text(json.dumps({"thoughts": [{"id": "original-id", "content": "Original note"}]}), encoding="utf-8")
    result = service.import_data(str(path))
    assert result["format"] == "json" and result["imported"] == 1
    assert service.get_thought("original-id")["content"] == "Original note"


@pytest.mark.parametrize("kind", ["missing", "file"])
def test_markdown_import_requires_directory(service, tmp_path, kind):
    source = tmp_path / kind
    if kind == "file":
        source.write_text("a note", encoding="utf-8")
    with pytest.raises(ValueError, match="directory"):
        service.import_data(str(source), format="markdown")
    assert service.list_thoughts()["total"] == 0


def test_markdown_dry_run_rejects_blank_note(service, tmp_path):
    directory = tmp_path / "markdown"
    directory.mkdir()
    (directory / "blank.md").write_text("---\nid: empty\n---\n  ", encoding="utf-8")
    preview = service.import_data(str(directory), dry_run=True)
    actual = service.import_data(str(directory))
    assert preview["imported"] == 0 and len(preview["errors"]) == 1
    assert actual == {**preview, "dry_run": False}


def test_concurrent_restore_winning_id_is_reported_as_skipped(service, tmp_path, monkeypatch):
    path = write_backup(tmp_path, [{"id": "concurrent-id", "content": "Keep the winning restore"}])
    insert = service.db.insert_thought
    def won_elsewhere(thought):
        insert(thought)
        raise sqlite3.IntegrityError("UNIQUE constraint failed: thoughts.id")
    monkeypatch.setattr(service.db, "insert_thought", won_elsewhere)
    result = service.import_data(str(path))
    assert result["imported"] == 0 and result["skipped"] == 1 and result["errors"] == []
    assert service.get_thought("concurrent-id")["content"] == "Keep the winning restore"
