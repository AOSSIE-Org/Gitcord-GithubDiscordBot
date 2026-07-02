"""
Discord slash commands for managing social profiles (X, LinkedIn).

Week 5 Day 6 – Social profile integration.
Provides: /profile set x, /profile set linkedin, /profile view, /profile remove
Extends: /status to show linked social profiles
"""

import logging
from typing import Optional

import discord
from discord import app_commands

logger = logging.getLogger(__name__)


def create_success_embed(platform: str, profile_handle: str) -> discord.Embed:
    """Create success embed for profile linking."""
    platform_display = {
        "x": "X",
        "linkedin": "LinkedIn",
        "bluesky": "Bluesky",
        "mastodon": "Mastodon",
    }.get(platform, platform)
    
    embed = discord.Embed(
        title=f"✅ {platform_display} profile linked",
        description=f"Your {platform_display} profile has been linked successfully.",
        color=discord.Color.green()
    )
    embed.add_field(name="Profile", value=profile_handle, inline=False)
    embed.set_footer(text="Use /profile view to see all profiles")
    return embed


def create_error_embed(platform: str, error_msg: str) -> discord.Embed:
    """Create error embed for invalid input."""
    platform_display = {
        "x": "X",
        "linkedin": "LinkedIn",
        "bluesky": "Bluesky",
        "mastodon": "Mastodon",
    }.get(platform, platform)
    
    if platform == "x":
        valid_formats = "• @username\n• username\n• https://x.com/username\n• https://twitter.com/username"
        rules = "Length: 1–15 characters, alphanumeric + underscore only."
    elif platform == "linkedin":
        valid_formats = "• https://linkedin.com/in/profile-name\n• https://www.linkedin.com/in/profile-name"
        rules = "Note: Company pages and school profiles are not supported."
    else:
        valid_formats = "• Check platform documentation"
        rules = ""
    
    embed = discord.Embed(
        title=f"❌ Invalid {platform_display} input",
        description="Please check the format.",
        color=discord.Color.red()
    )
    embed.add_field(name="Valid formats", value=valid_formats, inline=False)
    if rules:
        embed.add_field(name="Rules", value=rules, inline=False)
    return embed


def create_profile_list_embed(profiles: dict) -> discord.Embed:
    """Create embed showing all user's social profiles."""
    embed = discord.Embed(
        title="Your Social Profiles",
        color=discord.Color.blue()
    )
    
    platforms = ["x", "linkedin", "bluesky", "mastodon"]
    platform_display = {
        "x": "X (Twitter)",
        "linkedin": "LinkedIn",
        "bluesky": "Bluesky",
        "mastodon": "Mastodon",
    }
    
    has_any = False
    for platform in platforms:
        if platform in profiles:
            profile = profiles[platform]
            value = f"{profile.display_value} ✔️"
            embed.add_field(name=platform_display[platform], value=value, inline=False)
            has_any = True
        else:
            embed.add_field(name=platform_display[platform], value="Not linked", inline=False)
    
    if not has_any:
        embed.description = "No social profiles linked yet.\n\nLink your profiles:\n• `/profile set x <username>`\n• `/profile set linkedin <url>`"
    
    return embed


