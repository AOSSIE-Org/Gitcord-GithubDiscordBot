from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import ValidationError

from ghdcbot.config.models import BotConfig
from ghdcbot.core.errors import ConfigError

_ACTIVE_CONFIG: BotConfig | None = None
_ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)\}")


class _ConfigLoader(yaml.SafeLoader):
    """YAML loader with !include relative to the config file directory."""

    def __init__(self, stream, *, config_dir: Path) -> None:
        super().__init__(stream)
        self.config_dir = config_dir


def _construct_include(loader: _ConfigLoader, node: yaml.Node) -> Any:
    relative = loader.construct_scalar(node)
    include_path = (loader.config_dir / relative).resolve()
    if not include_path.is_file():
        raise ConfigError(f"Included config file does not exist: {relative}")
    return _load_yaml(include_path)


_ConfigLoader.add_constructor("!include", _construct_include)


def _load_yaml(config_path: Path) -> Any:
    config_dir = config_path.parent

    class Loader(_ConfigLoader):
        def __init__(self, stream) -> None:
            super().__init__(stream, config_dir=config_dir)

    try:
        with config_path.open(encoding="utf-8") as handle:
            return yaml.load(handle, Loader=Loader)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Failed to parse YAML: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"Failed to read config file: {config_path}") from exc


def load_config(path: str) -> BotConfig:
    load_dotenv()
    config_path = Path(path)
    if not config_path.exists() or not config_path.is_file():
        raise ConfigError(f"Config file does not exist: {path}")

    raw = _load_yaml(config_path)
    if raw is None:
        raise ConfigError("Config file is empty")

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
