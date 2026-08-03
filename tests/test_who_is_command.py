"""Tests for the /who-is slash command handler and freshness calculation."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ghdcbot.engine.issue_assignment import resolve_github_to_discord


class _FakeFollowup:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send(self, content: str | None = None, **kwargs) -> None:
        self.messages.append({"content": content, **kwargs})


class _FakeResponse:
    def __init__(self) -> None:
        self.deferred = False
        self.ephemeral = False

    async def defer(self, *, ephemeral: bool = False) -> None:
        self.deferred = True
        self.ephemeral = ephemeral


class _FakeInteraction:
    def __init__(self, user_id: int = 123) -> None:
        self.user = MagicMock(id=user_id)
        self.response = _FakeResponse()
        self.followup = _FakeFollowup()


async def _run_who_is_handler(
    interaction: _FakeInteraction,
    storage: MagicMock,
    config: MagicMock,
    github_username: str,
) -> None:
    """Helper representing the who_is_cmd handler logic in bot.py."""
    await interaction.response.defer(ephemeral=True)
    github_username = github_username.strip()
    discord_user_id = resolve_github_to_discord(storage, github_username)

    if discord_user_id:
        get_status = getattr(storage, "get_identity_status", None)
        max_age_days = None
        if getattr(config, "identity", None) is not None:
            max_age_days = getattr(config.identity, "verified_max_age_days", None)

        status_info = {}
        if callable(get_status):
            try:
                status_info = get_status(discord_user_id, max_age_days=max_age_days) or {}
            except Exception:
                status_info = {}

        is_stale = status_info.get("is_stale", True) if status_info else True
        status_badge = "✅ Verified" if not is_stale else "⚠️ Verification may be outdated"
        await interaction.followup.send(
            f"GitHub user **{github_username}** is **{status_badge}** as Discord member <@{discord_user_id}>.",
            ephemeral=True,
        )
    else:
        await interaction.followup.send(
            f"❌ GitHub user **{github_username}** is not linked or verified with any Discord account.",
            ephemeral=True,
        )


@pytest.mark.asyncio
async def test_who_is_current_verified_mapping() -> None:
    """Test /who-is returns ✅ Verified badge for active fresh mappings."""
    storage = MagicMock()
    config = MagicMock()
    config.identity.verified_max_age_days = 30

    mapping_record = MagicMock()
    mapping_record.github_user = "octocat"
    mapping_record.discord_user_id = "999888777"
    storage.list_verified_identity_mappings.return_value = [mapping_record]

    storage.get_identity_status.return_value = {
        "status": "verified",
        "is_stale": False,
        "github_user": "octocat",
    }

    interaction = _FakeInteraction()
    await _run_who_is_handler(interaction, storage, config, "octocat")

    storage.get_identity_status.assert_called_once_with("999888777", max_age_days=30)
    assert interaction.response.deferred is True
    assert interaction.response.ephemeral is True
    assert len(interaction.followup.messages) == 1
    assert (
        interaction.followup.messages[0]["content"]
        == "GitHub user **octocat** is **✅ Verified** as Discord member <@999888777>."
    )
    assert interaction.followup.messages[0]["ephemeral"] is True


@pytest.mark.asyncio
async def test_who_is_stale_mapping_non_default_freshness_window() -> None:
    """Test /who-is passes non-default verified_max_age_days and outputs ⚠️ Verification may be outdated badge."""
    storage = MagicMock()
    config = MagicMock()
    # Non-default freshness window of 14 days
    config.identity.verified_max_age_days = 14

    mapping_record = MagicMock()
    mapping_record.github_user = "stale_dev"
    mapping_record.discord_user_id = "111222333"
    storage.list_verified_identity_mappings.return_value = [mapping_record]

    storage.get_identity_status.return_value = {
        "status": "verified_stale",
        "is_stale": True,
        "github_user": "stale_dev",
    }

    interaction = _FakeInteraction()
    await _run_who_is_handler(interaction, storage, config, "  stale_dev  ")

    storage.get_identity_status.assert_called_once_with("111222333", max_age_days=14)
    assert len(interaction.followup.messages) == 1
    assert (
        interaction.followup.messages[0]["content"]
        == "GitHub user **stale_dev** is **⚠️ Verification may be outdated** as Discord member <@111222333>."
    )
    assert interaction.followup.messages[0]["ephemeral"] is True


@pytest.mark.asyncio
async def test_who_is_missing_github_link() -> None:
    """Test /who-is returns failure message when no GitHub link exists in storage."""
    storage = MagicMock()
    config = MagicMock()
    storage.list_verified_identity_mappings.return_value = []

    interaction = _FakeInteraction()
    await _run_who_is_handler(interaction, storage, config, "unknown_user")

    assert len(interaction.followup.messages) == 1
    assert (
        interaction.followup.messages[0]["content"]
        == "❌ GitHub user **unknown_user** is not linked or verified with any Discord account."
    )
    assert interaction.followup.messages[0]["ephemeral"] is True
