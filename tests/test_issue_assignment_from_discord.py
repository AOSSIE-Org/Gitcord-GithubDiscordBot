"""Tests for issue assignment from Discord feature."""

from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest

from ghdcbot.core.modes import MutationPolicy, RunMode
from ghdcbot.engine.issue_assignment import (
    build_assignment_confirmation_embed,
    fetch_issue_context,
    parse_issue_url,
    resolve_discord_to_github,
    resolve_github_to_discord,
)


def test_parse_issue_url_valid() -> None:
    """Test parsing valid GitHub issue URLs."""
    assert parse_issue_url("https://github.com/owner/repo/issues/123") == ("owner", "repo", 123)
    assert parse_issue_url("https://github.com/owner/repo/issues/123/") == ("owner", "repo", 123)
    assert parse_issue_url("github.com/owner/repo/issues/456") == ("owner", "repo", 456)
    assert parse_issue_url("http://github.com/owner/repo/issues/789") == ("owner", "repo", 789)


def test_parse_issue_url_invalid() -> None:
    """Test parsing invalid URLs returns None."""
    assert parse_issue_url("not a url") is None
    assert parse_issue_url("https://github.com/owner/repo") is None
    assert parse_issue_url("https://github.com/owner/repo/pull/123") is None
    assert parse_issue_url("https://gitlab.com/owner/repo/issues/123") is None
    assert parse_issue_url("") is None


def test_fetch_issue_context_success() -> None:
    """Test fetching issue context from GitHub adapter."""
    mock_adapter = MagicMock()
    
    issue_data = {
        "title": "Test Issue",
        "state": "open",
        "number": 123,
        "assignees": [],
        "created_at": "2026-01-01T00:00:00+00:00",
        "html_url": "https://github.com/owner/repo/issues/123",
    }
    
    mock_adapter.get_issue.return_value = issue_data
    
    issue = fetch_issue_context(mock_adapter, "owner", "repo", 123)
    
    assert issue == issue_data
    mock_adapter.get_issue.assert_called_once_with("owner", "repo", 123)


def test_fetch_issue_context_not_found() -> None:
    """Test fetching issue context when issue doesn't exist."""
    mock_adapter = MagicMock()
    mock_adapter.get_issue.return_value = None
    
    issue = fetch_issue_context(mock_adapter, "owner", "repo", 999)
    
    assert issue is None


def test_resolve_discord_to_github() -> None:
    """Test resolving Discord user ID to GitHub username."""
    mock_storage = MagicMock()
    
    class MockMapping:
        def __init__(self, discord_id: str, github_user: str) -> None:
            self.discord_user_id = discord_id
            self.github_user = github_user
    
    mock_storage.list_verified_identity_mappings.return_value = [
        MockMapping("123456789", "testuser"),
        MockMapping("987654321", "otheruser"),
    ]
    
    github_user = resolve_discord_to_github(mock_storage, "123456789")
    assert github_user == "testuser"
    
    github_user = resolve_discord_to_github(mock_storage, "999999999")
    assert github_user is None


def test_resolve_github_to_discord() -> None:
    """Test resolving GitHub username to Discord user ID."""
    mock_storage = MagicMock()
    
    class MockMapping:
        def __init__(self, discord_id: str, github_user: str) -> None:
            self.discord_user_id = discord_id
            self.github_user = github_user
    
    mock_storage.list_verified_identity_mappings.return_value = [
        MockMapping("123456789", "testuser"),
        MockMapping("987654321", "otheruser"),
    ]
    
    discord_id = resolve_github_to_discord(mock_storage, "testuser")
    assert discord_id == "123456789"
    
    discord_id = resolve_github_to_discord(mock_storage, "unknownuser")
    assert discord_id is None


