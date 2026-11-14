"""Small startup configuration: environment > local TOML > defaults."""

from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path
from typing import Any

import tomli_w
from pydantic import BaseModel, ConfigDict, Field


def _default_data_dir() -> Path:
    if sys.platform == "win32":
        return Path.home() / "AppData" / "Local" / "sticky"
    return Path.home() / ".local" / "share" / "sticky"


def _default_config_dir() -> Path:
    if sys.platform == "win32":
        return Path.home() / "AppData" / "Local" / "sticky"
    return Path.home() / ".config" / "sticky"


class StickyConfig(BaseModel):
    """Settings read at startup. Old Alpha fields are ignored on load."""

    data_dir: Path = Field(default_factory=_default_data_dir)
    openrouter_api_key: str = Field(default="", repr=False)
    openrouter_model: str = Field(default="anthropic/claude-sonnet-4.6", min_length=1)
    default_search_limit: int = Field(default=10, gt=0)
    default_list_limit: int = Field(default=20, gt=0)

    model_config = ConfigDict(validate_assignment=True, extra="ignore", hide_input_in_errors=True)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "sticky.db"

    @property
    def config_dir(self) -> Path:
        return _default_config_dir()

    @property
    def config_file(self) -> Path:
        return self.config_dir / "config.toml"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.config_dir.mkdir(parents=True, exist_ok=True)

    def set(self, key: str, value: Any) -> Any:
        """Validate a retained setting; return its previous value."""
        if key not in type(self).model_fields:
            raise ValueError(f"Unknown config key: {key}")
        previous = getattr(self, key)
        setattr(self, key, value)
        return previous

    def save_to_file(self, path: Path | None = None) -> None:
        """Save settings locally, including an optional API key."""
        path = path or self.config_file
        data = self.model_dump(mode="json")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as config_file:
            tomli_w.dump(data, config_file)

    @classmethod
    def load_from_file(cls, path: Path, data_dir: Path | None = None) -> StickyConfig:
        with path.open("rb") as config_file:
            data = tomllib.load(config_file)
        if data_dir is not None:
            data["data_dir"] = data_dir
        return cls(**data)

    def to_display_dict(self) -> dict[str, dict[str, Any]]:
        """Display retained settings and sources, without any API key prefix."""
        file_data: dict = {}
        if self.config_file.exists():
            try:
                with self.config_file.open("rb") as config_file:
                    file_data = tomllib.load(config_file)
            except (OSError, tomllib.TOMLDecodeError):
                pass

        display = {}
        for key in type(self).model_fields:
            value = getattr(self, key)
            source = "env" if f"STICKY_{key.upper()}" in os.environ else (
                "config file" if key in file_data else "default"
            )
            if key == "openrouter_api_key":
                value = "configured" if value else "unset"
            elif isinstance(value, Path):
                value = str(value)
            display[key] = {"value": value, "source": source}
        return display


_config_instance: StickyConfig | None = None


def get_config(force_reload: bool = False) -> StickyConfig:
    """Load the retained startup settings once per process."""
    global _config_instance
    if _config_instance is not None and not force_reload:
        return _config_instance

    values: dict[str, Any] = {}
    config_file = _default_config_dir() / "config.toml"
    if config_file.exists():
        try:
            with config_file.open("rb") as config_stream:
                values.update(tomllib.load(config_stream))
        except (OSError, tomllib.TOMLDecodeError):
            pass
    for key in StickyConfig.model_fields:
        env_value = os.environ.get(f"STICKY_{key.upper()}")
        if env_value is not None:
            values[key] = env_value

    _config_instance = StickyConfig(**values)
    return _config_instance
