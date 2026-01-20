from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from ghdcbot.config.models import BotConfig
from ghdcbot.core.errors import ConfigError

_ACTIVE_CONFIG: BotConfig | None = None
_ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)\}")


def load_config(path: str) -> BotConfig:
    config_path = Path(path)
    if not config_path.exists() or not config_path.is_file():
        raise ConfigError(f"Config file does not exist: {path}")
    try:
        raw_text = config_path.read_text(encoding="utf-8")
        raw: Any = yaml.safe_load(raw_text)
    except OSError as exc:
        raise ConfigError(f"Failed to read config file: {path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"Failed to parse YAML: {exc}") from exc

    try:
        expanded = _expand_env_vars(raw)
        config = BotConfig.model_validate(expanded)
        global _ACTIVE_CONFIG
        _ACTIVE_CONFIG = config
        return config
    except ValidationError as exc:
        raise ConfigError(f"Invalid configuration: {exc}") from exc


def get_active_config() -> BotConfig | None:
    """Return the last loaded config for adapter access."""
    return _ACTIVE_CONFIG


def _expand_env_vars(value: Any) -> Any:
    """Recursively expand ${VAR} in strings using environment variables."""
    if isinstance(value, dict):
        return {key: _expand_env_vars(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_expand_env_vars(item) for item in value]
    if isinstance(value, str):
        return _ENV_PATTERN.sub(_replace_env_var, value)
    return value


def _replace_env_var(match: re.Match[str]) -> str:
    env_key = match.group(1)
    env_value = os.getenv(env_key)
    if env_value is None:
        raise ConfigError(f"Missing required environment variable: {env_key}")
    return env_value
