"""/unlink must honour identity.unlink_cooldown_hours, including 0 (no cooldown)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from ghdcbot.adapters.storage.sqlite import SqliteStorage
from ghdcbot.bot import run_bot
from ghdcbot.config.models import BotConfig
from ghdcbot.engine.identity_linking import IdentityLinkService

DISCORD_USER_ID = 42


def _config(data_dir: str, identity: dict[str, Any] | None) -> BotConfig:
    raw: dict[str, Any] = {
        "runtime": {
            "mode": "active",
            "log_level": "INFO",
            "data_dir": data_dir,
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
        "discord": {"guild_id": "123", "token": "fake-discord-token"},
        "assignments": {"review_roles": [], "issue_assignees": []},
        "identity_mappings": [],
    }
    if identity is not None:
        raw["identity"] = identity
    return BotConfig.model_validate(raw)


def _unlink_command(config: BotConfig, storage: Any, service: Any) -> Any:
    """Build the bot with fakes and return the registered /unlink command."""
    trees: list[Any] = []
    original_init = discord.app_commands.CommandTree.__init__

    def capture_tree(tree_self: Any, client: Any) -> None:
        trees.append(tree_self)
        original_init(tree_self, client)

    def fake_build_adapter(adapter_name: str, **_kwargs: Any) -> Any:
        return storage if "storage" in adapter_name.lower() else MagicMock()

    with (
        patch("ghdcbot.bot.load_config", return_value=config),
        patch("ghdcbot.bot.resolve_github_token", return_value="fake-token"),
        patch("ghdcbot.bot.build_adapter", side_effect=fake_build_adapter),
        patch("ghdcbot.bot.GitHubIdentityReader"),
        patch("ghdcbot.bot.IdentityLinkService", return_value=service),
        patch("ghdcbot.bot.SocialProfileService"),
        patch("discord.app_commands.CommandTree.__init__", capture_tree),
        patch("discord.Client.run", side_effect=SystemExit(0)),
        pytest.raises(SystemExit),
    ):
        run_bot("dummy.yaml")

    guild = discord.Object(id=int(config.discord.guild_id))
    return {c.name: c for c in trees[0].get_commands(guild=guild)}["unlink"]


def _interaction() -> MagicMock:
    interaction = MagicMock()
    interaction.user = SimpleNamespace(id=DISCORD_USER_ID)
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


@pytest.mark.parametrize(
    ("identity", "expected_hours"),
    [
        ({"unlink_cooldown_hours": 0}, 0),
        ({"unlink_cooldown_hours": 6}, 6),
        ({}, 24),
        (None, 24),
    ],
    ids=["zero-disables-cooldown", "custom-value", "default-in-section", "no-identity-section"],
)
def test_unlink_passes_configured_cooldown_to_service(
    tmp_path: Path, identity: dict[str, Any] | None, expected_hours: int
) -> None:
    service = MagicMock()
    command = _unlink_command(_config(str(tmp_path), identity), MagicMock(), service)

    asyncio.run(command.callback(_interaction()))

    service.unlink.assert_called_once_with(str(DISCORD_USER_ID), expected_hours)


def _verified_storage(tmp_path: Path) -> SqliteStorage:
    storage = SqliteStorage(str(tmp_path))
    storage.init_schema()
    expires = datetime.now(UTC) + timedelta(minutes=10)
    storage.create_identity_claim(str(DISCORD_USER_ID), "octocat", "CODE123", expires)
    storage.mark_identity_verified(str(DISCORD_USER_ID), "octocat")
    return storage


def test_unlink_with_zero_cooldown_unlinks_immediately_after_verification(
    tmp_path: Path,
) -> None:
    storage = _verified_storage(tmp_path)
    service = IdentityLinkService(storage, MagicMock())
    config = _config(str(tmp_path), {"unlink_cooldown_hours": 0})
    command = _unlink_command(config, storage, service)
    interaction = _interaction()

    asyncio.run(command.callback(interaction))

    assert storage.list_verified_identity_mappings() == []
    assert "unlinked" in interaction.followup.send.call_args.args[0].lower()


def test_unlink_with_default_cooldown_is_still_blocked_after_verification(
    tmp_path: Path,
) -> None:
    storage = _verified_storage(tmp_path)
    service = IdentityLinkService(storage, MagicMock())
    command = _unlink_command(_config(str(tmp_path), None), storage, service)
    interaction = _interaction()

    asyncio.run(command.callback(interaction))

    assert len(storage.list_verified_identity_mappings()) == 1
    assert "verified recently" in interaction.followup.send.call_args.args[0].lower()