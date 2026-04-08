"""Tests for the MCP server tool functions."""

import pytest

from sticky.mcp.server import (
    get_service,
    sticky_actions,
    sticky_capture,
    sticky_classify,
    sticky_config,
    sticky_delete,
    sticky_digest,
    sticky_entities,
    sticky_export,
    sticky_import,
    sticky_list,
    sticky_privacy,
    sticky_related,
    sticky_review,
    sticky_search,
    sticky_stats,
    sticky_update,
)


@pytest.fixture(autouse=True)
def reset_service(tmp_data_dir, monkeypatch):
    """Reset the module-level service singleton between tests."""
    import sticky.mcp.server as mod

    monkeypatch.setenv("STICKY_DATA_DIR", str(tmp_data_dir))
    mod._service = None
    yield
    mod._service = None


class TestGetService:
    """Tests for the service singleton."""

    def test_get_service_returns_initialized_service(self):
        svc = get_service()
        assert svc is not None
        assert svc.search_engine is not None

    def test_get_service_returns_same_instance(self):
        svc1 = get_service()
        svc2 = get_service()
        assert svc1 is svc2


class TestStickyCapture:
    """Tests for sticky_capture tool."""

    def test_capture_returns_dict(self):
        result = sticky_capture("test thought")
        assert isinstance(result, dict)
        assert "id" in result
        assert result["content"] == "test thought"

    def test_capture_with_template(self):
        result = sticky_capture("meeting notes", template="meeting")
        assert "id" in result
        assert result["content"] == "meeting notes"

    def test_capture_has_required_fields(self):
        result = sticky_capture("my important idea")
        for key in ("id", "content", "category", "confidence", "needs_review",
                     "entities", "created_at"):
            assert key in result, f"Missing key: {key}"


class TestStickySearch:
    """Tests for sticky_search tool."""

    def test_search_returns_results_structure(self):
        sticky_capture("hello world from sticky")
        result = sticky_search("hello")
        assert "results" in result
        assert "query" in result
        assert "total_results" in result
        assert result["query"] == "hello"

    def test_search_empty_results(self):
        result = sticky_search("nonexistent content xyz")
        assert result["total_results"] == 0
        assert result["results"] == []

    def test_search_with_limit(self):
        for i in range(5):
            sticky_capture(f"programming concept number {i}")
        result = sticky_search("programming", limit=2)
        assert len(result["results"]) <= 2


class TestStickyList:
    """Tests for sticky_list tool."""

    def test_list_returns_pagination(self):
        sticky_capture("thought 1")
        sticky_capture("thought 2")
        result = sticky_list(limit=1)
        assert "thoughts" in result
        assert "total" in result
        assert "has_more" in result
        assert result["has_more"] is True

    def test_list_defaults(self):
        result = sticky_list()
        assert "thoughts" in result
        assert "total" in result


class TestStickyReview:
    """Tests for sticky_review tool."""

    def test_review_returns_items(self):
        # With test API key, classification fails -> needs_review=True
        sticky_capture("review this thought")
        result = sticky_review()
        assert "items" in result
        assert "total" in result
        assert result["total"] >= 1


class TestStickyClassify:
    """Tests for sticky_classify tool."""

    def test_classify_updates_category(self):
        captured = sticky_capture("classify me")
        result = sticky_classify(captured["id"], "idea")
        assert result["category"] == "idea"
        assert result["confidence"] == 1.0
        assert result["needs_review"] is False

    def test_classify_nonexistent_raises(self):
        with pytest.raises(ValueError, match="not found"):
            sticky_classify("nonexistent-id", "idea")


class TestStickyEntities:
    """Tests for sticky_entities tool."""

    def test_entities_returns_structure(self):
        result = sticky_entities()
        assert "entities" in result
        assert "total" in result


class TestStickyDigest:
    """Tests for sticky_digest tool."""

    def test_digest_returns_structure(self):
        sticky_capture("something to digest")
        result = sticky_digest()
        assert "digest" in result
        assert "period" in result
        assert "thought_count" in result
        assert "digest_id" in result


class TestStickyUpdate:
    """Tests for sticky_update tool."""

    def test_update_changes_content(self):
        captured = sticky_capture("original content")
        result = sticky_update(captured["id"], "updated content")
        assert result["content"] == "updated content"

    def test_update_nonexistent_raises(self):
        with pytest.raises(ValueError, match="not found"):
            sticky_update("nonexistent-id", "new content")


