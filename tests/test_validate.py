"""Tests for ghdcbot validate (Week 4 Day 4)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from ghdcbot.config.setup_validate import run_validate


def _minimal_config_yaml(
    *,
    mode: str = "dry-run",
    enable_discord_role_updates: bool = True,
    guild_id: str = "123456789012345678",
    discord_role: str = "Contributor",
    extra_roles: list[str] | None = None,
) -> str:
    review_roles = extra_roles or []
    review_yaml = "\n".join(f'    - "{r}"' for r in review_roles)
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
  - discord_role: "{discord_role}"
    min_score: 10
assignments:
  review_roles:
{review_yaml if review_roles else "    []"}
  issue_assignees: []
  issue_request_eligible_roles: []
identity_mappings: []
"""


@dataclass
class _MockResponse:
    status_code: int
    payload: Any = field(default_factory=dict)

    def json(self) -> Any:
        return self.payload


class _MockHttpClient:
    def __init__(self, routes: dict[str, _MockResponse]) -> None:
        self._routes = routes

    def get(self, path: str, **kwargs: Any) -> _MockResponse:
        for key, response in self._routes.items():
            if path == key or path.startswith(key):
                return response
        return _MockResponse(404, {})

    def close(self) -> None:
        return None


def _github_ok_routes() -> dict[str, _MockResponse]:
    return {
        "/user": _MockResponse(200, {"login": "test-user"}),
        "/orgs/example-org": _MockResponse(200, {"login": "example-org"}),
        "/orgs/example-org/repos": _MockResponse(
            200,
            [{"name": "repo-one"}, {"name": "repo-two"}],
        ),
    }


def _discord_ok_routes() -> dict[str, _MockResponse]:
    return {
        "/users/@me": _MockResponse(200, {"username": "gitcord-bot"}),
        "/guilds/123456789012345678": _MockResponse(200, {"name": "Test Guild"}),
    }


@pytest.fixture
def disable_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    import ghdcbot.config.loader as loader_module

    monkeypatch.setattr(loader_module, "load_dotenv", lambda: None)


@pytest.fixture
def env_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test_token")
    monkeypatch.setenv("DISCORD_TOKEN", "discord_test_token")


def test_validate_passes_with_valid_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    disable_dotenv: None,
    env_tokens: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(_minimal_config_yaml(extra_roles=["Mentor"]))

    github_client = _MockHttpClient(_github_ok_routes())
    discord_client = _MockHttpClient(_discord_ok_routes())
    mock_adapter = MagicMock()
    mock_adapter.list_roles.return_value = [
        {"id": "1", "name": "Contributor"},
        {"id": "2", "name": "Mentor"},
    ]
    mock_adapter.close = MagicMock()

    def fake_client(**kwargs: Any) -> _MockHttpClient:
        base = str(kwargs.get("base_url", ""))
        if "github" in base:
            return github_client
        return discord_client

    with (
        patch("ghdcbot.config.setup_validate.httpx.Client", side_effect=fake_client),
        patch("ghdcbot.config.setup_validate.build_adapter", return_value=mock_adapter),
    ):
        code = run_validate(str(config_path))

    output = capsys.readouterr().out
    assert code == 0
    assert "✓ Config file loaded" in output
    assert "✓ GitHub authentication successful" in output
    assert "✓ Discord authentication successful" in output
    assert "✓ Guild found" in output
    assert "Guild: Test Guild" in output
    assert '✓ Role "Contributor" found' in output
    assert '✓ Role "Mentor" found' in output
    assert "Validation passed." in output


def test_validate_fails_when_github_token_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    disable_dotenv: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("DISCORD_TOKEN", "discord_test_token")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(_minimal_config_yaml())

    code = run_validate(str(config_path))

    output = capsys.readouterr().out
    assert code == 1
    assert "✗ Config file could not be loaded" in output
    assert "GITHUB_TOKEN" in output
    assert "Validation failed." in output


def test_validate_fails_when_discord_token_invalid(
    tmp_path: Path,
    disable_dotenv: None,
    env_tokens: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(_minimal_config_yaml())

    github_client = _MockHttpClient(_github_ok_routes())
    discord_client = _MockHttpClient(
        {"/users/@me": _MockResponse(401, {"message": "401: Unauthorized"})}
    )

    def fake_client(**kwargs: Any) -> _MockHttpClient:
        base = str(kwargs.get("base_url", ""))
        if "github" in base:
            return github_client
        return discord_client

    mock_adapter = MagicMock()
    mock_adapter.close = MagicMock()

    with (
        patch("ghdcbot.config.setup_validate.httpx.Client", side_effect=fake_client),
        patch("ghdcbot.config.setup_validate.build_adapter", return_value=mock_adapter),
    ):
        code = run_validate(str(config_path))

    output = capsys.readouterr().out
    assert code == 1
    assert "✓ GitHub authentication successful" in output
    assert "✗ Invalid DISCORD_TOKEN" in output
    assert "Please update DISCORD_TOKEN" in output


def test_validate_fails_when_guild_missing(
    tmp_path: Path,
    disable_dotenv: None,
    env_tokens: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(_minimal_config_yaml(enable_discord_role_updates=False))

    github_client = _MockHttpClient(_github_ok_routes())
    discord_routes = _discord_ok_routes()
    discord_routes["/guilds/123456789012345678"] = _MockResponse(404, {"message": "Unknown Guild"})
    discord_client = _MockHttpClient(discord_routes)
    mock_adapter = MagicMock()
    mock_adapter.close = MagicMock()

    def fake_client(**kwargs: Any) -> _MockHttpClient:
        base = str(kwargs.get("base_url", ""))
        if "github" in base:
            return github_client
        return discord_client

    with (
        patch("ghdcbot.config.setup_validate.httpx.Client", side_effect=fake_client),
        patch("ghdcbot.config.setup_validate.build_adapter", return_value=mock_adapter),
    ):
        code = run_validate(str(config_path))

    output = capsys.readouterr().out
    assert code == 1
    assert "✗ Guild not found" in output
    assert "⚠ Role mapping check skipped" in output


def test_validate_fails_when_role_missing(
    tmp_path: Path,
    disable_dotenv: None,
    env_tokens: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(_minimal_config_yaml(discord_role="Contributor", extra_roles=["Mentor"]))

    github_client = _MockHttpClient(_github_ok_routes())
    discord_client = _MockHttpClient(_discord_ok_routes())
    mock_adapter = MagicMock()
    mock_adapter.list_roles.return_value = [{"id": "1", "name": "Contributor"}]
    mock_adapter.close = MagicMock()

    def fake_client(**kwargs: Any) -> _MockHttpClient:
        base = str(kwargs.get("base_url", ""))
        if "github" in base:
            return github_client
        return discord_client

    with (
        patch("ghdcbot.config.setup_validate.httpx.Client", side_effect=fake_client),
        patch("ghdcbot.config.setup_validate.build_adapter", return_value=mock_adapter),
    ):
        code = run_validate(str(config_path))

    output = capsys.readouterr().out
    assert code == 1
    assert '✓ Role "Contributor" found' in output
    assert '✗ Role "Mentor" not found' in output
    assert "Validation failed." in output
