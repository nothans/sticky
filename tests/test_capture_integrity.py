"""Preservation regressions for the smaller local capture/edit pipeline."""

import json
import struct

import httpx
import pytest

from sticky.core.config import StickyConfig
from sticky.core.models import Thought
from sticky.core.service import StickyService


@pytest.fixture
def local_service(tmp_data_dir, monkeypatch):
    svc = StickyService(config=StickyConfig(data_dir=tmp_data_dir))
    monkeypatch.setattr(svc.embedding_engine, "embed", lambda text: struct.pack("384f", *([1.0] * 384)))
    svc.initialize()
    yield svc
    svc.db.close()


@pytest.mark.parametrize("first,second", [
    ("Meeting with Sarah at 2 pm tomorrow", "Meeting with Sarah at 3 pm tomorrow"),
    ("Call Steve on Monday", "Call Steve on Tuesday"),
    ("Daily standup", "Daily standup"),
])
def test_each_capture_survives_even_with_identical_vectors(local_service, first, second):
    one = local_service.capture(first)
    two = local_service.capture(second)
    assert one["id"] != two["id"]
    assert local_service.get_thought(one["id"])["content"] == first
    assert local_service.get_thought(two["id"])["content"] == second
    assert local_service.list_thoughts()["total"] == 2


def test_normal_work_never_calls_cloud_even_with_a_key(local_service, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Local operations attempted cloud work")
    monkeypatch.setattr(httpx.Client, "post", forbidden)
    monkeypatch.setattr(httpx.AsyncClient, "post", forbidden)
    local_service.config.openrouter_api_key = "configured-but-unused"
    local_service.initialize()
    note = local_service.capture("local note")
    assert local_service.search("note")[0]["id"] == note["id"]
    local_service.update(note["id"], "edited local note")
    assert local_service.get_thought(note["id"])["category"] is None
    assert not local_service.db.list_action_items()
    assert local_service.db.list_entities()[1] == 0


def test_failed_edit_embedding_clears_old_vector_and_confidence(local_service, monkeypatch):
    note = local_service.capture("Bananas")
    local_service.classify_thought(note["id"], "idea")
    original = local_service.db.get_thought(note["id"])
    original_metadata = {"thread": "research", "custom": "keep"}
    local_service.db.update_thought(note["id"], metadata=original_metadata)
    def unavailable(text):
        raise RuntimeError("model unavailable")
    monkeypatch.setattr(local_service.embedding_engine, "embed", unavailable)
    edited = local_service.update(note["id"], "My passport expires next month")
    stored = local_service.db.get_thought(note["id"])
    assert stored.created_at == original.created_at
    assert stored.embedding is None
    assert stored.embedding_model is None
    assert edited["category"] is None and edited["confidence"] is None
    assert edited["metadata"] == original_metadata
    assert local_service.search("passport")[0]["id"] == note["id"]
    assert not local_service.search("Bananas")
    if local_service.db.has_vec:
        assert local_service.db.execute("SELECT COUNT(*) FROM vec_thoughts WHERE thought_id = ?", (note["id"],)).fetchone()[0] == 0


def test_wrong_dimension_capture_preserves_text(local_service, monkeypatch):
    monkeypatch.setattr(local_service.embedding_engine, "embed", lambda text: struct.pack("768f", *([1.0] * 768)))
    note = local_service.capture("Find this unique passport note")
    assert local_service.db.get_thought(note["id"]).embedding is None
    assert local_service.search("passport", mode="keyword")[0]["id"] == note["id"]


def test_markdown_distinct_ids_with_same_prefix_export_distinct_files(local_service, tmp_path):
    notes = [Thought(id=f"01HZY00000000000000000000{i}", content="The initial forty characters of this note exactly are the same " + person, created_at="2024-06-09T12:00:00+00:00") for i, person in enumerate(["Alice", "Bob"])]
    for note in notes:
        local_service.db.insert_thought(note)
    destination = tmp_path / "markdown"
    result = local_service.export_data("markdown", str(destination))
    assert result["count"] == 2 and len(set(result["files"])) == 2
    contents = [p.read_text(encoding="utf-8") for p in destination.glob("*.md") if p.name != "_index.md"]
    assert len(contents) == 2
    assert all(any(note.content in text for text in contents) for note in notes)
    before = {p.name: p.read_bytes() for p in destination.iterdir()}
    with pytest.raises(FileExistsError):
        local_service.export_data("markdown", str(destination))
    assert before == {p.name: p.read_bytes() for p in destination.iterdir()}


def test_markdown_imported_id_cannot_escape_destination(local_service, tmp_path):
    note = Thought(id="../../arbitrary:id", content="Imported record")
    local_service.db.insert_thought(note)
    result = local_service.export_data("markdown", str(tmp_path / "markdown"))
    assert (tmp_path / "markdown" / result["files"][0]).is_file()
    assert "/" not in result["files"][0] and ":" not in result["files"][0]


def test_secret_setting_receipt_contains_no_key(local_service):
    for secret in ["first-fake-key", "second-fake-key"]:
        receipt = local_service.set_config_value("openrouter_api_key", secret)
        text = json.dumps(receipt)
        assert "fake-key" not in text
        assert receipt["new"] == "configured"


def test_blank_edit_preserves_original(local_service):
    note = local_service.capture("Keep this")
    with pytest.raises(ValueError, match="text"):
        local_service.update(note["id"], " \n ")
    assert local_service.get_thought(note["id"])["content"] == "Keep this"


def test_cursor_cannot_change_direction(local_service):
    for i in range(4):
        local_service.db.insert_thought(Thought(id=f"note-{i}", content=f"Note {i}", created_at="2026-01-01T12:00:00+00:00"))
    page = local_service.list_thoughts(limit=2)
    with pytest.raises(ValueError, match="cursor"):
        local_service.list_thoughts(limit=2, cursor=page["next_cursor"], sort="created_at_asc")
    final = local_service.list_thoughts(limit=2, cursor=page["next_cursor"])
    assert not final["has_more"] and "next_cursor" not in final
    assert len({n["id"] for n in page["thoughts"] + final["thoughts"]}) == 4


def test_json_export_exceeds_old_global_cap(local_service, tmp_path):
    count = 100001
    conn = local_service.db._get_conn()
    with conn:
        conn.executemany(
            "INSERT INTO thoughts (id, content, created_at, updated_at) VALUES (?, ?, ?, ?)",
            ((f"note-{i}", f"Complete export note {i}", "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00") for i in range(count)),
        )
    destination = tmp_path / "all-notes.json"
    result = local_service.export_data("json", str(destination))
    exported = json.loads(destination.read_text(encoding="utf-8"))["thoughts"]
    assert result["count"] == count and len(exported) == count
    assert len({note["id"] for note in exported}) == count


def test_oversized_digest_fails_before_cloud_and_preserves_notes(local_service, monkeypatch):
    local_service.config.openrouter_api_key = "fake-key"
    monkeypatch.setattr(httpx.Client, "post", lambda *args, **kwargs: pytest.fail("Oversized selection must not call cloud"))
    for i in range(1001):
        local_service.db.insert_thought(Thought(content=f"Note {i}"))
    with pytest.raises(ValueError, match="1000 notes"):
        local_service.digest()
    assert local_service.stats()["thoughts"]["total"] == 1001
    assert local_service.db.list_digests() == []


@pytest.mark.parametrize("destination", ["database", "wal", "shm", "journal", "config"])
def test_json_export_cannot_overwrite_active_storage(local_service, destination):
    note = local_service.capture("Keep the live database")
    paths = {"database": local_service.config.db_path, "config": local_service.config.config_file}
    paths.update({key: local_service.config.db_path.with_name("sticky.db-" + key) for key in ("wal", "shm", "journal")})
    path = paths[destination]
    before = path.read_bytes() if path.exists() else None
    with pytest.raises(ValueError, match="active"):
        local_service.export_data("json", str(path))
    after = path.read_bytes() if path.exists() else None
    if destination == "shm" and before is not None:
        # Read-only SQLite queries legitimately update shared-memory lock bytes.
        assert after[:16] == before[:16]
    else:
        assert after == before
    assert local_service.get_thought(note["id"])["content"] == "Keep the live database"


def test_failed_json_export_preserves_existing_backup(local_service, tmp_path, monkeypatch):
    local_service.capture("New export")
    target = tmp_path / "backup.json"
    before = b'{"thoughts": [{"id": "old", "content": "Prior backup"}]}'
    target.write_bytes(before)
    before_files = set(tmp_path.iterdir())
    def interrupted(data, file, **kwargs):
        file.write('{"thoughts":')
        raise OSError("Disk full")
    monkeypatch.setattr(json, "dump", interrupted)
    with pytest.raises(OSError, match="Disk full"):
        local_service.export_data("json", str(target))
    assert target.read_bytes() == before
    assert set(tmp_path.iterdir()) == before_files


def test_json_export_replaces_existing_backup_completely(local_service, tmp_path):
    note = local_service.capture("Current export")
    target = tmp_path / "backup.json"
    target.write_text('{"old": true}', encoding="utf-8")
    before_files = set(tmp_path.iterdir())
    local_service.export_data("json", str(target))
    exported = json.loads(target.read_text(encoding="utf-8"))
    assert [thought["id"] for thought in exported["thoughts"]] == [note["id"]]
    assert set(tmp_path.iterdir()) == before_files


def test_markdown_case_collision_refuses_before_writing(local_service, tmp_path):
    for note_id in ("Note-ID", "note-id"):
        local_service.db.insert_thought(Thought(id=note_id, content="Same opening", created_at="2026-10-07"))
    destination = tmp_path / "markdown"
    with pytest.raises(ValueError, match="filenames"):
        local_service.export_data("markdown", str(destination))
    assert not destination.exists() or list(destination.iterdir()) == []
