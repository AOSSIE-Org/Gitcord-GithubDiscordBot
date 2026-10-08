"""Assisted identity linking: /help-link → Start button → modal → /link verify UI."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import discord

logger = logging.getLogger("ghdcbot.help_link")

HELP_LINK_COMMAND_NAME = "help-link"
# Synthetic initiator id for join-welcome sessions (not a real Discord snowflake).
WELCOME_INITIATOR_ID = "welcome-on-join"

# Default validity duration for /help-link requests (2 hours).
HELP_LINK_SESSION_TTL: timedelta = timedelta(hours=2)
HELP_LINK_EXPIRY_SECONDS: float = HELP_LINK_SESSION_TTL.total_seconds()


def should_skip_welcome_for_identity(status: dict[str, Any] | None) -> str | None:
    """Return a log-friendly skip reason if join welcome should not be sent."""
    if not status:
        return None
    st = status.get("status")
    github_user = status.get("github_user") or "unknown"
    if st in {"verified", "verified_stale"}:
        return f"already_{st}:{github_user}"
    return None


def build_welcome_link_prompt_embed(*, org_label: str) -> discord.Embed:
    """Private DM prompt for new members (welcome wording, not help-link wording)."""
    label = (org_label or "this community").strip() or "this community"
    return discord.Embed(
        title=f"Welcome to {label}",
        description=(
            "Link your GitHub account to get contributor tools and notifications.\n\n"
            "Click **Start linking**, enter your GitHub username, then verify with the code "
            "(same steps as `/link`)."
        ),
        color=0x2563EB,
    )


async def deliver_welcome_link_dm(
    *,
    member: discord.abc.User,
    view: HelpLinkStartView,
    org_label: str,
) -> bool:
    """Send the welcome Start-linking DM. Returns True if delivered."""
    embed = build_welcome_link_prompt_embed(org_label=org_label)
    try:
        await member.send(embed=embed, view=view)
        return True
    except (discord.Forbidden, discord.HTTPException) as exc:
        logger.info(
            "welcome-on-join DM failed for %s (DMs closed or blocked): %s",
            getattr(member, "id", "?"),
            exc,
        )
        return False


@dataclass(frozen=True)
class HelpLinkSession:
    """Mentor→contributor help session (valid for 2 hours by default)."""

    session_id: str
    mentor_discord_id: str
    target_discord_id: str
    created_at: datetime
    expires_at: datetime | None = None

    def is_expired(self, now: datetime | None = None) -> bool:
        if self.expires_at is None:
            return False
        now = now or datetime.now(UTC)
        return now >= self.expires_at


class HelpLinkSessionStore:
    """In-memory sessions (one active help flow per target Discord user)."""

    def __init__(self, *, ttl: timedelta | None = HELP_LINK_SESSION_TTL) -> None:
        # Defaults to 2 hours (HELP_LINK_SESSION_TTL); None disables time-based expiry.
        self._ttl = ttl
        self._by_target: dict[str, HelpLinkSession] = {}

    def create(
        self,
        *,
        mentor_discord_id: str,
        target_discord_id: str,
        ttl: timedelta | None | object = ...,
    ) -> HelpLinkSession:
        now = datetime.now(UTC)
        effective_ttl = self._ttl if ttl is ... else ttl  # type: ignore[assignment]
        session = HelpLinkSession(
            session_id=uuid.uuid4().hex,
            mentor_discord_id=str(mentor_discord_id),
            target_discord_id=str(target_discord_id),
            created_at=now,
            expires_at=(now + effective_ttl) if effective_ttl is not None else None,
        )
        self._by_target[session.target_discord_id] = session
        return session

    def get_active(
        self, target_discord_id: str, *, now: datetime | None = None
    ) -> HelpLinkSession | None:
        session = self._by_target.get(str(target_discord_id))
        if session is None:
            return None
        if session.is_expired(now=now):
            self._by_target.pop(str(target_discord_id), None)
            return None
        return session

    def get_active_matching(
        self, target_discord_id: str, session_id: str, *, now: datetime | None = None
    ) -> HelpLinkSession | None:
        """Return the active session only if it still matches ``session_id``."""
        session = self.get_active(target_discord_id, now=now)
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
    embed = discord.Embed(
        title="Link your GitHub with Gitcord",
        description=(
            f"{mentor_mention} asked Gitcord to help you link your GitHub account.\n\n"
            "Click **Start linking**, enter your GitHub username, then verify with the code "
            "(same steps as `/link`)."
        ),
        color=0x2563EB,
    )
    embed.set_footer(text="Request expires in 2 hours")
    return embed


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
                "This help session is no longer active. Ask someone to run `/help-link` again.",
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
        timeout: float | None = HELP_LINK_EXPIRY_SECONDS,
        delete_channel_message_on_timeout: bool = True,
    ) -> None:
        # Default timeout is 2 hours (HELP_LINK_EXPIRY_SECONDS).
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
        self.delete_channel_message_on_timeout = delete_channel_message_on_timeout
        self.channel_message: Any = None

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
                "This help session is no longer active. Ask someone to run `/help-link` again.",
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

        if self.delete_channel_message_on_timeout:
            target_msg = self.channel_message or getattr(self, "message", None)
            if target_msg is not None and hasattr(target_msg, "delete"):
                try:
                    await target_msg.delete()
                except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
                    logger.debug("Failed to delete expired help-link channel message: %s", exc)


async def deliver_help_link_prompt(
    *,
    interaction: discord.Interaction,
    contributor: discord.abc.User,
    mentor: discord.abc.User,
    view: HelpLinkStartView,
    delete_after: float | None = HELP_LINK_EXPIRY_SECONDS,
) -> str:
    """DM the contributor; fall back to a channel ping if DMs are closed.

    Returns a short status string for the mentor ephemeral reply.
    Channel fallback is visible to others; only the tagged user can use the button.
    Channel fallback message is deleted after ``delete_after`` seconds (default 2 hours).
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

    send_kwargs: dict[str, Any] = {
        "content": (
            f"{contributor.mention} — a mentor asked Gitcord to help you link GitHub. "
            "Only you can use the button below."
        ),
        "embed": embed,
        "view": view,
    }
    if delete_after is not None:
        send_kwargs["delete_after"] = delete_after

    fallback_msg: Any = None
    try:
        fallback_msg = await channel.send(**send_kwargs)
    except TypeError:
        # Compatibility fallback if mock or custom channel doesn't accept delete_after
        send_kwargs.pop("delete_after", None)
        fallback_msg = await channel.send(**send_kwargs)
        if (
            fallback_msg is not None
            and hasattr(fallback_msg, "delete")
            and delete_after is not None
        ):
            try:
                await fallback_msg.delete(delay=delete_after)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
                logger.debug("Failed to schedule help-link message deletion: %s", exc)

    if fallback_msg is not None:
        view.channel_message = fallback_msg
        if not hasattr(view, "message") or view.message is None:
            view.message = fallback_msg

    return (
        f"✅ Help started for {contributor.mention}. "
        "DMs were closed, so I posted a **Start linking** button in this channel "
        "(visible here; only they can use it)."
    )
