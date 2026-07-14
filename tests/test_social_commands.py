"""Tests for social connect/disconnect Discord command handlers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from ghdcbot.adapters.discord import social_commands
from ghdcbot.core.social_models import SocialProfile


class _FakeFollowup:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send(self, content: str | None = None, **kwargs) -> None:
        self.messages.append({"content": content, **kwargs})


class _FakeResponse:
    def __init__(self) -> None:
        self.deferred = False

    async def defer(self, *, ephemeral: bool = False) -> None:
        self.deferred = True
        self.ephemeral = ephemeral


class _FakeInteraction:
    def __init__(self, user_id: int = 123) -> None:
        self.user = MagicMock(id=user_id)
        self.response = _FakeResponse()
        self.followup = _FakeFollowup()


def _profile(display: str, platform: str = "x") -> SocialProfile:
    return SocialProfile(
        discord_user_id="123",
        platform=platform,
        profile_handle=display.lstrip("@"),
        display_value=display,
        verified=False,
        created_at=None,
        updated_at=None,
    )


@pytest.mark.asyncio
async def test_connect_social_new_profile_sends_connected_embed() -> None:
    service = AsyncMock()
    service.get_profile.return_value = None
    service.set_profile.return_value = _profile("@newuser")
    interaction = _FakeInteraction()

    await social_commands.handle_connect_social(interaction, service, "x", "@newuser")

    assert interaction.response.deferred is True
    assert len(interaction.followup.messages) == 1
    embed = interaction.followup.messages[0]["embed"]
    assert embed.title == "✅ X connected"
    assert "@newuser" in embed.description
    assert "Made a mistake?" in embed.description


@pytest.mark.asyncio
async def test_connect_social_existing_profile_sends_updated_embed() -> None:
    service = AsyncMock()
    service.get_profile.return_value = _profile("@olduser")
    service.set_profile.return_value = _profile("@newuser")
    interaction = _FakeInteraction()

    await social_commands.handle_connect_social(interaction, service, "x", "@newuser")

    embed = interaction.followup.messages[0]["embed"]
    assert embed.title == "✅ X updated"
    assert "@olduser" in embed.description
    assert "@newuser" in embed.description


@pytest.mark.asyncio
async def test_connect_social_invalid_input_sends_error_embed() -> None:
    service = AsyncMock()
    service.get_profile.return_value = None
    service.set_profile.side_effect = ValueError("bad handle")
    interaction = _FakeInteraction()

    await social_commands.handle_connect_social(interaction, service, "x", "!!!")

    embed = interaction.followup.messages[0]["embed"]
    assert embed.title == "❌ Invalid X input"
    assert embed.description == "bad handle"


@pytest.mark.asyncio
async def test_connect_social_generic_failure_sends_text_error() -> None:
    service = AsyncMock()
    service.get_profile.return_value = None
    service.set_profile.side_effect = RuntimeError("db down")
    interaction = _FakeInteraction()

    await social_commands.handle_connect_social(interaction, service, "x", "@ok")

    msg = interaction.followup.messages[0]
    assert msg["content"] == "❌ Could not save your profile. Please try again later."
    assert msg["ephemeral"] is True


@pytest.mark.asyncio
async def test_disconnect_social_not_found_sends_error_embed() -> None:
    service = AsyncMock()
    service.remove_profile.return_value = False
    interaction = _FakeInteraction()

    await social_commands.handle_disconnect_social(interaction, service, "linkedin")

    embed = interaction.followup.messages[0]["embed"]
    assert embed.title == "❌ LinkedIn not connected"
    assert "No LinkedIn account was linked" in embed.description


@pytest.mark.asyncio
async def test_disconnect_social_success_sends_ok_embed() -> None:
    service = AsyncMock()
    service.remove_profile.return_value = True
    interaction = _FakeInteraction()

    await social_commands.handle_disconnect_social(interaction, service, "x")

    embed = interaction.followup.messages[0]["embed"]
    assert embed.title == "✅ X disconnected"


def test_platform_choices_are_x_and_linkedin() -> None:
    values = {c.value for c in social_commands.PLATFORM_CHOICES}
    assert values == {"x", "linkedin"}
