"""Tests for opt-in welcome DM on guild member join."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import discord

from ghdcbot.bot import IdentityVerificationView, build_identity_verification_embed
from ghdcbot.config.models import DiscordConfig
from ghdcbot.help_link import (
    WELCOME_INITIATOR_ID,
    HelpLinkSessionStore,
    HelpLinkStartView,
    build_welcome_link_prompt_embed,
    deliver_welcome_link_dm,
    should_skip_welcome_for_identity,
)


class _FakeMember:
    def __init__(self, user_id: int, *, bot: bool = False) -> None:
        self.id = user_id
        self.bot = bot
        self.dms: list[dict] = []

    async def send(self, **kwargs: Any) -> None:
        self.dms.append(kwargs)


class _ForbiddenMember(_FakeMember):
    async def send(self, **kwargs: Any) -> None:  # noqa: ARG002
        response = SimpleNamespace(status=403, reason="Forbidden")
        raise discord.Forbidden(response, "dm closed")


def test_discord_config_welcome_defaults_off() -> None:
    cfg = DiscordConfig(guild_id="1", token="t")
    assert cfg.welcome_dm_on_join is False
    assert cfg.welcome_org_label is None


def test_welcome_embed_uses_org_label() -> None:
    embed = build_welcome_link_prompt_embed(org_label="AOSSIE")
    assert "Welcome to AOSSIE" in embed.title
    assert "Start linking" in embed.description
    assert "mentor" not in embed.description.lower()


def test_should_skip_welcome_for_verified() -> None:
    assert (
        should_skip_welcome_for_identity(
            {"status": "verified", "github_user": "octocat"}
        )
        == "already_verified:octocat"
    )
    assert (
        should_skip_welcome_for_identity(
            {"status": "verified_stale", "github_user": "octocat"}
        )
        == "already_verified_stale:octocat"
    )
    assert should_skip_welcome_for_identity({"status": "not_linked"}) is None
    assert should_skip_welcome_for_identity({"status": "pending", "github_user": "x"}) is None
    assert should_skip_welcome_for_identity(None) is None


def test_deliver_welcome_link_dm_success() -> None:
    member = _FakeMember(42)
    store = HelpLinkSessionStore()
    session = store.create(
        mentor_discord_id=WELCOME_INITIATOR_ID,
        target_discord_id="42",
    )
    view = HelpLinkStartView(
        service=SimpleNamespace(),
        storage=SimpleNamespace(),
        target_discord_id="42",
        mentor_discord_id=WELCOME_INITIATOR_ID,
        session_id=session.session_id,
        profile_settings_url="https://github.com/settings/profile",
        verification_view_factory=IdentityVerificationView,
        build_verification_embed=build_identity_verification_embed,
        session_store=store,
    )
    ok = asyncio.run(
        deliver_welcome_link_dm(member=member, view=view, org_label="AOSSIE")  # type: ignore[arg-type]
    )
    assert ok is True
    assert len(member.dms) == 1
    assert member.dms[0]["view"] is view
    assert "AOSSIE" in member.dms[0]["embed"].title


def test_deliver_welcome_link_dm_closed() -> None:
    member = _ForbiddenMember(42)
    store = HelpLinkSessionStore()
    session = store.create(
        mentor_discord_id=WELCOME_INITIATOR_ID,
        target_discord_id="42",
    )
    view = HelpLinkStartView(
        service=SimpleNamespace(),
        storage=SimpleNamespace(),
        target_discord_id="42",
        mentor_discord_id=WELCOME_INITIATOR_ID,
        session_id=session.session_id,
        profile_settings_url="https://github.com/settings/profile",
        verification_view_factory=IdentityVerificationView,
        build_verification_embed=build_identity_verification_embed,
        session_store=store,
    )
    ok = asyncio.run(
        deliver_welcome_link_dm(member=member, view=view, org_label="AOSSIE")  # type: ignore[arg-type]
    )
    assert ok is False
    assert member.dms == []


def test_welcome_start_view_opens_modal_for_member() -> None:
    store = HelpLinkSessionStore()
    session = store.create(
        mentor_discord_id=WELCOME_INITIATOR_ID,
        target_discord_id="42",
    )
    view = HelpLinkStartView(
        service=SimpleNamespace(),
        storage=SimpleNamespace(),
        target_discord_id="42",
        mentor_discord_id=WELCOME_INITIATOR_ID,
        session_id=session.session_id,
        profile_settings_url="https://github.com/settings/profile",
        verification_view_factory=IdentityVerificationView,
        build_verification_embed=build_identity_verification_embed,
        session_store=store,
    )

    class _Resp:
        def __init__(self) -> None:
            self.modals: list[Any] = []
            self.messages: list[dict] = []

        async def send_message(self, content: str, *, ephemeral: bool = False) -> None:
            self.messages.append({"content": content, "ephemeral": ephemeral})

        async def send_modal(self, modal: Any) -> None:
            self.modals.append(modal)

    interaction = SimpleNamespace(user=SimpleNamespace(id="42"), response=_Resp())
    asyncio.run(view.start_linking(interaction))  # type: ignore[arg-type]
    assert len(interaction.response.modals) == 1
