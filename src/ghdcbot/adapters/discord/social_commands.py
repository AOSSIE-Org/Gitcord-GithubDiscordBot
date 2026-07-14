"""
Discord slash commands for connecting / disconnecting X and LinkedIn.

Contributors enter their username / profile URL manually.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands

from ghdcbot.engine.social_profiles import SocialProfileService

logger = logging.getLogger(__name__)

PLATFORM_CHOICES = [
    app_commands.Choice(name="X", value="x"),
    app_commands.Choice(name="LinkedIn", value="linkedin"),
]


def _platform_label(platform: str) -> str:
    return "X" if platform == "x" else "LinkedIn"


def _manual_profile_hint(platform: str) -> str:
    if platform == "x":
        return "Examples: `@username`, `username`, or `https://x.com/username`"
    return "Example: `https://linkedin.com/in/your-name`"


def register_social_commands(
    tree: app_commands.CommandTree,
    guild_id: int,
    social_service: SocialProfileService,
) -> None:
    """Register /connect-social and /disconnect-social (idempotent)."""
    guild = discord.Object(id=guild_id)
    if tree.get_command("connect-social", guild=guild) is not None:
        return

    @tree.command(
        name="connect-social",
        description="Connect your X or LinkedIn account (enter username or URL)",
        guild=guild,
    )
    @app_commands.describe(
        platform="Social platform to connect",
        profile="X username or LinkedIn profile URL",
    )
    @app_commands.choices(platform=PLATFORM_CHOICES)
    async def connect_social_cmd(
        interaction: discord.Interaction,
        platform: app_commands.Choice[str],
        profile: str,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        platform_value = platform.value
        label = _platform_label(platform_value)
        discord_user_id = str(interaction.user.id)

        if not profile or not profile.strip():
            await interaction.followup.send(
                (
                    f"Please provide your {label} username or profile URL.\n"
                    f"{_manual_profile_hint(platform_value)}"
                ),
                ephemeral=True,
            )
            return

        existing = await social_service.get_profile(discord_user_id, platform_value)

        try:
            linked = await social_service.set_profile(
                discord_user_id,
                platform_value,
                profile.strip(),
            )
        except ValueError as exc:
            embed = discord.Embed(
                title=f"❌ Invalid {label} input",
                description=str(exc),
                color=discord.Color.red(),
            )
            embed.add_field(
                name="Valid formats",
                value=_manual_profile_hint(platform_value),
                inline=False,
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        except Exception:
            logger.exception(
                "Failed to set social profile",
                extra={"platform": platform_value},
            )
            await interaction.followup.send(
                "❌ Could not save your profile. Please try again later.",
                ephemeral=True,
            )
            return

        if existing is not None:
            title = f"✅ {label} updated"
            description = (
                f"Changed from **{existing.display_value}** to **{linked.display_value}**.\n\n"
                "Use `/profile` to view your social profiles."
            )
        else:
            title = f"✅ {label} connected"
            description = (
                f"Linked **{linked.display_value}**.\n\n"
                "Made a mistake? Just run `/connect-social` again to update it.\n"
                "Use `/profile` to view your social profiles."
            )
        embed = discord.Embed(
            title=title,
            description=description,
            color=discord.Color.green(),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @tree.command(
        name="disconnect-social",
        description="Disconnect your X or LinkedIn account from Gitcord",
        guild=guild,
    )
    @app_commands.describe(platform="Social platform to disconnect")
    @app_commands.choices(platform=PLATFORM_CHOICES)
    async def disconnect_social_cmd(
        interaction: discord.Interaction,
        platform: app_commands.Choice[str],
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        platform_value = platform.value
        label = _platform_label(platform_value)
        discord_user_id = str(interaction.user.id)

        try:
            removed = await social_service.remove_profile(
                discord_user_id,
                platform_value,
            )
        except Exception:
            logger.exception(
                "Failed to disconnect social profile",
                extra={"platform": platform_value, "discord_user_id": discord_user_id},
            )
            await interaction.followup.send(
                "❌ Could not disconnect. Please try again later.",
                ephemeral=True,
            )
            return

        if removed:
            embed = discord.Embed(
                title=f"✅ {label} disconnected",
                description=f"Your {label} account was removed from Gitcord.",
                color=discord.Color.green(),
            )
        else:
            embed = discord.Embed(
                title=f"❌ {label} not connected",
                description=f"No {label} account was linked to your Discord user.",
                color=discord.Color.red(),
            )
        await interaction.followup.send(embed=embed, ephemeral=True)
