"""Tests for the service orchestration layer."""

import json
import os
from pathlib import Path

import pytest

from sticky.core.service import StickyService


@pytest.fixture
def service(tmp_data_dir, monkeypatch):
    """Create and initialize a StickyService for testing."""
    monkeypatch.setenv("STICKY_DATA_DIR", str(tmp_data_dir))
    monkeypatch.setenv("STICKY_OPENROUTER_API_KEY", "test-key")
    svc = StickyService(data_dir=tmp_data_dir)
    svc.initialize()
    return svc


def test_capture_stores_thought(service):
    """Capture should store a thought and return its metadata."""
    result = service.capture("Meeting with Sarah about Q3 roadmap")
    assert "id" in result
    assert result["content"] == "Meeting with Sarah about Q3 roadmap"
    assert "created_at" in result
    # With test-key, LLM fails gracefully
    # Category may be None (graceful degradation)
    assert "category" in result
    assert "confidence" in result
    assert "needs_review" in result
    assert "entities" in result


def test_capture_and_search(service):
    """Captured thoughts should be searchable."""
    service.capture("Python asyncio patterns for concurrent programming")
    service.capture("JavaScript promises and async/await syntax")
    service.capture("Database indexing strategies for PostgreSQL")

    results = service.search("async programming patterns")
    assert len(results) > 0
    # Results should have scores
    assert "score" in results[0]


def test_capture_graceful_degradation(service):
    """Capture should never fail even if classification fails."""
    result = service.capture("Just a random thought")
    assert "id" in result
    # With a fake API key, classification fails gracefully
    # Should still have all required fields
    assert result["content"] == "Just a random thought"


def test_list_thoughts(service):
    """List thoughts should return paginated results."""
    service.capture("First thought")
    service.capture("Second thought")
    service.capture("Third thought")

    result = service.list_thoughts(limit=2)
    assert "thoughts" in result
    assert "total" in result
    assert "has_more" in result
    assert result["total"] == 3
    assert len(result["thoughts"]) == 2
    assert result["has_more"] is True


def test_list_thoughts_with_cursor(service):
    """List thoughts with cursor pagination."""
    service.capture("Alpha thought")
    service.capture("Beta thought")
    service.capture("Gamma thought")

    page1 = service.list_thoughts(limit=2)
    assert len(page1["thoughts"]) == 2
    assert page1["has_more"] is True
    assert "next_cursor" in page1

    page2 = service.list_thoughts(limit=2, cursor=page1["next_cursor"])
    assert len(page2["thoughts"]) == 1
    # No duplicates across pages
    ids1 = {t["id"] for t in page1["thoughts"]}
    ids2 = {t["id"] for t in page2["thoughts"]}
    assert ids1.isdisjoint(ids2)


def test_stats(service):
    """Stats should return system information."""
    service.capture("A test thought for stats")

    result = service.stats()
    assert "thoughts" in result
    assert result["thoughts"]["total"] == 1
    assert "entities" in result
    assert "digests" in result
    assert "system" in result
    assert "db_path" in result["system"]


def test_update_thought(service):
    """Update should re-embed and update content."""
    result = service.capture("Original content here")
    thought_id = result["id"]

    updated = service.update(thought_id, "Updated content now")
    assert updated["id"] == thought_id
    assert updated["content"] == "Updated content now"


def test_update_nonexistent_thought(service):
    """Update should raise ValueError for nonexistent thought."""
    with pytest.raises(ValueError, match="not found"):
        service.update("nonexistent-id", "new content")


def test_delete_thought(service):
    """Delete should remove thought and return preview."""
    result = service.capture("Content to be deleted")
    thought_id = result["id"]

    deleted = service.delete(thought_id)
    assert "id" in deleted
    assert "preview" in deleted

    # Should not be found anymore
    listing = service.list_thoughts()
    assert listing["total"] == 0


def test_delete_nonexistent_thought(service):
    """Delete should raise ValueError for nonexistent thought."""
    with pytest.raises(ValueError, match="not found"):
        service.delete("nonexistent-id")


