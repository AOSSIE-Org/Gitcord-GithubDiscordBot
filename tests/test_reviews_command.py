"""Tests for /reviews Discord slash command and GitHub review requests adapter."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from ghdcbot.adapters.github.rest import GitHubRestAdapter
from ghdcbot.config.models import BotConfig, RepoFilterConfig, SlashCommandPermissionRule


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

    assert len(client.calls) >= 1
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

    assert len(client.calls) >= 1
    method, path, params = client.calls[0]
    assert method == "GET"
    assert path == "/search/issues"
    assert params is not None
    assert params["q"] == "is:pr is:open review-requested:alice repo:AOSSIE-Org/Gitcord"
    assert len(prs) == 1
    assert prs[0]["repo"] == "Gitcord"


def test_list_review_requests_empty_user_or_error() -> None:
    adapter = GitHubRestAdapter(token="t", org="AOSSIE-Org", api_base="https://api.github.com")

    # Empty user returns empty list
    assert adapter.list_review_requests_for_user("") == []
    assert adapter.list_review_requests_for_user("   ") == []

    # API error raises RuntimeError to propagate failure
    error_client = _SearchMockClient(status_code=500)
    adapter._client = error_client  # type: ignore[assignment]
    with pytest.raises(RuntimeError):
        adapter.list_review_requests_for_user("alice")


def test_list_review_requests_uses_timeline_review_requested_timestamp(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = GitHubRestAdapter(token="t", org="AOSSIE-Org", api_base="https://api.github.com")
    client = _SearchMockClient({
        "items": [
            {
                "number": 101,
                "title": "PR with later review request",
                "html_url": "https://github.com/AOSSIE-Org/PictoPy/pull/101",
                "created_at": "2026-07-01T10:00:00Z",
                "updated_at": "2026-07-25T12:00:00Z",
                "repository_url": "https://api.github.com/repos/AOSSIE-Org/PictoPy",
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
    monkeypatch.setattr(
        adapter,
        "_fetch_issue_timeline",
        lambda owner, repo, issue_num, **kwargs: [
            {
                "event": "review_requested",
                "requested_reviewer": {"login": "Alice"},
                "created_at": "2026-07-20T15:30:00Z",
            }
        ],
    )

    prs = adapter.list_review_requests_for_user("alice")
    assert len(prs) == 1
    assert prs[0]["created_at"] == "2026-07-01T10:00:00Z"
    assert prs[0]["requested_at"] == "2026-07-20T15:30:00Z"


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

    await cmd.callback(interaction, contributor=None, repo=None, page=1)

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

    await cmd.callback(interaction, contributor=contributor, repo="PictoPy", page=1)

    interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    mock_github.list_review_requests_for_user.assert_called_once_with("mentor_bob", repo="PictoPy")
    interaction.followup.send.assert_awaited_once()

    embed = interaction.followup.send.call_args[1].get("embed")
    assert embed is not None
    assert "All caught up" in embed.description
    assert "<@222>" in embed.description


@pytest.mark.asyncio
async def test_reviews_command_contributor_permission_denied() -> None:
    config = _build_test_bot_config()
    config.discord.unrestricted_slash_commands = False
    config.discord.command_permissions = {
        "reviews": SlashCommandPermissionRule(role_names=["Mentor"]),
    }
    mock_github = MagicMock()
    mock_storage = MagicMock()

    commands = _setup_commands(config, mock_github, mock_storage)
    cmd = commands["reviews"]

    interaction = MagicMock()
    interaction.user.id = 111
    interaction.user.roles = []  # not a mentor
    interaction.guild.get_member.return_value = interaction.user
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()

    contributor = MagicMock()
    contributor.id = 222

    await cmd.callback(interaction, contributor=contributor)

    interaction.followup.send.assert_awaited_once()
    msg = interaction.followup.send.call_args[0][0]
    assert "permission" in msg.lower() or "not allowed" in msg.lower()
    assert interaction.followup.send.call_args[1].get("ephemeral") is True


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


def test_list_review_requests_timeline_failure_falls_back_to_created_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = GitHubRestAdapter(token="t", org="AOSSIE-Org", api_base="https://api.github.com")
    client = _SearchMockClient({
        "items": [
            {
                "number": 102,
                "title": "PR with failing timeline pagination",
                "html_url": "https://github.com/AOSSIE-Org/PictoPy/pull/102",
                "created_at": "2026-07-05T10:00:00Z",
                "repository_url": "https://api.github.com/repos/AOSSIE-Org/PictoPy",
                "user": {"login": "bob"},
            }
        ]
    })
    adapter._client = client  # type: ignore[assignment]
    monkeypatch.setattr(
        "ghdcbot.adapters.github.rest._load_repo_filter",
        lambda: None,
    )

    def _failing_timeline(*args: Any, **kwargs: Any) -> list[dict]:
        raise RuntimeError("Timeline pagination failed midway")

    monkeypatch.setattr(adapter, "_fetch_issue_timeline", _failing_timeline)

    prs = adapter.list_review_requests_for_user("alice")
    assert len(prs) == 1
    # Fallback to created_at when timeline retrieval fails
    assert prs[0]["requested_at"] == "2026-07-05T10:00:00Z"


def test_list_review_requests_capped_search_marks_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = GitHubRestAdapter(token="t", org="AOSSIE-Org", api_base="https://api.github.com")

    # Simulate search that returns 5 full pages and total_count = 600
    page_items = [
        {
            "number": i,
            "title": f"PR {i}",
            "html_url": f"https://github.com/AOSSIE-Org/Gitcord/pull/{i}",
            "created_at": "2026-07-01T10:00:00Z",
            "repository_url": "https://api.github.com/repos/AOSSIE-Org/Gitcord",
            "user": {"login": "bob"},
        }
        for i in range(100)
    ]
    client = _SearchMockClient({
        "total_count": 600,
        "items": page_items,
    })
    adapter._client = client  # type: ignore[assignment]
    monkeypatch.setattr(
        "ghdcbot.adapters.github.rest._load_repo_filter",
        lambda: None,
    )
    monkeypatch.setattr(adapter, "_fetch_active_review_requested_at", lambda *args: None)

    prs = adapter.list_review_requests_for_user("alice")
    assert getattr(prs, "incomplete", False) is True


def test_list_review_requests_capped_search_narrows_allowed_repos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ghdcbot.config.models import RepoFilterConfig

    adapter = GitHubRestAdapter(token="t", org="AOSSIE-Org", api_base="https://api.github.com")

    # When org query runs, it returns total_count = 600 (capped).
    # Then it should re-run per-repo search for allowed repos "Gitcord" and "PictoPy".
    calls: list[str] = []

    def mock_request(method: str, path: str, *, params: dict | None = None, **kwargs: Any) -> Any:
        q = (params or {}).get("q", "")
        calls.append(q)
        if "org:AOSSIE-Org" in q:
            # Org search returns 100 items with total_count=600 to trigger capping
            return SimpleNamespace(
                status_code=200,
                json=lambda: {
                    "total_count": 600,
                    "items": [
                        {
                            "number": 999,
                            "title": "PR from other repo",
                            "html_url": "https://github.com/AOSSIE-Org/OtherRepo/pull/999",
                            "created_at": "2026-07-01T10:00:00Z",
                            "repository_url": "https://api.github.com/repos/AOSSIE-Org/OtherRepo",
                            "user": {"login": "bob"},
                        }
                    ] * 100,
                },
            )
        # Narrow per-repo search returns 1 repo-specific item and total_count=1 (not capped)
        repo_target = "Gitcord" if "Gitcord" in q else "PictoPy"
        return SimpleNamespace(
            status_code=200,
            json=lambda: {
                "total_count": 1,
                "items": [
                    {
                        "number": 1,
                        "title": f"PR in {repo_target}",
                        "html_url": f"https://github.com/AOSSIE-Org/{repo_target}/pull/1",
                        "created_at": "2026-07-01T10:00:00Z",
                        "repository_url": f"https://api.github.com/repos/AOSSIE-Org/{repo_target}",
                        "user": {"login": "bob"},
                    }
                ],
            },
        )

    monkeypatch.setattr(adapter, "_request", mock_request)
    monkeypatch.setattr(
        "ghdcbot.adapters.github.rest._load_repo_filter",
        lambda: RepoFilterConfig(mode="allow", names=["Gitcord", "PictoPy"]),
    )
    monkeypatch.setattr(adapter, "_fetch_active_review_requested_at", lambda *args: None)

    prs = adapter.list_review_requests_for_user("alice")
    # Both allowed repos were queried narrower
    assert any("repo:AOSSIE-Org/Gitcord" in c for c in calls)
    assert any("repo:AOSSIE-Org/PictoPy" in c for c in calls)
    assert len(prs) == 2
    assert getattr(prs, "incomplete", False) is False


@pytest.mark.asyncio
async def test_reviews_command_shows_incomplete_warning() -> None:
    from ghdcbot.adapters.github.rest import ReviewRequestList

    config = _build_test_bot_config()
    mock_github = MagicMock()
    mock_github.list_review_requests_for_user.return_value = ReviewRequestList([], incomplete=True)
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
    embed = interaction.followup.send.call_args[1].get("embed")
    assert embed is not None
    assert embed.color.value == 0xFEE75C  # Yellow warning
    assert "reached the GitHub API limit" in embed.description


def test_list_review_requests_empty_response_with_incomplete_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When GitHub search times out and returns 0 items with incomplete_results=True, mark incomplete."""
    adapter = GitHubRestAdapter(token="t", org="AOSSIE-Org", api_base="https://api.github.com")
    client = _SearchMockClient({
        "total_count": 0,
        "incomplete_results": True,
        "items": [],
    })
    adapter._client = client  # type: ignore[assignment]
    monkeypatch.setattr(
        "ghdcbot.adapters.github.rest._load_repo_filter",
        lambda: None,
    )

    prs = adapter.list_review_requests_for_user("alice")
    assert len(prs) == 0
    assert getattr(prs, "incomplete", False) is True


