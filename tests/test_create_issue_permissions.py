"""Tests for /create-issue command_permissions enforcement (Issue #204)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from ghdcbot.bot import run_bot
from ghdcbot.config.models import BotConfig, SlashCommandPermissionRule
from ghdcbot.discord_command_permissions import format_slash_command_permission_denied


class FakeGitHubAdapter:
    """Mock GitHub adapter that records create_issue calls."""

    def __init__(self) -> None:
        self.created_issues: list[dict[str, Any]] = []

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
            "number": 101,
            "title": title,
            "html_url": f"https://github.com/{owner}/{repo}/issues/101",
            "state": "open",
        }

    def list_org_repo_names(self) -> list[str]:
        return ["allowed-repo", "another-repo"]


def _build_test_config(
    *,
    command_permissions: dict[str, Any] | None = None,
    unrestricted_slash_commands: bool = False,
) -> BotConfig:
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
        },
        "discord": {
            "guild_id": "123",
            "token": "fake-discord-token",
            "command_permissions": command_permissions,
            "unrestricted_slash_commands": unrestricted_slash_commands,
        },
        "assignments": {"review_roles": [], "issue_assignees": ["Mentor"]},
        "identity_mappings": [],
    })


def _setup_bot_and_get_create_command(
    config: BotConfig, fake_github: FakeGitHubAdapter
) -> tuple[Any, Any]:
    captured_trees: list[Any] = []
    orig_tree_init = discord.app_commands.CommandTree.__init__

    def mock_tree_init(tree_self: Any, client: Any) -> None:
        captured_trees.append(tree_self)
        orig_tree_init(tree_self, client)

    mock_storage = MagicMock()
    mock_storage.list_verified_identity_mappings.return_value = [
        SimpleNamespace(discord_user_id="123", github_user="alice"),
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
    commands = {c.name: c for c in tree.get_commands(guild=discord.Object(id=guild_id))}
    return commands["create-issue"], commands


def _make_mock_interaction(
    user_id: int = 123,
    user_name: str = "alice",
    *,
    role_names: list[str] | None = None,
    role_ids: list[int] | None = None,
    administrator: bool = False,
    is_guild_member: bool = True,
) -> MagicMock:
    interaction = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.followup.send = AsyncMock()

    if is_guild_member:
        member = MagicMock()
        member.id = user_id
        member.name = user_name
        member.guild_permissions = MagicMock(administrator=administrator)

        roles = []
        r_names = role_names or []
        r_ids = role_ids or [i + 1 for i in range(len(r_names))]
        for rid, rname in zip(r_ids, r_names, strict=False):
            r = MagicMock()
            r.id = rid
            r.name = rname
            roles.append(r)
        member.roles = roles
        interaction.user = member
    else:
        # Non-guild user (e.g. DM), lacking roles and guild_permissions
        user = MagicMock(spec=["id", "name"])
        user.id = user_id
        user.name = user_name
        interaction.user = user

    return interaction


def test_user_with_allowed_role_proceeds_and_creates_issue() -> None:
    """User with an allowed role has their command processed and GitHub create_issue called."""
    config = _build_test_config(
        command_permissions={
            "create-issue": SlashCommandPermissionRule(role_names=["Core Contributor"])
        }
    )
    fake_github = FakeGitHubAdapter()
    create_cmd, _ = _setup_bot_and_get_create_command(config, fake_github)

    interaction = _make_mock_interaction(
        user_id=123,
        role_names=["Core Contributor"],
    )
    asyncio.run(create_cmd.callback(interaction, repo="allowed-repo", title="Test Issue"))

    assert len(fake_github.created_issues) == 1
    assert fake_github.created_issues[0]["repo"] == "allowed-repo"
    assert fake_github.created_issues[0]["title"] == "Test Issue"
    interaction.response.defer.assert_called_once_with(ephemeral=True)
    interaction.followup.send.assert_called_once()


def test_user_without_allowed_role_denied_no_github_call() -> None:
    """User without an allowed role is denied with an ephemeral message, before defer and without GitHub call."""
    config = _build_test_config(
        command_permissions={
            "create-issue": SlashCommandPermissionRule(role_names=["Core Contributor"])
        }
    )
    fake_github = FakeGitHubAdapter()
    create_cmd, _ = _setup_bot_and_get_create_command(config, fake_github)

    interaction = _make_mock_interaction(
        user_id=123,
        role_names=["Regular Contributor"],
    )
    asyncio.run(create_cmd.callback(interaction, repo="allowed-repo", title="Test Issue"))

    assert len(fake_github.created_issues) == 0
    interaction.response.defer.assert_not_called()
    interaction.response.send_message.assert_called_once()
    args, kwargs = interaction.response.send_message.call_args
    assert kwargs.get("ephemeral") is True
    expected_msg = format_slash_command_permission_denied(config, "create-issue")
    assert args[0] == expected_msg
    assert "Permission denied" in args[0]


def test_command_with_no_configured_entry_allows_by_default() -> None:
    """When discord.command_permissions has no create-issue entry, verified users are allowed by default."""
    config = _build_test_config(command_permissions=None)
    fake_github = FakeGitHubAdapter()
    create_cmd, _ = _setup_bot_and_get_create_command(config, fake_github)

    interaction = _make_mock_interaction(
        user_id=123,
        role_names=["Regular Contributor"],
    )
    asyncio.run(create_cmd.callback(interaction, repo="allowed-repo", title="Open Issue"))

    assert len(fake_github.created_issues) == 1
    assert fake_github.created_issues[0]["title"] == "Open Issue"
    interaction.response.defer.assert_called_once_with(ephemeral=True)


def test_invocation_outside_guild_denied() -> None:
    """Invocations outside a guild (DMs with no member/roles) are denied ephemerally without GitHub calls."""
    config = _build_test_config(
        command_permissions={
            "create-issue": SlashCommandPermissionRule(role_names=["Core Contributor"])
        }
    )
    fake_github = FakeGitHubAdapter()
    create_cmd, _ = _setup_bot_and_get_create_command(config, fake_github)

    interaction = _make_mock_interaction(
        user_id=123,
        is_guild_member=False,
    )
    asyncio.run(create_cmd.callback(interaction, repo="allowed-repo", title="DM Issue"))

    assert len(fake_github.created_issues) == 0
    interaction.response.defer.assert_not_called()
    interaction.response.send_message.assert_called_once()
    args, kwargs = interaction.response.send_message.call_args
    assert kwargs.get("ephemeral") is True
    assert "Permission denied" in args[0]


def test_administrator_bypass_allowed() -> None:
    """Discord administrators bypass role restrictions when allow_discord_administrators is True."""
    config = _build_test_config(
        command_permissions={
            "create-issue": SlashCommandPermissionRule(
                role_names=["Core Contributor"],
                allow_discord_administrators=True,
            )
        }
    )
    fake_github = FakeGitHubAdapter()
    create_cmd, _ = _setup_bot_and_get_create_command(config, fake_github)

    interaction = _make_mock_interaction(
        user_id=123,
        role_names=["Other Role"],
        administrator=True,
    )
    asyncio.run(create_cmd.callback(interaction, repo="allowed-repo", title="Admin Issue"))

    assert len(fake_github.created_issues) == 1
    assert fake_github.created_issues[0]["title"] == "Admin Issue"


def test_role_id_match_allowed() -> None:
    """Role ID matching works correctly for create-issue."""
    config = _build_test_config(
        command_permissions={
            "create-issue": SlashCommandPermissionRule(role_ids=["98765"])
        }
    )
    fake_github = FakeGitHubAdapter()
    create_cmd, _ = _setup_bot_and_get_create_command(config, fake_github)

    # User with matching role ID
    interaction_match = _make_mock_interaction(
        user_id=123,
        role_names=["Custom Role"],
        role_ids=[98765],
    )
    asyncio.run(create_cmd.callback(interaction_match, repo="allowed-repo", title="Role ID Match"))
    assert len(fake_github.created_issues) == 1

    # User with non-matching role ID
    interaction_mismatch = _make_mock_interaction(
        user_id=123,
        role_names=["Custom Role"],
        role_ids=[11111],
    )
    asyncio.run(create_cmd.callback(interaction_mismatch, repo="allowed-repo", title="Role ID Mismatch"))
    assert len(fake_github.created_issues) == 1  # No second issue created


def test_create_issue_repo_autocomplete_permission_gated() -> None:
    """Autocomplete for repo option returns choices only for authorized users and [] for unauthorized."""
    config = _build_test_config(
        command_permissions={
            "create-issue": SlashCommandPermissionRule(role_names=["Core Contributor"])
        }
    )
    fake_github = FakeGitHubAdapter()
    create_cmd, _ = _setup_bot_and_get_create_command(config, fake_github)

    autocomplete_fn = create_cmd._params["repo"].autocomplete

    # 1. Authorized user -> gets repo choices
    interaction_allowed = _make_mock_interaction(
        user_id=123,
        role_names=["Core Contributor"],
    )
    choices_allowed = asyncio.run(autocomplete_fn(interaction_allowed, current="allowed"))
    assert len(choices_allowed) == 1
    assert choices_allowed[0].value == "allowed-repo"

    # 2. Unauthorized user -> denied, returns []
    interaction_denied = _make_mock_interaction(
        user_id=123,
        role_names=["Regular Contributor"],
    )
    choices_denied = asyncio.run(autocomplete_fn(interaction_denied, current="allowed"))
    assert choices_denied == []

    # 3. User outside guild -> returns []
    interaction_dm = _make_mock_interaction(
        user_id=123,
        is_guild_member=False,
    )
    choices_dm = asyncio.run(autocomplete_fn(interaction_dm, current="allowed"))
    assert choices_dm == []


def test_regression_issue_204_create_issue_ignores_command_permissions() -> None:
    """Regression test for Issue #204:

    Verify that when discord.command_permissions specifies a role restriction on create-issue,
    a verified user who lacks that role is blocked from creating an issue.
    """
    config = _build_test_config(
        command_permissions={
            "create-issue": SlashCommandPermissionRule(
                role_names=["Core Contributor"],
                allow_discord_administrators=False,
            )
        }
    )
    fake_github = FakeGitHubAdapter()
    create_cmd, _ = _setup_bot_and_get_create_command(config, fake_github)

    # Contributor has valid linked GitHub account, but only "Regular Contributor" Discord role
    interaction = _make_mock_interaction(
        user_id=123,
        role_names=["Regular Contributor"],
        administrator=False,
    )

    asyncio.run(create_cmd.callback(interaction, repo="allowed-repo", title="Unauthorized Bug"))

    # Must NOT call create_issue on GitHub
    assert len(fake_github.created_issues) == 0, (
        "Regression #204: create_issue was called for user lacking allowed role!"
    )
    # Must reject immediately without deferral
    interaction.response.defer.assert_not_called()
    interaction.response.send_message.assert_called_once()
    assert interaction.response.send_message.call_args[1].get("ephemeral") is True
