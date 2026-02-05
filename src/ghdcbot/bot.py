"""Discord bot for identity linking and informational slash commands."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands

from ghdcbot.adapters.github.identity import GitHubIdentityReader
from ghdcbot.config.loader import load_config
from ghdcbot.core.errors import ConfigError
from ghdcbot.engine.identity_linking import IdentityLinkService
from ghdcbot.engine.metrics import (
    format_metrics_summary,
    get_contribution_metrics,
    get_rank_for_user,
    rank_by_activity,
)
from ghdcbot.logging.setup import configure_logging
from ghdcbot.plugins.registry import build_adapter


def run_bot(config_path: str) -> None:
    """Run the Discord bot with /link, /verify-link, /verify, /status, and /summary."""
    config = load_config(config_path)
    configure_logging(config.runtime.log_level)
    logger = logging.getLogger("ghdcbot.bot")
    logger.info(
        "Using config: %s → data_dir: %s (identity links persist here)",
        config_path,
        config.runtime.data_dir,
    )

    storage = build_adapter(
        config.runtime.storage_adapter,
        data_dir=config.runtime.data_dir,
    )
    storage.init_schema()
    github_identity = GitHubIdentityReader(
        token=config.github.token,
        api_base=str(config.github.api_base),
    )
    service = IdentityLinkService(storage=storage, github_identity=github_identity)
    discord_reader = build_adapter(
        config.runtime.discord_adapter,
        token=config.discord.token,
        guild_id=config.discord.guild_id,
    )

    intents = discord.Intents.default()
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
        msg = (
            f"**Verification code:** `{claim.verification_code}`\n\n"
            "1. Put this code in your **GitHub profile bio** or in a **public gist**.\n"
            f"2. Run `/verify-link` with `{github_username}` here.\n\n"
            f"Code expires at (UTC): {claim.expires_at.isoformat()}"
        )
        await interaction.followup.send(msg, ephemeral=True)

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
            ok, location = service.verify_claim(discord_user_id, github_username)
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
                    f"Verified: **{github_username}** ↔ your Discord (found in {location}).",
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
        name="verify",
        description="Show your GitHub link verification status (read-only)",
        guild=discord.Object(id=guild_id),
    )
    async def verify_cmd(interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        discord_user_id = str(interaction.user.id)
        get_links = getattr(storage, "get_identity_links_for_discord_user", None)
        if callable(get_links):
            links = get_links(discord_user_id)
        else:
            links = []
            verified = getattr(storage, "list_verified_identity_mappings", None)
            if callable(verified):
                for m in verified():
                    if m.discord_user_id == discord_user_id:
                        links.append({"github_user": m.github_user, "verified": 1})
                        break
        if not links:
            await interaction.followup.send(
                "Not linked. Use `/link` to start.",
                ephemeral=True,
            )
            return
        verified_row = next((r for r in links if int(r.get("verified") or 0) == 1), None)
        pending = [r for r in links if int(r.get("verified") or 0) == 0]
        if verified_row:
            msg = f"Linked to GitHub: **{verified_row.get('github_user', '?')}**."
            # Check for stale status
            get_status = getattr(storage, "get_identity_status", None)
            if callable(get_status):
                max_age_days = None
                if getattr(config, "identity", None) is not None:
                    max_age_days = getattr(config.identity, "verified_max_age_days", None)
                status = get_status(discord_user_id, max_age_days=max_age_days)
                if status.get("is_stale"):
                    msg += "\n\n⚠️ **Warning:** Your identity verification is stale. Use `/verify-link` to refresh it."
            await interaction.followup.send(msg, ephemeral=True)
        elif pending:
            p = pending[0]
            exp = p.get("expires_at") or "—"
            await interaction.followup.send(
                f"Pending: link to **{p.get('github_user', '?')}** (expires: {exp}). Run `/verify-link` to complete.",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                "Not linked. Use `/link` to start.",
                ephemeral=True,
            )

    @tree.command(
        name="status",
        description="Show verification state, activity window, and your roles (read-only)",
        guild=discord.Object(id=guild_id),
    )
    async def status_cmd(interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        discord_user_id = str(interaction.user.id)
        period_days = config.scoring.period_days
        lines = [f"**Activity window:** last {period_days} days (from bot config)."]
        get_links = getattr(storage, "get_identity_links_for_discord_user", None)
        if callable(get_links):
            links = get_links(discord_user_id)
            verified_row = next((r for r in links if int(r.get("verified") or 0) == 1), None)
            if verified_row:
                lines.append(f"**Linked GitHub:** {verified_row.get('github_user', '?')}.")
                # Check for stale status
                get_status = getattr(storage, "get_identity_status", None)
                if callable(get_status):
                    max_age_days = None
                    if getattr(config, "identity", None) is not None:
                        max_age_days = getattr(config.identity, "verified_max_age_days", None)
                    status = get_status(discord_user_id, max_age_days=max_age_days)
                    if status.get("is_stale"):
                        lines.append("⚠️ **Warning:** Identity verification is stale. Use `/verify-link` to refresh.")
            else:
                lines.append("**Linked GitHub:** not linked.")
        else:
            lines.append("**Linked GitHub:** (link status unavailable).")
        member_roles = discord_reader.list_member_roles()
        my_roles = member_roles.get(discord_user_id, [])
        if my_roles:
            lines.append(f"**Your roles:** {', '.join(my_roles)}.")
        else:
            lines.append("**Your roles:** (none or unable to read).")
        await interaction.followup.send("\n".join(lines), ephemeral=True)

    @tree.command(
        name="summary",
        description="Show your contribution metrics summary (last 7 and 30 days; read-only)",
        guild=discord.Object(id=guild_id),
    )
    async def summary_cmd(interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
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
        # Check for stale status
        stale_warning = ""
        get_status = getattr(storage, "get_identity_status", None)
        if callable(get_status):
            max_age_days = None
            if getattr(config, "identity", None) is not None:
                max_age_days = getattr(config.identity, "verified_max_age_days", None)
            status = get_status(discord_user_id, max_age_days=max_age_days)
            if status.get("is_stale"):
                stale_warning = "\n\n⚠️ **Warning:** Identity verification is stale. Use `/verify-link` to refresh it."
        now = datetime.now(timezone.utc)
        weights = getattr(config.scoring, "weights", None) or {}
        parts = []
        for days in (7, 30):
            start = now - timedelta(days=days)
            metrics_list = get_contribution_metrics(storage, start, now, weights)
            user_metrics = next((m for m in metrics_list if m.github_user == github_user), None)
            parts.append(f"**Last {days} days:**\n{format_metrics_summary(user_metrics)}")
        ranked_30 = rank_by_activity(
            get_contribution_metrics(storage, now - timedelta(days=30), now, weights)
        )
        rank = get_rank_for_user(ranked_30, github_user)
        if rank is not None:
            parts.append(f"Top contributors by activity (last 30 days): you're #{rank}.")
        await interaction.followup.send("\n\n".join(parts) + stale_warning, ephemeral=True)

    identity_group = app_commands.Group(
        name="identity",
        description="Identity linking status (read-only)",
    )

    @identity_group.command(
        name="status",
        description="Show your linked GitHub account and verification status (read-only)",
    )
    async def identity_status_cmd(interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        discord_user_id = str(interaction.user.id)
        get_status = getattr(storage, "get_identity_status", None)
        if not callable(get_status):
            await interaction.followup.send(
                "Identity status is unavailable.",
                ephemeral=True,
            )
            return
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
        verified_at = status.get("verified_at")
        verified_at_str = verified_at if verified_at else "—"
        if verified_at_str != "—":
            try:
                dt = datetime.fromisoformat(verified_at_str.replace("Z", "+00:00"))
                verified_at_str = dt.strftime("%Y-%m-%d %H:%M:%S UTC")
            except (ValueError, TypeError):
                pass
        msg = (
            f"**GitHub user:** {github_user}\n"
            f"**Status:** {status_label}\n"
            f"**Verified at:** {verified_at_str}"
        )
        if status.get("is_stale"):
            msg += "\n\n⚠️ **Warning:** Your identity verification is stale. Use `/verify-link` to refresh it."
        await interaction.followup.send(msg, ephemeral=True)

    tree.add_command(identity_group, guild=discord.Object(id=guild_id))

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

    @client.event
    async def on_ready() -> None:
        await tree.sync(guild=discord.Object(id=guild_id))
        logger.info("Bot ready; slash commands synced for guild %s", guild_id)

    client.run(config.discord.token)


def main(config_path: str) -> None:
    """Entry point for running the bot (handles ConfigError)."""
    try:
        run_bot(config_path)
    except ConfigError as e:
        logging.getLogger("ghdcbot.bot").error("Config error: %s", e)
        raise SystemExit(1) from e
