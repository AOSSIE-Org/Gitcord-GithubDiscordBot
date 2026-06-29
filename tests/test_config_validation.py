"""Tests for startup configuration validation (Week 4 Day 3)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from ghdcbot.config.loader import load_config
from ghdcbot.core.errors import ConfigError

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_CONFIG_PATH = REPO_ROOT / "config" / "example.yaml"


def _minimal_config_yaml(
    *,
    mode: str = "dry-run",
    enable_discord_role_updates: bool = True,
    guild_id: str = "123456789012345678",
) -> str:
    return f"""
runtime:
  mode: "{mode}"
  log_level: "INFO"
  data_dir: "/tmp/ghdcbot-test"
  enable_discord_role_updates: {str(enable_discord_role_updates).lower()}
  github_adapter: "ghdcbot.adapters.github.rest:GitHubRestAdapter"
  discord_adapter: "ghdcbot.adapters.discord.api:DiscordApiAdapter"
  storage_adapter: "ghdcbot.adapters.storage.sqlite:SqliteStorage"
github:
  org: "example-org"
  token: "${{GITHUB_TOKEN}}"
  api_base: "https://api.github.com"
discord:
  guild_id: "{guild_id}"
  token: "${{DISCORD_TOKEN}}"
scoring:
  period_days: 30
  weights:
    pr_merged: 5
role_mappings:
  - discord_role: "Contributor"
    min_score: 10
assignments:
  review_roles: []
  issue_assignees: []
identity_mappings: []
"""


@pytest.fixture
def disable_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    import ghdcbot.config.loader as loader_module

    monkeypatch.setattr(loader_module, "load_dotenv", lambda: None)


def test_missing_github_token_fails_with_clear_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, disable_dotenv: None
) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("DISCORD_TOKEN", "discord-token")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(_minimal_config_yaml())

    with pytest.raises(ConfigError) as excinfo:
        load_config(str(config_path))

    message = str(excinfo.value)
    assert "GITHUB_TOKEN is missing" in message
    assert "Personal Access Token" in message or ".env" in message


def test_empty_github_token_fails_with_clear_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, disable_dotenv: None
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "")
    monkeypatch.setenv("DISCORD_TOKEN", "discord-token")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(_minimal_config_yaml())

    with pytest.raises(ConfigError) as excinfo:
        load_config(str(config_path))

    message = str(excinfo.value)
    assert "GITHUB_TOKEN is configured but empty" in message
    assert "GitHub Personal Access Token" in message


def test_missing_discord_token_fails_with_clear_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, disable_dotenv: None
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "github-token")
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(_minimal_config_yaml())

    with pytest.raises(ConfigError) as excinfo:
        load_config(str(config_path))

    message = str(excinfo.value)
    assert "DISCORD_TOKEN is missing" in message
    assert "Discord" in message


def test_missing_config_file_fails_with_clear_message(tmp_path: Path) -> None:
    missing = tmp_path / "config" / "config.yaml"
    with pytest.raises(ConfigError) as excinfo:
        load_config(str(missing))

    message = str(excinfo.value)
    assert "Config file not found" in message
    assert str(missing) in message
    assert "docker-example.yaml" in message


def test_invalid_yaml_fails_with_clear_message(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("runtime:\n  mode: [unclosed\n")
    with pytest.raises(ConfigError) as excinfo:
        load_config(str(config_path))

    message = str(excinfo.value)
    assert "Invalid YAML syntax detected" in message
    assert str(config_path) in message


def test_active_mode_misconfiguration_fails_with_helpful_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, disable_dotenv: None
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "github-token")
    monkeypatch.setenv("DISCORD_TOKEN", "discord-token")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        _minimal_config_yaml(
            mode="active",
            enable_discord_role_updates=True,
            guild_id="000000000000000000",
        )
    )

    with pytest.raises(ConfigError) as excinfo:
        load_config(str(config_path))

    message = str(excinfo.value)
    assert "Active mode requires" in message
    assert "guild_id" in message


def test_active_mode_role_updates_require_role_mappings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, disable_dotenv: None
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "github-token")
    monkeypatch.setenv("DISCORD_TOKEN", "discord-token")
    config_path = tmp_path / "config.yaml"
    yaml_text = _minimal_config_yaml(
        mode="active",
        enable_discord_role_updates=True,
        guild_id="123456789012345678",
    ).replace(
        "role_mappings:\n  - discord_role: \"Contributor\"\n    min_score: 10",
        "role_mappings: []",
    )
    config_path.write_text(yaml_text)

    with pytest.raises(ConfigError) as excinfo:
        load_config(str(config_path))

    message = str(excinfo.value)
    assert "role_mappings" in message
    assert "empty" in message.lower() or "Active mode requires" in message


def test_cli_exits_nonzero_on_missing_config(tmp_path: Path) -> None:
    missing = tmp_path / "nope.yaml"
    result = subprocess.run(
        [sys.executable, "-m", "ghdcbot.cli", "--config", str(missing), "run-once"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode != 0
    assert "Config file not found" in result.stderr


def test_example_config_loads_with_valid_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "test-token-github")
    monkeypatch.setenv("DISCORD_TOKEN", "test-token-discord")
    config = load_config(str(EXAMPLE_CONFIG_PATH))
    assert config.runtime.mode.value == "dry-run"