def test_build_assignment_confirmation_embed_unassigned() -> None:
    """Test building confirmation embed for unassigned issue."""
    now = datetime.now(timezone.utc)
    created_at = now.replace(year=2025, month=1, day=1)
    
    issue = {
        "title": "Test Issue",
        "state": "open",
        "number": 123,
        "assignees": [],
        "created_at": created_at.isoformat(),
        "updated_at": created_at.isoformat(),
        "html_url": "https://github.com/owner/repo/issues/123",
    }
    
    embed = build_assignment_confirmation_embed(
        issue=issue,
        owner="owner",
        repo="repo",
        current_assignee_github=None,
        current_assignee_discord=None,
        new_assignee_github="newuser",
        new_assignee_discord="123456789",
        assignee_activity="Unknown",
        now=now,
    )
    
    assert embed["title"] == "📋 Issue Assignment Confirmation"
    assert embed["url"] == "https://github.com/owner/repo/issues/123"
    
    # Check fields
    fields = {f["name"]: f["value"] for f in embed["fields"]}
    assert fields["Repository"] == "owner/repo"
    assert "Test Issue" in fields["Issue"]
    assert fields["Current Assignment"] == "None"
    assert "newuser" in fields["Proposed Assignment"]


def test_build_assignment_confirmation_embed_assigned() -> None:
    """Test building confirmation embed for already assigned issue."""
    now = datetime.now(timezone.utc)
    created_at = now.replace(year=2025, month=1, day=1)
    
    issue = {
        "title": "Test Issue",
        "state": "open",
        "number": 123,
        "assignees": [{"login": "olduser"}],
        "created_at": created_at.isoformat(),
        "updated_at": created_at.isoformat(),
        "html_url": "https://github.com/owner/repo/issues/123",
    }
    
    embed = build_assignment_confirmation_embed(
        issue=issue,
        owner="owner",
        repo="repo",
        current_assignee_github="olduser",
        current_assignee_discord="987654321",
        new_assignee_github="newuser",
        new_assignee_discord="123456789",
        assignee_activity="2 days ago",
        now=now,
    )
    
    fields = {f["name"]: f["value"] for f in embed["fields"]}
    assert "olduser" in fields["Current Assignment"]
    assert "2 days ago" in fields["Current Assignment"]
    assert "newuser" in fields["Proposed Assignment"]


def test_build_assignment_confirmation_embed_closed_issue() -> None:
    """Test building confirmation embed handles closed issue gracefully."""
    now = datetime.now(timezone.utc)
    
    issue = {
        "title": "Closed Issue",
        "state": "closed",
        "number": 123,
        "assignees": [],
        "created_at": "2026-01-01T00:00:00+00:00",
        "html_url": "https://github.com/owner/repo/issues/123",
    }
    
    embed = build_assignment_confirmation_embed(
        issue=issue,
        owner="owner",
        repo="repo",
        current_assignee_github=None,
        current_assignee_discord=None,
        new_assignee_github="newuser",
        new_assignee_discord=None,
        assignee_activity="Unknown",
        now=now,
    )
    
    fields = {f["name"]: f["value"] for f in embed["fields"]}
    assert fields["Status"] == "Closed"


def test_mutation_policy_dry_run() -> None:
    """Test that MutationPolicy prevents mutations in dry-run mode."""
    policy = MutationPolicy(
        mode=RunMode.DRY_RUN,
        github_write_allowed=True,
        discord_write_allowed=True,
    )
    
    assert not policy.allow_github_mutations
    assert not policy.allow_discord_mutations


def test_mutation_policy_observer() -> None:
    """Test that MutationPolicy prevents mutations in observer mode."""
    policy = MutationPolicy(
        mode=RunMode.OBSERVER,
        github_write_allowed=True,
        discord_write_allowed=True,
    )
    
    assert not policy.allow_github_mutations
    assert not policy.allow_discord_mutations


def test_mutation_policy_active() -> None:
    """Test that MutationPolicy allows mutations in active mode with permissions."""
    policy = MutationPolicy(
        mode=RunMode.ACTIVE,
        github_write_allowed=True,
        discord_write_allowed=True,
    )
    
    assert policy.allow_github_mutations
    assert policy.allow_discord_mutations


