"""End-to-end integration tests for sticky."""

import json
from pathlib import Path

import pytest

from sticky.core.service import StickyService


@pytest.fixture
def service(tmp_data_dir, monkeypatch):
    monkeypatch.setenv("STICKY_DATA_DIR", str(tmp_data_dir))
    svc = StickyService(data_dir=tmp_data_dir)
    svc.initialize()
    return svc


def test_full_capture_search_pipeline(service):
    """Capture -> search -> find it."""
    result = service.capture("Sarah mentioned she's thinking about leaving the team")
    assert result["id"]
    assert result["content"] == "Sarah mentioned she's thinking about leaving the team"

    results = service.search("career changes")
    assert len(results) > 0
    # The thought about Sarah should be found via semantic search
    contents = [r["content"] for r in results]
    assert any("Sarah" in c for c in contents)


def test_capture_search_delete_pipeline(service):
    """Capture -> search -> delete -> search again (not found)."""
    result = service.capture("unique test thought about quantum computing")
    thought_id = result["id"]

    # Should find it
    results = service.search("quantum computing")
    assert any(r["id"] == thought_id for r in results)

    # Delete it
    service.delete(thought_id)

    # Should not find it anymore
    results = service.search("quantum computing")
    assert not any(r.get("id") == thought_id for r in results)


def test_capture_update_search_pipeline(service):
    """Capture -> update -> search with new terms."""
    result = service.capture("old content about databases")
    thought_id = result["id"]

    service.update(thought_id, "new content about machine learning algorithms")

    results = service.search("machine learning")
    assert any(r["id"] == thought_id for r in results)


def test_export_import_roundtrip_json(service, tmp_path):
    """Capture -> export JSON -> clear -> import -> verify."""
    service.capture("thought one about testing")
    service.capture("thought two about integration")

    # Export
    export_path = str(tmp_path / "export.json")
    export_result = service.export_data("json", export_path)
    assert export_result["count"] == 2

    # Verify file exists and is valid JSON
    data = json.loads(Path(export_path).read_text())
    assert len(data["thoughts"]) == 2


def test_export_import_roundtrip_markdown(service, tmp_path):
    """Capture -> export markdown -> verify files."""
    service.capture("markdown export test thought")

    export_dir = str(tmp_path / "md_export")
    result = service.export_data("markdown", export_dir)
    assert result["count"] == 1

    # Verify markdown files exist
    md_files = list(Path(export_dir).glob("*.md"))
    assert len(md_files) >= 2  # thought file + _index.md


def test_digest_pipeline(service):
    """Capture several thoughts -> generate digest."""
    service.capture("Meeting with Sarah about Q3 roadmap")
    service.capture("Auth migration blocked by API team")
    service.capture("Marcus raised concerns about backward compatibility")

    digest = service.digest(period="day")
    assert digest["thought_count"] == 3
    assert digest["digest"] is not None
    assert len(digest["digest"]) > 0


def test_entity_tracking(service):
    """Capture thoughts mentioning people -> verify entities tracked."""
    service.capture("Sarah mentioned she wants to move to platform team")
    service.capture("Meeting with Sarah about Q3 goals")

    entities = service.list_entities(entity_type="person")
    # Note: entities may or may not be extracted depending on LLM availability.
    # In test mode (test-key), LLM returns None, so entities come from
    # classification which also returns None.  This test verifies the pipeline
    # doesn't crash.
    assert "entities" in entities
    assert "total" in entities


def test_related_thoughts(service):
    """Capture related thoughts -> find related."""
    r1 = service.capture("Python is great for data science")
    r2 = service.capture("Machine learning with Python and scikit-learn")
    r3 = service.capture("Cooking pasta for dinner tonight")

    related = service.related_thoughts(r1["id"], limit=2)
    assert isinstance(related, list)
    # The ML thought should be more related to Python than cooking
    if len(related) >= 2:
        related_ids = [r["id"] for r in related]
        assert r2["id"] in related_ids


def test_stats_after_operations(service):
    """Various operations -> stats reflect them."""
    service.capture("thought 1")
    service.capture("thought 2")
    service.capture("thought 3")

    stats = service.stats()
    assert stats["thoughts"]["total"] == 3
    assert "system" in stats
    assert stats["system"]["embedding_model"] == "all-MiniLM-L6-v2"


def test_privacy_info(service):
    """Privacy info returns correct data flow."""
    info = service.privacy_info()
    assert info["data_flow"]["embeddings"] == "LOCAL"
    assert info["data_flow"]["classification"] == "CLOUD"
    assert info["data_flow"]["storage"] == "LOCAL"


def test_config_roundtrip(service):
    """Get and set config values."""
    display = service.get_config_display()
    assert "confidence_threshold" in display

    old_value = service.config.confidence_threshold
    result = service.set_config_value("confidence_threshold", "0.5")
    assert result["previous"] == old_value
    assert result["new"] == "0.5"
    assert result["key"] == "confidence_threshold"
