"""MCP schemas and retained operations use isolated local storage."""

import asyncio
import inspect
import json
import os
from pathlib import Path
import struct
import sys
import threading

import httpx
import pytest
from mcp import Client, StdioServerParameters
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult

from sticky.mcp import server


@pytest.fixture(autouse=True)
def local_service(monkeypatch):
    from sticky.core.embeddings import EmbeddingEngine
    monkeypatch.setattr(EmbeddingEngine, "embed", lambda self, text: struct.pack("384f", *([1.0] + [0.0] * 383)))
    monkeypatch.setattr(server, "_service", None)
    def forbidden(*args, **kwargs):
        raise AssertionError("No cloud request expected")
    monkeypatch.setattr(httpx.Client, "post", forbidden)
    monkeypatch.setattr(httpx.AsyncClient, "post", forbidden)
    yield
    if server._service is not None:
        server._service.db.close()
    server._service = None


@pytest.mark.asyncio
async def test_registered_schema_has_only_retained_tools():
    tools = {tool.name: tool for tool in await server.mcp.list_tools()}
    assert set(tools) == {
        "sticky_capture", "sticky_search", "sticky_list", "sticky_get", "sticky_classify",
        "sticky_update", "sticky_delete", "sticky_export", "sticky_import", "sticky_digest",
        "sticky_stats", "sticky_config", "sticky_privacy",
    }
    assert set(tools["sticky_config"].input_schema["properties"]) == {"key"}
    for name in ("sticky_search", "sticky_list", "sticky_export"):
        assert not {"entity", "needs_review", "mode"}.intersection(tools[name].input_schema["properties"])
    assert "template" not in tools["sticky_capture"].input_schema["properties"]
    assert all(tool.output_schema["type"] == "object" for tool in tools.values())
    assert all(tool.annotations.open_world_hint is False for name, tool in tools.items() if name != "sticky_digest")
    assert tools["sticky_digest"].annotations.open_world_hint is True
    assert tools["sticky_config"].annotations.read_only_hint is True
    assert tools["sticky_capture"].annotations.destructive_hint is False
    assert tools["sticky_capture"].annotations.idempotent_hint is False
    assert tools["sticky_delete"].annotations.destructive_hint is True
    assert tools["sticky_update"].annotations.idempotent_hint is False
    assert tools["sticky_export"].annotations.read_only_hint is False
    assert "OpenRouter" in tools["sticky_digest"].description
    assert "send" in tools["sticky_digest"].description.lower()
    resources = await server.mcp.list_resources()
    assert {str(resource.uri) for resource in resources} == {"sticky://stats", "sticky://privacy"}
    assert {prompt.name for prompt in await server.mcp.list_prompts()} == {"research_session"}
    prompt = await server.research_session("local research", "paper")
    assert "sticky_synthesize" not in prompt
    assert "sticky_list" in prompt


async def test_capture_get_edit_and_manual_category_without_network():
    first = await server.sticky_capture("Call Monday", source_url="https://example.org", thread="work")
    second = await server.sticky_capture("Call Tuesday")
    repeat = await server.sticky_capture("Call Monday")
    assert len({first["id"], second["id"], repeat["id"]}) == 3
    assert first["category"] is None
    assert "entities" not in first
    assert (await server.sticky_get(first["id"]))["source_url"] == "https://example.org"
    assert (await server.sticky_classify(first["id"], "action"))["category"] == "action"
    updated = await server.sticky_update(first["id"], "Call Wednesday")
    assert updated["metadata"]["thread"] == "work"
    assert updated["category"] is None
    assert (await server.sticky_delete(first["id"]))["deleted"]
    with pytest.raises(ToolError, match="not found"):
        await server.sticky_get(first["id"])


async def test_search_and_filtered_paginated_list():
    first = await server.sticky_capture("quantum research", thread="physics")
    await server.sticky_classify(first["id"], "reference")
    await server.sticky_capture("quantum theory")
    results = await server.sticky_search("quantum", category="reference", limit=1)
    assert results["total_results"] == 1
    assert results["results"][0]["id"] == first["id"]
    listing = await server.sticky_list(limit=1)
    assert listing["has_more"]
    next_page = await server.sticky_list(limit=1, cursor=listing["next_cursor"])
    assert not next_page["has_more"]
    assert next_page["thoughts"][0]["id"] != listing["thoughts"][0]["id"]
    assert (await server.sticky_list(thread="physics"))["total"] == 1


