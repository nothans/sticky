"""Exercise the retained CLI against isolated storage and local embeddings."""

import json
import struct

import httpx
import pytest
from typer.main import get_command
from typer.testing import CliRunner

from sticky.cli.app import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def local_service(monkeypatch):
    import sticky.cli.app as cli
    from sticky.core.embeddings import EmbeddingEngine

    monkeypatch.setattr(EmbeddingEngine, "embed", lambda self, text: struct.pack("384f", *([1.0] + [0.0] * 383)))
    monkeypatch.setattr(cli, "_service", None)
    def forbidden(*args, **kwargs):
        raise AssertionError("No cloud request expected")
    monkeypatch.setattr(httpx.Client, "post", forbidden)
    monkeypatch.setattr(httpx.AsyncClient, "post", forbidden)
    yield
    if cli._service is not None:
        cli._service.db.close()
    cli._service = None


def invoke_json(*args):
    result = runner.invoke(app, [*args, "--json"])
    assert result.exit_code == 0, result.output or repr(result.exception)
    return json.loads(result.output)


def test_command_surface_is_small():
    command = get_command(app)
    assert set(command.commands) == {
        "add", "search", "list", "get", "classify", "update", "delete", "export", "import",
        "digest", "stats", "privacy", "config", "setup", "tui", "mcp-serve",
    }
    for name in ("search", "list", "export"):
        params = {option.name for option in command.commands[name].params}
        assert not {"entity", "needs_review", "mode"}.intersection(params)
    assert "template" not in {option.name for option in command.commands["add"].params}
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "schedule" not in result.output


def test_capture_read_edit_categorize_and_delete():
    first = invoke_json("add", "Meeting at 2 pm", "--url", "https://example.org", "--thread", "work")
    second = invoke_json("add", "Meeting at 3 pm")
    repeated = invoke_json("add", "Meeting at 2 pm")
    assert len({first["id"], second["id"], repeated["id"]}) == 3
    assert first["category"] is None
    assert "entities" not in first
    read = invoke_json("get", first["id"])
    assert read["source_url"] == "https://example.org"
    assert read["metadata"]["thread"] == "work"
    categorized = invoke_json("classify", first["id"], "--category", "meeting")
    assert categorized["category"] == "meeting"
    edited = invoke_json("update", first["id"], "Meeting at 4 pm")
    assert edited["content"] == "Meeting at 4 pm"
    assert edited["category"] is None
    assert edited["metadata"]["thread"] == "work"
    assert invoke_json("delete", first["id"], "--yes")["deleted"]
    missing = runner.invoke(app, ["get", first["id"]])
    assert missing.exit_code == 1


def test_search_filters_list_pagination_and_configured_defaults():
    from sticky.cli.app import get_service
    get_service().config.default_search_limit = 1
    get_service().config.default_list_limit = 1
    first = invoke_json("add", "quantum computing notes", "--thread", "science")
    invoke_json("classify", first["id"], "--category", "reference")
    invoke_json("add", "quantum mechanics")
    assert len(invoke_json("search", "quantum")) == 1
    assert invoke_json("search", "quantum", "--category", "reference")[0]["id"] == first["id"]
    listing = invoke_json("list")
    assert len(listing["thoughts"]) == 1 and listing["has_more"]
    next_page = invoke_json("list", "--cursor", listing["next_cursor"])
    assert not next_page["has_more"]
    assert invoke_json("list", "--thread", "science")["thoughts"][0]["id"] == first["id"]


def test_export_and_idempotent_json_restore(tmp_path):
    original = invoke_json("add", "recovery note")
    target = tmp_path / "notes.json"
    assert invoke_json("export", "json", "--output", str(target))["count"] == 1
    assert invoke_json("import", str(target))["skipped"] == 1
    assert invoke_json("delete", original["id"], "--yes")["deleted"]
    assert invoke_json("import", str(target))["imported"] == 1
    assert invoke_json("get", original["id"])["content"] == "recovery note"
    markdown = tmp_path / "markdown"
    assert invoke_json("export", "markdown", "--output", str(markdown))["count"] == 1


def test_repeated_markdown_export_reports_refusal_without_traceback(tmp_path):
    invoke_json("add", "keep the first export")
    target = tmp_path / "markdown"
    invoke_json("export", "markdown", "--output", str(target))
    before = {path.name: path.read_bytes() for path in target.iterdir()}

    result = runner.invoke(app, ["export", "markdown", "--output", str(target)])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "Error: Markdown export requires an empty directory" in result.output
    assert "Traceback" not in result.output
    assert {path.name: path.read_bytes() for path in target.iterdir()} == before


