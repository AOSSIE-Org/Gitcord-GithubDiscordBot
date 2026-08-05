"""Discord bot for identity linking and informational slash commands."""

from __future__ import annotations

import asyncio
import logging

from datetime import datetime, timedelta, timezone
from typing import Any

from datetime import datetime
from typing import Any, Optional


import discord
from discord import app_commands

from ghdcbot.adapters.github.app_auth import resolve_github_token
from ghdcbot.adapters.github.identity import GitHubIdentityReader
from ghdcbot.config.loader import load_config
from ghdcbot.core.errors import ConfigError
from ghdcbot.engine.identity_linking import IdentityLinkService, LinkClaim
from ghdcbot.engine.metrics import (
    build_contribution_summary_message,
    get_contribution_metrics,
    rank_by_activity,
    get_rank_for_user,
)
from ghdcbot.engine.issue_assignment import (
    resolve_discord_to_github,
)
from ghdcbot.engine.open_prs import format_open_prs_report, list_open_prs_for_author
from ghdcbot.engine.pr_list import (
    clamp_pr_list_args,
    format_pr_list_messages,
    select_recent_prs,
)
from ghdcbot.engine.pr_context import (
    build_pr_embed,
    fetch_pr_context,
    parse_pr_url,
)
from ghdcbot.logging.setup import configure_logging
from ghdcbot.plugins.registry import build_adapter
from ghdcbot.discord_command_permissions import (
    format_slash_command_permission_denied,
    slash_command_allowed,
)
from ghdcbot.adapters.discord.social_commands import register_social_commands
from ghdcbot.engine.social_profiles import SocialProfileService

# Slash command names used for permission checks (must match @tree.command name=...)
SLASH_CMD_SYNC = "sync"

VERIFICATION_CODE_REMOVAL_NOTE = (
    "You may now safely remove the verification code from your GitHub bio. "
    "It was only required to prove ownership during the verification process."
)


def _identity_status_lines(
    storage: Any,
    config: Any,
    discord_user_id: str,
    viewing_self: bool,
    *,
    stale_action: str = "Use `/verify-link` to refresh.",
) -> list[str]:
    """Format GitHub verification status lines for /profile (and stale warnings)."""
    get_status = getattr(storage, "get_identity_status", None)
    if not callable(get_status):
        return ["**GitHub:** (link status unavailable)."]

    max_age_days = None
    if getattr(config, "identity", None) is not None:
        max_age_days = getattr(config.identity, "verified_max_age_days", None)
    status = get_status(discord_user_id, max_age_days=max_age_days)
    github_user = status.get("github_user") or "—"
    st = status.get("status") or "not_linked"
    if st == "verified":
        status_label = "Verified ✅"
    elif st == "verified_stale":
        status_label = "Verified ⚠️ (Stale)"
    elif st == "pending":
        status_label = "Pending ⏳"
    else:
        status_label = "Not linked ❌"

    lines = [
        f"**GitHub:** {github_user}",
        f"**Verification:** {status_label}",
    ]
    verified_at = status.get("verified_at")
    if verified_at:
        verified_at_str = verified_at
        try:
            dt = datetime.fromisoformat(verified_at.replace("Z", "+00:00"))
            verified_at_str = dt.strftime("%Y-%m-%d %H:%M:%S UTC")
        except (ValueError, TypeError):
            pass
        lines.append(f"**Verified at:** {verified_at_str}")
    if status.get("is_stale"):
        if viewing_self:
            lines.append(f"⚠️ **Warning:** Identity verification is stale. {stale_action}")
        else:
            lines.append("⚠️ **Warning:** Their identity verification is stale.")
    return lines


async def _format_social_profiles_line(
    social_service: SocialProfileService,
    discord_user_id: str,
    viewing_self: bool,
) -> str:
    try:
        social_profiles = await social_service.get_profiles(discord_user_id)
        if social_profiles:
            profiles_text = []
            for platform, profile in social_profiles.items():
                suffix = " ✔️" if profile.verified else ""
                profiles_text.append(f"  • {platform.upper()}: {profile.display_value}{suffix}")
            return "**Social Profiles:**\n" + "\n".join(profiles_text)
        if viewing_self:
            return "**Social Profiles:** Not linked yet. Use `/connect-social`."
        return "**Social Profiles:** none linked."
    except Exception as e:
        logging.getLogger("ghdcbot.bot").debug(
            "Error fetching social profiles for /profile: %s", e
        )
        return "**Social Profiles:** (unavailable)"


