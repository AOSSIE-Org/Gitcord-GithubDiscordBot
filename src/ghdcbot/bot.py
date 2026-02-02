"""Discord bot for identity linking via slash commands."""

from __future__ import annotations

import logging

import discord
from discord import app_commands

from ghdcbot.adapters.github.identity import GitHubIdentityReader
from ghdcbot.adapters.storage.sqlite import SqliteStorage
from ghdcbot.config.loader import load_config
from ghdcbot.core.errors import ConfigError
from ghdcbot.engine.identity_linking import IdentityLinkService
from ghdcbot.logging.setup import configure_logging
from ghdcbot.plugins.registry import build_adapter


def run_bot(config_path: str) -> None:
    """Run the Discord bot with /link and /verify-link slash commands."""
    config = load_config(config_path)
    configure_logging(config.runtime.log_level)
    logger = logging.getLogger("ghdcbot.bot")

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
        try:
            claim = service.create_claim(discord_user_id, github_username)
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
