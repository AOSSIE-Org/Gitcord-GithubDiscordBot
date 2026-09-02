"""Assisted identity linking: /help-link → Start button → modal → /link verify UI."""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import discord

logger = logging.getLogger("ghdcbot.help_link")

HELP_LINK_SESSION_TTL = timedelta(minutes=20)
HELP_LINK_COMMAND_NAME = "help-link"


def already_linked_help_reply(
    *,
    contributor_mention: str,
    status: dict[str, Any] | None,
) -> str | None:
    """If the contributor is already verified, return an ephemeral reply; else None."""
    if not status:
        return None
    st = status.get("status")
    github_user = status.get("github_user")
    if not github_user:
        return None
    if st == "verified":
        return (
            f"✅ {contributor_mention} is already linked to "
            f"**{github_user}** — no help needed.\n"
            "If they need to switch accounts, they can use `/unlink` then `/link`."
        )
    if st == "verified_stale":
        return (
            f"⚠️ {contributor_mention} is already linked to **{github_user}**, "
            "but verification may be stale.\n"
            "Ask them to run `/verify-link` (or `/unlink` then `/link`) — "
            "no new help session started."
        )
    return None


@dataclass(frozen=True)
class HelpLinkSession:
    """Short-lived mentor→contributor help session."""

    session_id: str
    mentor_discord_id: str
    target_discord_id: str
    created_at: datetime
    expires_at: datetime

    def is_expired(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        return now >= self.expires_at


class HelpLinkSessionStore:
    """In-memory sessions (one active help flow per target Discord user)."""

    def __init__(self, *, ttl: timedelta = HELP_LINK_SESSION_TTL) -> None:
        self._ttl = ttl
        self._by_target: dict[str, HelpLinkSession] = {}

    def create(self, *, mentor_discord_id: str, target_discord_id: str) -> HelpLinkSession:
        now = datetime.now(timezone.utc)
        session = HelpLinkSession(
            session_id=uuid.uuid4().hex,
            mentor_discord_id=str(mentor_discord_id),
            target_discord_id=str(target_discord_id),
            created_at=now,
            expires_at=now + self._ttl,
        )
        self._by_target[session.target_discord_id] = session
        return session

    def get_active(self, target_discord_id: str) -> HelpLinkSession | None:
        session = self._by_target.get(str(target_discord_id))
        if session is None:
            return None
        if session.is_expired():
            self._by_target.pop(str(target_discord_id), None)
            return None
        return session

    def get_active_matching(
        self, target_discord_id: str, session_id: str
    ) -> HelpLinkSession | None:
        """Return the active session only if it still matches ``session_id``."""
        session = self.get_active(target_discord_id)
        if session is None or session.session_id != session_id:
            return None
        return session

    def clear(self, target_discord_id: str) -> None:
        self._by_target.pop(str(target_discord_id), None)

    def clear_if_match(self, target_discord_id: str, session_id: str) -> bool:
        """Clear the active session only when it still belongs to ``session_id``."""
        session = self._by_target.get(str(target_discord_id))
        if session is None or session.session_id != session_id:
            return False
        self._by_target.pop(str(target_discord_id), None)
        return True


def build_help_link_prompt_embed(*, mentor_mention: str) -> discord.Embed:
    """DM/channel prompt for the tagged contributor (channel fallback is visible)."""
    return discord.Embed(
        title="Link your GitHub with Gitcord",
        description=(
            f"{mentor_mention} asked Gitcord to help you link your GitHub account.\n\n"
            "Click **Start linking**, enter your GitHub username, then verify with the code "
            "(same steps as `/link`)."
        ),
        color=0x2563EB,
    )


class HelpLinkUsernameModal(discord.ui.Modal, title="Link your GitHub"):
    """Modal box: contributor enters GitHub username, then gets standard verify UI."""

    github_username = discord.ui.TextInput(
        label="GitHub username",
        placeholder="e.g. octocat",
        min_length=1,
        max_length=39,
        required=True,
    )

    def __init__(
        self,
        *,
        service: Any,
        storage: Any,
        target_discord_id: str,
        session_id: str,
        profile_settings_url: str,
        verification_view_factory: Callable[..., discord.ui.View],
        build_verification_embed: Callable[..., discord.Embed],
        session_store: HelpLinkSessionStore,
        max_age_days: int | None = None,
    ) -> None:
        super().__init__()
        self.service = service
        self.storage = storage
        self.target_discord_id = str(target_discord_id)
        self.session_id = session_id
        self.profile_settings_url = profile_settings_url
        self.verification_view_factory = verification_view_factory
        self.build_verification_embed = build_verification_embed
        self.session_store = session_store
        self.max_age_days = max_age_days

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if str(interaction.user.id) != self.target_discord_id:
            await interaction.response.send_message(
                "This linking form belongs to another Discord user.",
                ephemeral=True,
            )
            return

        session = self.session_store.get_active_matching(
            self.target_discord_id, self.session_id
        )
        if session is None:
            await interaction.response.send_message(
                "This help session expired. Ask someone to run `/help-link` again.",
                ephemeral=True,
            )
            return

        github_username = str(self.github_username.value).strip()
        if not github_username:
            await interaction.response.send_message(
                "Please enter a GitHub username.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        try:
            claim = await asyncio.to_thread(
                self.service.create_claim,
                self.target_discord_id,
                github_username,
                max_age_days=self.max_age_days,
            )
        except ValueError as exc:
            await interaction.followup.send(f"Cannot create link: {exc}", ephemeral=True)
            return

        embed = self.build_verification_embed(
            claim, profile_settings_url=self.profile_settings_url
        )
        view = self.verification_view_factory(
            service=self.service,
            storage=self.storage,
            discord_user_id=self.target_discord_id,
            github_user=github_username,
            profile_settings_url=self.profile_settings_url,
        )
        self.session_store.clear_if_match(self.target_discord_id, self.session_id)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)


class HelpLinkStartView(discord.ui.View):
    """Channel/DM button: only the tagged contributor can open the username modal."""

    def __init__(
        self,
        *,
        service: Any,
        storage: Any,
        target_discord_id: str,
        mentor_discord_id: str,
        session_id: str,
        profile_settings_url: str,
        verification_view_factory: Callable[..., discord.ui.View],
        build_verification_embed: Callable[..., discord.Embed],
        session_store: HelpLinkSessionStore,
        max_age_days: int | None = None,
        timeout: float = HELP_LINK_SESSION_TTL.total_seconds(),
    ) -> None:
        super().__init__(timeout=timeout)
        self.service = service
        self.storage = storage
        self.target_discord_id = str(target_discord_id)
        self.mentor_discord_id = str(mentor_discord_id)
        self.session_id = session_id
        self.profile_settings_url = profile_settings_url
        self.verification_view_factory = verification_view_factory
        self.build_verification_embed = build_verification_embed
        self.session_store = session_store
        self.max_age_days = max_age_days

        start_button = discord.ui.Button(
            label="Start linking",
            style=discord.ButtonStyle.primary,
            custom_id=f"help_link_start:{self.target_discord_id}:{self.session_id}",
        )
        start_button.callback = self.start_linking
        self.add_item(start_button)

    async def start_linking(self, interaction: discord.Interaction) -> None:
        clicker_id = str(interaction.user.id)
        if clicker_id != self.target_discord_id:
            await interaction.response.send_message(
                "This linking help is only for the tagged Discord user.",
                ephemeral=True,
            )
            return

        session = self.session_store.get_active_matching(
            self.target_discord_id, self.session_id
        )
        if session is None:
            await interaction.response.send_message(
                "This help session expired. Ask someone to run `/help-link` again.",
                ephemeral=True,
            )
            return

        modal = HelpLinkUsernameModal(
            service=self.service,
            storage=self.storage,
            target_discord_id=self.target_discord_id,
            session_id=self.session_id,
            profile_settings_url=self.profile_settings_url,
            verification_view_factory=self.verification_view_factory,
            build_verification_embed=self.build_verification_embed,
            session_store=self.session_store,
            max_age_days=self.max_age_days,
        )
        await interaction.response.send_modal(modal)

    async def on_timeout(self) -> None:
        # Only clear if this view still owns the active session (a newer /help-link
        # may have replaced it before this timeout fired).
        self.session_store.clear_if_match(self.target_discord_id, self.session_id)
        for item in self.children:
            item.disabled = True


async def deliver_help_link_prompt(
    *,
    interaction: discord.Interaction,
    contributor: discord.abc.User,
    mentor: discord.abc.User,
    view: HelpLinkStartView,
) -> str:
    """DM the contributor; fall back to a channel ping if DMs are closed.

    Returns a short status string for the mentor ephemeral reply.
    Channel fallback is visible to others; only the tagged user can use the button.
    """
    embed = build_help_link_prompt_embed(mentor_mention=mentor.mention)
    try:
        await contributor.send(embed=embed, view=view)
        return (
            f"✅ Help started for {contributor.mention}. "
            "I sent them a DM with **Start linking**."
        )
    except (discord.Forbidden, discord.HTTPException) as exc:
        logger.info(
            "help-link DM failed for %s; falling back to channel: %s",
            contributor.id,
            exc,
        )

    channel = interaction.channel
    if channel is None or not hasattr(channel, "send"):
        return (
            f"⚠️ Could not DM {contributor.mention} and no channel is available for a fallback. "
            "Ask them to enable DMs from server members, then retry `/help-link`."
        )

    await channel.send(
        content=(
            f"{contributor.mention} — a mentor asked Gitcord to help you link GitHub. "
            "Only you can use the button below."
        ),
        embed=embed,
        view=view,
    )
    return (
        f"✅ Help started for {contributor.mention}. "
        "DMs were closed, so I posted a **Start linking** button in this channel "
        "(visible here; only they can use it)."
    )
