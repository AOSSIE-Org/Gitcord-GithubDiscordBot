"""Regression tests for the /unlink slash command cooldown handling."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from ghdcbot.adapters.github.identity import VerificationMatch
from ghdcbot.adapters.storage.sqlite import SqliteStorage
from ghdcbot.bot import run_bot
from ghdcbot.config.models import BotConfig
from ghdcbot.engine.identity_linking import IdentityLinkService


class _AlwaysVerifiedGitHubIdentity:
    def search_verification_code(self, github_user: str, code: str) -> VerificationMatch:
        return VerificationMatch(found=True, location="bio")


def _build_bot_config(tmp_path: Path, *, identity: Any = None) -> BotConfig:
    payload: dict[str, Any] = {
        "runtime": {
            "mode": "active",
            "log_level": "INFO",
            "data_dir": str(tmp_path),
            "github_adapter": "ghdcbot.adapters.github.rest:GitHubRestAdapter",
            "discord_adapter": "ghdcbot.adapters.discord.api:DiscordApiAdapter",
            "storage_adapter": "ghdcbot.adapters.storage.sqlite:SqliteStorage",
        },
        "github": {
            "org": "test-org",
            "token": "fake-token",
            "api_base": "https://api.github.com",
        },
        "discord": {
            "guild_id": "123456",
            "token": "fake-discord-token",
            "unrestricted_slash_commands": True,
        },
        "assignments": {"review_roles": [], "issue_assignees": []},
        "identity_mappings": [],
    }
    config = BotConfig.model_validate(payload)
    if identity is not None:
        return config.model_copy(update={"identity": identity})
    return config


def _capture_unlink_command(config: BotConfig, storage: SqliteStorage) -> Any:
    """Register slash commands via run_bot and return the /unlink command."""
    captured_trees: list[discord.app_commands.CommandTree] = []
    orig_tree_init = discord.app_commands.CommandTree.__init__

    def _capture_tree_init(tree_self: Any, client: Any) -> None:
        captured_trees.append(tree_self)
        orig_tree_init(tree_self, client)

    def _fake_build_adapter(adapter_name: str, **kwargs: Any) -> Any:
        if "storage" in adapter_name.lower():
            return storage
        return MagicMock()

    with (
        patch("ghdcbot.bot.load_config", return_value=config),
        patch("ghdcbot.bot.resolve_github_token", return_value="fake-token"),
        patch("ghdcbot.bot.build_adapter", side_effect=_fake_build_adapter),
        patch("ghdcbot.bot.GitHubIdentityReader"),
        patch("ghdcbot.bot.SocialProfileService"),
        patch("discord.app_commands.CommandTree.__init__", _capture_tree_init),
        patch("discord.Client.run", side_effect=SystemExit(0)),
    ):
        try:
            run_bot("dummy.yaml")
        except SystemExit:
            pass

    guild_id = int(config.discord.guild_id)
    commands = {
        cmd.name: cmd
        for cmd in captured_trees[0].get_commands(guild=discord.Object(id=guild_id))
    }
    return commands["unlink"]


def _seed_verified_link(storage: SqliteStorage, discord_user_id: str, github_user: str) -> None:
    storage.init_schema()
    svc = IdentityLinkService(storage=storage, github_identity=_AlwaysVerifiedGitHubIdentity())
    svc.create_claim(discord_user_id, github_user)
    svc.verify_claim(discord_user_id, github_user)


def _fake_interaction(user_id: int = 987654321) -> MagicMock:
    interaction = MagicMock()
    interaction.user.id = user_id
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


@pytest.mark.asyncio
async def test_unlink_cmd_respects_zero_cooldown(tmp_path: Path) -> None:
    """/unlink must pass cooldown=0 rather than falling back to 24 when configured to 0."""
    storage = SqliteStorage(data_dir=str(tmp_path))
    _seed_verified_link(storage, "987654321", "octocat")

    config = BotConfig.model_validate(
        {
            **_build_bot_config(tmp_path).model_dump(mode="python"),
            "identity": {"unlink_cooldown_hours": 0},
        }
    )
    unlink_cmd = _capture_unlink_command(config, storage)
    interaction = _fake_interaction(987654321)

    recorded_cooldowns: list[int] = []
    orig_unlink = IdentityLinkService.unlink

    def _spy_unlink(self: IdentityLinkService, discord_user_id: str, cooldown_hours: int = 24) -> None:
        recorded_cooldowns.append(cooldown_hours)
        orig_unlink(self, discord_user_id, cooldown_hours)

    with patch.object(IdentityLinkService, "unlink", _spy_unlink):
        await unlink_cmd.callback(interaction)

    assert recorded_cooldowns == [0]
    interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    interaction.followup.send.assert_awaited_once_with(
        "Identity unlinked. You can use `/link` again to relink.",
        ephemeral=True,
    )
    row = storage.get_identity_link("987654321", "octocat")
    assert row is not None
    assert row["verified"] == 0
    assert row["unlinked_at"] is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "identity_value",
    [
        None,
        SimpleNamespace(),
        SimpleNamespace(unlink_cooldown_hours=None),
    ],
    ids=["identity-none", "missing-attr", "cooldown-none"],
)
async def test_unlink_cmd_defaults_to_24_when_identity_or_cooldown_missing(
    tmp_path: Path,
    identity_value: Any,
) -> None:
    """/unlink falls back to 24h cooldown when identity or unlink_cooldown_hours is None/missing."""
    storage = SqliteStorage(data_dir=str(tmp_path))
    _seed_verified_link(storage, "987654321", "octocat")

    config = _build_bot_config(tmp_path, identity=identity_value)
    unlink_cmd = _capture_unlink_command(config, storage)
    interaction = _fake_interaction(987654321)

    recorded_cooldowns: list[int] = []
    orig_unlink = IdentityLinkService.unlink

    def _spy_unlink(self: IdentityLinkService, discord_user_id: str, cooldown_hours: int = 24) -> None:
        recorded_cooldowns.append(cooldown_hours)
        orig_unlink(self, discord_user_id, cooldown_hours)

    with patch.object(IdentityLinkService, "unlink", _spy_unlink):
        await unlink_cmd.callback(interaction)

    assert recorded_cooldowns == [24]
    interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    interaction.followup.send.assert_awaited_once()
    sent_text = interaction.followup.send.call_args[0][0]
    assert "Identity was verified recently" in sent_text
    assert interaction.followup.send.call_args[1].get("ephemeral") is True
    row = storage.get_identity_link("987654321", "octocat")
    assert row is not None
    assert row["verified"] == 1