def test_mutation_policy_write_disabled() -> None:
    """Test that MutationPolicy prevents mutations when write is disabled."""
    policy = MutationPolicy(
        mode=RunMode.ACTIVE,
        github_write_allowed=False,
        discord_write_allowed=False,
    )
    
    assert not policy.allow_github_mutations
    assert not policy.allow_discord_mutations


def _build_test_bot_config() -> Any:
    from ghdcbot.config.models import BotConfig

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
            "repos": {"mode": "allow", "names": ["allowed-repo"]},
        },
        "discord": {
            "guild_id": "123",
            "token": "fake-discord-token",
            "unrestricted_slash_commands": True,
        },
        "assignments": {"review_roles": [], "issue_assignees": []},
        "identity_mappings": [],
    })


def _setup_commands_for_assign_test(
    config: Any,
    mock_github: Any,
    mock_storage: Any,
) -> dict[str, Any]:
    from unittest.mock import patch

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
async def test_issue_assignment_view_confirm_defers_ephemerally() -> None:
    """IssueAssignmentView.confirm must defer ephemerally to prevent leaking confirmation status."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from ghdcbot.bot import IssueAssignmentView

    mock_github = MagicMock()
    mock_github.get_issue.return_value = {
        "title": "Open Issue",
        "state": "open",
        "number": 123,
        "assignees": [],
    }
    mock_github.assign_issue.return_value = True

    mock_storage = MagicMock()
    mock_storage.list_verified_identity_mappings.return_value = [
        SimpleNamespace(discord_user_id="456", github_user="alice"),
    ]

    view = IssueAssignmentView(
        github_adapter=mock_github,
        owner="test-org",
        repo="allowed-repo",
        issue_number=123,
        storage=mock_storage,
        discord_ids=["456"],
        initial_github_assignees=["alice"],
        requester_id=123,
    )

    mock_interaction = MagicMock()
    mock_interaction.response.defer = AsyncMock()
    mock_interaction.edit_original_response = AsyncMock()

    await view.confirm(mock_interaction)

    mock_interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    mock_interaction.edit_original_response.assert_awaited_once()


@pytest.mark.asyncio
async def test_assign_issue_cmd_defers_ephemerally_on_success_and_error() -> None:
    """/assign-issue must defer ephemerally and respond ephemerally on validation failures and success."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    config = _build_test_bot_config()
    mock_github = MagicMock()
    mock_github.get_issue.return_value = {
        "title": "Open Issue",
        "state": "open",
        "number": 123,
        "assignees": [],
        "created_at": "2026-01-01T00:00:00+00:00",
        "html_url": "https://github.com/test-org/allowed-repo/issues/123",
    }
    mock_storage = MagicMock()
    mock_storage.list_verified_identity_mappings.return_value = [
        SimpleNamespace(discord_user_id="456", github_user="alice"),
    ]

    commands = _setup_commands_for_assign_test(config, mock_github, mock_storage)
    cmd = commands["assign-issue"]

    # 1. Test success path: defers with ephemeral=True and followup is ephemeral=True
    interaction = MagicMock()
    interaction.user.id = 123
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    assignee = MagicMock()
    assignee.id = 456

    await cmd.callback(interaction, repo="allowed-repo", issue_number=123, assignee_1=assignee)

    interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    interaction.followup.send.assert_awaited_once()
    send_kwargs = interaction.followup.send.call_args[1]
    assert send_kwargs.get("ephemeral") is True

    # 2. Test unlinked user error path: defers with ephemeral=True and followup is ephemeral=True
    interaction_err = MagicMock()
    interaction_err.user.id = 123
    interaction_err.response.defer = AsyncMock()
    interaction_err.followup.send = AsyncMock()
    unlinked_assignee = MagicMock()
    unlinked_assignee.id = 999  # not in verified mappings

    await cmd.callback(interaction_err, repo="allowed-repo", issue_number=123, assignee_1=unlinked_assignee)

    interaction_err.response.defer.assert_awaited_once_with(ephemeral=True)
    interaction_err.followup.send.assert_awaited_once()
    assert interaction_err.followup.send.call_args[1].get("ephemeral") is True
    assert "not linked their GitHub account" in str(interaction_err.followup.send.call_args[0][0])
