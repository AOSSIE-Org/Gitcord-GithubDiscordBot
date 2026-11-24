"""Tests for ``ghdcbot validate`` (config + credential preflight)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from ghdcbot.config.setup_validate import run_validate


def _minimal_config_yaml(
    *,
    enable_discord_role_updates: bool = True,
    guild_id: str = "123456789012345678",
    discord_role: str = "Contributor",
    extra_roles: list[str] | None = None,
    github_token_line: str = '  token: "${GITHUB_TOKEN}"',
) -> str:
    review_roles = extra_roles or []
    review_yaml = "\n".join(f'    - "{r}"' for r in review_roles)
    return f"""
runtime:
  mode: "dry-run"
  log_level: "INFO"
  data_dir: "/tmp/ghdcbot-validate-test"
  enable_discord_role_updates: {str(enable_discord_role_updates).lower()}
  github_adapter: "ghdcbot.adapters.github.rest:GitHubRestAdapter"
  discord_adapter: "ghdcbot.adapters.discord.api:DiscordApiAdapter"
  storage_adapter: "ghdcbot.adapters.storage.sqlite:SqliteStorage"
github:
  org: "example-org"
{github_token_line}
  api_base: "https://api.github.com"
discord:
  guild_id: "{guild_id}"
  token: "${{DISCORD_TOKEN}}"
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
        self.requested: list[str] = []
        self.closed = False

    def get(self, path: str, **kwargs: Any) -> _MockResponse:
        self.requested.append(path)
        for key in sorted(self._routes.keys(), key=len, reverse=True):
            if path == key or path.startswith(key):
                return self._routes[key]
        return _MockResponse(404, {})

    def close(self) -> None:
        self.closed = True


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
    for name in ("GITHUB_APP_ID", "GITHUB_APP_INSTALLATION_ID"):
        monkeypatch.delenv(name, raising=False)


def _run(
    config_path: Path,
    *,
    github_client: _MockHttpClient | None = None,
    discord_client: _MockHttpClient | None = None,
    adapter: MagicMock | None = None,
) -> int:
    github_client = github_client or _MockHttpClient(_github_ok_routes())
    discord_client = discord_client or _MockHttpClient(_discord_ok_routes())
    adapter = adapter if adapter is not None else MagicMock()
    with (
        patch(
            "ghdcbot.config.setup_validate.build_github_httpx_client",
            return_value=github_client,
        ),
        patch("ghdcbot.config.setup_validate.httpx.Client", return_value=discord_client),
        patch("ghdcbot.config.setup_validate.build_adapter", return_value=adapter),
    ):
        return run_validate(str(config_path))