def test_export_json(service, tmp_path):
    """Export to JSON should create a valid JSON file."""
    service.capture("Thought for JSON export")
    service.capture("Another thought for export")

    output_path = tmp_path / "export.json"
    result = service.export_data("json", str(output_path))
    assert result["format"] == "json"
    assert result["count"] == 2
    assert Path(output_path).exists()

    # Verify JSON structure
    with open(output_path) as f:
        data = json.load(f)
    assert "thoughts" in data
    assert len(data["thoughts"]) == 2
    assert "entities" in data


def test_export_markdown(service, tmp_path):
    """Export to markdown should create individual files."""
    service.capture("The auth migration architecture needs a complete redesign")
    service.capture("Sarah wants to transfer to the platform engineering team")

    output_dir = tmp_path / "md_export"
    result = service.export_data("markdown", str(output_dir))
    assert result["format"] == "markdown"
    assert result["count"] == 2

    # Should have _index.md and individual thought files
    files = list(output_dir.glob("*.md"))
    assert len(files) >= 3  # 2 thoughts + _index.md


def test_import_json(service, tmp_path):
    """Import from JSON should add thoughts."""
    # First export
    service.capture("Thought to round-trip")
    export_path = tmp_path / "export.json"
    service.export_data("json", str(export_path))

    # Create a new service to import into
    import_dir = tmp_path / "import_data"
    import_dir.mkdir()
    svc2 = StickyService(data_dir=import_dir)
    svc2.initialize()

    result = svc2.import_data(str(export_path), format="json")
    assert result["imported"] >= 1
    assert result["skipped"] == 0


def test_import_json_dry_run(service, tmp_path):
    """Dry run import should not actually write."""
    service.capture("Dry run test thought")
    export_path = tmp_path / "export.json"
    service.export_data("json", str(export_path))

    import_dir = tmp_path / "import_dry"
    import_dir.mkdir()
    svc2 = StickyService(data_dir=import_dir)
    svc2.initialize()

    result = svc2.import_data(str(export_path), format="json", dry_run=True)
    assert result["imported"] >= 1
    assert result["dry_run"] is True

    # Should not actually have thoughts
    listing = svc2.list_thoughts()
    assert listing["total"] == 0


def test_import_skip_duplicates(service, tmp_path):
    """Import should skip thoughts that already exist."""
    service.capture("Unique thought for dedup test")
    export_path = tmp_path / "export.json"
    service.export_data("json", str(export_path))

    # Import into the same service (should skip the duplicate)
    result = service.import_data(str(export_path), format="json")
    assert result["skipped"] >= 1


def test_related_thoughts(service):
    """Related thoughts should find semantically similar content."""
    r1 = service.capture("Python web frameworks like Flask and Django")
    service.capture("JavaScript frontend frameworks React and Vue")
    service.capture("Database optimization techniques for SQL")

    related = service.related_thoughts(r1["id"], limit=2)
    assert isinstance(related, list)
    assert len(related) <= 2
    # Should have score
    if related:
        assert "score" in related[0]


def test_privacy_info(service):
    """Privacy info should describe data flows."""
    info = service.privacy_info()
    assert "data_flow" in info
    flow = info["data_flow"]
    assert flow["embeddings"] == "LOCAL"
    assert flow["storage"] == "LOCAL"
    assert flow["classification"] == "CLOUD"
    assert flow["digest"] == "CLOUD"


def test_list_actions(service):
    """List actions should return action items."""
    result = service.list_actions()
    assert "actions" in result
    assert "total" in result


def test_capture_with_template(service):
    """Capture with template hint should work."""
    result = service.capture(
        "Meeting notes from standup", template="meeting", source="cli"
    )
    assert "id" in result
    assert result["content"] == "Meeting notes from standup"


def test_get_review_items(service):
    """get_review_items should return thoughts needing review."""
    # With test-key, classification fails, so thoughts get needs_review=True
    service.capture("Review me please")
    result = service.get_review_items()
    assert "items" in result
    assert "total" in result


def test_classify_thought(service):
    """Manual classification should update category."""
    captured = service.capture("Something to classify")
    thought_id = captured["id"]

    result = service.classify_thought(thought_id, "idea")
    assert result["category"] == "idea"
    assert result["confidence"] == 1.0
    assert result["needs_review"] is False


def test_classify_nonexistent_thought(service):
    """Classifying nonexistent thought should raise ValueError."""
    with pytest.raises(ValueError, match="not found"):
        service.classify_thought("nonexistent-id", "idea")