def register_social_commands(tree: app_commands.CommandTree, guild_id: int, service, storage) -> None:
    """Register /profile commands group (idempotent; call once before client.run)."""
    guild = discord.Object(id=guild_id)
    if tree.get_command("profile", guild=guild) is not None:
        return
    
    # Create profile command group
    profile_group = app_commands.Group(
        name="profile",
        description="Manage your social profiles (X, LinkedIn, etc.)"
    )
    
    # /profile set subcommand group
    set_group = app_commands.Group(
        name="set",
        description="Link a social profile"
    )
    
    @set_group.command(
        name="x",
        description="Link your X (Twitter) profile"
    )
    @app_commands.describe(
        username="X username, @mention, or URL"
    )
    async def profile_set_x(interaction: discord.Interaction, username: str) -> None:
        """Set X profile."""
        await interaction.response.defer(ephemeral=True)
        discord_user_id = str(interaction.user.id)
        
        try:
            profile = await service.set_profile(
                discord_user_id,
                "x",
                username
            )
            embed = create_success_embed("x", profile.display_value)
            await interaction.followup.send(embed=embed, ephemeral=True)
        except ValueError as e:
            embed = create_error_embed("x", str(e))
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            logger.exception(f"Error setting X profile: {e}")
            await interaction.followup.send(
                "❌ An error occurred. Please try again later.",
                ephemeral=True
            )
    
    @set_group.command(
        name="linkedin",
        description="Link your LinkedIn profile"
    )
    @app_commands.describe(
        url="LinkedIn profile URL"
    )
    async def profile_set_linkedin(interaction: discord.Interaction, url: str) -> None:
        """Set LinkedIn profile."""
        await interaction.response.defer(ephemeral=True)
        discord_user_id = str(interaction.user.id)
        
        try:
            profile = await service.set_profile(
                discord_user_id,
                "linkedin",
                url
            )
            embed = create_success_embed("linkedin", profile.display_value)
            await interaction.followup.send(embed=embed, ephemeral=True)
        except ValueError as e:
            embed = create_error_embed("linkedin", str(e))
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            logger.exception(f"Error setting LinkedIn profile: {e}")
            await interaction.followup.send(
                "❌ An error occurred. Please try again later.",
                ephemeral=True
            )
    
    profile_group.add_command(set_group)
    
    @profile_group.command(
        name="view",
        description="View all your linked social profiles"
    )
    async def profile_view(interaction: discord.Interaction) -> None:
        """View all profiles."""
        await interaction.response.defer(ephemeral=True)
        discord_user_id = str(interaction.user.id)
        
        try:
            profiles_dict = await service.get_profiles(
                discord_user_id
            )
            embed = create_profile_list_embed(profiles_dict)
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            logger.exception(f"Error viewing profiles: {e}")
            await interaction.followup.send(
                "❌ An error occurred. Please try again later.",
                ephemeral=True
            )
    
    # /profile remove subcommand group
    remove_group = app_commands.Group(
        name="remove",
        description="Remove a linked social profile"
    )
    
    @remove_group.command(
        name="x",
        description="Remove your X profile"
    )
    async def profile_remove_x(interaction: discord.Interaction) -> None:
        """Remove X profile."""
        await interaction.response.defer(ephemeral=True)
        discord_user_id = str(interaction.user.id)
        
        try:
            removed = await service.remove_profile(
                discord_user_id,
                "x"
            )
            
            if removed:
                embed = discord.Embed(
                    title="✅ X profile removed",
                    color=discord.Color.green()
                )
            else:
                embed = discord.Embed(
                    title="❌ X profile not found",
                    description="This profile was not linked to your account.",
                    color=discord.Color.red()
                )
            
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            logger.exception(f"Error removing X profile: {e}")
            await interaction.followup.send(
                "❌ An error occurred. Please try again later.",
                ephemeral=True
            )
    
    @remove_group.command(
        name="linkedin",
        description="Remove your LinkedIn profile"
    )
    async def profile_remove_linkedin(interaction: discord.Interaction) -> None:
        """Remove LinkedIn profile."""
        await interaction.response.defer(ephemeral=True)
        discord_user_id = str(interaction.user.id)
        
        try:
            removed = await service.remove_profile(
                discord_user_id,
                "linkedin"
            )
            
            if removed:
                embed = discord.Embed(
                    title="✅ LinkedIn profile removed",
                    color=discord.Color.green()
                )
            else:
                embed = discord.Embed(
                    title="❌ LinkedIn profile not found",
                    description="This profile was not linked to your account.",
                    color=discord.Color.red()
                )
            
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            logger.exception(f"Error removing LinkedIn profile: {e}")
            await interaction.followup.send(
                "❌ An error occurred. Please try again later.",
                ephemeral=True
            )
    
    profile_group.add_command(remove_group)
    tree.add_command(profile_group, guild=guild)
