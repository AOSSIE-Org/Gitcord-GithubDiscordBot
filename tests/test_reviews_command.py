"""Tests for /reviews Discord slash command and GitHub review requests adapter."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from ghdcbot.adapters.github.rest import GitHubRestAdapter
from ghdcbot.config.models import BotConfig, RepoFilterConfig


class _SearchMockClient:
    def __init__(self, payload: dict | None = None, *, status_code: int = 200) -> None:
        self._payload = payload or {"items": []}
        self._status_code = status_code
        self.calls: list[tuple[str, str, dict | None]] = []

    def request(self, method: str, path: str, params: dict | None = None, **kwargs: object) -> httpx.Response:
        self.calls.append((method, path, params))
        return httpx.Response(self._status_code, json=self._payload, headers={"X-RateLimit-Remaining": "10"})


# -----------------------------------------------------------------------------
# Adapter Tests
# -----------------------------------------------------------------------------

def test_list_review_requests_for_user_queries_and_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = GitHubRestAdapter(token="t", org="AOSSIE-Org", api_base="https://api.github.com")
    client = _SearchMockClient({
        "items": [
            {
                "number": 101,
                "title": "Allowed PR",
                "html_url": "https://github.com/AOSSIE-Org/PictoPy/pull/101",
                "created_at": "2026-07-11T10:00:00Z",
                "updated_at": "2026-07-11T12:00:00Z",
                "repository_url": "https://api.github.com/repos/AOSSIE-Org/PictoPy",
                "user": {"login": "bob"},
                "draft": False,
            },
            {
                "number": 202,
                "title": "Denied PR",
                "html_url": "https://github.com/AOSSIE-Org/DeniedRepo/pull/202",
                "created_at": "2026-07-11T11:00:00Z",
                "updated_at": "2026-07-11T13:00:00Z",
                "repository_url": "https://api.github.com/repos/AOSSIE-Org/DeniedRepo",
                "user": {"login": "charlie"},
                "draft": True,
            },
        ]
    })
    adapter._client = client  # type: ignore[assignment]
    monkeypatch.setattr(
        "ghdcbot.adapters.github.rest._load_repo_filter",
        lambda: RepoFilterConfig(mode="allow", names=["PictoPy"]),
    )

    prs = adapter.list_review_requests_for_user("alice")

    assert len(client.calls) == 1
    method, path, params = client.calls[0]
    assert method == "GET"
    assert path == "/search/issues"
    assert params is not None
    assert params["q"] == "is:pr is:open review-requested:alice org:AOSSIE-Org"
    assert params["sort"] == "created"
    assert params["order"] == "asc"

    assert len(prs) == 1
    assert prs[0]["repo"] == "PictoPy"
    assert prs[0]["number"] == 101
    assert prs[0]["author"] == "bob"
    assert prs[0]["title"] == "Allowed PR"
    assert prs[0]["draft"] is False


def test_list_review_requests_for_user_with_repo_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = GitHubRestAdapter(token="t", org="AOSSIE-Org", api_base="https://api.github.com")
    client = _SearchMockClient({
        "items": [
            {
                "number": 42,
                "title": "Repo Specific PR",
                "html_url": "https://github.com/AOSSIE-Org/Gitcord/pull/42",
                "created_at": "2026-07-11T10:00:00Z",
                "updated_at": "2026-07-11T12:00:00Z",
                "repository_url": "https://api.github.com/repos/AOSSIE-Org/Gitcord",
                "user": {"login": "bob"},
                "draft": False,
            }
        ]
    })
    adapter._client = client  # type: ignore[assignment]
    monkeypatch.setattr(
        "ghdcbot.adapters.github.rest._load_repo_filter",
        lambda: None,
    )

    prs = adapter.list_review_requests_for_user("alice", repo="Gitcord")

    assert len(client.calls) == 1
    params = client.calls[0][2]
    assert params is not None
    assert params["q"] == "is:pr is:open review-requested:alice repo:AOSSIE-Org/Gitcord"
    assert len(prs) == 1
    assert prs[0]["repo"] == "Gitcord"


def test_list_review_requests_empty_user_or_error() -> None:
    adapter = GitHubRestAdapter(token="t", org="AOSSIE-Org", api_base="https://api.github.com")

    # Empty user returns empty list
    assert adapter.list_review_requests_for_user("") == []
    assert adapter.list_review_requests_for_user("   ") == []

    # API error returns empty list gracefully without throwing
    error_client = _SearchMockClient(status_code=500)
    adapter._client = error_client  # type: ignore[assignment]
    assert adapter.list_review_requests_for_user("alice") == []


# -----------------------------------------------------------------------------
# Bot Slash Command Tests
# -----------------------------------------------------------------------------

def _build_test_bot_config() -> Any:
    return BotConfig.model_validate({
        "runtime": {
            "mode": "active",
            "log_level": "INFO",
            "data_dir": "/tmp",
            "github_adapter": "ghdcbot.adapters.github.rest:GitHubRestAdapter",
            "discord_adapter": "ghdcbot.adapters.discord.api:DiscordApiAdapter",
            "storage_adapter": "ghdcbot.adapters.storage.sqlite:SqliteStorage",
        },
        "github": {
            "org": "AOSSIE-Org",
            "token": "fake-token",
            "api_base": "https://api.github.com",
            "permissions": {"write": True},
            "repos": {"mode": "allow", "names": ["Gitcord", "PictoPy"]},
        },
        "discord": {
            "guild_id": "123",
            "token": "fake-discord-token",
            "unrestricted_slash_commands": True,
        },
        "assignments": {"review_roles": [], "issue_assignees": []},
        "identity_mappings": [],
    })


def _setup_commands(config: Any, mock_github: Any, mock_storage: Any) -> dict[str, Any]:
    import discord

    from ghdcbot.bot import run_bot

    captured_trees: list[Any] = []
    orig_tree_init = discord.app_commands.CommandTree.__init__

    def mock_tree_init(tree_self: Any, client: Any) -> None:
        captured_trees.append(tree_self)
        orig_tree_init(tree_self, client)

    def fake_build_adapter(adapter_name: str, **kwargs: Any) -> Any:
        if "github" in adapter_name.lower():
            return mock_github
        if "storage" in adapter_name.lower():
            return mock_storage
        return MagicMock()

    with (
        patch("ghdcbot.bot.load_config", return_value=config),
        patch("ghdcbot.bot.resolve_github_token", return_value="fake_token"),
        patch("ghdcbot.bot.build_adapter", side_effect=fake_build_adapter),
        patch("ghdcbot.bot.GitHubIdentityReader"),
        patch("ghdcbot.bot.IdentityLinkService"),
        patch("ghdcbot.bot.SocialProfileService"),
        patch("discord.app_commands.CommandTree.__init__", mock_tree_init),
        patch("discord.Client.run", side_effect=SystemExit(0)),
    ):
        try:
            run_bot("dummy.yaml")
        except SystemExit:
            pass

    guild_id = int(config.discord.guild_id)
    tree = captured_trees[0]
    return {c.name: c for c in tree.get_commands(guild=discord.Object(id=guild_id))}


@pytest.mark.asyncio
async def test_reviews_command_self_lookup_success() -> None:
    config = _build_test_bot_config()
    mock_github = MagicMock()
    mock_github.list_review_requests_for_user.return_value = [
        {
            "repo": "Gitcord",
            "number": 42,
            "title": "Fix bug",
            "author": "bob",
            "created_at": "2026-06-10T10:00:00Z",
            "html_url": "https://github.com/AOSSIE-Org/Gitcord/pull/42",
            "draft": False,
        }
    ]
    mock_storage = MagicMock()
    mock_storage.list_verified_identity_mappings.return_value = [
        SimpleNamespace(discord_user_id="111", github_user="alice"),
    ]

    commands = _setup_commands(config, mock_github, mock_storage)
    cmd = commands["reviews"]

    interaction = MagicMock()
    interaction.user.id = 111
    interaction.user.mention = "<@111>"
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()

    await cmd.callback(interaction, contributor=None, repo=None)

    interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    mock_github.list_review_requests_for_user.assert_called_once_with("alice", repo=None)
    interaction.followup.send.assert_awaited_once()

    send_kwargs = interaction.followup.send.call_args[1]
    assert send_kwargs.get("ephemeral") is True
    embed = send_kwargs.get("embed")
    assert embed is not None
    assert "Pending Review Requests" in embed.title
    assert "alice" in embed.description
    assert len(embed.fields) == 1
    assert "Gitcord#42" in embed.fields[0].name


@pytest.mark.asyncio
async def test_reviews_command_contributor_lookup() -> None:
    config = _build_test_bot_config()
    mock_github = MagicMock()
    mock_github.list_review_requests_for_user.return_value = []
    mock_storage = MagicMock()
    mock_storage.list_verified_identity_mappings.return_value = [
        SimpleNamespace(discord_user_id="222", github_user="mentor_bob"),
    ]

    commands = _setup_commands(config, mock_github, mock_storage)
    cmd = commands["reviews"]

    interaction = MagicMock()
    interaction.user.id = 111
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()

    contributor = MagicMock()
    contributor.id = 222
    contributor.mention = "<@222>"

    await cmd.callback(interaction, contributor=contributor, repo="PictoPy")

    interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    mock_github.list_review_requests_for_user.assert_called_once_with("mentor_bob", repo="PictoPy")
    interaction.followup.send.assert_awaited_once()

    embed = interaction.followup.send.call_args[1].get("embed")
    assert embed is not None
    assert "All caught up" in embed.description
    assert "<@222>" in embed.description


@pytest.mark.asyncio
async def test_reviews_command_unlinked_identity() -> None:
    config = _build_test_bot_config()
    mock_github = MagicMock()
    mock_storage = MagicMock()
    mock_storage.list_verified_identity_mappings.return_value = []  # no verified links

    commands = _setup_commands(config, mock_github, mock_storage)
    cmd = commands["reviews"]

    # 1. Unlinked self
    interaction_self = MagicMock()
    interaction_self.user.id = 999
    interaction_self.response.defer = AsyncMock()
    interaction_self.followup.send = AsyncMock()

    await cmd.callback(interaction_self, contributor=None)
    assert "You have not verified your GitHub identity" in interaction_self.followup.send.call_args[0][0]
    assert interaction_self.followup.send.call_args[1].get("ephemeral") is True

    # 2. Unlinked contributor
    interaction_other = MagicMock()
    interaction_other.user.id = 111
    interaction_other.response.defer = AsyncMock()
    interaction_other.followup.send = AsyncMock()
    target = MagicMock()
    target.id = 888
    target.mention = "<@888>"

    await cmd.callback(interaction_other, contributor=target)
    assert "<@888> has not verified their GitHub identity" in interaction_other.followup.send.call_args[0][0]


@pytest.mark.asyncio
async def test_reviews_command_error_handling() -> None:
    config = _build_test_bot_config()
    mock_github = MagicMock()
    mock_github.list_review_requests_for_user.side_effect = RuntimeError("GitHub outage")
    mock_storage = MagicMock()
    mock_storage.list_verified_identity_mappings.return_value = [
        SimpleNamespace(discord_user_id="111", github_user="alice"),
    ]

    commands = _setup_commands(config, mock_github, mock_storage)
    cmd = commands["reviews"]

    interaction = MagicMock()
    interaction.user.id = 111
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()

    await cmd.callback(interaction, contributor=None)

    interaction.followup.send.assert_awaited_once()
    msg = interaction.followup.send.call_args[0][0]
    assert "Error fetching review requests" in msg
    assert interaction.followup.send.call_args[1].get("ephemeral") is True


@pytest.mark.asyncio
async def test_reviews_repo_autocomplete() -> None:
    config = _build_test_bot_config()
    commands = _setup_commands(config, MagicMock(), MagicMock())
    cmd = commands["reviews"]

    repo_params = [p for p in cmd.parameters if p.name == "repo"]
    assert len(repo_params) == 1
    assert repo_params[0].autocomplete is True

    callback = cmd._params["repo"].autocomplete
    interaction = MagicMock()

    choices = await callback(interaction, "Git")
    choice_values = [c.value for c in choices]
    assert "Gitcord" in choice_values
    assert "PictoPy" not in choice_values
