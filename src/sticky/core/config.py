"""Configuration system for sticky.

Manages settings with precedence: env vars > TOML config file > defaults.
Env vars use STICKY_ prefix (e.g., STICKY_CONFIDENCE_THRESHOLD).
"""

from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path
from typing import Any

import tomli_w
from pydantic import BaseModel, Field


def _default_data_dir() -> Path:
    """Return platform-specific default data directory."""
    if sys.platform == "win32":
        return Path.home() / "AppData" / "Local" / "sticky"
    return Path.home() / ".local" / "share" / "sticky"


def _default_config_dir() -> Path:
    """Return platform-specific default config directory."""
    if sys.platform == "win32":
        return Path.home() / "AppData" / "Local" / "sticky"
    return Path.home() / ".config" / "sticky"


# Keys that are sensitive but CAN be saved to local config file.
# The config file is on the user's machine — same security as an env var.
# We mask them in display output but allow saving for convenience.
_SENSITIVE_KEYS: set[str] = set()  # Empty — allow all keys to be saved
_MASK_KEYS = {"openrouter_api_key"}  # Mask in display, but save normally

# ENV prefix
_ENV_PREFIX = "STICKY_"


class StickyConfig(BaseModel):
    """Main configuration model for sticky."""

    # Storage
    data_dir: Path = Field(default_factory=_default_data_dir)

    # Embedding
    embedding_model: str = "all-MiniLM-L6-v2"
    embedding_dimensions: int = 384

    # Scoring / search
    confidence_threshold: float = 0.6
    search_vector_weight: float = 0.6
    search_fts_weight: float = 0.4
    search_mode: str = "hybrid"

    # LLM
    openrouter_api_key: str = ""
    openrouter_model: str = "anthropic/claude-sonnet-4.6"

    # Digest
    digest_default_period: str = "day"

    # Limits
    default_search_limit: int = 10
    default_list_limit: int = 20

    # TUI
    tui_default_view: str = "auto"
    tui_show_filter_bar: bool = False
    tui_score_hint_shown: bool = False

    # Review
    review_auto_resolve_days: int = 7

    # Privacy
    privacy_show_in_status: bool = True

    model_config = {"arbitrary_types_allowed": True}

    # --- Properties ---

    @property
    def db_path(self) -> Path:
        """Path to the SQLite database file."""
        return self.data_dir / "sticky.db"

    @property
    def config_dir(self) -> Path:
        """Platform-specific configuration directory."""
        return _default_config_dir()

    @property
    def config_file(self) -> Path:
        """Path to the TOML configuration file."""
        return self.config_dir / "config.toml"

    # --- Methods ---

    def ensure_dirs(self) -> None:
        """Create data_dir and config_dir if they don't exist."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.config_dir.mkdir(parents=True, exist_ok=True)

    def set(self, key: str, value: Any) -> Any:
        """Set a configuration value, returning the previous value.

        Args:
            key: The configuration key to set.
            value: The new value.

        Returns:
            The previous value of the key.
        """
        previous = getattr(self, key)
        # Use model's __setattr__ for proper validation
        object.__setattr__(self, key, value)
        return previous

    def save_to_file(self, path: Path | None = None) -> None:
        """Save configuration to a TOML file.

        Skips sensitive values (e.g., API keys) and data_dir
        (which is typically set via env or left as default).

        Args:
            path: File path to write to. Defaults to self.config_file.
        """
        if path is None:
            path = self.config_file

        data: dict[str, Any] = {}
        defaults = StickyConfig()
        for field_name in StickyConfig.model_fields:
            if field_name in _SENSITIVE_KEYS:
                continue
            if field_name == "data_dir":
                continue
            value = getattr(self, field_name)
            # Convert Path to string for TOML serialization
            if isinstance(value, Path):
                value = str(value)
            data[field_name] = value

        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            tomli_w.dump(data, f)

    @classmethod
    def load_from_file(
        cls, path: Path, data_dir: Path | None = None
    ) -> StickyConfig:
        """Load configuration from a TOML file.

        Args:
            path: Path to the TOML config file.
            data_dir: Override for data_dir (since it's not saved to file).

        Returns:
            A StickyConfig instance with values from the file.
        """
        with open(path, "rb") as f:
            data = tomllib.load(f)

        if data_dir is not None:
            data["data_dir"] = data_dir

        return cls(**data)

    def to_display_dict(self) -> dict[str, dict[str, Any]]:
        """Return a dict with value and source info per key.

        API keys are masked in the output. Source is determined by
        checking if an env var is set, then if a config file exists,
        otherwise 'default'.

        Returns:
            Dict mapping key names to dicts with 'value' and 'source'.
        """
        result: dict[str, dict[str, Any]] = {}

        for field_name in StickyConfig.model_fields:
            value = getattr(self, field_name)
            env_key = f"{_ENV_PREFIX}{field_name.upper()}"

            # Determine source
            if env_key in os.environ:
                source = "env"
            elif self.config_file.exists():
                # Check if key is in the config file
                try:
                    with open(self.config_file, "rb") as f:
                        file_data = tomllib.load(f)
                    source = "config file" if field_name in file_data else "default"
                except (OSError, tomllib.TOMLDecodeError):
                    source = "default"
            else:
                source = "default"

            # Mask sensitive values
            display_value = value
            if field_name in _MASK_KEYS and value:
                # Show first 4 chars then mask the rest
                s = str(value)
                if len(s) > 4:
                    display_value = s[:4] + "*" * (len(s) - 4)
                else:
                    display_value = "*" * len(s)

            # Convert Path to string for display
            if isinstance(display_value, Path):
                display_value = str(display_value)

            result[field_name] = {
                "value": display_value,
                "source": source,
            }

        return result


# --- Singleton accessor ---

_config_instance: StickyConfig | None = None


def get_config(force_reload: bool = False) -> StickyConfig:
    """Get the singleton configuration instance.

    Loads configuration with precedence: env vars > TOML config file > defaults.

    Args:
        force_reload: If True, reload configuration from sources.

    Returns:
        The global StickyConfig instance.
    """
    global _config_instance

    if _config_instance is not None and not force_reload:
        return _config_instance

    # Start with defaults
    kwargs: dict[str, Any] = {}

    # Load from TOML config file if it exists
    config_file = _default_config_dir() / "config.toml"
    if config_file.exists():
        try:
            with open(config_file, "rb") as f:
                file_data = tomllib.load(f)
            kwargs.update(file_data)
        except (OSError, tomllib.TOMLDecodeError):
            pass

    # Override with environment variables (highest precedence)
    for field_name, field_info in StickyConfig.model_fields.items():
        env_key = f"{_ENV_PREFIX}{field_name.upper()}"
        env_value = os.environ.get(env_key)
        if env_value is not None:
            # Convert env string to appropriate type
            annotation = field_info.annotation
            if annotation is bool:
                kwargs[field_name] = env_value.lower() in ("true", "1", "yes")
            elif annotation is int:
                kwargs[field_name] = int(env_value)
            elif annotation is float:
                kwargs[field_name] = float(env_value)
            elif annotation is Path:
                kwargs[field_name] = Path(env_value)
            else:
                kwargs[field_name] = env_value

    _config_instance = StickyConfig(**kwargs)
    return _config_instance
