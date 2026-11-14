"""Shared test fixtures for sticky."""

import os
import tempfile
from pathlib import Path

import pytest
import httpx


@pytest.fixture
def tmp_data_dir(tmp_path):
    """Provide a temporary data directory for tests."""
    data_dir = tmp_path / "sticky-test"
    data_dir.mkdir()
    return data_dir


@pytest.fixture(autouse=True)
def offline_cloud(monkeypatch):
    """Tests may simulate a digest response, but cannot send notes to the cloud."""
    def unavailable(*args, **kwargs):
        raise httpx.ConnectError("Cloud unavailable during testing")

    async def async_unavailable(*args, **kwargs):
        raise httpx.ConnectError("Cloud unavailable during testing")

    monkeypatch.setattr(httpx.Client, "post", unavailable)
    monkeypatch.setattr(httpx.AsyncClient, "post", async_unavailable)


@pytest.fixture(autouse=True)
def env_override(tmp_data_dir, monkeypatch):
    """Override data directory for all tests."""
    monkeypatch.setenv("STICKY_DATA_DIR", str(tmp_data_dir))
    monkeypatch.setenv("STICKY_OPENROUTER_API_KEY", "test-key")

    # Reset singletons so stale state doesn't leak between tests
    import sticky.core.embeddings as _emb
    import sticky.core.config as _cfg
    monkeypatch.setattr(_cfg, "_default_config_dir", lambda: tmp_data_dir / "config")
    _emb._engine = None
    _cfg._config_instance = None
