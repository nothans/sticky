"""End-to-end local capture, search, editing, and JSON recovery."""

import json
from pathlib import Path

import httpx
import pytest

from sticky.core.service import StickyService


@pytest.fixture
def service(tmp_data_dir, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Local operations must not call the cloud")
    monkeypatch.setattr(httpx.Client, "post", forbidden)
    monkeypatch.setattr(httpx.AsyncClient, "post", forbidden)
    svc = StickyService(data_dir=tmp_data_dir)
    svc.initialize()
    yield svc
    svc.db.close()


def test_full_capture_search_pipeline(service):
    note = service.capture("Sarah mentioned she's thinking about leaving the team")
    assert note["category"] is None
    assert note["confidence"] is None
    results = service.search("career changes")
    assert any(result["id"] == note["id"] for result in results)


def test_changed_fact_and_repeated_text_all_persist(service):
    notes = [service.capture(text) for text in (
        "Meeting with Sarah at 2 pm tomorrow", "Meeting with Sarah at 3 pm tomorrow",
        "Meeting with Sarah at 2 pm tomorrow", "Call Steve on Monday", "Call Steve on Tuesday",
    )]
    assert len({note["id"] for note in notes}) == 5
    assert service.stats()["thoughts"]["total"] == 5
    assert {result["id"] for result in service.search("Sarah", limit=5)}.issuperset(
        note["id"] for note in notes[:3]
    )


def test_capture_edit_search_delete_pipeline(service):
    note = service.capture("old content about databases", thread="learning", source_url="https://example.org")
    service.classify_thought(note["id"], "reference")
    edited = service.update(note["id"], "new content about machine learning algorithms")
    assert edited["category"] is None
    assert edited["metadata"]["thread"] == "learning"
    assert edited["source_url"] == "https://example.org"
    assert any(result["id"] == note["id"] for result in service.search("machine learning"))
    service.delete(note["id"])
    assert not any(result["id"] == note["id"] for result in service.search("machine learning"))


def test_json_restore_into_fresh_database(service, tmp_path):
    first = service.capture("thought one about testing", thread="testing", source_url="https://example.org")
    service.classify_thought(first["id"], "reference")
    second = service.capture("thought two about integration")
    path = tmp_path / "export.json"
    assert service.export_data("json", str(path))["count"] == 2
    assert len(json.loads(path.read_text(encoding="utf-8"))["thoughts"]) == 2
    restored = StickyService(data_dir=tmp_path / "restored")
    restored.initialize()
    try:
        receipt = restored.import_data(str(path))
        assert receipt["imported"] == 2 and not receipt["errors"]
        record = restored.get_thought(first["id"])
        assert record["metadata"]["thread"] == "testing"
        assert record["source_url"] == "https://example.org"
        assert record["created_at"] == first["created_at"]
        assert record["category"] == "reference"
        assert any(result["id"] == second["id"] for result in restored.search("integration"))
        assert restored.import_data(str(path))["skipped"] == 2
    finally:
        restored.db.close()


def test_markdown_export_preserves_one_file_per_note(service, tmp_path):
    notes = [service.capture("same repeated content") for _ in range(2)]
    directory = tmp_path / "markdown"
    receipt = service.export_data("markdown", str(directory))
    assert receipt["count"] == 2
    files = [path for path in directory.glob("*.md") if path.name != "_index.md"]
    assert len(files) == 2
    for note in notes:
        assert any(note["id"] in path.read_text(encoding="utf-8") for path in files)


def test_digest_explicit_request_only(service, monkeypatch):
    note = service.capture("Meeting about Q3 roadmap")
    calls = []
    def response(self, url, **kwargs):
        calls.append(kwargs["json"])
        return httpx.Response(200, json={"choices": [{"message": {"content": "Q3 roadmap discussed [1]."}}]},
                              request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx.Client, "post", response)
    result = service.digest()
    assert len(calls) == 1
    assert note["content"] in calls[0]["messages"][1]["content"]
    assert result["thought_ids"] == [note["id"]]
    assert result["thought_count"] == 1
    assert service.get_thought(note["id"])["content"] == note["content"]


def test_missing_key_digest_never_calls_network(service):
    service.capture("keep this locally")
    service.config.openrouter_api_key = ""
    with pytest.raises(ValueError, match="API key"):
        service.digest()
    assert service.stats()["thoughts"]["total"] == 1


def test_privacy_matches_active_flow(service):
    info = service.privacy_info()
    assert info["data_flow"]["capture"] == "LOCAL"
    assert info["data_flow"]["search"] == "LOCAL"
    assert info["data_flow"]["storage"] == "LOCAL"
    assert info["data_flow"]["digest"] == "CLOUD_ON_REQUEST"
    assert "retention policies vary" in info["description"]


def test_configuration_write_is_validated_and_masks_keys(service):
    assert "confidence_threshold" not in service.get_config_display()
    result = service.set_config_value("default_search_limit", "5")
    assert result["previous"] == 10
    assert result["new"] == 5
    with pytest.raises(ValueError):
        service.set_config_value("default_list_limit", "0")
    result = service.set_config_value("openrouter_api_key", "dummy-new-secret")
    assert result["new"] == "configured"
    assert "dummy-new-secret" not in str(result)


def test_saved_setting_applies_next_session(service):
    previous = service.config.default_search_limit
    service.set_config_value("default_search_limit", "5")
    assert service.config.default_search_limit == previous
    next_session = StickyService(data_dir=service.config.data_dir)
    try:
        assert next_session.config.default_search_limit == 5
        assert next_session.embedding_engine.model_name == "all-MiniLM-L6-v2"
    finally:
        next_session.db.close()