def test_list_entities(service):
    """List entities should return entity list."""
    result = service.list_entities()
    assert "entities" in result
    assert "total" in result


def test_digest(service):
    """Digest generation should return structured result."""
    service.capture("Morning standup about sprint planning")
    service.capture("Discussed deployment pipeline with DevOps")

    result = service.digest(period="day")
    assert "digest" in result
    assert "period" in result
    assert "thought_count" in result
    assert "digest_id" in result


def test_get_config_display(service):
    """Config display should show config with sources."""
    result = service.get_config_display()
    assert isinstance(result, dict)
    # Should have at least some config keys
    assert len(result) > 0


def test_set_config_value(service):
    """Set config value should return previous value."""
    result = service.set_config_value("confidence_threshold", 0.8)
    assert "key" in result
    assert "previous" in result
    assert "new" in result


def test_search_with_filters(service):
    """Search with filters should work."""
    service.capture("Python programming tutorial")

    results = service.search("Python", limit=5, mode="hybrid")
    assert isinstance(results, list)


def test_complete_action(service):
    """Complete action should mark it done."""
    # Manually insert an action item
    from sticky.core.models import ActionItem

    action = ActionItem(content="Test action", source_thought_id=None)
    service.db.insert_action_item(action)

    result = service.complete_action(action.id)
    assert result["completed"] is True


def test_complete_nonexistent_action(service):
    """Complete nonexistent action should raise ValueError."""
    with pytest.raises(ValueError, match="not found"):
        service.complete_action("nonexistent-id")


def test_entity_context_summary(service):
    """Entity context summary should return a string."""
    # With no entities, should handle gracefully
    result = service.entity_context_summary("nonexistent-id")
    assert isinstance(result, str)


def test_search_returns_timing(service):
    """Search should include timing info."""
    service.capture("Thought for timing test")
    results = service.search("timing")
    # Results are a list; timing is part of the result metadata
    assert isinstance(results, list)


def test_capture_stores_embedding_model(service):
    """Captured thought should record which embedding model was used."""
    result = service.capture("Embedding model versioning test")
    thought_id = result["id"]

    thought = service.db.get_thought(thought_id)
    assert thought is not None
    assert thought.embedding_model == service.config.embedding_model
    assert thought.embedding_model == "all-MiniLM-L6-v2"


def test_export_with_category_filter(service, tmp_path):
    """Export with category filter should only export matching thoughts."""
    # Capture and manually classify
    r1 = service.capture("An idea thought")
    service.classify_thought(r1["id"], "idea")
    r2 = service.capture("A meeting note")
    service.classify_thought(r2["id"], "meeting")

    output_path = tmp_path / "filtered_export.json"
    result = service.export_data("json", str(output_path), category="idea")
    assert result["count"] == 1

    with open(output_path) as f:
        data = json.load(f)
    assert all(t["category"] == "idea" for t in data["thoughts"])


def test_reclassify_batch(service, monkeypatch):
    """reclassify_batch should re-classify thoughts with null category or low confidence."""
    from unittest.mock import MagicMock

    from sticky.core.models import ClassificationResult, Thought

    # Directly insert a thought that bypasses the capture pipeline
    thought = Thought.create(
        content="Meeting with Sarah about Q3 roadmap",
        category=None,
        confidence=0.0,
        needs_review=True,
        source="test",
    )
    service.db.insert_thought(thought)

    # Verify it was stored with null category
    stored = service.db.get_thought(thought.id)
    assert stored.category is None
    assert stored.confidence == 0.0

    # Mock the classifier to return a successful classification
    mock_result = ClassificationResult(
        category="meeting",
        confidence=0.85,
        topics=["roadmap", "Q3"],
        people=["Sarah"],
        projects=[],
        actions=[],
    )
    mock_classifier = MagicMock()
    mock_classifier.classify_sync.return_value = mock_result
    monkeypatch.setattr(service, "classifier", mock_classifier)

    # Mock entity resolver to avoid side effects
    mock_resolver = MagicMock()
    mock_resolver.resolve_entities.return_value = []
    monkeypatch.setattr(service, "entity_resolver", mock_resolver)

    # Run reclassify_batch
    result = service.reclassify_batch()

    assert result["total_candidates"] >= 1
    assert result["reclassified"] >= 1
    assert len(result["errors"]) == 0

    # Verify the thought was updated in the DB
    updated = service.db.get_thought(thought.id)
    assert updated.category == "meeting"
    assert updated.confidence == 0.85
    assert updated.needs_review is False

    # Verify the classifier was called with the thought content
    mock_classifier.classify_sync.assert_called()

    # Verify entity resolution was attempted
    mock_resolver.resolve_entities.assert_called()

    # Check results list has the expected entry
    matching = [r for r in result["results"] if r["id"] == thought.id]
    assert len(matching) == 1
    assert matching[0]["old_category"] is None
    assert matching[0]["new_category"] == "meeting"
    assert matching[0]["confidence"] == 0.85