async def test_config_read_only_and_secret_masked():
    assert list(inspect.signature(server.sticky_config).parameters) == ["key"]
    server.get_service().config.openrouter_api_key = "dummy-private-value"
    display = await server.sticky_config()
    assert display["openrouter_api_key"]["value"] == "configured"
    assert "dummy" not in str(display)
    with pytest.raises(ToolError, match="Unknown config key"):
        await server.sticky_config("embedding_model")
    with pytest.raises(TypeError):
        await server.sticky_config(action="set", value="broken")


async def test_export_and_restore(tmp_path):
    thought = await server.sticky_capture("JSON recovery")
    target = str(tmp_path / "export.json")
    assert (await server.sticky_export("json", target))["count"] == 1
    assert (await server.sticky_import(target, dry_run=True))["skipped"] == 1
    await server.sticky_delete(thought["id"])
    assert (await server.sticky_import(target))["imported"] == 1
    assert (await server.sticky_get(thought["id"]))["content"] == "JSON recovery"


async def test_explicit_digest_missing_key_and_mocked_request(monkeypatch):
    await server.sticky_capture("summarize this note")
    svc = server.get_service()
    svc.config.openrouter_api_key = ""
    with pytest.raises(ToolError, match="API key"):
        await server.sticky_digest()
    svc.config.openrouter_api_key = "dummy-key"
    monkeypatch.setattr(svc.digest_generator, "generate_sync", lambda thoughts: {"text": "Summary [1]."})
    result = await server.sticky_digest()
    assert result["thought_count"] == 1
    assert len(result["thought_ids"]) == 1
    assert not {"action_items", "resurfaced", "sources"}.intersection(result)


async def test_stats_privacy_and_singleton():
    assert server.get_service() is server.get_service()
    await server.sticky_capture("count this")
    assert (await server.sticky_stats())["thoughts"]["total"] == 1
    assert (await server.sticky_privacy())["data_flow"]["capture"] == "LOCAL"


async def test_failed_initialization_can_retry(monkeypatch):
    original = server.StickyService.initialize
    attempts = 0

    def initialize(service):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("Temporary storage failure")
        original(service)

    monkeypatch.setattr(server.StickyService, "initialize", initialize)
    with pytest.raises(OSError, match="Temporary storage failure"):
        await server.sticky_stats()
    assert server._service is None
    assert (await server.sticky_stats())["thoughts"]["total"] == 0
    assert attempts == 2


def test_package_entrypoint_uses_shared_startup(monkeypatch):
    import runpy

    started = []
    monkeypatch.setattr(server, "run_server", lambda: started.append(True))
    monkeypatch.setattr(server.mcp, "run", lambda **kwargs: pytest.fail("Bypassed shared startup"))
    runpy.run_module("sticky.mcp", run_name="__main__")
    assert started == [True]


@pytest.mark.parametrize("operation", [lambda: server.sticky_capture(" "),
    lambda: server.sticky_search("query", limit=0), lambda: server.sticky_list(limit=-1),
    lambda: server.sticky_update("missing", "new text"), lambda: server.sticky_delete("missing")])
async def test_invalid_calls_fail(operation):
    with pytest.raises(ToolError):
        await operation()


def _payload(result):
    assert isinstance(result, CallToolResult)
    assert not result.is_error, result
    assert isinstance(result.structured_content, dict)
    assert json.loads(result.content[0].text) == result.structured_content
    return result.structured_content


@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_concurrent_protocol_operations_keep_sqlite_on_event_loop(monkeypatch, mode):
    loop_thread = threading.get_ident()
    initialized_on = []
    original = server.StickyService.initialize

    def initialize(service):
        initialized_on.append(threading.get_ident())
        original(service)

    monkeypatch.setattr(server.StickyService, "initialize", initialize)
    async with Client(server.mcp, mode=mode) as client:
        saved = await asyncio.gather(*[
            client.call_tool("sticky_capture", {"content": f"Concurrent research note {i}", "thread": "parallel"})
            for i in range(8)
        ])
        ids = [_payload(result)["id"] for result in saved]
        assert len(set(ids)) == 8
        stats, listing, resource, privacy, prompt = await asyncio.gather(
            client.call_tool("sticky_stats", {}),
            client.call_tool("sticky_list", {"thread": "parallel", "limit": 3}),
            client.read_resource("sticky://stats"),
            client.read_resource("sticky://privacy"),
            client.get_prompt("research_session", {"topic": "Parallel research", "source": "paper"}),
        )
        assert _payload(stats)["thoughts"]["total"] == 8
        assert _payload(listing)["total"] == 8
        assert _payload(listing)["has_more"]
        assert json.loads(resource.contents[0].text)["thoughts"]["total"] == 8
        assert json.loads(privacy.contents[0].text)["data_flow"]["capture"] == "LOCAL"
        assert "sticky_list" in prompt.messages[0].content.text
        changed = await asyncio.gather(*[
            client.call_tool("sticky_update", {"thought_id": thought_id, "content": f"Revised research note {i}"})
            for i, thought_id in enumerate(ids)
        ])
        assert all(_payload(result)["content"].startswith("Revised") for result in changed)
        read = await asyncio.gather(*[
            client.call_tool("sticky_get", {"thought_id": thought_id}) for thought_id in ids
        ])
        assert all(_payload(result)["metadata"]["thread"] == "parallel" for result in read)
        deleted = await asyncio.gather(*[
            client.call_tool("sticky_delete", {"thought_id": thought_id}) for thought_id in ids
        ])
        assert all(_payload(result)["deleted"] for result in deleted)
        assert _payload(await client.call_tool("sticky_stats", {}))["thoughts"]["total"] == 0
    assert initialized_on == [loop_thread]


