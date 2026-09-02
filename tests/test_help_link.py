"""Tests for mentor-assisted /help-link flow."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import discord
import pytest

from ghdcbot.adapters.github.identity import VerificationMatch
from ghdcbot.adapters.storage.sqlite import SqliteStorage
from ghdcbot.bot import IdentityVerificationView, build_identity_verification_embed
from ghdcbot.config.models import DiscordConfig, SlashCommandPermissionRule
from ghdcbot.discord_command_permissions import slash_command_allowed
from ghdcbot.engine.identity_linking import IdentityLinkService
from ghdcbot.help_link import (
    HELP_LINK_COMMAND_NAME,
    HelpLinkSession,
    HelpLinkSessionStore,
    HelpLinkStartView,
    HelpLinkUsernameModal,
    build_help_link_prompt_embed,
    deliver_help_link_prompt,
)


class _GitHubIdentityAlways:
    def __init__(self, found: bool, location: str | None = None) -> None:
        self._found = found
        self._location = location

    def search_verification_code(self, github_user: str, code: str) -> VerificationMatch:  # noqa: ARG002
        return VerificationMatch(found=self._found, location=self._location)


class _FakeInteractionResponse:
    def __init__(self) -> None:
        self.deferred = False
        self.messages: list[dict] = []
        self.modals: list[Any] = []
        self._done = False

    def is_done(self) -> bool:
        return self._done

    async def defer(self, *, ephemeral: bool = False) -> None:  # noqa: ARG002
        self.deferred = True
        self._done = True

    async def send_message(self, content: str, *, ephemeral: bool = False) -> None:
        self.messages.append({"content": content, "ephemeral": ephemeral})
        self._done = True

    async def send_modal(self, modal: discord.ui.Modal) -> None:
        self.modals.append(modal)
        self._done = True


class _FakeFollowup:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send(self, content: str | None = None, **kwargs: Any) -> None:
        self.messages.append({"content": content, **kwargs})


class _FakeButtonInteraction:
    def __init__(self, discord_user_id: str) -> None:
        self.user = SimpleNamespace(id=discord_user_id)
        self.response = _FakeInteractionResponse()
        self.followup = _FakeFollowup()


class _FakeMember:
    def __init__(self, user_id: int, *, bot: bool = False, mention: str | None = None) -> None:
        self.id = user_id
        self.bot = bot
        self.mention = mention or f"<@{user_id}>"

    async def send(self, **kwargs: Any) -> None:
        self.last_dm = kwargs


class _ForbiddenMember(_FakeMember):
    async def send(self, **kwargs: Any) -> None:  # noqa: ARG002
        response = SimpleNamespace(status=403, reason="Forbidden")
        raise discord.Forbidden(response, "dm closed")


class _FakeChannel:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send(self, **kwargs: Any) -> None:
        self.messages.append(kwargs)


def test_help_link_session_store_expires() -> None:
    store = HelpLinkSessionStore(ttl=timedelta(minutes=20))
    now = datetime.now(timezone.utc)
    store._by_target["t1"] = HelpLinkSession(
        mentor_discord_id="m1",
        target_discord_id="t1",
        created_at=now - timedelta(minutes=30),
        expires_at=now - timedelta(minutes=10),
    )
    assert store.get_active("t1") is None
    assert "t1" not in store._by_target


def test_help_link_session_replaces_previous_for_same_target() -> None:
    store = HelpLinkSessionStore()
    first = store.create(mentor_discord_id="m1", target_discord_id="t1")
    second = store.create(mentor_discord_id="m2", target_discord_id="t1")
    assert store.get_active("t1") is second
    assert first is not second


def test_help_link_prompt_embed_mentions_mentor() -> None:
    embed = build_help_link_prompt_embed(mentor_mention="<@99>")
    assert "<@99>" in embed.description
    assert "Start linking" in embed.description


def test_start_view_rejects_other_users(tmp_path: Path) -> None:
    storage = SqliteStorage(data_dir=str(tmp_path))
    storage.init_schema()
    svc = IdentityLinkService(storage=storage, github_identity=_GitHubIdentityAlways(False))
    store = HelpLinkSessionStore()
    store.create(mentor_discord_id="m1", target_discord_id="t1")
    view = HelpLinkStartView(
        service=svc,
        storage=storage,
        target_discord_id="t1",
        mentor_discord_id="m1",
        profile_settings_url="https://github.com/settings/profile",
        verification_view_factory=IdentityVerificationView,
        build_verification_embed=build_identity_verification_embed,
        session_store=store,
    )
    interaction = _FakeButtonInteraction("someone-else")
    asyncio.run(view.start_linking(interaction))
    assert interaction.response.modals == []
    assert "only for the tagged" in interaction.response.messages[0]["content"]


def test_start_view_rejects_expired_session(tmp_path: Path) -> None:
    storage = SqliteStorage(data_dir=str(tmp_path))
    storage.init_schema()
    svc = IdentityLinkService(storage=storage, github_identity=_GitHubIdentityAlways(False))
    store = HelpLinkSessionStore()
    view = HelpLinkStartView(
        service=svc,
        storage=storage,
        target_discord_id="t1",
        mentor_discord_id="m1",
        profile_settings_url="https://github.com/settings/profile",
        verification_view_factory=IdentityVerificationView,
        build_verification_embed=build_identity_verification_embed,
        session_store=store,
    )
    interaction = _FakeButtonInteraction("t1")
    asyncio.run(view.start_linking(interaction))
    assert interaction.response.modals == []
    assert "expired" in interaction.response.messages[0]["content"].lower()


def test_start_view_opens_modal_for_target(tmp_path: Path) -> None:
    storage = SqliteStorage(data_dir=str(tmp_path))
    storage.init_schema()
    svc = IdentityLinkService(storage=storage, github_identity=_GitHubIdentityAlways(False))
    store = HelpLinkSessionStore()
    store.create(mentor_discord_id="m1", target_discord_id="t1")
    view = HelpLinkStartView(
        service=svc,
        storage=storage,
        target_discord_id="t1",
        mentor_discord_id="m1",
        profile_settings_url="https://github.com/settings/profile",
        verification_view_factory=IdentityVerificationView,
        build_verification_embed=build_identity_verification_embed,
        session_store=store,
    )
    interaction = _FakeButtonInteraction("t1")
    asyncio.run(view.start_linking(interaction))
    assert len(interaction.response.modals) == 1
    assert isinstance(interaction.response.modals[0], HelpLinkUsernameModal)


def test_modal_creates_claim_and_sends_verify_ui(tmp_path: Path) -> None:
    storage = SqliteStorage(data_dir=str(tmp_path))
    storage.init_schema()
    svc = IdentityLinkService(storage=storage, github_identity=_GitHubIdentityAlways(False))
    store = HelpLinkSessionStore()
    store.create(mentor_discord_id="m1", target_discord_id="t1")
    modal = HelpLinkUsernameModal(
        service=svc,
        storage=storage,
        target_discord_id="t1",
        profile_settings_url="https://github.com/settings/profile",
        verification_view_factory=IdentityVerificationView,
        build_verification_embed=build_identity_verification_embed,
        session_store=store,
    )
    modal.github_username._value = "octocat"

    interaction = _FakeButtonInteraction("t1")
    asyncio.run(modal.on_submit(interaction))

    row = storage.get_identity_link("t1", "octocat")
    assert row is not None
    assert row["verified"] == 0
    assert store.get_active("t1") is None
    assert interaction.response.deferred is True
    assert len(interaction.followup.messages) == 1
    sent = interaction.followup.messages[0]
    assert sent.get("ephemeral") is True
    assert sent.get("embed") is not None
    assert isinstance(sent.get("view"), IdentityVerificationView)


def test_deliver_help_link_prefers_dm() -> None:
    contributor = _FakeMember(42)
    mentor = _FakeMember(7, mention="<@7>")
    interaction = SimpleNamespace(channel=_FakeChannel())
    store = HelpLinkSessionStore()
    store.create(mentor_discord_id="7", target_discord_id="42")
    view = HelpLinkStartView(
        service=SimpleNamespace(),
        storage=SimpleNamespace(),
        target_discord_id="42",
        mentor_discord_id="7",
        profile_settings_url="https://github.com/settings/profile",
        verification_view_factory=IdentityVerificationView,
        build_verification_embed=build_identity_verification_embed,
        session_store=store,
    )

    status = asyncio.run(
        deliver_help_link_prompt(
            interaction=interaction,  # type: ignore[arg-type]
            contributor=contributor,  # type: ignore[arg-type]
            mentor=mentor,  # type: ignore[arg-type]
            view=view,
        )
    )
    assert "DM" in status
    assert hasattr(contributor, "last_dm")
    assert interaction.channel.messages == []


def test_deliver_help_link_falls_back_to_channel() -> None:
    contributor = _ForbiddenMember(42)
    mentor = _FakeMember(7, mention="<@7>")
    channel = _FakeChannel()
    interaction = SimpleNamespace(channel=channel)
    store = HelpLinkSessionStore()
    store.create(mentor_discord_id="7", target_discord_id="42")
    view = HelpLinkStartView(
        service=SimpleNamespace(),
        storage=SimpleNamespace(),
        target_discord_id="42",
        mentor_discord_id="7",
        profile_settings_url="https://github.com/settings/profile",
        verification_view_factory=IdentityVerificationView,
        build_verification_embed=build_identity_verification_embed,
        session_store=store,
    )

    status = asyncio.run(
        deliver_help_link_prompt(
            interaction=interaction,  # type: ignore[arg-type]
            contributor=contributor,  # type: ignore[arg-type]
            mentor=mentor,  # type: ignore[arg-type]
            view=view,
        )
    )
    assert "channel" in status.lower()
    assert len(channel.messages) == 1
    assert "Only you can use the button" in channel.messages[0]["content"]


def test_help_link_permission_key_uses_command_permissions() -> None:
    config = SimpleNamespace(
        discord=DiscordConfig(
            guild_id="1",
            token="t",
            unrestricted_slash_commands=False,
            command_permissions={
                HELP_LINK_COMMAND_NAME: SlashCommandPermissionRule(
                    role_names=["Mentor"],
                    allow_discord_administrators=True,
                )
            },
        ),
        assignments=None,
    )
    mentor = SimpleNamespace(
        roles=[SimpleNamespace(id=1, name="Mentor")],
        guild_permissions=SimpleNamespace(administrator=False),
    )
    student = SimpleNamespace(
        roles=[SimpleNamespace(id=2, name="Student")],
        guild_permissions=SimpleNamespace(administrator=False),
    )
    assert (
        slash_command_allowed(SimpleNamespace(user=mentor), config, HELP_LINK_COMMAND_NAME)
        is True
    )
    assert (
        slash_command_allowed(SimpleNamespace(user=student), config, HELP_LINK_COMMAND_NAME)
        is False
    )
