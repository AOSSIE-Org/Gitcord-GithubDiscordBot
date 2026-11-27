"""Tests for ${VAR} expansion in the config loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from ghdcbot.config.loader import ConfigError, _expand_env_vars


def test_expand_env_vars_replaces_set_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "abc")

    assert _expand_env_vars({"discord": {"token": "${DISCORD_TOKEN}"}}) == {
        "discord": {"token": "abc"}
    }


def test_expand_env_vars_rejects_missing_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)

    with pytest.raises(ConfigError, match="DISCORD_TOKEN"):
        _expand_env_vars("${DISCORD_TOKEN}")


def test_expand_env_vars_rejects_empty_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "")

    with pytest.raises(ConfigError, match="GITHUB_TOKEN"):
        _expand_env_vars(["${GITHUB_TOKEN}"])


def test_load_config_rejects_empty_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "test-token-github")
    monkeypatch.setenv("DISCORD_TOKEN", "")
    config_path = Path(__file__).resolve().parent.parent / "config" / "example.yaml"

    from ghdcbot.config.loader import load_config

    with pytest.raises(ConfigError, match="DISCORD_TOKEN"):
        load_config(str(config_path))