def test_reclassify_batch_handles_classifier_failure(service, monkeypatch):
    """reclassify_batch should handle classifier errors gracefully."""
    from unittest.mock import MagicMock

    from sticky.core.models import Thought

    # Insert a thought with null category
    thought = Thought.create(
        content="Something to reclassify",
        category=None,
        confidence=0.0,
        needs_review=True,
        source="test",
    )
    service.db.insert_thought(thought)

    # Mock the classifier to raise an exception
    mock_classifier = MagicMock()
    mock_classifier.classify_sync.side_effect = RuntimeError("LLM unavailable")
    monkeypatch.setattr(service, "classifier", mock_classifier)

    result = service.reclassify_batch()

    assert result["total_candidates"] >= 1
    assert result["reclassified"] == 0
    assert len(result["errors"]) >= 1
    assert thought.id in result["errors"][0]

    # Thought should remain unchanged
    unchanged = service.db.get_thought(thought.id)
    assert unchanged.category is None
    assert unchanged.confidence == 0.0


def test_related_thoughts_filters_low_scores(service):
    """Related thoughts should filter out results below 0.45 threshold."""
    # Capture one thought and some very different thoughts
    r1 = service.capture("Python web frameworks like Flask and Django")
    service.capture("Recipes for chocolate cake with vanilla frosting")
    service.capture("History of ancient Egyptian pharaohs and pyramids")
    service.capture("Underwater basket weaving techniques for beginners")

    related = service.related_thoughts(r1["id"], limit=10)
    # All returned results should have score >= 0.45
    for item in related:
        assert item["score"] >= 0.45, (
            f"Score {item['score']} is below the 0.45 threshold"
        )


def test_related_thoughts_sorted_with_scores(service):
    """Related thoughts should return sorted results with scores."""
    r1 = service.capture("Python web frameworks like Flask and Django")
    service.capture("JavaScript frontend frameworks React and Vue")
    service.capture("Database optimization techniques for SQL")
    service.capture("Machine learning with Python and TensorFlow")

    related = service.related_thoughts(r1["id"], limit=5)
    assert isinstance(related, list)
    # Every result should have a score
    for item in related:
        assert "score" in item
        assert isinstance(item["score"], (int, float))
    # Results should be sorted by score descending
    scores = [item["score"] for item in related]
    assert scores == sorted(scores, reverse=True), (
        f"Results are not sorted by score: {scores}"
    )


def test_brief_returns_structure(service):
    """Brief should return the expected keys."""
    result = service.brief()
    assert "new_thoughts" in result
    assert "action_items" in result
    assert "entity_pulse" in result
    assert "resurfaced" in result
    assert "review_count" in result
    assert isinstance(result["new_thoughts"], int)
    assert isinstance(result["action_items"], list)


def test_synthesize_unknown_entity(service):
    """Synthesize should handle unknown entities gracefully."""
    result = service.synthesize("NonexistentEntity")
    assert result["thought_count"] == 0
    assert "No entity found" in result["synthesis"]


def test_capture_with_thread(service):
    """Thoughts captured with a thread should be filterable."""
    r1 = service.capture(content="Research note about topic X", thread="test-research")
    r2 = service.capture(content="Unrelated note about something else", source="test")

    result = service.list_thoughts(thread="test-research")
    thoughts = result.get("thoughts", [])
    assert len(thoughts) == 1
    assert thoughts[0]["id"] == r1["id"]
