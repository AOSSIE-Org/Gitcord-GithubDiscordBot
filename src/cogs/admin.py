import discord
from discord import app_commands
from discord.ext import commands
from database import add_repo, remove_repo, add_maintainer, remove_maintainer, get_user_by_discord
import re

class Admin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # Regex for basic GitHub repo URL validation
    GITHUB_REPO_REGEX = r"^https?://github\.com/[\w\-\.]+ /[\w\-\.]+(/)?$"

    # Group for repo commands
    repo_group = app_commands.Group(name="repo", description="Manage linked repositories")

    @repo_group.command(name="add", description="Link repositories to this channel")
    @app_commands.checks.has_permissions(administrator=True)
    async def repo_add(self, interaction: discord.Interaction, repo_urls: str):
        """Link repositories to this channel. Separated by comma or space."""
        urls = repo_urls.replace(',', ' ').split()
        added = []
        failed = []
        invalid = []

        for url in urls:
            url = url.strip().rstrip('/')
            if not url: continue
            
            # Simple validation
            if not re.match(r"^https?://github\.com/[\w\-\.]+/[\w\-\.]+$", url):
                invalid.append(url)
                continue

            if add_repo(url, interaction.channel_id):
                added.append(url)
            else:
                failed.append(url)
        
        msg = ""
        if added:
            msg += f"✅ Linked: {', '.join(added)}\n"
        if invalid:
            msg += f"⚠️ Invalid GitHub URLs (Skipped): {', '.join(invalid)}\n"
        if failed:
            msg += f"❌ Failed (Already linked or DB error): {', '.join(failed)}"
        
        if not msg:
            msg = "❌ No valid URLs provided."
            
        await interaction.response.send_message(msg)

    @repo_group.command(name="remove", description="Unlink a repository")
    @app_commands.checks.has_permissions(administrator=True)
    async def repo_remove(self, interaction: discord.Interaction, repo_url: str):
        """Remove a linked repository from this channel."""
        repo_url = repo_url.strip().rstrip('/')
        if remove_repo(repo_url, interaction.channel_id):
            await interaction.response.send_message(f"✅ Removed {repo_url}")
        else:
            await interaction.response.send_message(f"❌ Could not find {repo_url} linked to this channel.", ephemeral=True)

    # Group for maintainer commands
    maintainer_group = app_commands.Group(name="maintainer", description="Manage project maintainers")

    @maintainer_group.command(name="add", description="Add a maintainer for a repository")
    @app_commands.checks.has_permissions(administrator=True)
    async def maintainer_add(self, interaction: discord.Interaction, user: discord.User, repo_url: str):
        """Add a maintainer for a specific linked repository."""
        repo_url = repo_url.strip().rstrip('/')
        
        # Validate GitHub URL format
        if not re.match(r"^https?://github\.com/[\w\-\.]+/[\w\-\.]+$", repo_url):
            await interaction.response.send_message(f"❌ `{repo_url}` is not a valid GitHub repository URL.", ephemeral=True)
            return

        # Check if user is linked first
        db_user = get_user_by_discord(user.id)
        if not db_user:
             await interaction.response.send_message(f"❌ {user.mention} is not linked! They must use `/link` first.", ephemeral=True)
             return

        if add_maintainer(user.id, repo_url):
            await interaction.response.send_message(f"✅ Added {user.mention} as maintainer for {repo_url}")
        else:
            await interaction.response.send_message(f"❌ Failed. Check if they are already a maintainer or if the repo is valid.", ephemeral=True)

    @maintainer_group.command(name="remove", description="Remove a maintainer from a repository")
    @app_commands.checks.has_permissions(administrator=True)
    async def maintainer_remove(self, interaction: discord.Interaction, user: discord.User, repo_url: str):
        repo_url = repo_url.strip().rstrip('/')
        if remove_maintainer(user.id, repo_url):
            await interaction.response.send_message(f"✅ Removed {user.mention} from maintainers of {repo_url}")
        else:
             await interaction.response.send_message(f"❌ Failed to remove. Check repo URL and if they are a maintainer.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(Admin(bot))
