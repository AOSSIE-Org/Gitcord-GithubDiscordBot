"""Tests that passive PR previews in on_message respect the configured org and repo filter."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from ghdcbot.bot import run_bot
from ghdcbot.config.models import BotConfig

PREVIEW_CHANNEL = "pr-previews"


def _build_test_config(*, repos: dict[str, Any] | None = None) -> BotConfig:
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
            "org": "Test-Org",
            "token": "fake-token",
            "api_base": "https://api.github.com",
            "permissions": {"write": False},
            "repos": repos,
        },
        "discord": {
            "guild_id": "123",
            "token": "fake-discord-token",
            "unrestricted_slash_commands": True,
            "pr_preview_channels": [PREVIEW_CHANNEL],
        },
        "assignments": {"review_roles": [], "issue_assignees": []},
        "identity_mappings": [],
    })


def _get_on_message(config: BotConfig, fake_github: MagicMock) -> Any:
    captured_clients: list[Any] = []
    orig_client_init = discord.Client.__init__

    def mock_client_init(client_self: Any, *args: Any, **kwargs: Any) -> None:
        captured_clients.append(client_self)
        orig_client_init(client_self, *args, **kwargs)

    mock_storage = MagicMock()
    mock_storage.list_verified_identity_mappings.return_value = []

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
        patch("discord.Client.__init__", mock_client_init),
        patch("discord.Client.run", side_effect=SystemExit(0)),
    ):
        try:
            run_bot("dummy.yaml")
        except SystemExit:
            pass
    return captured_clients[0].on_message


def _make_message(url: str) -> MagicMock:
    message = MagicMock()
    message.author.bot = False
    message.channel.name = PREVIEW_CHANNEL
    message.channel.send = AsyncMock()
    message.content = url
    return message


def _make_github() -> MagicMock:
    return MagicMock(name="github_adapter")


def _run_expecting_ignored(config: BotConfig, url: str) -> None:
    """Post ``url`` and assert nothing reached the token-authenticated adapter."""
    github = _make_github()
    on_message = _get_on_message(config, github)
    message = _make_message(url)
    # fetch_pr_context is NOT patched: if the guard failed, it would call the adapter.
    asyncio.run(on_message(message))

    assert github.mock_calls == [], f"adapter was called: {github.mock_calls}"
    message.channel.send.assert_not_called()


def test_pr_url_from_other_org_is_ignored() -> None:
    config = _build_test_config(repos=None)
    _run_expecting_ignored(config, "https://github.com/evil-org/secret-repo/pull/5")


def test_pr_url_for_repo_outside_allowlist_is_ignored() -> None:
    config = _build_test_config(repos={"mode": "allow", "names": ["allowed-repo"]})
    _run_expecting_ignored(config, "https://github.com/Test-Org/other-repo/pull/5")


def test_pr_url_for_repo_on_deny_list_is_ignored() -> None:
    config = _build_test_config(repos={"mode": "deny", "names": ["private-repo"]})
    _run_expecting_ignored(config, "https://github.com/Test-Org/private-repo/pull/5")


def test_pr_url_for_allowed_repo_in_configured_org_still_previews() -> None:
    config = _build_test_config(repos={"mode": "allow", "names": ["allowed-repo"]})
    github = _make_github()
    on_message = _get_on_message(config, github)
    message = _make_message("https://github.com/test-org/Allowed-Repo/pull/7")
    pr = {
        "number": 7,
        "title": "Add feature",
        "html_url": "https://github.com/Test-Org/allowed-repo/pull/7",
        "state": "open",
        "user": {"login": "alice"},
    }

    with (
        patch("ghdcbot.bot.fetch_pr_context", return_value=(pr, [], "unknown", None)) as fetch,
        patch("ghdcbot.bot.resolve_github_to_discord", return_value=None),
    ):
        asyncio.run(on_message(message))

    # Owner/repo match case-insensitively, as elsewhere in the bot.
    fetch.assert_called_once()
    assert fetch.call_args[0][1:] == ("test-org", "Allowed-Repo", 7)
    message.channel.send.assert_awaited_once()
    assert isinstance(message.channel.send.call_args.kwargs["embed"], discord.Embed)


def test_pr_url_with_no_repo_filter_in_configured_org_still_previews() -> None:
    config = _build_test_config(repos=None)
    github = _make_github()
    on_message = _get_on_message(config, github)
    message = _make_message("https://github.com/Test-Org/any-repo/pull/3")
    pr = {
        "number": 3,
        "title": "Fix",
        "html_url": "https://github.com/Test-Org/any-repo/pull/3",
        "state": "open",
        "user": {"login": "alice"},
    }

    with (
        patch("ghdcbot.bot.fetch_pr_context", return_value=(pr, [], "unknown", None)) as fetch,
        patch("ghdcbot.bot.resolve_github_to_discord", return_value=None),
    ):
        asyncio.run(on_message(message))

    fetch.assert_called_once()
    message.channel.send.assert_awaited_once()