async def _format_roles_line(discord_reader: Any, discord_user_id: str) -> str:
    try:
        fetch_one = getattr(discord_reader, "list_roles_for_member", None)
        if callable(fetch_one):
            target_roles = await asyncio.to_thread(fetch_one, discord_user_id)
        else:
            member_roles = await asyncio.to_thread(discord_reader.list_member_roles)
            target_roles = member_roles.get(discord_user_id, [])
    except Exception as e:
        logging.getLogger("ghdcbot.bot").debug("Error fetching roles for /profile: %s", e)
        target_roles = []
    if target_roles:
        return f"**Roles:** {', '.join(target_roles)}."
    return "**Roles:** (none or unable to read)."


def github_profile_settings_url(api_base: str) -> str:
    """Derive the GitHub web profile settings URL from config.github.api_base."""
    base = api_base.rstrip("/")
    if base in ("https://api.github.com", "http://api.github.com"):
        return "https://github.com/settings/profile"
    if base.endswith("/api/v3"):
        return f"{base[: -len('/api/v3')]}/settings/profile"
    if base.endswith("/api"):
        return f"{base[: -len('/api')]}/settings/profile"
    return f"{base}/settings/profile"


def build_identity_verification_embed(claim: LinkClaim, *, profile_settings_url: str) -> discord.Embed:
    """Build the ephemeral /link verification instructions embed."""
    embed = discord.Embed(
        title="Verify GitHub Account",
        description=(
            "1. Copy the verification code below.\n"
            f"2. Open GitHub profile settings:\n   {profile_settings_url}\n"
            "3. Paste the code into your GitHub bio.\n"
            "4. Return here and click Verify."
        ),
        color=0x2563EB,
    )
    embed.add_field(name="GitHub Account", value=claim.github_user, inline=False)
    embed.add_field(name="Verification Code", value=f"`{claim.verification_code}`", inline=False)
    embed.add_field(name="Expires At (UTC)", value=claim.expires_at.isoformat(), inline=False)
    return embed


class IdentityVerificationView(discord.ui.View):
    """Ephemeral /link buttons that reuse IdentityLinkService.verify_claim()."""

    def __init__(
        self,
        *,
        service: IdentityLinkService,
        storage: Any,
        discord_user_id: str,
        github_user: str,
        profile_settings_url: str,
        timeout: float = 600.0,
    ) -> None:
        super().__init__(timeout=timeout)
        self.service = service
        self.storage = storage
        self.discord_user_id = discord_user_id
        self.github_user = github_user

        github_button = discord.ui.Button(
            label="Open GitHub Profile",
            style=discord.ButtonStyle.link,
            url=profile_settings_url,
        )
        self.add_item(github_button)

        verify_button = discord.ui.Button(label="Verify", style=discord.ButtonStyle.success)
        verify_button.callback = self.verify_identity
        self.add_item(verify_button)

        cancel_button = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.secondary)
        cancel_button.callback = self.cancel_verification
        self.add_item(cancel_button)

    def _disable(self) -> None:
        for item in self.children:
            item.disabled = True

    def _pending_github_user(self, discord_user_id: str) -> str:
        get_link = getattr(self.storage, "get_identity_link", None)
        if not callable(get_link):
            raise ValueError("Identity link status is unavailable.")
        row = get_link(discord_user_id, self.github_user)
        if not row:
            raise ValueError("No identity claim found for this Discord user and GitHub user")
        return str(row.get("github_user") or self.github_user)

    async def _edit_response(self, interaction: discord.Interaction, content: str) -> None:
        self._disable()
        if interaction.response.is_done():
            await interaction.edit_original_response(content=content, embed=None, view=self)
        else:
            await interaction.response.edit_message(content=content, embed=None, view=self)

    async def _lock_verification_ui(self, interaction: discord.Interaction) -> None:
        self._disable()
        await interaction.edit_original_response(view=self)

    async def _unlock_verification_ui(self, interaction: discord.Interaction) -> None:
        for item in self.children:
            item.disabled = False
        await interaction.edit_original_response(view=self)

    async def verify_identity(self, interaction: discord.Interaction) -> None:
        clicker_id = str(interaction.user.id)
        if clicker_id != self.discord_user_id:
            await interaction.response.send_message(
                "This verification button belongs to another Discord user.",
                ephemeral=True,
            )
            return

        # Acknowledge immediately; GitHub bio/gist checks can exceed Discord's 3s limit.
        await interaction.response.defer()
        await self._lock_verification_ui(interaction)

        try:
            github_user = self._pending_github_user(clicker_id)
            ok, location = await asyncio.to_thread(
                self.service.verify_claim, clicker_id, github_user
            )
        except ValueError as e:
            await self._edit_response(interaction, f"Verification failed: {e}")
            return

        if ok:
            await self._edit_response(
                interaction,
                (
                    "✅ Successfully verified GitHub account\n\n"
                    f"GitHub: {github_user}\n\n"
                    f"Status: Verified\n\n"
                    f"{VERIFICATION_CODE_REMOVAL_NOTE}"
                ),
            )
            return

        if location == "expired":
            await self._edit_response(
                interaction,
                "❌ Verification expired.\n\nRun /link again to generate a new verification code.",
            )
            return

        await self._unlock_verification_ui(interaction)
        await interaction.followup.send(
            (
                "❌ Verification code not found.\n\n"
                "Please ensure the code is present in your GitHub bio or public gist and try again."
            ),
            ephemeral=True,
        )

    async def cancel_verification(self, interaction: discord.Interaction) -> None:
        if str(interaction.user.id) != self.discord_user_id:
            await interaction.response.send_message(
                "This verification button belongs to another Discord user.",
                ephemeral=True,
            )
            return
        await self._edit_response(interaction, "Verification cancelled.")