def test_list_review_requests_short_response_with_incomplete_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When GitHub search returns a partial page (< 100 items) with incomplete_results=True, mark incomplete."""
    adapter = GitHubRestAdapter(token="t", org="AOSSIE-Org", api_base="https://api.github.com")
    client = _SearchMockClient({
        "total_count": 2,
        "incomplete_results": True,
        "items": [
            {
                "number": 1,
                "title": "PR 1",
                "html_url": "https://github.com/AOSSIE-Org/Gitcord/pull/1",
                "created_at": "2026-07-01T10:00:00Z",
                "repository_url": "https://api.github.com/repos/AOSSIE-Org/Gitcord",
                "user": {"login": "bob"},
            },
            {
                "number": 2,
                "title": "PR 2",
                "html_url": "https://github.com/AOSSIE-Org/Gitcord/pull/2",
                "created_at": "2026-07-02T10:00:00Z",
                "repository_url": "https://api.github.com/repos/AOSSIE-Org/Gitcord",
                "user": {"login": "charlie"},
            },
        ],
    })
    adapter._client = client  # type: ignore[assignment]
    monkeypatch.setattr(
        "ghdcbot.adapters.github.rest._load_repo_filter",
        lambda: None,
    )
    monkeypatch.setattr(adapter, "_fetch_active_review_requested_at", lambda *args: None)

    prs = adapter.list_review_requests_for_user("alice")
    assert len(prs) == 2
    assert getattr(prs, "incomplete", False) is True
