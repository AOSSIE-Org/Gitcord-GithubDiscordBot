import discord
from discord import app_commands
from discord.ext import commands
import yaml
import os
import re
from database import add_user, get_user_by_discord
from github_client import GitHubClient

CONFIG_PATH = os.path.join(os.path.dirname(__file__), '../../config.yaml')
with open(CONFIG_PATH, 'r') as f:
    config = yaml.safe_load(f)

class Verification(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.gh_client = GitHubClient(config['github']['token'], config['github']['organization'])

    @app_commands.command(name="link", description="Link your Discord account to a GitHub username")
    async def link_account(self, interaction: discord.Interaction, github_username: str):
        """Link your Discord account to a GitHub username."""
        
        # Validate GitHub username format
        if not re.match(r"^[a-z\d](?:[a-z\d]|-(?=[a-z\d])){0,38}$", github_username, re.I):
            await interaction.response.send_message(f"❌ `{github_username}` is not a valid GitHub username format.", ephemeral=True)
            return

        # Check if already linked
        existing_user = get_user_by_discord(interaction.user.id)
        if existing_user:
            await interaction.response.send_message(f"You are already linked as GitHub user: `{existing_user['github_username']}`", ephemeral=True)
            return

        await interaction.response.send_message(f"Verifying ownership of GitHub account `{github_username}`...", ephemeral=True)

        # Feature 1: Verification Logic
        is_verified = self.gh_client.verify_identity(github_username, interaction.user.id)

        if is_verified:
            if add_user(interaction.user.id, github_username):
                await interaction.edit_original_response(content=f"✅ Successfully linked Discord `@{interaction.user.name}` to GitHub `{github_username}`!")
            else:
                await interaction.edit_original_response(content=f"❌ Failed to save to database. That GitHub username might be taken.")
        else:
            await interaction.edit_original_response(content=f"❌ Verification failed. Please add `https://discord.com/users/{interaction.user.id}` to your GitHub Social Accounts (on your profile) and try again.")

async def setup(bot):
    await bot.add_cog(Verification(bot))
