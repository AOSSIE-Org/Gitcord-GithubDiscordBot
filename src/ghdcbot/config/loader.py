from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import ValidationError

from ghdcbot.config.models import BotConfig
from ghdcbot.config.validation import (
    config_not_found_message,
    empty_env_var_message,
    invalid_yaml_message,
    missing_env_var_message,
    validate_active_mode,
)
from ghdcbot.core.errors import ConfigError

_ACTIVE_CONFIG: BotConfig | None = None
_ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)\}")


def load_config(path: str) -> BotConfig:
    load_dotenv()
    config_path = Path(path)
    if not config_path.exists() or not config_path.is_file():
        raise ConfigError(config_not_found_message(path))
    try:
        raw_text = config_path.read_text(encoding="utf-8")
        raw: Any = yaml.safe_load(raw_text)
    except OSError as exc:
        raise ConfigError(f"Failed to read config file: {path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(invalid_yaml_message(path, str(exc))) from exc

    if raw is None:
        raise ConfigError(f"Config file is empty: {path}")

    try:
        expanded = _expand_env_vars(raw)
        config = BotConfig.model_validate(expanded)
        validate_active_mode(config)
        global _ACTIVE_CONFIG
        _ACTIVE_CONFIG = config
        return config
    except ValidationError as exc:
        raise ConfigError(_format_validation_error(path, exc)) from exc


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
        raise ConfigError(missing_env_var_message(env_key))
    if not env_value.strip():
        raise ConfigError(empty_env_var_message(env_key))
    return env_value


def _format_validation_error(path: str, exc: ValidationError) -> str:
    lines = [f"Invalid configuration in {path}:"]
    for err in exc.errors():
        loc = ".".join(str(part) for part in err.get("loc", ()))
        msg = err.get("msg", "invalid value")
        lines.append(f"  - {loc}: {msg}" if loc else f"  - {msg}")
    return "\n".join(lines)
