"""Tests for the Typer CLI."""

import json
import logging
import warnings

import pytest
from typer.testing import CliRunner

from sticky.cli.app import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def reset_service(tmp_data_dir, monkeypatch):
    """Reset service singleton between tests so each test gets a fresh DB."""
    import sticky.cli.app as mod

    monkeypatch.setenv("STICKY_DATA_DIR", str(tmp_data_dir))
    monkeypatch.setenv("STICKY_OPENROUTER_API_KEY", "test-key")
    # Suppress HF Hub warnings that pollute captured CLI output
    monkeypatch.setenv("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    monkeypatch.setenv("HF_HUB_DISABLE_TELEMETRY", "1")
    monkeypatch.setenv("TOKENIZERS_PARALLELISM", "false")
    mod._service = None


def _parse_json(output: str) -> dict | list:
    """Extract JSON from CLI output, skipping non-JSON prefix/suffix lines.

    The BertModel LOAD REPORT may be printed to stdout before or after the
    JSON payload (depending on when the model is lazily initialised), so we
    use ``raw_decode`` to extract only the first complete JSON value.
    """
    # Find the first line that starts JSON ('{' or '[')
    lines = output.split("\n")
    start_idx = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            start_idx = i
            break
    json_text = "\n".join(lines[start_idx:])
    decoder = json.JSONDecoder()
    obj, _ = decoder.raw_decode(json_text)
    return obj


# ---------------------------------------------------------------------------
# add
# ---------------------------------------------------------------------------


def test_add_thought():
    result = runner.invoke(app, ["add", "test thought"])
    assert result.exit_code == 0
    assert "Captured" in result.output


def test_add_quiet():
    result = runner.invoke(app, ["add", "quiet thought", "--quiet"])
    assert result.exit_code == 0
    # Should only output the ID (a ULID)
    assert len(result.output.strip()) > 10


def test_add_json_output():
    result = runner.invoke(app, ["add", "test", "--json"])
    assert result.exit_code == 0
    data = _parse_json(result.output)
    assert "id" in data
    assert data["content"] == "test"


def test_add_with_template():
    result = runner.invoke(app, ["add", "meeting notes", "--template", "meeting"])
    assert result.exit_code == 0


def test_add_with_source():
    result = runner.invoke(app, ["add", "from api", "--source", "api"])
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


def test_add_and_search():
    runner.invoke(app, ["add", "Sarah is leaving the team"])
    result = runner.invoke(app, ["search", "Sarah"])
    assert result.exit_code == 0
    assert "Sarah" in result.output


def test_search_no_results():
    result = runner.invoke(app, ["search", "nonexistentxyzquery"])
    assert result.exit_code == 0
    assert "No results" in result.output or "0 results" in result.output or result.output.strip() != ""


def test_search_json():
    runner.invoke(app, ["add", "Python web development"])
    result = runner.invoke(app, ["search", "Python", "--json"])
    assert result.exit_code == 0
    data = _parse_json(result.output)
    assert isinstance(data, list)


def test_search_with_limit():
    runner.invoke(app, ["add", "thought one"])
    runner.invoke(app, ["add", "thought two"])
    result = runner.invoke(app, ["search", "thought", "--limit", "1"])
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def test_list():
    runner.invoke(app, ["add", "thought 1"])
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0
    assert "thought 1" in result.output


def test_list_empty():
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0
    assert "No thoughts" in result.output


def test_list_json():
    runner.invoke(app, ["add", "json list thought"])
    result = runner.invoke(app, ["list", "--json"])
    assert result.exit_code == 0
    data = _parse_json(result.output)
    assert "thoughts" in data
    assert "total" in data


# ---------------------------------------------------------------------------
# review
# ---------------------------------------------------------------------------


def test_review():
    # With a test API key, classification fails and thoughts get needs_review=True
    runner.invoke(app, ["add", "review me"])
    result = runner.invoke(app, ["review"])
    assert result.exit_code == 0


def test_review_json():
    runner.invoke(app, ["add", "review json"])
    result = runner.invoke(app, ["review", "--json"])
    assert result.exit_code == 0
    data = _parse_json(result.output)
    assert "items" in data


# ---------------------------------------------------------------------------
# classify
# ---------------------------------------------------------------------------


def test_classify():
    add_result = runner.invoke(app, ["add", "something to classify", "--json"])
    thought_id = _parse_json(add_result.output)["id"]
    result = runner.invoke(app, ["classify", thought_id, "--category", "idea"])
    assert result.exit_code == 0
    assert "Reclassified" in result.output


def test_classify_nonexistent():
    result = runner.invoke(app, ["classify", "nonexistent-id", "--category", "idea"])
    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# entities
# ---------------------------------------------------------------------------


def test_entities():
    result = runner.invoke(app, ["entities"])
    assert result.exit_code == 0


def test_entities_json():
    result = runner.invoke(app, ["entities", "--json"])
    assert result.exit_code == 0
    data = _parse_json(result.output)
    assert "entities" in data


# ---------------------------------------------------------------------------
# digest
# ---------------------------------------------------------------------------


def test_digest():
    runner.invoke(app, ["add", "digest thought"])
    result = runner.invoke(app, ["digest"])
    assert result.exit_code == 0
    assert "Digest" in result.output


def test_digest_json():
    runner.invoke(app, ["add", "digest json thought"])
    result = runner.invoke(app, ["digest", "--json"])
    assert result.exit_code == 0
    data = _parse_json(result.output)
    assert "digest" in data
    assert "period" in data


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


def test_export_json(tmp_path):
    runner.invoke(app, ["add", "exportable"])
    result = runner.invoke(
        app, ["export", "json", "--output", str(tmp_path / "out.json")]
    )
    assert result.exit_code == 0
    assert "Exported" in result.output


def test_export_markdown(tmp_path):
    runner.invoke(app, ["add", "md exportable"])
    result = runner.invoke(
        app, ["export", "markdown", "--output", str(tmp_path / "md_out")]
    )
    assert result.exit_code == 0
    assert "Exported" in result.output


def test_export_json_output(tmp_path):
    runner.invoke(app, ["add", "export json flag"])
    result = runner.invoke(
        app,
        ["export", "json", "--output", str(tmp_path / "out2.json"), "--json"],
    )
    assert result.exit_code == 0
    data = _parse_json(result.output)
    assert "count" in data


# ---------------------------------------------------------------------------
# import
# ---------------------------------------------------------------------------


def test_import_json(tmp_path):
    # First export, then import
    runner.invoke(app, ["add", "importable"])
    export_path = str(tmp_path / "import_test.json")
    runner.invoke(app, ["export", "json", "--output", export_path])
    # Import into same service (will skip duplicates)
    result = runner.invoke(app, ["import", export_path])
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------


def test_stats():
    result = runner.invoke(app, ["stats"])
    assert result.exit_code == 0
    assert "Stats" in result.output


def test_stats_json():
    result = runner.invoke(app, ["stats", "--json"])
    assert result.exit_code == 0
    data = _parse_json(result.output)
    assert "thoughts" in data


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------


def test_update():
    add_result = runner.invoke(app, ["add", "original content", "--json"])
    thought_id = _parse_json(add_result.output)["id"]
    result = runner.invoke(app, ["update", thought_id, "updated content"])
    assert result.exit_code == 0
    assert "Updated" in result.output


def test_update_nonexistent():
    result = runner.invoke(app, ["update", "nonexistent-id", "new content"])
    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


def test_delete_with_yes():
    add_result = runner.invoke(app, ["add", "to be deleted", "--json"])
    thought_id = _parse_json(add_result.output)["id"]
    result = runner.invoke(app, ["delete", thought_id, "--yes"])
    assert result.exit_code == 0
    assert "Deleted" in result.output


def test_delete_abort():
    add_result = runner.invoke(app, ["add", "keep me", "--json"])
    thought_id = _parse_json(add_result.output)["id"]
    result = runner.invoke(app, ["delete", thought_id], input="n\n")
    assert result.exit_code != 0 or "Aborted" in result.output


def test_delete_nonexistent():
    result = runner.invoke(app, ["delete", "nonexistent-id", "--yes"])
    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------


def test_config_show():
    result = runner.invoke(app, ["config"])
    assert result.exit_code == 0
    assert "Configuration" in result.output or "config" in result.output.lower()


def test_config_json():
    result = runner.invoke(app, ["config", "--json"])
    assert result.exit_code == 0
    data = _parse_json(result.output)
    assert isinstance(data, dict)


def test_config_get():
    result = runner.invoke(app, ["config", "get", "confidence_threshold"])
    assert result.exit_code == 0
    assert "confidence_threshold" in result.output


def test_config_get_unknown():
    result = runner.invoke(app, ["config", "get", "unknown_key_xyz"])
    assert result.exit_code == 1


def test_config_set():
    result = runner.invoke(app, ["config", "set", "confidence_threshold", "0.8"])
    assert result.exit_code == 0
    assert "Set" in result.output


def test_config_path():
    result = runner.invoke(app, ["config", "path"])
    assert result.exit_code == 0
    assert "config" in result.output.lower() or len(result.output.strip()) > 0


# ---------------------------------------------------------------------------
# tui
# ---------------------------------------------------------------------------


def test_tui():
    """Verify the TUI command imports correctly (actual TUI tested in test_tui.py)."""
    from sticky.tui.app import StickyApp

    assert StickyApp is not None
    assert StickyApp.TITLE == "sticky"


# ---------------------------------------------------------------------------
# privacy
# ---------------------------------------------------------------------------


def test_privacy():
    result = runner.invoke(app, ["privacy"])
    assert result.exit_code == 0
    assert "LOCAL" in result.output


def test_privacy_json():
    result = runner.invoke(app, ["privacy", "--json"])
    assert result.exit_code == 0
    data = _parse_json(result.output)
    assert "data_flow" in data


# ---------------------------------------------------------------------------
# actions
# ---------------------------------------------------------------------------


def test_actions():
    result = runner.invoke(app, ["actions"])
    assert result.exit_code == 0


def test_actions_json():
    result = runner.invoke(app, ["actions", "--json"])
    assert result.exit_code == 0
    data = _parse_json(result.output)
    assert "actions" in data


def test_actions_complete():
    # Insert an action item via service then complete via CLI
    from sticky.cli.app import get_service
    from sticky.core.models import ActionItem

    svc = get_service()
    action = ActionItem(content="Test action CLI", source_thought_id=None)
    svc.db.insert_action_item(action)

    result = runner.invoke(app, ["actions", "complete", action.id])
    assert result.exit_code == 0
    assert "Completed" in result.output


def test_actions_complete_nonexistent():
    result = runner.invoke(app, ["actions", "complete", "nonexistent-id"])
    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# related
# ---------------------------------------------------------------------------


def test_related():
    add_result = runner.invoke(app, ["add", "Python Flask web development", "--json"])
    thought_id = _parse_json(add_result.output)["id"]
    runner.invoke(app, ["add", "Django web framework for Python"])
    result = runner.invoke(app, ["related", thought_id])
    assert result.exit_code == 0


def test_related_json():
    add_result = runner.invoke(app, ["add", "related json test", "--json"])
    thought_id = _parse_json(add_result.output)["id"]
    result = runner.invoke(app, ["related", thought_id, "--json"])
    assert result.exit_code == 0
    data = _parse_json(result.output)
    assert isinstance(data, list)
