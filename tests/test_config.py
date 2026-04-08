"""Tests for the sticky configuration system."""

import sys
from pathlib import Path

import pytest

from sticky.core.config import StickyConfig, get_config


def test_default_config():
    config = StickyConfig()
    assert config.embedding_model == "all-MiniLM-L6-v2"
    assert config.embedding_dimensions == 384
    assert config.confidence_threshold == 0.6
    assert config.search_vector_weight == 0.6
    assert config.search_fts_weight == 0.4
    assert config.tui_default_view == "auto"
    assert config.review_auto_resolve_days == 7


def test_default_config_additional_fields():
    """Verify all remaining default values."""
    config = StickyConfig()
    assert config.openrouter_api_key == ""
    assert config.openrouter_model == "anthropic/claude-sonnet-4.6"
    assert config.digest_default_period == "day"
    assert config.default_search_limit == 10
    assert config.default_list_limit == 20
    assert config.tui_show_filter_bar is False
    assert config.tui_score_hint_shown is False
    assert config.privacy_show_in_status is True
    assert config.search_mode == "hybrid"


def test_config_from_env(monkeypatch, tmp_data_dir):
    monkeypatch.setenv("STICKY_CONFIDENCE_THRESHOLD", "0.5")
    monkeypatch.setenv("STICKY_DATA_DIR", str(tmp_data_dir))
    config = get_config(force_reload=True)
    assert config.confidence_threshold == 0.5


def test_config_data_dir_created(tmp_data_dir):
    config = StickyConfig(data_dir=tmp_data_dir / "new_subdir")
    config.ensure_dirs()
    assert config.data_dir.exists()


def test_config_db_path(tmp_data_dir):
    config = StickyConfig(data_dir=tmp_data_dir)
    assert config.db_path == tmp_data_dir / "sticky.db"


def test_config_toml_roundtrip(tmp_path):
    config = StickyConfig(data_dir=tmp_path)
    config_file = tmp_path / "config.toml"
    config.save_to_file(config_file)
    loaded = StickyConfig.load_from_file(config_file, data_dir=tmp_path)
    assert loaded.confidence_threshold == config.confidence_threshold


def test_config_set_value(tmp_data_dir):
    config = StickyConfig(data_dir=tmp_data_dir)
    prev = config.set("confidence_threshold", 0.5)
    assert prev == 0.6
    assert config.confidence_threshold == 0.5


def test_config_default_data_dir():
    """Verify platform-specific default data_dir."""
    config = StickyConfig()
    if sys.platform == "win32":
        local_app_data = Path.home() / "AppData" / "Local" / "sticky"
        assert config.data_dir == local_app_data
    else:
        assert config.data_dir == Path.home() / ".local" / "share" / "sticky"


def test_config_config_dir_property():
    """Verify config_dir is platform-specific."""
    config = StickyConfig()
    if sys.platform == "win32":
        expected = Path.home() / "AppData" / "Local" / "sticky"
    else:
        expected = Path.home() / ".config" / "sticky"
    assert config.config_dir == expected


def test_config_config_file_property():
    """Verify config_file points to config.toml inside config_dir."""
    config = StickyConfig()
    assert config.config_file == config.config_dir / "config.toml"


def test_config_ensure_dirs_creates_config_dir(tmp_path):
    """ensure_dirs creates both data_dir and config_dir."""
    config = StickyConfig(data_dir=tmp_path / "data_subdir")
    config.ensure_dirs()
    assert config.data_dir.exists()


def test_config_save_includes_api_key(tmp_path):
    """save_to_file saves all keys including api_key (local file, same security as env var)."""
    config = StickyConfig(data_dir=tmp_path, openrouter_api_key="secret-key-123")
    config_file = tmp_path / "config.toml"
    config.save_to_file(config_file)
    content = config_file.read_text()
    assert "secret-key-123" in content


def test_config_to_display_dict(tmp_data_dir):
    """to_display_dict should return source info and mask API keys."""
    config = StickyConfig(data_dir=tmp_data_dir, openrouter_api_key="sk-1234567890")
    display = config.to_display_dict()
    # Each entry should have 'value' and 'source' keys
    assert "embedding_model" in display
    assert "value" in display["embedding_model"]
    assert "source" in display["embedding_model"]
    # API key should be masked
    api_entry = display["openrouter_api_key"]
    assert "sk-1234567890" not in str(api_entry["value"])


def test_config_singleton(monkeypatch, tmp_data_dir):
    """get_config returns the same instance unless force_reload."""
    monkeypatch.setenv("STICKY_DATA_DIR", str(tmp_data_dir))
    config1 = get_config(force_reload=True)
    config2 = get_config()
    assert config1 is config2


def test_config_singleton_force_reload(monkeypatch, tmp_data_dir):
    """get_config with force_reload returns a new instance."""
    monkeypatch.setenv("STICKY_DATA_DIR", str(tmp_data_dir))
    config1 = get_config(force_reload=True)
    config2 = get_config(force_reload=True)
    assert config1 is not config2


def test_config_env_bool(monkeypatch, tmp_data_dir):
    """Boolean env vars should be correctly parsed."""
    monkeypatch.setenv("STICKY_DATA_DIR", str(tmp_data_dir))
    monkeypatch.setenv("STICKY_TUI_SHOW_FILTER_BAR", "true")
    config = get_config(force_reload=True)
    assert config.tui_show_filter_bar is True


def test_config_env_int(monkeypatch, tmp_data_dir):
    """Integer env vars should be correctly parsed."""
    monkeypatch.setenv("STICKY_DATA_DIR", str(tmp_data_dir))
    monkeypatch.setenv("STICKY_DEFAULT_SEARCH_LIMIT", "25")
    config = get_config(force_reload=True)
    assert config.default_search_limit == 25


def test_config_toml_roundtrip_all_fields(tmp_path):
    """Verify non-sensitive fields survive TOML roundtrip."""
    config = StickyConfig(
        data_dir=tmp_path,
        confidence_threshold=0.75,
        embedding_model="custom-model",
        default_search_limit=50,
        tui_show_filter_bar=True,
    )
    config_file = tmp_path / "config.toml"
    config.save_to_file(config_file)
    loaded = StickyConfig.load_from_file(config_file, data_dir=tmp_path)
    assert loaded.confidence_threshold == 0.75
    assert loaded.embedding_model == "custom-model"
    assert loaded.default_search_limit == 50
    assert loaded.tui_show_filter_bar is True


def test_config_set_returns_previous(tmp_data_dir):
    """set() returns the previous value."""
    config = StickyConfig(data_dir=tmp_data_dir)
    prev = config.set("embedding_model", "new-model")
    assert prev == "all-MiniLM-L6-v2"
    assert config.embedding_model == "new-model"
