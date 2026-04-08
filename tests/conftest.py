"""Shared test fixtures for sticky."""

import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def tmp_data_dir(tmp_path):
    """Provide a temporary data directory for tests."""
    data_dir = tmp_path / "sticky-test"
    data_dir.mkdir()
    return data_dir


@pytest.fixture(autouse=True)
def env_override(tmp_data_dir, monkeypatch):
    """Override data directory for all tests."""
    monkeypatch.setenv("STICKY_DATA_DIR", str(tmp_data_dir))
    monkeypatch.setenv("STICKY_OPENROUTER_API_KEY", "test-key")

    # Reset singletons so stale state doesn't leak between tests
    import sticky.core.embeddings as _emb
    import sticky.core.config as _cfg
    _emb._engine = None
    _cfg._config = None
