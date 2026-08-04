"""Tests for remote gitcord.yaml loading, merge, and cache fallback."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest
import yaml

from ghdcbot.config.loader import load_config
from ghdcbot.config.remote import (
    apply_bootstrap_overlays,
    deep_merge,
    fetch_remote_config_yaml,
)
from ghdcbot.core.errors import ConfigError


def _contents_response(text: str, *, sha: str = "abc123") -> httpx.Response:
    payload = {
        "encoding": "base64",
        "content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
        "sha": sha,
        "type": "file",
        "name": "gitcord.yaml",
        "path": "gitcord.yaml",
    }
    return httpx.Response(200, json=payload)


def test_deep_merge_nested_overlay_wins() -> None:
    base = {"runtime": {"data_dir": "/data", "mode": "dry-run"}, "github": {"token": "local"}}
    overlay = {"runtime": {"mode": "active"}, "github": {"org": "AOSSIE-Org"}}
    merged = deep_merge(base, overlay)
    assert merged["runtime"]["data_dir"] == "/data"
    assert merged["runtime"]["mode"] == "active"
    assert merged["github"]["token"] == "local"
    assert merged["github"]["org"] == "AOSSIE-Org"


def test_apply_bootstrap_overlays_keeps_secrets_and_data_dir() -> None:
    remote = {
        "runtime": {
            "mode": "active",
            "log_level": "INFO",
            "data_dir": "/should-not-win",
            "github_adapter": "ghdcbot.adapters.github.rest:GitHubRestAdapter",
            "discord_adapter": "ghdcbot.adapters.discord.api:DiscordApiAdapter",
            "storage_adapter": "ghdcbot.adapters.storage.sqlite:SqliteStorage",
        },
        "github": {"org": "AOSSIE-Org", "permissions": {"read": True, "write": True}},
        "discord": {"guild_id": "1", "permissions": {"read": True, "write": True}},
    }
    bootstrap = {
        "runtime": {"data_dir": "/data"},
        "remote_config": {
            "enabled": True,
            "owner": "AOSSIE-Org",
            "repo": ".github",
            "path": "gitcord.yaml",
        },
        "github": {"token": "${GITHUB_TOKEN}"},
        "discord": {"token": "${DISCORD_TOKEN}"},
    }
    merged = apply_bootstrap_overlays(remote, bootstrap)
    assert merged["runtime"]["data_dir"] == "/data"
    assert merged["runtime"]["mode"] == "active"
    assert merged["github"]["token"] == "${GITHUB_TOKEN}"
    assert merged["github"]["org"] == "AOSSIE-Org"
    assert merged["discord"]["token"] == "${DISCORD_TOKEN}"
    assert merged["remote_config"]["enabled"] is True


def test_fetch_remote_config_yaml_decodes_contents(monkeypatch: pytest.MonkeyPatch) -> None:
    remote_yaml = "runtime:\n  mode: active\ngithub:\n  org: AOSSIE-Org\n"
    client = MagicMock()
    client.get.return_value = _contents_response(remote_yaml, sha="deadbeef")
    client.close = MagicMock()
    monkeypatch.setattr(
        "ghdcbot.config.remote.build_github_httpx_client",
        lambda *args, **kwargs: client,
    )
    monkeypatch.setattr(
        "ghdcbot.config.remote.resolve_github_token",
        lambda **kwargs: "tok",
    )

    result = fetch_remote_config_yaml(
        owner="AOSSIE-Org",
        repo=".github",
        path="gitcord.yaml",
        ref="main",
        pat="tok",
    )
    assert result.sha == "deadbeef"
    assert result.data["github"]["org"] == "AOSSIE-Org"
    assert "AOSSIE-Org/.github/gitcord.yaml" in result.source


def test_fetch_remote_config_yaml_404(monkeypatch: pytest.MonkeyPatch) -> None:
    client = MagicMock()
    client.get.return_value = httpx.Response(404, text="Not Found")
    client.close = MagicMock()
    monkeypatch.setattr(
        "ghdcbot.config.remote.build_github_httpx_client",
        lambda *args, **kwargs: client,
    )
    monkeypatch.setattr(
        "ghdcbot.config.remote.resolve_github_token",
        lambda **kwargs: "tok",
    )
    with pytest.raises(ConfigError, match="not found"):
        fetch_remote_config_yaml(owner="AOSSIE-Org", repo=".github", path="missing.yaml")


def _minimal_remote_dict() -> dict[str, Any]:
    return {
        "runtime": {
            "mode": "dry-run",
            "log_level": "INFO",
            "data_dir": "/ignored",
            "github_adapter": "ghdcbot.adapters.github.rest:GitHubRestAdapter",
            "discord_adapter": "ghdcbot.adapters.discord.api:DiscordApiAdapter",
            "storage_adapter": "ghdcbot.adapters.storage.sqlite:SqliteStorage",
            "activity_period_days": 7,
        },
        "github": {
            "org": "AOSSIE-Org",
            "api_base": "https://api.github.com",
            "permissions": {"read": True, "write": False},
        },
        "discord": {
            "guild_id": "123456789012345678",
            "permissions": {"read": True, "write": False},
        },
        "assignments": {},
    }


def test_load_config_remote_merge_and_env_expand(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    bootstrap = {
        "runtime": {"data_dir": str(data_dir)},
        "remote_config": {
            "enabled": True,
            "owner": "AOSSIE-Org",
            "repo": ".github",
            "path": "gitcord.yaml",
            "ref": "main",
        },
        "github": {"token": "${GITHUB_TOKEN}"},
        "discord": {"token": "${DISCORD_TOKEN}"},
    }
    bootstrap_path = tmp_path / "bootstrap.yaml"
    bootstrap_path.write_text(yaml.safe_dump(bootstrap), encoding="utf-8")

    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
    monkeypatch.setenv("DISCORD_TOKEN", "discord_test")
    monkeypatch.setattr(
        "ghdcbot.config.loader.fetch_remote_config_yaml",
        lambda **kwargs: MagicMock(
            data=_minimal_remote_dict(),
            sha="cafef00d",
            source="AOSSIE-Org/.github/gitcord.yaml@main",
        ),
    )

    config = load_config(str(bootstrap_path))
    assert config.github.org == "AOSSIE-Org"
    assert config.github.token == "ghp_test"
    assert config.discord.token == "discord_test"
    assert config.runtime.data_dir == str(data_dir)
    assert config.remote_config is not None
    assert config.remote_config.enabled is True
    assert (data_dir / "remote_config_cache.yaml").is_file()


def test_load_config_uses_cache_when_fetch_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    cache = _minimal_remote_dict()
    (data_dir / "remote_config_cache.yaml").write_text(
        yaml.safe_dump(cache), encoding="utf-8"
    )
    (data_dir / "remote_config_cache.meta.yaml").write_text(
        yaml.safe_dump({"source": "cached", "sha": "old"}),
        encoding="utf-8",
    )

    bootstrap = {
        "runtime": {"data_dir": str(data_dir)},
        "remote_config": {
            "enabled": True,
            "owner": "AOSSIE-Org",
            "repo": ".github",
            "path": "gitcord.yaml",
        },
        "github": {"token": "${GITHUB_TOKEN}"},
        "discord": {"token": "${DISCORD_TOKEN}"},
    }
    bootstrap_path = tmp_path / "bootstrap.yaml"
    bootstrap_path.write_text(yaml.safe_dump(bootstrap), encoding="utf-8")

    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
    monkeypatch.setenv("DISCORD_TOKEN", "discord_test")

    def _boom(**kwargs: object) -> None:
        raise ConfigError("network down")

    monkeypatch.setattr("ghdcbot.config.loader.fetch_remote_config_yaml", _boom)

    config = load_config(str(bootstrap_path))
    assert config.github.org == "AOSSIE-Org"
    assert config.github.token == "ghp_test"


def test_load_config_fetch_failure_without_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    bootstrap = {
        "runtime": {"data_dir": str(data_dir)},
        "remote_config": {
            "enabled": True,
            "owner": "AOSSIE-Org",
            "repo": ".github",
            "path": "gitcord.yaml",
        },
        "github": {"token": "${GITHUB_TOKEN}"},
        "discord": {"token": "${DISCORD_TOKEN}"},
    }
    bootstrap_path = tmp_path / "bootstrap.yaml"
    bootstrap_path.write_text(yaml.safe_dump(bootstrap), encoding="utf-8")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
    monkeypatch.setenv("DISCORD_TOKEN", "discord_test")

    def _boom(**kwargs: object) -> None:
        raise ConfigError("network down")

    monkeypatch.setattr("ghdcbot.config.loader.fetch_remote_config_yaml", _boom)

    with pytest.raises(ConfigError, match="No local remote_config_cache"):
        load_config(str(bootstrap_path))


def test_load_config_local_only_still_works(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
    monkeypatch.setenv("DISCORD_TOKEN", "discord_test")
    cfg = {
        "runtime": {
            "mode": "dry-run",
            "log_level": "INFO",
            "data_dir": str(tmp_path / "data"),
            "github_adapter": "ghdcbot.adapters.github.rest:GitHubRestAdapter",
            "discord_adapter": "ghdcbot.adapters.discord.api:DiscordApiAdapter",
            "storage_adapter": "ghdcbot.adapters.storage.sqlite:SqliteStorage",
        },
        "github": {
            "org": "example-org",
            "token": "${GITHUB_TOKEN}",
            "api_base": "https://api.github.com",
            "permissions": {"read": True, "write": False},
        },
        "discord": {
            "guild_id": "123456789012345678",
            "token": "${DISCORD_TOKEN}",
            "permissions": {"read": True, "write": False},
        },
    }
    path = tmp_path / "local.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    config = load_config(str(path))
    assert config.github.org == "example-org"
    assert config.remote_config is None