def run_bot(config_path: str) -> None:
    """Run the Discord bot with /link, /verify-link, /profile, /identity status, and /summary."""
    config = load_config(config_path)
    configure_logging(config.runtime.log_level)
    logger = logging.getLogger("ghdcbot.bot")
    logger.info(
        "Using config: %s → data_dir: %s (identity links persist here)",
        config_path,
        config.runtime.data_dir,
    )
    repo_contributor_roles = getattr(config, "repo_contributor_roles", None) or {}
    if repo_contributor_roles:
        logger.info(
            "repo_contributor_roles enabled: %s (use this same config for /sync to assign these roles)",
            list(repo_contributor_roles.keys()),
        )

    storage = build_adapter(
        config.runtime.storage_adapter,
        data_dir=config.runtime.data_dir,
    )
    storage.init_schema()
    github_token = resolve_github_token(
        pat=config.github.token,
        api_base=str(config.github.api_base),
    )
    github_identity = GitHubIdentityReader(
        token=github_token,
        api_base=str(config.github.api_base),
    )
    service = IdentityLinkService(storage=storage, github_identity=github_identity)
    social_service = SocialProfileService(storage=storage)
    profile_settings_url = github_profile_settings_url(str(config.github.api_base))
    discord_reader = build_adapter(
        config.runtime.discord_adapter,
        token=config.discord.token,
        guild_id=config.discord.guild_id,
    )
    github_adapter = build_adapter(
        config.runtime.github_adapter,
        token=github_token,
        org=config.github.org,
        api_base=str(config.github.api_base),
    )

    intents = discord.Intents.default()
    # Enable message content intent if passive PR preview is enabled
    if getattr(config.discord, "pr_preview_channels", None):
        intents.message_content = True

    client = discord.Client(intents=intents)
    tree = app_commands.CommandTree(client)
    guild_id = int(config.discord.guild_id)

    @tree.command(
        name="link",
        description="Link your Discord account to a GitHub account (you get a verification code)",
        guild=discord.Object(id=guild_id),
    )
    @app_commands.describe(github_username="Your GitHub username")
    async def link_cmd(interaction: discord.Interaction, github_username: str) -> None:
        await interaction.response.defer(ephemeral=True)
        discord_user_id = str(interaction.user.id)
        max_age_days = None
        if getattr(config, "identity", None) is not None:
            max_age_days = getattr(config.identity, "verified_max_age_days", None)
        try:
            claim = service.create_claim(discord_user_id, github_username, max_age_days=max_age_days)
        except ValueError as e:
            await interaction.followup.send(
                f"Cannot create link: {e}",
                ephemeral=True,
            )
            return
        embed = build_identity_verification_embed(claim, profile_settings_url=profile_settings_url)
        view = IdentityVerificationView(
            service=service,
            storage=storage,
            discord_user_id=discord_user_id,
            github_user=github_username,
            profile_settings_url=profile_settings_url,
        )
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    @tree.command(
        name="verify-link",
        description="Verify your GitHub link after adding the code to your bio or a gist",
        guild=discord.Object(id=guild_id),
    )
    @app_commands.describe(github_username="Your GitHub username")
    async def verify_link_cmd(interaction: discord.Interaction, github_username: str) -> None:
        await interaction.response.defer(ephemeral=True)
        discord_user_id = str(interaction.user.id)
        try:
            ok, location = await asyncio.to_thread(
                service.verify_claim, discord_user_id, github_username
            )
        except ValueError as e:
            await interaction.followup.send(
                f"Verification failed: {e}",
                ephemeral=True,
            )
            return
        if ok:
            if location == "already-verified":
                await interaction.followup.send(
                    f"Your account is already linked to **{github_username}**.",
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(
                    (
                        f"Verified: **{github_username}** ↔ your Discord (found in {location}).\n\n"
                        f"{VERIFICATION_CODE_REMOVAL_NOTE}"
                    ),
                    ephemeral=True,
                )
        else:
            if location == "expired":
                await interaction.followup.send(
                    "Verification code expired. Run `/link` again to get a new code.",
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(
                    "Code not found yet. Add the code to your GitHub bio or a public gist, then run `/verify-link` again.",
                    ephemeral=True,
                )

    @tree.command(
        name="profile",
        description="Show contributor profile (GitHub, socials, roles; optional Discord member)",
        guild=discord.Object(id=guild_id),
    )
    @app_commands.describe(
        contributor="Discord member to view (omit for yourself)",
    )
    async def profile_cmd(
        interaction: discord.Interaction,
        contributor: discord.Member | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        viewing_self = contributor is None or contributor.id == interaction.user.id
        target = interaction.user if viewing_self else contributor
        assert target is not None
        discord_user_id = str(target.id)

        # 1. Fetch identity status from storage
        get_status = getattr(storage, "get_identity_status", None)
        max_age_days = None
        if getattr(config, "identity", None) is not None:
            max_age_days = getattr(config.identity, "verified_max_age_days", None)
        status = get_status(discord_user_id, max_age_days=max_age_days) if callable(get_status) else {}

        github_user = status.get("github_user")
        st = status.get("status") or "not_linked"

        # 2. Determine verification badge & color
        if st == "verified":
            status_label = "Verified ✅"
            color = discord.Color.green()
        elif st == "verified_stale":
            status_label = "Verified ⚠️ (Stale)"
            color = discord.Color.gold()
        elif st == "pending":
            status_label = "Pending ⏳"
            color = discord.Color.orange()
        else:
            status_label = "Not linked ❌"
            color = discord.Color.red()

        # 3. Create the Embed
        embed = discord.Embed(
            color=color,
            timestamp=datetime.now(timezone.utc)
        )

        # Show github user link and avatar if linked, and set author details
        if github_user:
            github_profile_url = f"https://github.com/{github_user}"
            embed.set_thumbnail(url=f"https://github.com/{github_user}.png")
            embed.set_author(
                name=f"{target.display_name} (@{github_user})",
                icon_url=target.display_avatar.url if target.display_avatar else None,
                url=github_profile_url,
            )
            embed.add_field(
                name="GitHub Account",
                value=f"[{github_user}]({github_profile_url})",
                inline=True,
            )
        else:
            embed.set_author(
                name=f"{target.display_name}",
                icon_url=target.display_avatar.url if target.display_avatar else None,
            )
            embed.add_field(name="GitHub Account", value="Not Linked ❌", inline=True)

        embed.add_field(name="Verification Status", value=status_label, inline=True)

        # Add verification date if verified
        verified_at = status.get("verified_at")
        if verified_at:
            try:
                dt = datetime.fromisoformat(verified_at.replace("Z", "+00:00"))
                verified_at_str = dt.strftime("%Y-%m-%d %H:%M UTC")
            except (ValueError, TypeError):
                verified_at_str = verified_at
            embed.add_field(name="Verified At", value=verified_at_str, inline=True)
        elif github_user:
            embed.add_field(name="Verified At", value="—", inline=True)

        # Add warnings in the description if verification is stale
        if status.get("is_stale"):
            if viewing_self:
                embed.description = "⚠️ **Warning:** Your identity verification is stale. Use `/verify-link` to refresh it."
            else:
                embed.description = "⚠️ **Warning:** Their identity verification is stale."

        # 4. Fetch contribution metrics if GitHub user exists
        if github_user:
            metrics_available = True
            try:
                now_utc = datetime.now(timezone.utc)
                start_30 = now_utc - timedelta(days=30)

                def _fetch_metrics():
                    metrics_list = get_contribution_metrics(storage, start_30, now_utc)
                    user_metrics = next((m for m in metrics_list if m.github_user == github_user), None)
                    ranked_30 = rank_by_activity(metrics_list)
                    rank = get_rank_for_user(ranked_30, github_user)
                    return user_metrics, rank

                user_metrics, rank = await asyncio.to_thread(_fetch_metrics)
            except Exception as e:
                logging.getLogger("ghdcbot.bot").debug(
                    "Error fetching contribution metrics for /profile: %s", e
                )
                metrics_available = False
                user_metrics, rank = None, None

            if user_metrics:
                metrics_lines = [
                    f"📝 **PRs:** {user_metrics.prs_opened} Opened / {user_metrics.prs_merged} Merged",
                    f"👀 **Reviews:** {user_metrics.reviews_submitted} Submitted",
                    f"💬 **Issues & Comments:** {user_metrics.issues_opened} Issues / {user_metrics.comments} Comments",
                ]
                if rank is not None:
                    metrics_lines.append(f"🏆 **Rank:** #{rank} in server activity")

                embed.add_field(
                    name="📊 Contributions (Last 30 Days)",
                    value="\n".join(metrics_lines),
                    inline=False,
                )
            elif metrics_available:
                embed.add_field(
                    name="📊 Contributions (Last 30 Days)",
                    value="No activity recorded in the last 30 days.",
                    inline=False,
                )
            else:
                embed.add_field(
                    name="📊 Contributions (Last 30 Days)",
                    value="Contribution data is temporarily unavailable.",
                    inline=False,
                )
        def _truncate_embed_field_value(value: str, limit: int = 1024) -> str:
            if len(value) <= limit:
                return value
            return f"{value[:limit - 1]}…"
        # 5. Fetch and add Social Profiles
        socials_line = await _format_social_profiles_line(social_service, discord_user_id, viewing_self)
        if socials_line.startswith("**Social Profiles:**"):
            socials_value = socials_line.replace("**Social Profiles:**", "").strip()
        else:
            socials_value = socials_line.strip()
        embed.add_field(
            name="Connected Socials",
            value=_truncate_embed_field_value(socials_value),
            inline=False,
        )
        # 6. Fetch and add Discord Roles
        roles_line = await _format_roles_line(discord_reader, discord_user_id)
        if roles_line.startswith("**Roles:**"):
            roles_value = roles_line.replace("**Roles:**", "").strip()
        else:
            roles_value = roles_line.strip()
        
        embed.add_field(
            name="Server Roles",
            value=_truncate_embed_field_value(roles_value),
            inline=False,
        )

        embed.set_footer(text="Gitcord Automation Engine")

        await interaction.followup.send(embed=embed, ephemeral=True)

    @tree.command(
        name="summary",
        description="Show contribution metrics (last 7 and 30 days; optional Discord member)",
        guild=discord.Object(id=guild_id),
    )
    @app_commands.describe(
        contributor="Discord member to summarize (omit for yourself; must be verified on Gitcord)",
    )
    async def summary_cmd(
        interaction: discord.Interaction,
        contributor: discord.Member | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        viewing_self = contributor is None or contributor.id == interaction.user.id
        stale_warning = ""
        header = ""

        if viewing_self:
            discord_user_id = str(interaction.user.id)
            get_links = getattr(storage, "get_identity_links_for_discord_user", None)
            if not callable(get_links):
                await interaction.followup.send(
                    "Link status unavailable. Use `/link` to link your GitHub account.",
                    ephemeral=True,
                )
                return
            links = get_links(discord_user_id)
            verified_row = next((r for r in links if int(r.get("verified") or 0) == 1), None)
            if not verified_row:
                await interaction.followup.send(
                    "Link your account with `/link` and `/verify-link` to see your summary.",
                    ephemeral=True,
                )
                return
            github_user = verified_row.get("github_user", "")
            if not github_user:
                await interaction.followup.send("Linked user unknown.", ephemeral=True)
                return
            for line in _identity_status_lines(
                storage,
                config,
                discord_user_id,
                viewing_self=True,
                stale_action="Use `/verify-link` to refresh it.",
            ):
                if line.startswith("⚠️"):
                    stale_warning = f"\n\n{line}"
                    break
            rank_subject = "you're"
        else:
            assert contributor is not None
            github_user = resolve_discord_to_github(storage, str(contributor.id))
            if not github_user:
                await interaction.followup.send(
                    (
                        f"❌ {contributor.mention} has not verified their GitHub identity. "
                        "They need `/link` and `/verify-link` first."
                    ),
                    ephemeral=True,
                )
                return
            header = f"Summary for {contributor.mention} (`{github_user}`)\n\n"
            rank_subject = "they're"

        body = await asyncio.to_thread(
            build_contribution_summary_message,
            storage,
            github_user,
            rank_subject=rank_subject,
        )
        await interaction.followup.send(header + body + stale_warning, ephemeral=True)

    @tree.command(
        name="unlink",
        description="Unlink your verified GitHub identity (cooldown applies after verification)",
        guild=discord.Object(id=guild_id),
    )
    async def unlink_cmd(interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        discord_user_id = str(interaction.user.id)
        cooldown = 24
        if getattr(config, "identity", None) is not None:
            cooldown = getattr(config.identity, "unlink_cooldown_hours", 24) or 24
        try:
            service.unlink(discord_user_id, cooldown)
            await interaction.followup.send(
                "Identity unlinked. You can use `/link` again to relink.",
                ephemeral=True,
            )
        except ValueError as e:
            await interaction.followup.send(str(e), ephemeral=True)

    @tree.command(
        name="open-prs",
        description="List a contributor's currently open PRs in configured repos (read-only)",
        guild=discord.Object(id=guild_id),
    )
    @app_commands.describe(contributor="Discord member whose open PRs to list")
    async def open_prs_cmd(interaction: discord.Interaction, contributor: discord.Member) -> None:
        await interaction.response.defer(ephemeral=True)

        github_user = resolve_discord_to_github(storage, str(contributor.id))
        if not github_user:
            await interaction.followup.send(
                (
                    f"❌ {contributor.mention} has not verified their GitHub identity. "
                    "Use `/link` and `/verify-link` first."
                ),
                ephemeral=True,
            )
            return

        try:
            # Prefer author-scoped Search API (one query) over scanning every configured repo.
            list_for_author = getattr(github_adapter, "list_open_pull_requests_for_author", None)
            if callable(list_for_author):
                open_prs = await asyncio.to_thread(list_for_author, github_user)
            else:
                open_prs = await asyncio.to_thread(
                    lambda: list(github_adapter.list_open_pull_requests())
                )
        except Exception:
            logger.exception(
                "Failed to list open pull requests",
                extra={"github_user": github_user, "discord_user_id": str(contributor.id)},
            )
            await interaction.followup.send(
                "❌ Error fetching open PRs. Please try again later.",
                ephemeral=True,
            )
            return

        prs = list_open_prs_for_author(open_prs, github_user)
        message = format_open_prs_report(
            contributor_mention=contributor.mention,
            github_user=github_user,
            prs=prs,
            org=config.github.org,
        )
        await interaction.followup.send(message, ephemeral=True, suppress_embeds=True)

    @tree.command(
        name="pr",
        description="List a contributor's recent PRs grouped by closed / merged / open",
        guild=discord.Object(id=guild_id),
    )
    @app_commands.describe(
        contributor="Discord member whose PRs to list",
        count="How many recent PRs to show (optional, default 10, max 100)",
        skip="How many recent PRs to skip first (M, optional)",
    )
    @app_commands.checks.cooldown(1, 30.0)
    async def pr_list_cmd(
        interaction: discord.Interaction,
        contributor: discord.Member,
        count: Optional[app_commands.Range[int, 1, 100]] = None,
        skip: app_commands.Range[int, 0, 500] = 0,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        n, m = clamp_pr_list_args(count=count, skip=int(skip))
        logger.info(
            "/pr requested",
            extra={
                "requested_count": count,
                "effective_count": n,
                "skip": m,
                "contributor_id": str(contributor.id),
            },
        )
        github_user = resolve_discord_to_github(storage, str(contributor.id))
        if not github_user:
            await interaction.followup.send(
                (
                    f"❌ {contributor.mention} has not verified their GitHub identity. "
                    "Use `/link` and `/verify-link` first."
                ),
                ephemeral=True,
            )
            return

        try:
            list_for_author = getattr(github_adapter, "list_pull_requests_for_author", None)
            if not callable(list_for_author):
                await interaction.followup.send(
                    "❌ This GitHub adapter cannot list author PRs.",
                    ephemeral=True,
                )
                return
            all_prs = await asyncio.to_thread(list_for_author, github_user)
        except Exception:
            logger.exception(
                "Failed to list pull requests for /pr",
                extra={"github_user": github_user, "discord_user_id": str(contributor.id)},
            )
            await interaction.followup.send(
                "❌ Error fetching PRs. Please try again later.",
                ephemeral=True,
            )
            return

        selected = select_recent_prs(all_prs, count=n, skip=m)
        messages = format_pr_list_messages(
            contributor_mention=contributor.mention,
            github_user=github_user,
            prs=selected,
            org=config.github.org,
            count=n,
            skip=m,
        )
        for message in messages:
            await interaction.followup.send(
                message,
                ephemeral=True,
                suppress_embeds=True,
            )

    def command_permission_check(command_name: str):
        """Restrict slash commands via discord.command_permissions or legacy issue_assignees."""

        def check(interaction: discord.Interaction) -> bool:
            return slash_command_allowed(interaction, config, command_name)

        return check

    @client.event
    async def on_message(message: discord.Message) -> None:
        """Handle passive PR URL detection in configured channels."""
        # Ignore bot messages
        if message.author.bot:
            return
        
        # Check if passive detection is enabled
        pr_preview_channels = getattr(config.discord, "pr_preview_channels", None)
        if not pr_preview_channels:
            return
        
        # Check if message is in a configured channel
        channel_name = message.channel.name if hasattr(message.channel, "name") else None
        if channel_name not in pr_preview_channels:
            return
        
        # Look for PR URLs in message content
        content = message.content or ""
        parsed = parse_pr_url(content)
        if not parsed:
            return
        
        owner, repo, pr_number = parsed
        
        # Fetch and send PR preview
        try:
            pr, reviews, ci_status, last_commit_time = await asyncio.to_thread(
                fetch_pr_context, github_adapter, owner, repo, pr_number
            )
        except Exception:
            logger.exception(
                "Failed to fetch PR context from message",
                extra={"owner": owner, "repo": repo, "pr_number": pr_number},
            )
            return
        
        if not pr:
            # Silently fail for passive detection
            return
        
        # Get Discord mention if author is linked
        author_github = pr.get("user", {}).get("login", "")
        discord_mention = None
        if author_github:
            verified = getattr(storage, "list_verified_identity_mappings", None)
            if callable(verified):
                for mapping in verified():
                    if mapping.github_user == author_github:
                        discord_mention = f"<@{mapping.discord_user_id}>"
                        break
        
        # Build and send embed
        embed_dict = build_pr_embed(
            pr=pr,
            owner=owner,
            repo=repo,
            reviews=reviews,
            ci_status=ci_status,
            last_commit_time=last_commit_time,
            discord_mention=discord_mention,
        )
        
        embed = discord.Embed.from_dict(embed_dict)
        await message.channel.send(embed=embed)

    @tree.command(
        name="sync",
        description="Sync GitHub events and send notifications (mentor-only)",
        guild=discord.Object(id=guild_id),
    )
    @app_commands.check(command_permission_check(SLASH_CMD_SYNC))
    async def sync_cmd(interaction: discord.Interaction) -> None:
        """Manually trigger run-once to sync GitHub events and send notifications."""
        await interaction.response.defer(ephemeral=True)

        status_msg = None
        try:
            # Build orchestrator and run once
            from ghdcbot.engine.orchestrator import Orchestrator

            github_adapter_for_sync = build_adapter(
                config.runtime.github_adapter,
                token=github_token,
                org=config.github.org,
                api_base=str(config.github.api_base),
            )
            discord_reader_for_sync = build_adapter(
                config.runtime.discord_adapter,
                token=config.discord.token,
                guild_id=config.discord.guild_id,
            )
            github_writer_for_sync = build_adapter(
                config.runtime.github_adapter,
                token=github_token,
                org=config.github.org,
                api_base=str(config.github.api_base),
            )
            discord_writer_for_sync = build_adapter(
                config.runtime.discord_adapter,
                token=config.discord.token,
                guild_id=config.discord.guild_id,
            )

            orchestrator = Orchestrator(
                github_reader=github_adapter_for_sync,
                github_writer=github_writer_for_sync,
                discord_reader=discord_reader_for_sync,
                discord_writer=discord_writer_for_sync,
                storage=storage,
                config=config,
            )

            status_msg = await interaction.followup.send(
                "🔄 Syncing GitHub events and sending notifications...",
                ephemeral=True,
                wait=True,
            )

            # run_once() is synchronous and can take many minutes for large orgs; running it on the
            # event loop blocks Discord heartbeats and delays other slash commands (they then hit
            # "application did not respond"). Run it in a worker thread instead.
            def _run_sync_and_close() -> None:
                try:
                    orchestrator.run_once()
                finally:
                    orchestrator.close()

            await asyncio.to_thread(_run_sync_and_close)

            await status_msg.edit(
                content="✅ Sync complete! Notifications sent for new GitHub events.",
            )
        except Exception as exc:
            logger.exception("Sync failed")
            err_text = f"❌ Sync failed: {exc}"
            if status_msg is not None:
                try:
                    await status_msg.edit(content=err_text)
                except Exception:
                    await interaction.followup.send(err_text, ephemeral=True)
            else:
                await interaction.followup.send(err_text, ephemeral=True)

    @tree.error
    async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        """Handle app command errors, including check failures."""
        if isinstance(error, app_commands.CommandOnCooldown):
            retry_after = int(error.retry_after) + 1
            message = f"⏳ This command is on cooldown. Try again in {retry_after}s."
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(message, ephemeral=True)
                else:
                    await interaction.response.send_message(message, ephemeral=True)
            except Exception:
                logger.exception("Failed to send cooldown message")
            return
        if isinstance(error, app_commands.CheckFailure):
            try:
                cmd_name = interaction.command.name if interaction.command else "unknown"
                error_message = format_slash_command_permission_denied(config, cmd_name)
                logger.info(
                    "Check failure for user %s (%s) on command %s.",
                    interaction.user.name,
                    interaction.user.id,
                    cmd_name,
                )
                
                # Check if response is already sent (shouldn't happen for check failures, but be safe)
                if interaction.response.is_done():
                    await interaction.followup.send(error_message, ephemeral=True)
                else:
                    await interaction.response.send_message(error_message, ephemeral=True)
            except Exception as e:
                logger.exception("Failed to send permission denied message", exc_info=e)
                # Try one more time with a simple message
                try:
                    if not interaction.response.is_done():
                        await interaction.response.send_message(
                            "❌ Permission denied. Only mentors can use this command.",
                            ephemeral=True,
                        )
                except Exception:
                    logger.error("Could not send any error message to user")
        else:
            logger.exception("App command error", exc_info=error)
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "❌ An error occurred while processing your command.",
                        ephemeral=True,
                    )
            except Exception:
                logger.error("Could not send error message to user")

    register_social_commands(tree, guild_id, social_service)

    @client.event
    async def on_ready() -> None:
        synced = await tree.sync(guild=discord.Object(id=guild_id))
        cmd_names = [c.name for c in synced]
        logger.info("Bot ready; slash commands synced for guild %s: %s", guild_id, cmd_names)

    client.run(config.discord.token)


def main(config_path: str) -> None:
    """Entry point for running the bot (handles ConfigError)."""
    try:
        run_bot(config_path)
    except ConfigError as e:
        logging.getLogger("ghdcbot.bot").error("Config error: %s", e)
        raise SystemExit(1) from e
