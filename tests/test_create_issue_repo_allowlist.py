"""Tests verifying repository allowlist enforcement on /create-issue vs /assign-issue."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from ghdcbot.bot import run_bot
from ghdcbot.config.models import BotConfig


class FakeGitHubAdapter:
    """Mock GitHub adapter that records create_issue and assign_issue calls."""

    def __init__(self) -> None:
        self.created_issues: list[dict[str, Any]] = []
        self.assigned_issues: list[dict[str, Any]] = []

    def create_issue(
        self,
        owner: str,
        repo: str,
        title: str,
        description: str = "",
        labels: list[str] | None = None,
    ) -> dict[str, Any]:
        call_record = {
            "owner": owner,
            "repo": repo,
            "title": title,
            "description": description,
            "labels": labels,
        }
        self.created_issues.append(call_record)
        return {
            "number": 42,
            "title": title,
            "html_url": f"https://github.com/{owner}/{repo}/issues/42",
            "state": "open",
        }

    def get_issue(self, owner: str, repo: str, issue_number: int) -> dict[str, Any]:
        return {
            "number": issue_number,
            "title": "Existing Issue",
            "state": "open",
            "assignees": [],
        }

    def assign_issue(
        self, owner: str, repo: str, issue_number: int, assignee: str
    ) -> bool:
        self.assigned_issues.append({
            "owner": owner,
            "repo": repo,
            "issue_number": issue_number,
            "assignee": assignee,
        })
        return True


def _build_test_config(*, repo_names: list[str] | None = None) -> BotConfig:
    repos_cfg = (
        {"mode": "allow", "names": repo_names}
        if repo_names is not None
        else None
    )
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
            "org": "test-org",
            "token": "fake-token",
            "api_base": "https://api.github.com",
            "permissions": {"write": True},
            "repos": repos_cfg,
        },
        "discord": {
            "guild_id": "123",
            "token": "fake-discord-token",
            "unrestricted_slash_commands": True,
        },
        "assignments": {"review_roles": [], "issue_assignees": []},
        "identity_mappings": [],
    })


def _setup_bot_and_get_commands(config: BotConfig, fake_github: FakeGitHubAdapter) -> dict[str, Any]:
    captured_trees: list[Any] = []
    orig_tree_init = discord.app_commands.CommandTree.__init__

    def mock_tree_init(tree_self: Any, client: Any) -> None:
        captured_trees.append(tree_self)
        orig_tree_init(tree_self, client)

    mock_storage = MagicMock()
    mock_storage.list_verified_identity_mappings.return_value = [
        SimpleNamespace(discord_user_id="123", github_user="alice"),
        SimpleNamespace(discord_user_id="456", github_user="bob"),
    ]

    def fake_build_adapter(adapter_name: str, **kwargs: Any) -> Any:
        if "github" in adapter_name.lower():
            return fake_github
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


def _make_mock_interaction(user_id: int = 123, user_name: str = "alice") -> MagicMock:
    interaction = MagicMock()
    interaction.user.id = user_id
    interaction.user.name = user_name
    interaction.response.defer = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


def test_create_issue_rejected_for_excluded_repo() -> None:
    """Test A: /create-issue with excluded-repo should be rejected and writer should record 0 calls."""
    config = _build_test_config(repo_names=["allowed-repo"])
    fake_github = FakeGitHubAdapter()
    commands = _setup_bot_and_get_commands(config, fake_github)
    create_cmd = commands["create-issue"]

    interaction = _make_mock_interaction(user_id=123)
    asyncio.run(create_cmd.callback(interaction, repo="excluded-repo", title="Test Issue"))

    # If repository allowlist is enforced, create_issue should NOT be called
    assert len(fake_github.created_issues) == 0, (
        f"Vulnerability verified: create_issue was called on excluded repo {fake_github.created_issues}"
    )
    interaction.followup.send.assert_called_once()
    msg = interaction.followup.send.call_args[0][0]
    assert "not allowed" in str(msg).lower()


def test_create_issue_succeeds_for_allowed_repo() -> None:
    """Test B: /create-issue with allowed-repo should succeed and writer should record 1 call."""
    config = _build_test_config(repo_names=["allowed-repo"])
    fake_github = FakeGitHubAdapter()
    commands = _setup_bot_and_get_commands(config, fake_github)
    create_cmd = commands["create-issue"]

    interaction = _make_mock_interaction(user_id=123)
    asyncio.run(create_cmd.callback(interaction, repo="allowed-repo", title="Test Issue"))

    assert len(fake_github.created_issues) == 1
    call = fake_github.created_issues[0]
    assert call["repo"] == "allowed-repo"
    assert call["title"] == "Test Issue"


def test_create_issue_no_filter_configured() -> None:
    """Test C: no github.repos filter configured should keep current behavior."""
    config = _build_test_config(repo_names=None)
    fake_github = FakeGitHubAdapter()
    commands = _setup_bot_and_get_commands(config, fake_github)
    create_cmd = commands["create-issue"]

    interaction = _make_mock_interaction(user_id=123)
    asyncio.run(create_cmd.callback(interaction, repo="any-repo", title="Test Issue"))

    assert len(fake_github.created_issues) == 1
    call = fake_github.created_issues[0]
    assert call["repo"] == "any-repo"
    assert call["title"] == "Test Issue"


def test_assign_issue_rejected_for_excluded_repo() -> None:
    """Test D (comparison): /assign-issue with excluded-repo is rejected by is_repo_allowed."""
    config = _build_test_config(repo_names=["allowed-repo"])
    fake_github = FakeGitHubAdapter()
    commands = _setup_bot_and_get_commands(config, fake_github)
    assign_cmd = commands["assign-issue"]

    interaction = _make_mock_interaction(user_id=123)
    assignee = MagicMock()
    assignee.id = 456

    asyncio.run(assign_cmd.callback(interaction, repo="excluded-repo", issue_number=1, assignee_1=assignee))

    # assign_issue must not be called
    assert len(fake_github.assigned_issues) == 0
    # Error message must indicate repository is not allowed
    interaction.followup.send.assert_called_once()
    msg = interaction.followup.send.call_args[0][0]
    assert "not allowed by Gitcord configuration" in str(msg)