def test_invalid_startup_config_reports_error_without_secret(monkeypatch):
    monkeypatch.setenv("STICKY_DEFAULT_SEARCH_LIMIT", "garbage")
    monkeypatch.setenv("STICKY_OPENROUTER_API_KEY", "dummy-startup-secret")

    result = runner.invoke(app, ["config", "--json"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "Error:" in result.output and "default_search_limit" in result.output
    assert "Traceback" not in result.output
    assert "dummy-startup-secret" not in result.output
    import sticky.cli.app as cli
    assert cli._service is None


@pytest.mark.parametrize("args", [["add", " "], ["search", "notes", "--limit", "0"],
                                ["list", "--limit", "-1"], ["update", "missing", "text"],
                                ["delete", "missing", "--yes"],
                                ["config", "set", "default_list_limit", "0"],
                                ["config", "set", "embedding_model", "new-model"]])
def test_invalid_input_fails(args):
    result = runner.invoke(app, args)
    assert result.exit_code != 0


@pytest.mark.parametrize("json_output", [False, True])
def test_config_secret_read_and_write_are_masked(json_output):
    from sticky.cli.app import get_service
    get_service().config.openrouter_api_key = "old-dummy-secret"
    suffix = ["--json"] if json_output else []
    written = runner.invoke(app, ["config", "set", "openrouter_api_key", "new-dummy-secret", *suffix])
    assert written.exit_code == 0, written.output
    for result in (written, runner.invoke(app, ["config", *suffix]),
                   runner.invoke(app, ["config", "get", "openrouter_api_key", *suffix])):
        assert "old-dummy-secret" not in result.output
        assert "new-dummy-secret" not in result.output
    assert "configured" in written.output


def test_setup_masks_key_and_advertises_only_working_commands():
    result = runner.invoke(app, ["setup", "--api-key", "setup-dummy-secret", "--json"])
    assert result.exit_code == 0
    assert "setup-dummy-secret" not in result.output
    assert json.loads(result.output)["api_key"] == "configured"
    rendered = runner.invoke(app, ["setup"])
    assert "sticky brief" not in rendered.output
    mcp = runner.invoke(app, ["setup", "--mcp"])
    assert "mcpServers" in json.loads(mcp.output)


def test_digest_requires_key_and_reports_cloud_errors(monkeypatch):
    from sticky.cli.app import get_service
    invoke_json("add", "digest source")
    get_service().config.openrouter_api_key = ""
    missing = runner.invoke(app, ["digest"])
    assert missing.exit_code == 1
    assert "API key" in missing.output
    get_service().config.openrouter_api_key = "dummy-key"
    monkeypatch.setattr(get_service().digest_generator, "generate_sync",
                        lambda thoughts: {"text": "One source note [1]."})
    assert invoke_json("digest")["thought_count"] == 1


def test_local_stats_and_privacy():
    invoke_json("add", "local count")
    assert invoke_json("stats")["thoughts"]["total"] == 1
    info = invoke_json("privacy")
    assert info["data_flow"]["capture"] == "LOCAL"
    assert "retention" in info["description"]


def test_delete_cancellation_preserves_note():
    note = invoke_json("add", "keep this")
    result = runner.invoke(app, ["delete", note["id"]], input="n\n")
    assert result.exit_code != 0
    assert invoke_json("get", note["id"])["content"] == "keep this"


def test_partial_import_reports_failure_and_keeps_valid_records(tmp_path):
    source = tmp_path / "partial.json"
    source.write_text(json.dumps({"thoughts": [
        {"id": "valid-record", "content": "Keep this restored note"}, {"content": " "},
    ]}), encoding="utf-8")
    result = runner.invoke(app, ["import", str(source), "--json"])
    assert result.exit_code == 1
    receipt = json.loads(result.output)
    assert receipt["imported"] == 1 and len(receipt["errors"]) == 1
    assert invoke_json("get", "valid-record")["content"] == "Keep this restored note"


def test_malformed_json_import_reports_clean_error(tmp_path):
    source = tmp_path / "invalid.json"
    source.write_text("null", encoding="utf-8")
    result = runner.invoke(app, ["import", str(source), "--json"])
    assert result.exit_code == 1 and isinstance(result.exception, SystemExit)
    assert "thoughts list" in result.output and "Traceback" not in result.output


@pytest.mark.parametrize("content,category", [
    ("[red]literal[/red] note", "draft [/urgent]"),
    ("draft [/urgent]", "[cyan]literal[/cyan]"),
])
def test_list_renders_note_fields_literally(tmp_path, monkeypatch, content, category):
    import sticky.cli.app as cli

    monkeypatch.setattr(cli.console, "width", 150)
    source = tmp_path / "literal.json"
    source.write_text(json.dumps({"thoughts": [{"id": "[id]literal", "content": content, "category": category}]}), encoding="utf-8")
    invoke_json("import", str(source))
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0, result.exception
    assert content in result.output and category in result.output
    assert "[id]literal" in result.output