@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_protocol_errors_are_useful_and_session_can_recover(tmp_path, monkeypatch, mode):
    async with Client(server.mcp, mode=mode) as client:
        for name, arguments, message in [
            ("sticky_capture", {"content": " "}, "contain text"),
            ("sticky_get", {"thought_id": "missing"}, "not found"),
            ("sticky_search", {"query": "valid", "limit": 0}, "positive integer"),
            ("sticky_config", {"key": "embedding_model"}, "Unknown config key"),
            ("sticky_import", {"source_path": str(tmp_path / "missing.json")}, "missing.json"),
        ]:
            result = await client.call_tool(name, arguments)
            assert result.is_error, result
            assert result.structured_content is None
            assert message in result.content[0].text
        invalid_type = await client.call_tool("sticky_capture", {"content": ["invalid"]})
        assert invalid_type.is_error
        svc = server.get_service()
        svc.config.openrouter_api_key = ""
        missing_key = await client.call_tool("sticky_digest", {})
        assert missing_key.is_error and "API key" in missing_key.content[0].text
        note = _payload(await client.call_tool("sticky_capture", {"content": "Recovered session"}))
        classified = _payload(await client.call_tool("sticky_classify", {"thought_id": note["id"], "category": "reference"}))
        assert classified["category"] == "reference"
        path = str(tmp_path / "recovery.json")
        assert _payload(await client.call_tool("sticky_export", {"format": "json", "output_path": path}))["count"] == 1
        assert _payload(await client.call_tool("sticky_import", {"source_path": path, "dry_run": True}))["skipped"] == 1
        _payload(await client.call_tool("sticky_delete", {"thought_id": note["id"]}))
        assert _payload(await client.call_tool("sticky_import", {"source_path": path}))["imported"] == 1
        svc.config.openrouter_api_key = "dummy-key"
        monkeypatch.setattr(svc.digest_generator, "generate_sync", lambda thoughts: {"text": "Summary [1]."})
        digest = _payload(await client.call_tool("sticky_digest", {}))
        assert digest["thought_ids"] == [note["id"]]
        assert _payload(await client.call_tool("sticky_privacy", {}))["data_flow"]["digest"] == "CLOUD_ON_REQUEST"

        def broken_search(*args, **kwargs):
            raise RuntimeError("unexpected failure containing a private secret")

        monkeypatch.setattr(svc, "search", broken_search)
        crashed = await client.call_tool("sticky_search", {"query": "valid"})
        assert crashed.is_error
        assert "private secret" not in str(crashed)
        assert _payload(await client.call_tool("sticky_get", {"thought_id": note["id"]}))["content"] == "Recovered session"


@pytest.mark.parametrize("preload_fails", [False, True])
def test_startup_preloads_libraries_without_loading_weights(monkeypatch, preload_fails):
    events = []

    def preload(name):
        assert name == "sentence_transformers"
        events.append("libraries")
        if preload_fails:
            raise ImportError("unavailable")

    def run(**kwargs):
        assert kwargs == {"transport": "stdio"}
        assert server._service is None
        events.append("stdio")

    monkeypatch.setattr(server.importlib, "import_module", preload)
    monkeypatch.setattr(server.mcp, "run", run)
    server.run_server()
    assert events == ["libraries", "stdio"]


