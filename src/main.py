import discord
from discord.ext import commands
import yaml
import sys
import os

# Load config
CONFIG_PATH = os.path.join(os.path.dirname(__file__), '../config.yaml')
try:
    with open(CONFIG_PATH, 'r') as f:
        config = yaml.safe_load(f)
except FileNotFoundError:
    print("config.yaml not found!")
    sys.exit(1)

# Initialize Bot
intents = discord.Intents.default()
intents.message_content = True
intents.members = True # Needed for role management

bot = commands.Bot(command_prefix='!', intents=intents)

# Database Init
from database import init_db
init_db()

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user} (ID: {bot.user.id})')
    print('------')
    # Load Cogs
    await bot.load_extension('cogs.verification')
    await bot.load_extension('cogs.events')
    await bot.load_extension('cogs.admin')
    
    # Sync Slash Commands
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s)")
    except Exception as e:
        print(f"Failed to sync commands: {e}")

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
    # Determine if user is an admin
    is_admin = False
    if interaction.guild:
        is_admin = interaction.user.guild_permissions.administrator
    elif interaction.user.id == bot.owner_id: # Fallback if owner is set
        is_admin = True

    if isinstance(error, discord.app_commands.errors.MissingPermissions):
        await interaction.response.send_message(f"❌ You don't have the required permissions to run this command.", ephemeral=True)
        return
    elif isinstance(error, discord.app_commands.errors.CommandOnCooldown):
        await interaction.response.send_message(f"⌛ Command is on cooldown. Try again in {error.retry_after:.2f}s.", ephemeral=True)
        return
    elif isinstance(error, discord.app_commands.errors.CheckFailure):
        await interaction.response.send_message(f"❌ You are not authorized to use this command.", ephemeral=True)
        return

    # Ultimate error handler
    print(f"Unhandled command error: {error}")
    error_msg = f"❌ something went wrong"
    if is_admin:
        error_msg += f", Error: `{str(error)}`"
    
    try:
        if not interaction.response.is_done():
            await interaction.response.send_message(error_msg, ephemeral=True)
        else:
            await interaction.followup.send(error_msg, ephemeral=True)
    except Exception as e:
        print(f"Failed to send error message: {e}")

@bot.event
async def on_command_error(ctx, error):
    """Handler for prefix-based command errors."""
    if isinstance(error, commands.CommandNotFound):
        return
        
    is_admin = False
    if ctx.guild:
        is_admin = ctx.author.guild_permissions.administrator
        
    print(f"Prefix command error: {error}")
    error_msg = f"❌ something went wrong"
    if is_admin:
        error_msg += f", Error: `{str(error)}`"
    
    await ctx.send(error_msg, delete_after=15)

if __name__ == '__main__':
    token = config['discord']['token']
    if token == "YOUR_DISCORD_BOT_TOKEN":
        print("❌ Error: You must set the 'discord.token' in config.yaml")
        sys.exit(1)
        
    try:
        bot.run(token)
    except discord.errors.LoginFailure:
        print("❌ Error: Invalid Discord Token. Please check config.yaml")
    except Exception as e:
        print(f"❌ Error: {e}")