def test_validate_passes_with_valid_config(
    tmp_path: Path,
    disable_dotenv: None,
    env_tokens: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(_minimal_config_yaml(extra_roles=["Mentor"]))

    github_client = _MockHttpClient(_github_ok_routes())
    discord_client = _MockHttpClient(_discord_ok_routes())
    adapter = MagicMock()
    adapter.list_roles.return_value = [
        {"id": "1", "name": "Contributor"},
        {"id": "2", "name": "mentor"},
    ]

    code = _run(
        config_path, github_client=github_client, discord_client=discord_client, adapter=adapter
    )

    output = capsys.readouterr().out
    assert code == 0
    assert "✓ Config file loaded" in output
    assert "✓ YAML valid and schema OK" in output
    assert "✓ GITHUB_TOKEN configured" in output
    assert "✓ GitHub authentication successful" in output
    assert "Authenticated as test-user." in output
    assert "✓ Organization accessible" in output
    assert "✓ Repositories visible" in output
    assert "✓ DISCORD_TOKEN configured" in output
    assert "✓ Discord authentication successful" in output
    assert "✓ Guild found" in output
    assert "Guild: Test Guild" in output
    assert '✓ Role "Contributor" found' in output
    assert '✓ Role "Mentor" found' in output  # case-insensitive match
    assert "Validation passed." in output
    assert github_client.closed and discord_client.closed
    adapter.close.assert_called_once()


def test_validate_fails_when_config_missing(
    tmp_path: Path,
    disable_dotenv: None,
    env_tokens: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = run_validate(str(tmp_path / "nope.yaml"))

    output = capsys.readouterr().out
    assert code == 1
    assert "✗ Config file could not be loaded" in output
    assert "Config file does not exist" in output
    assert "Validation failed." in output


def test_validate_fails_when_github_token_env_missing(
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
    assert "Missing required environment variable: GITHUB_TOKEN" in output


def test_validate_fails_when_no_github_credentials_at_all(
    tmp_path: Path,
    disable_dotenv: None,
    env_tokens: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """github.token may be omitted (App auth); with no App env either it is a clear failure."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(_minimal_config_yaml(github_token_line=""))

    github_client = _MockHttpClient(_github_ok_routes())
    code = _run(config_path, github_client=github_client)

    output = capsys.readouterr().out
    assert code == 1
    assert "✗ GitHub credentials not configured" in output
    assert "GITHUB_TOKEN" in output
    assert github_client.requested == []
    # Discord checks still run so the user sees every problem in one go.
    assert "✓ Discord authentication successful" in output


def test_validate_fails_when_github_token_invalid(
    tmp_path: Path,
    disable_dotenv: None,
    env_tokens: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(_minimal_config_yaml())

    github_client = _MockHttpClient({"/user": _MockResponse(401, {"message": "Bad credentials"})})
    code = _run(config_path, github_client=github_client)

    output = capsys.readouterr().out
    assert code == 1
    assert "✗ Invalid GITHUB_TOKEN" in output
    assert "Organization accessible" not in output


def test_validate_reports_org_not_accessible(
    tmp_path: Path,
    disable_dotenv: None,
    env_tokens: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(_minimal_config_yaml())

    routes = _github_ok_routes()
    routes["/orgs/example-org"] = _MockResponse(404, {"message": "Not Found"})
    code = _run(config_path, github_client=_MockHttpClient(routes))

    output = capsys.readouterr().out
    assert code == 1
    assert "✓ GitHub authentication successful" in output
    assert "✗ Organization not accessible" in output
    assert '"example-org"' in output


def test_validate_uses_installation_endpoint_for_github_app(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    disable_dotenv: None,
    env_tokens: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(_minimal_config_yaml(enable_discord_role_updates=False))

    routes = _github_ok_routes()
    routes.pop("/user")
    routes["/installation/repositories"] = _MockResponse(200, {"total_count": 4})
    github_client = _MockHttpClient(routes)

    with patch(
        "ghdcbot.config.setup_validate.resolve_github_token",
        return_value=lambda: "ghs_installation_token",
    ):
        code = _run(config_path, github_client=github_client)

    output = capsys.readouterr().out
    assert code == 0
    assert "✓ GitHub App credentials configured" in output
    assert "✓ GitHub App authentication successful" in output
    assert "Installation can see 4 repo(s)." in output
    assert "/installation/repositories" in github_client.requested
    assert "/user" not in github_client.requested


def test_validate_fails_when_discord_token_invalid(
    tmp_path: Path,
    disable_dotenv: None,
    env_tokens: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(_minimal_config_yaml())

    discord_client = _MockHttpClient(
        {"/users/@me": _MockResponse(401, {"message": "401: Unauthorized"})}
    )
    adapter = MagicMock()
    code = _run(config_path, discord_client=discord_client, adapter=adapter)

    output = capsys.readouterr().out
    assert code == 1
    assert "✓ GitHub authentication successful" in output
    assert "✗ Invalid DISCORD_TOKEN" in output
    assert "Please update DISCORD_TOKEN" in output
    # No point checking the guild or roles with a token Discord rejects.
    assert "Guild" not in output.replace("Guild validation failed.", "")
    assert "⚠ Role mapping check skipped" in output
    adapter.list_roles.assert_not_called()


def test_validate_fails_when_guild_id_is_placeholder(
    tmp_path: Path,
    disable_dotenv: None,
    env_tokens: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(_minimal_config_yaml(guild_id="000000000000000000"))

    code = _run(config_path)

    output = capsys.readouterr().out
    assert code == 1
    assert "✗ Guild ID not configured" in output
    assert "⚠ Role mapping check skipped" in output


def test_validate_fails_when_guild_missing(
    tmp_path: Path,
    disable_dotenv: None,
    env_tokens: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(_minimal_config_yaml(enable_discord_role_updates=False))

    routes = _discord_ok_routes()
    routes["/guilds/123456789012345678"] = _MockResponse(404, {"message": "Unknown Guild"})
    adapter = MagicMock()
    code = _run(config_path, discord_client=_MockHttpClient(routes), adapter=adapter)

    output = capsys.readouterr().out
    assert code == 1
    assert "✗ Guild not found" in output
    assert "⚠ Role mapping check skipped" in output
    assert "runtime.enable_discord_role_updates is false." in output
    adapter.list_roles.assert_not_called()


def test_validate_skips_role_mapping_when_guild_validation_fails(
    tmp_path: Path,
    disable_dotenv: None,
    env_tokens: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(_minimal_config_yaml(extra_roles=["Mentor"]))

    routes = _discord_ok_routes()
    routes["/guilds/123456789012345678"] = _MockResponse(403, {"message": "Missing Access"})
    adapter = MagicMock()
    code = _run(config_path, discord_client=_MockHttpClient(routes), adapter=adapter)

    output = capsys.readouterr().out
    assert code == 1
    assert "✗ Guild not accessible" in output
    assert "⚠ Role mapping check skipped" in output
    assert "Guild validation failed." in output
    assert 'Role "' not in output
    adapter.list_roles.assert_not_called()


def test_validate_fails_when_role_missing(
    tmp_path: Path,
    disable_dotenv: None,
    env_tokens: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(_minimal_config_yaml(discord_role="Contributor", extra_roles=["Mentor"]))

    adapter = MagicMock()
    adapter.list_roles.return_value = [{"id": "1", "name": "Contributor"}]
    code = _run(config_path, adapter=adapter)

    output = capsys.readouterr().out
    assert code == 1
    assert '✓ Role "Contributor" found' in output
    assert '✗ Role "Mentor" not found' in output
    assert "Validation failed." in output


def test_validate_warns_when_roles_cannot_be_listed(
    tmp_path: Path,
    disable_dotenv: None,
    env_tokens: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(_minimal_config_yaml())

    adapter = MagicMock()
    adapter.list_roles.return_value = []
    code = _run(config_path, adapter=adapter)

    output = capsys.readouterr().out
    assert code == 0
    assert "⚠ Discord roles not listed" in output
    assert 'Role "Contributor" not found' not in output


def test_validate_renders_report_on_unexpected_error(
    tmp_path: Path,
    disable_dotenv: None,
    env_tokens: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(_minimal_config_yaml())

    with patch(
        "ghdcbot.config.setup_validate.build_github_httpx_client",
        side_effect=RuntimeError("client init failed"),
    ):
        code = run_validate(str(config_path))

    output = capsys.readouterr().out
    assert code == 1
    assert "✗ Validation error" in output
    assert "RuntimeError: client init failed" in output
    assert "Validation failed." in output


def test_cli_validate_subcommand_exits_with_validator_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ghdcbot import cli

    monkeypatch.setattr("sys.argv", ["ghdcbot", "--config", "cfg.yaml", "validate"])
    with (
        patch("ghdcbot.cli.run_validate", return_value=1) as run_mock,
        pytest.raises(SystemExit) as exc_info,
    ):
        cli.main()

    assert exc_info.value.code == 1
    run_mock.assert_called_once_with("cfg.yaml")