def test_server_shutdown_closes_service_even_if_transport_fails(monkeypatch):
    service = server.get_service()
    closed = []
    original_close = service.db.close

    def close():
        closed.append(True)
        original_close()

    def fail(**kwargs):
        raise RuntimeError("transport stopped")

    monkeypatch.setattr(service.db, "close", close)
    monkeypatch.setattr(server.importlib, "import_module", lambda name: None)
    monkeypatch.setattr(server.mcp, "run", fail)
    with pytest.raises(RuntimeError, match="transport stopped"):
        server.run_server()
    assert closed == [True]
    assert server._service is None


@pytest.mark.asyncio
@pytest.mark.parametrize("arguments", [
    ["-m", "sticky.mcp.server"],
    ["-m", "sticky.mcp"],
    ["-m", "sticky.cli.app", "mcp-serve"],
])
async def test_real_stdio_capture_and_search_after_config(tmp_path, arguments):
    """Real stdio requests must load local vectors and retrieve beyond keywords."""
    guard = tmp_path / "guard"
    guard.mkdir()
    guard.joinpath("sitecustomize.py").write_text(
        "import os, traceback, httpx, httpx2\n"
        "def forbidden(*args, **kwargs):\n"
        "    with open(os.environ['QA_HTTP_LOG'], 'a') as log:\n"
        "        request = args[1]\n"
        "        log.write(f'{request.method} {request.url}\\n' + ''.join(traceback.format_stack(limit=8)))\n"
        "    raise AssertionError('Local MCP operations must not call the cloud')\n"
        "async def async_forbidden(*args, **kwargs): return forbidden(*args, **kwargs)\n"
        "for library in (httpx, httpx2):\n"
        "    library.HTTPTransport.handle_request = forbidden\n"
        "    library.AsyncHTTPTransport.handle_async_request = async_forbidden\n",
        encoding="utf-8",
    )
    env = {key: value for key, value in os.environ.items() if not key.startswith("STICKY_")}
    env.setdefault("HF_HOME", str(Path.home() / ".cache" / "huggingface"))
    env.update({
        "STICKY_DATA_DIR": str(tmp_path / "data"), "USERPROFILE": str(tmp_path / "home"),
        "HOME": str(tmp_path / "home"), "XDG_CONFIG_HOME": str(tmp_path / "home" / ".config"),
        "STICKY_OPENROUTER_API_KEY": "dummy-protocol-test",
        "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "TOKENIZERS_PARALLELISM": "false",
        "PYTHONPATH": str(guard) + os.pathsep + str(Path(__file__).resolve().parents[1] / "src"),
        "QA_HTTP_LOG": str(tmp_path / "cloud-attempts.log"),
    })
    parameters = StdioServerParameters(command=sys.executable, args=arguments, env=env)
    async with Client(parameters, read_timeout_seconds=60.0) as client:
        listed = await client.list_tools()
        assert len(listed.tools) == 13
        assert {resource.uri for resource in (await client.list_resources()).resources} == {"sticky://stats", "sticky://privacy"}
        assert {prompt.name for prompt in (await client.list_prompts()).prompts} == {"research_session"}
        config = await client.call_tool("sticky_config", {})
        assert "dummy-protocol-test" not in str(config)
        _payload(config)
        saved = await client.call_tool("sticky_capture", {
            "content": "Protocol note about passport renewal", "thread": "protocol-test",
        })
        note = _payload(saved)
        assert note["embedding_model"] == "all-MiniLM-L6-v2"
        found, semantic, recovered, resource = await asyncio.gather(
            client.call_tool("sticky_search", {"query": "passport renewal", "limit": 3}),
            client.call_tool("sticky_search", {"query": "travel document expiry", "limit": 3}),
            client.call_tool("sticky_get", {"thought_id": note["id"]}),
            client.read_resource("sticky://stats"),
        )
        assert any(result["id"] == note["id"] for result in _payload(found)["results"])
        assert any(
            result["id"] == note["id"] and result["match_type"] == "vector"
            for result in _payload(semantic)["results"]
        )
        record = _payload(recovered)
        assert record["content"] == "Protocol note about passport renewal"
        assert record["embedding_model"] == "all-MiniLM-L6-v2"
        assert record["metadata"]["thread"] == "protocol-test"
        assert json.loads(resource.contents[0].text)["thoughts"]["total"] == 1
        rejected = await client.call_tool("sticky_get", {"thought_id": "missing"})
        assert rejected.is_error and "not found" in rejected.content[0].text
        assert _payload(await client.call_tool("sticky_get", {"thought_id": note["id"]}))["id"] == note["id"]
    assert not (tmp_path / "cloud-attempts.log").exists()