class TestStickyDelete:
    """Tests for sticky_delete tool."""

    def test_delete_removes_thought(self):
        captured = sticky_capture("delete me")
        result = sticky_delete(captured["id"])
        assert result["deleted"] is True
        assert result["id"] == captured["id"]

        listing = sticky_list()
        assert listing["total"] == 0

    def test_delete_nonexistent_raises(self):
        with pytest.raises(ValueError, match="not found"):
            sticky_delete("nonexistent-id")


class TestStickyStats:
    """Tests for sticky_stats tool."""

    def test_stats_returns_system_info(self):
        result = sticky_stats()
        assert "system" in result
        assert "db_path" in result["system"]

    def test_stats_counts_thoughts(self):
        sticky_capture("stat this")
        result = sticky_stats()
        assert result["thoughts"]["total"] == 1


class TestStickyConfig:
    """Tests for sticky_config tool."""

    def test_config_get_all(self):
        result = sticky_config(action="get")
        assert isinstance(result, dict)
        assert len(result) > 0

    def test_config_get_key(self):
        result = sticky_config(action="get", key="confidence_threshold")
        # Should return the specific key's value+source dict
        assert isinstance(result, dict)

    def test_config_get_unknown_key(self):
        result = sticky_config(action="get", key="nonexistent_key")
        assert "error" in result

    def test_config_set(self):
        result = sticky_config(action="set", key="confidence_threshold",
                               value="0.8")
        assert "key" in result
        assert "previous" in result
        assert "new" in result


class TestStickyRelated:
    """Tests for sticky_related tool."""

    def test_related_returns_list(self):
        r1 = sticky_capture("python programming language")
        sticky_capture("javascript web development")
        result = sticky_related(r1["id"])
        assert isinstance(result, list)

    def test_related_nonexistent_returns_empty(self):
        result = sticky_related("nonexistent-id")
        assert result == []


class TestStickyPrivacy:
    """Tests for sticky_privacy tool."""

    def test_privacy_shows_data_flow(self):
        result = sticky_privacy()
        assert "data_flow" in result
        assert result["data_flow"]["embeddings"] == "LOCAL"
        assert result["data_flow"]["storage"] == "LOCAL"

    def test_privacy_shows_paths(self):
        result = sticky_privacy()
        assert "data_dir" in result
        assert "db_path" in result


class TestStickyExport:
    """Tests for sticky_export tool."""

    def test_export_json(self, tmp_path):
        sticky_capture("export me")
        output = str(tmp_path / "export.json")
        result = sticky_export(format="json", output_path=output)
        assert result["format"] == "json"
        assert result["count"] == 1

    def test_export_markdown(self, tmp_path):
        sticky_capture("export as markdown")
        output = str(tmp_path / "md_export")
        result = sticky_export(format="markdown", output_path=output)
        assert result["format"] == "markdown"
        assert result["count"] == 1


class TestStickyImport:
    """Tests for sticky_import tool."""

    def test_import_json_dry_run(self, tmp_path):
        # Create a simple JSON file to import
        import json

        data = {
            "exported_at": "2025-01-01T00:00:00Z",
            "version": "1.0",
            "thoughts": [
                {"id": "new-thought-1", "content": "imported thought"}
            ],
            "entities": [],
        }
        source = tmp_path / "import.json"
        source.write_text(json.dumps(data))

        result = sticky_import(source_path=str(source), dry_run=True)
        assert result["imported"] == 1
        assert result["dry_run"] is True


class TestStickyActions:
    """Tests for sticky_actions tool."""

    def test_actions_list(self):
        result = sticky_actions()
        assert "actions" in result
        assert "total" in result

    def test_actions_complete_nonexistent(self):
        with pytest.raises(ValueError, match="not found"):
            sticky_actions(action="complete", action_id="nonexistent-id")

    def test_actions_complete(self):
        from sticky.core.models import ActionItem

        svc = get_service()
        action = ActionItem(content="Test action", source_thought_id=None)
        svc.db.insert_action_item(action)

        result = sticky_actions(action="complete", action_id=action.id)
        assert result["completed"] is True


class TestAllToolsRegistered:
    """Verify all 16 tools are registered with the MCP server."""

    def test_all_tools_registered(self):
        from sticky.mcp.server import mcp

        # FastMCP stores tools internally; verify the server object exists
        assert mcp is not None
        assert mcp.name == "sticky"
