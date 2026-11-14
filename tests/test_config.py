"""Configuration supports only the small retained startup surface."""

import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from sticky.core.config import StickyConfig, get_config

RETAINED = {"data_dir", "openrouter_api_key", "openrouter_model", "default_search_limit", "default_list_limit"}
REMOVED = {"embedding_model", "embedding_dimensions", "confidence_threshold", "search_mode",
           "search_vector_weight", "search_fts_weight", "tui_default_view", "review_auto_resolve_days"}


def test_only_retained_fields_and_default_paths(tmp_data_dir):
    config = StickyConfig(data_dir=tmp_data_dir)
    assert set(StickyConfig.model_fields) == RETAINED
    assert config.default_search_limit == 10
    assert config.default_list_limit == 20
    assert config.openrouter_api_key == ""
    assert config.db_path == tmp_data_dir / "sticky.db"
    assert config.config_file == config.config_dir / "config.toml"


def test_config_directories(tmp_path):
    config = StickyConfig(data_dir=tmp_path / "new-data")
    config.ensure_dirs()
    assert config.data_dir.is_dir()
    assert config.config_dir.is_dir()


@pytest.mark.parametrize("key", sorted(REMOVED))
def test_removed_fields_cannot_be_set(key):
    config = StickyConfig(**{key: "obsolete"})
    assert not hasattr(config, key)
    with pytest.raises(ValueError, match="Unknown config key"):
        config.set(key, "obsolete")
    with pytest.raises(ValueError):
        setattr(config, key, "obsolete")


def test_legacy_toml_and_environment_do_not_activate_settings(tmp_path, monkeypatch):
    path = tmp_path / "legacy.toml"
    path.write_text('embedding_model = "unavailable-custom-model"\nembedding_dimensions = 768\n'
                    'search_mode = "vector"\nconfidence_threshold = "broken"\n'
                    'default_search_limit = 7\n', encoding="utf-8")
    loaded = StickyConfig.load_from_file(path)
    assert loaded.default_search_limit == 7
    assert not REMOVED.intersection(loaded.model_dump())
    loaded.save_to_file(loaded.config_file)
    monkeypatch.setenv("STICKY_EMBEDDING_MODEL", "other-model")
    monkeypatch.setenv("STICKY_CONFIDENCE_THRESHOLD", "broken")
    active = get_config(force_reload=True)
    assert active.default_search_limit == 7
    assert not REMOVED.intersection(active.to_display_dict())


@pytest.mark.parametrize("key", ["default_search_limit", "default_list_limit"])
@pytest.mark.parametrize("value", [0, -1, "invalid", 1.5])
def test_limit_validation_preserves_previous_value(key, value):
    config = StickyConfig()
    previous = getattr(config, key)
    with pytest.raises(ValidationError):
        config.set(key, value)
    assert getattr(config, key) == previous


def test_settings_validate_string_limit_and_path(tmp_path):
    config = StickyConfig()
    assert config.set("default_search_limit", "25") == 10
    assert config.default_search_limit == 25
    config.set("data_dir", str(tmp_path))
    assert config.data_dir == tmp_path
    with pytest.raises(ValidationError):
        config.openrouter_model = ""


def test_roundtrip_saves_only_retained_settings_and_local_key(tmp_path):
    config = StickyConfig(data_dir=tmp_path, openrouter_api_key="dummy-secret", default_search_limit=23)
    path = tmp_path / "config.toml"
    config.save_to_file(path)
    stored = tomllib.loads(path.read_text(encoding="utf-8"))
    assert set(stored) == RETAINED
    assert stored["openrouter_api_key"] == "dummy-secret"
    loaded = StickyConfig.load_from_file(path)
    assert loaded.model_dump() == config.model_dump()


@pytest.mark.parametrize("secret", ["abcd-dummy-secret", "tiny", ""])
def test_secret_display_and_repr_reveal_no_prefix(secret):
    config = StickyConfig(openrouter_api_key=secret)
    display = config.to_display_dict()
    assert display["openrouter_api_key"]["value"] == ("configured" if secret else "unset")
    if secret:
        assert secret not in repr(config)
        assert secret[:4] not in str(display)


def test_env_precedence_and_singleton(tmp_path, monkeypatch):
    path = StickyConfig().config_file
    StickyConfig(default_search_limit=7).save_to_file(path)
    monkeypatch.setenv("STICKY_DEFAULT_SEARCH_LIMIT", "21")
    monkeypatch.setenv("STICKY_DATA_DIR", str(tmp_path))
    config = get_config(force_reload=True)
    assert config.default_search_limit == 21
    assert config.data_dir == tmp_path
    assert config.to_display_dict()["default_search_limit"]["source"] == "env"
    assert get_config() is config
    assert get_config(force_reload=True) is not config


def test_invalid_retained_env_fails_clearly(monkeypatch):
    monkeypatch.setenv("STICKY_DEFAULT_LIST_LIMIT", "0")
    with pytest.raises(ValidationError):
        get_config(force_reload=True)
