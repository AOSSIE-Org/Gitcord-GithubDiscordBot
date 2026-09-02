"""Tests for mentor-assisted /help-link flow."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import discord

from ghdcbot.adapters.github.identity import VerificationMatch
from ghdcbot.adapters.storage.sqlite import SqliteStorage
from ghdcbot.bot import IdentityVerificationView, build_identity_verification_embed
from ghdcbot.engine.identity_linking import IdentityLinkService
from ghdcbot.help_link import (
    HelpLinkSession,
    HelpLinkSessionStore,
    HelpLinkStartView,
    HelpLinkUsernameModal,
    already_linked_help_reply,
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


def _make_view(
    *,
    store: HelpLinkSessionStore,
    session: HelpLinkSession,
    service: Any = None,
    storage: Any = None,
) -> HelpLinkStartView:
    return HelpLinkStartView(
        service=service if service is not None else SimpleNamespace(),
        storage=storage if storage is not None else SimpleNamespace(),
        target_discord_id=session.target_discord_id,
        mentor_discord_id=session.mentor_discord_id,
        session_id=session.session_id,
        profile_settings_url="https://github.com/settings/profile",
        verification_view_factory=IdentityVerificationView,
        build_verification_embed=build_identity_verification_embed,
        session_store=store,
    )


def test_already_linked_help_reply_for_verified() -> None:
    msg = already_linked_help_reply(
        contributor_mention="<@42>",
        status={"status": "verified", "github_user": "octocat"},
    )
    assert msg is not None
    assert "already linked" in msg
    assert "octocat" in msg
    assert already_linked_help_reply(
        contributor_mention="<@42>",
        status={"status": "not_linked", "github_user": None},
    ) is None
    stale = already_linked_help_reply(
        contributor_mention="<@42>",
        status={"status": "verified_stale", "github_user": "octocat"},
    )
    assert stale is not None
    assert "stale" in stale.lower()


def test_help_link_session_store_expires() -> None:
    store = HelpLinkSessionStore(ttl=timedelta(minutes=20))
    now = datetime.now(timezone.utc)
    store._by_target["t1"] = HelpLinkSession(
        session_id="old",
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
    assert first.session_id != second.session_id


def test_older_view_timeout_does_not_clear_replacement_session() -> None:
    store = HelpLinkSessionStore()
    first = store.create(mentor_discord_id="m1", target_discord_id="t1")
    first_view = _make_view(store=store, session=first)
    second = store.create(mentor_discord_id="m2", target_discord_id="t1")
    second_view = _make_view(store=store, session=second)

    asyncio.run(first_view.on_timeout())

    active = store.get_active("t1")
    assert active is second
    assert active.session_id == second.session_id
    assert store.get_active_matching("t1", first.session_id) is None
    assert store.get_active_matching("t1", second.session_id) is second

    # Replacement view timeout still clears its own session.
    asyncio.run(second_view.on_timeout())
    assert store.get_active("t1") is None


def test_help_link_prompt_embed_mentions_mentor() -> None:
    embed = build_help_link_prompt_embed(mentor_mention="<@99>")
    assert "<@99>" in embed.description
    assert "Start linking" in embed.description


def test_start_view_rejects_other_users(tmp_path: Path) -> None:
    storage = SqliteStorage(data_dir=str(tmp_path))
    storage.init_schema()
    svc = IdentityLinkService(storage=storage, github_identity=_GitHubIdentityAlways(False))
    store = HelpLinkSessionStore()
    session = store.create(mentor_discord_id="m1", target_discord_id="t1")
    view = _make_view(store=store, session=session, service=svc, storage=storage)
    interaction = _FakeButtonInteraction("someone-else")
    asyncio.run(view.start_linking(interaction))
    assert interaction.response.modals == []
    assert "only for the tagged" in interaction.response.messages[0]["content"]


def test_start_view_rejects_expired_session(tmp_path: Path) -> None:
    storage = SqliteStorage(data_dir=str(tmp_path))
    storage.init_schema()
    svc = IdentityLinkService(storage=storage, github_identity=_GitHubIdentityAlways(False))
    store = HelpLinkSessionStore()
    orphan = HelpLinkSession(
        session_id="missing",
        mentor_discord_id="m1",
        target_discord_id="t1",
        created_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=20),
    )
    view = _make_view(store=store, session=orphan, service=svc, storage=storage)
    interaction = _FakeButtonInteraction("t1")
    asyncio.run(view.start_linking(interaction))
    assert interaction.response.modals == []
    assert "expired" in interaction.response.messages[0]["content"].lower()


def test_start_view_opens_modal_for_target(tmp_path: Path) -> None:
    storage = SqliteStorage(data_dir=str(tmp_path))
    storage.init_schema()
    svc = IdentityLinkService(storage=storage, github_identity=_GitHubIdentityAlways(False))
    store = HelpLinkSessionStore()
    session = store.create(mentor_discord_id="m1", target_discord_id="t1")
    view = _make_view(store=store, session=session, service=svc, storage=storage)
    interaction = _FakeButtonInteraction("t1")
    asyncio.run(view.start_linking(interaction))
    assert len(interaction.response.modals) == 1
    modal = interaction.response.modals[0]
    assert isinstance(modal, HelpLinkUsernameModal)
    assert modal.session_id == session.session_id


def test_modal_creates_claim_and_sends_verify_ui(tmp_path: Path) -> None:
    storage = SqliteStorage(data_dir=str(tmp_path))
    storage.init_schema()
    svc = IdentityLinkService(storage=storage, github_identity=_GitHubIdentityAlways(False))
    store = HelpLinkSessionStore()
    session = store.create(mentor_discord_id="m1", target_discord_id="t1")
    modal = HelpLinkUsernameModal(
        service=svc,
        storage=storage,
        target_discord_id="t1",
        session_id=session.session_id,
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
    session = store.create(mentor_discord_id="7", target_discord_id="42")
    view = _make_view(store=store, session=session)

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
    session = store.create(mentor_discord_id="7", target_discord_id="42")
    view = _make_view(store=store, session=session)

    status = asyncio.run(
        deliver_help_link_prompt(
            interaction=interaction,  # type: ignore[arg-type]
            contributor=contributor,  # type: ignore[arg-type]
            mentor=mentor,  # type: ignore[arg-type]
            view=view,
        )
    )
    assert "channel" in status.lower()
    assert "visible" in status.lower()
    assert len(channel.messages) == 1
    assert "Only you can use the button" in channel.messages[0]["content"]
