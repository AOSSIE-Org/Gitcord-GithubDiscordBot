from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import ValidationError

from ghdcbot.config.models import BotConfig, RemoteConfigSettings
from ghdcbot.config.remote import (
    CACHE_FILENAME,
    CACHE_META_FILENAME,
    apply_bootstrap_overlays,
    fetch_remote_config_yaml,
)
from ghdcbot.core.errors import ConfigError

logger = logging.getLogger(__name__)

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
            # Loader subclasses yaml.SafeLoader; !include only resolves relative paths.
            return yaml.load(handle, Loader=Loader)  # nosec B506
    except yaml.YAMLError as exc:
        raise ConfigError(f"Failed to parse YAML: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"Failed to read config file: {config_path}") from exc


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    raw = _load_yaml(path)
    if raw is None:
        raise ConfigError(f"Config file is empty: {path}")
    if not isinstance(raw, dict):
        raise ConfigError(f"Config file must be a YAML mapping: {path}")
    return raw


def _write_remote_cache(
    data_dir: str,
    remote_data: dict[str, Any],
    *,
    source: str,
    sha: str | None,
) -> None:
    cache_dir = Path(data_dir)
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = cache_dir / CACHE_FILENAME
        meta_path = cache_dir / CACHE_META_FILENAME
        with cache_path.open("w", encoding="utf-8") as handle:
            yaml.safe_dump(remote_data, handle, sort_keys=False, allow_unicode=True)
        with meta_path.open("w", encoding="utf-8") as handle:
            yaml.safe_dump({"source": source, "sha": sha}, handle, sort_keys=False)
    except OSError as exc:
        logger.warning(
            "Failed to write remote config cache",
            extra={"data_dir": data_dir, "error": str(exc)},
        )


def _read_remote_cache(data_dir: str) -> tuple[dict[str, Any], str | None, str | None] | None:
    cache_path = Path(data_dir) / CACHE_FILENAME
    if not cache_path.is_file():
        return None
    try:
        data = _load_yaml_mapping(cache_path)
    except ConfigError:
        return None
    source = None
    sha = None
    meta_path = Path(data_dir) / CACHE_META_FILENAME
    if meta_path.is_file():
        try:
            meta = _load_yaml_mapping(meta_path)
            source = meta.get("source") if isinstance(meta.get("source"), str) else None
            sha = meta.get("sha") if isinstance(meta.get("sha"), str) else None
        except ConfigError:
            pass
    return data, source, sha


def _resolve_remote_settings(bootstrap: dict[str, Any]) -> RemoteConfigSettings | None:
    raw = bootstrap.get("remote_config")
    if raw is None:
        return None
    try:
        return RemoteConfigSettings.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"Invalid remote_config: {exc}") from exc


def _merge_remote_into_bootstrap(bootstrap: dict[str, Any]) -> dict[str, Any]:
    settings = _resolve_remote_settings(bootstrap)
    if settings is None or not settings.enabled:
        return bootstrap

    data_dir = ""
    runtime = bootstrap.get("runtime")
    if isinstance(runtime, dict):
        data_dir = str(runtime.get("data_dir") or "")

    api_base = "https://api.github.com"
    github = bootstrap.get("github")
    if isinstance(github, dict) and github.get("api_base"):
        api_base = str(github["api_base"]).rstrip("/")

    pat = (os.getenv("GITHUB_TOKEN") or "").strip()

    try:
        fetched = fetch_remote_config_yaml(
            owner=settings.owner.strip(),
            repo=settings.repo.strip(),
            path=settings.path.strip(),
            ref=(settings.ref.strip() if settings.ref else None),
            api_base=api_base,
            pat=pat,
        )
        if data_dir:
            _write_remote_cache(
                data_dir,
                fetched.data,
                source=fetched.source,
                sha=fetched.sha,
            )
        logger.info(
            "Loaded remote config from %s%s",
            fetched.source,
            f" sha={fetched.sha}" if fetched.sha else "",
        )
        return apply_bootstrap_overlays(fetched.data, bootstrap)
    except ConfigError as fetch_exc:
        if not data_dir:
            raise
        cached = _read_remote_cache(data_dir)
        if cached is None:
            raise ConfigError(
                f"{fetch_exc} No local remote_config_cache.yaml under {data_dir}."
            ) from fetch_exc
        remote_data, source, sha = cached
        logger.warning(
            "Remote config fetch failed; using cached config",
            extra={
                "error": str(fetch_exc),
                "cache_source": source,
                "cache_sha": sha,
                "data_dir": data_dir,
            },
        )
        return apply_bootstrap_overlays(remote_data, bootstrap)


def load_config(path: str) -> BotConfig:
    load_dotenv()
    config_path = Path(path)
    if not config_path.exists() or not config_path.is_file():
        raise ConfigError(f"Config file does not exist: {path}")

    bootstrap = _load_yaml_mapping(config_path)
    raw = _merge_remote_into_bootstrap(bootstrap)

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
