"""Fetch Gitcord YAML configuration from a GitHub repository (Contents API)."""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from typing import Any

import httpx
import yaml

from ghdcbot.adapters.github.app_auth import build_github_httpx_client, resolve_github_token
from ghdcbot.core.errors import ConfigError

logger = logging.getLogger(__name__)

CACHE_FILENAME = "remote_config_cache.yaml"
CACHE_META_FILENAME = "remote_config_cache.meta.yaml"


@dataclass(frozen=True)
class RemoteConfigFetchResult:
    """Parsed remote config plus provenance for logging/caching."""

    data: dict[str, Any]
    sha: str | None
    source: str


def deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge overlay onto base; overlay values win for non-dict keys."""
    result: dict[str, Any] = dict(base)
    for key, value in overlay.items():
        existing = result.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            result[key] = deep_merge(existing, value)
        else:
            result[key] = value
    return result


def fetch_remote_config_yaml(
    *,
    owner: str,
    repo: str,
    path: str,
    ref: str | None = None,
    api_base: str = "https://api.github.com",
    pat: str = "",
) -> RemoteConfigFetchResult:
    """Download and parse a YAML file from GitHub Contents API."""
    token = resolve_github_token(pat=pat, api_base=api_base)
    source = f"{owner}/{repo}/{path}"
    if ref:
        source = f"{source}@{ref}"

    client = build_github_httpx_client(token, api_base=api_base.rstrip("/"), timeout=30.0)
    try:
        params: dict[str, str] = {}
        if ref:
            params["ref"] = ref
        response = client.get(f"/repos/{owner}/{repo}/contents/{path.lstrip('/')}", params=params)
    except httpx.HTTPError as exc:
        raise ConfigError(f"Failed to fetch remote config {source}: {exc}") from exc
    finally:
        client.close()

    if response.status_code == 404:
        raise ConfigError(
            f"Remote config not found: {source}. "
            "Create gitcord.yaml in the org .github repo (or fix remote_config.path)."
        )
    if response.status_code in {401, 403}:
        raise ConfigError(
            f"Permission denied fetching remote config {source} "
            f"(HTTP {response.status_code}). Check GitHub token/App installation access."
        )
    if response.status_code != 200:
        raise ConfigError(
            f"Failed to fetch remote config {source}: "
            f"HTTP {response.status_code} {(response.text or '')[:200]}"
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise ConfigError(f"Invalid JSON from GitHub Contents API for {source}") from exc

    if not isinstance(payload, dict):
        raise ConfigError(f"Unexpected Contents API payload for {source}")

    encoding = payload.get("encoding")
    content_b64 = payload.get("content")
    if encoding != "base64" or not isinstance(content_b64, str):
        raise ConfigError(
            f"Remote config {source} is not a regular file (encoding={encoding!r}). "
            "Directories and symlinks are not supported."
        )

    try:
        raw_text = base64.b64decode(content_b64).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise ConfigError(f"Failed to decode remote config {source}: {exc}") from exc

    try:
        data = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Failed to parse remote config YAML {source}: {exc}") from exc

    if data is None:
        raise ConfigError(f"Remote config is empty: {source}")
    if not isinstance(data, dict):
        raise ConfigError(f"Remote config must be a YAML mapping: {source}")

    sha = payload.get("sha") if isinstance(payload.get("sha"), str) else None
    logger.info(
        "Fetched remote Gitcord config",
        extra={"source": source, "sha": sha},
    )
    return RemoteConfigFetchResult(data=data, sha=sha, source=source)


def apply_bootstrap_overlays(
    remote_data: dict[str, Any],
    bootstrap: dict[str, Any],
) -> dict[str, Any]:
    """Merge remote org settings with local bootstrap secrets and machine paths.

    Remote wins for shared org keys; bootstrap then overlays:
    - remote_config
    - runtime.data_dir
    - github.token / discord.token
    """
    merged = deep_merge(bootstrap, remote_data)

    remote_cfg = bootstrap.get("remote_config")
    if isinstance(remote_cfg, dict):
        merged["remote_config"] = dict(remote_cfg)

    bootstrap_runtime = bootstrap.get("runtime")
    if isinstance(bootstrap_runtime, dict) and bootstrap_runtime.get("data_dir"):
        runtime = merged.setdefault("runtime", {})
        if isinstance(runtime, dict):
            runtime["data_dir"] = bootstrap_runtime["data_dir"]

    bootstrap_github = bootstrap.get("github")
    if isinstance(bootstrap_github, dict) and "token" in bootstrap_github:
        github = merged.setdefault("github", {})
        if isinstance(github, dict):
            github["token"] = bootstrap_github["token"]

    bootstrap_discord = bootstrap.get("discord")
    if isinstance(bootstrap_discord, dict) and "token" in bootstrap_discord:
        discord = merged.setdefault("discord", {})
        if isinstance(discord, dict):
            discord["token"] = bootstrap_discord["token"]

    return merged
