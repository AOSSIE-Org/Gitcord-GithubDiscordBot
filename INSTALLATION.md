# Gitcord Installation Guide

**Complete step-by-step guide for organizations setting up Gitcord**

---

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Step 1: Create GitHub Token (PAT)](#step-1-create-github-token-pat)
3. [Step 2: Create Discord Bot](#step-2-create-discord-bot)
4. [Step 3: Invite Bot to Discord Server](#step-3-invite-bot-to-discord-server)
5. [Step 4: Configure Gitcord](#step-4-configure-gitcord)
6. [Step 5: Run Gitcord](#step-5-run-gitcord)
7. [Security Best Practices](#security-best-practices)
8. [Troubleshooting](#troubleshooting)

---

## Prerequisites

Before starting, ensure you have:

- ✅ Python 3.11+ installed
- ✅ Access to your GitHub organization
- ✅ Admin access to your Discord server
- ✅ GitHub account with organization permissions
- ✅ Discord Developer Portal access

---

## Step 1: Create GitHub Token (PAT)

### 1.1 Navigate to GitHub Settings

1. Go to **GitHub** → **Settings** → **Developer Settings**
2. Click **Personal Access Tokens** → **Fine-grained tokens**
3. Click **Generate new token**

### 1.2 Configure Token

**Token Name:** `Gitcord Bot` (or your preferred name)

**Expiration:** Set appropriate expiration (recommended: 90 days or custom)

**Repository Access:**
- Select **Only select repositories** (recommended) OR
- Select **All repositories** (if you want org-wide access)

### 1.3 Set Repository Permissions

**Required Permissions:**

| Permission | Access Level | Why |
|------------|--------------|-----|
| **Contents** | Read & Write | Needed for GitHub snapshots |
| **Issues** | Read & Write | Assign issues to contributors |
| **Pull requests** | Read & Write | Request reviews, check merge status |
| **Metadata** | Read | Required automatically by GitHub |

**Note:** For testing locally, **Read** permissions are sufficient. **Write** permissions are needed for:
- Issue assignments
- Review requests
- GitHub snapshots

### 1.4 Generate and Save Token

1. Click **Generate token**
2. **⚠️ IMPORTANT:** Copy the token immediately (you won't see it again)
3. Save it securely (we'll use it in Step 4)

---

## Step 2: Create Discord Bot

### 2.1 Create Application

1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Click **New Application**
3. Enter application name: `Gitcord` (or your preferred name)
4. Click **Create**

### 2.2 Create Bot

1. Go to **Bot** section (left sidebar)
2. Click **Add Bot**
3. Click **Yes, do it!**

### 2.3 Configure Bot Settings

**Bot Username:** `Gitcord` (or your preferred name)

**Public Bot:** ❌ Uncheck (unless you want others to invite it)

**Requires OAuth2 Code Grant:** ❌ Uncheck

### 2.4 Set Privileged Gateway Intents

Go to **Bot** → **Privileged Gateway Intents**

**✅ REQUIRED:**
- ✅ **Server Members Intent** (required for role management and member reading)

**❌ NOT REQUIRED:**
- ❌ Presence Intent
- ❌ Message Content Intent (only needed if reading message text; Gitcord uses slash commands)

**Note:** Since Gitcord primarily uses slash commands, Message Content Intent is **NOT required** unless you enable PR preview channels.

### 2.5 Reset Token (Optional)

If you need a new bot token:
1. Click **Reset Token**
2. **⚠️ IMPORTANT:** Copy the token immediately
3. Save it securely (we'll use it in Step 4)

---

## Step 3: Invite Bot to Discord Server

### 3.1 Generate Invite URL

1. Go to **OAuth2** → **URL Generator** (left sidebar)

### 3.2 Select Scopes

**✅ REQUIRED Scopes:**
- ✅ **bot** (bot functionality)
- ✅ **applications.commands** (slash commands)

**❌ DO NOT SELECT:**
- ❌ bot (with Administrator) - Never use Administrator!

### 3.3 Select Bot Permissions

**✅ REQUIRED Permissions:**

#### General Permissions
- ✅ **Manage Roles** (for role automation based on contributions)
- ✅ **View Channels** (to read server structure)

#### Text Permissions
- ✅ **Send Messages** (for notifications and responses)
- ✅ **Embed Links** (for rich embeds in commands)
- ✅ **Read Message History** (for PR preview detection if enabled)
- ✅ **Use Slash Commands** (required for all commands)
- ✅ **Add Reactions** (optional, for UI feedback)

**❌ DO NOT SELECT:**
- ❌ **Administrator** (over-permission, security risk)
- ❌ **Manage Server** (not needed)
- ❌ **Kick Members** (not needed)
- ❌ **Ban Members** (not needed)
- ❌ **Manage Channels** (not needed)
- ❌ **Manage Webhooks** (not needed)
- ❌ **Voice Permissions** (not needed)

### 3.4 Copy Invite URL

1. Copy the generated URL at the bottom
2. Open the URL in your browser
3. Select your Discord server
4. Click **Authorize**
5. Complete CAPTCHA if prompted

### 3.5 ⚠️ CRITICAL: Set Bot Role Position

**After inviting the bot:**

1. Go to your Discord server
2. **Server Settings** → **Roles**
3. Find the **Gitcord** bot role
4. **Drag it ABOVE** any roles it needs to manage
5. Example: If bot assigns "Contributor" role, bot role must be above "Contributor"

**Why:** Discord requires roles to be higher in hierarchy to manage lower roles. If bot role is too low, role management will fail silently.

---

## Step 4: Configure Gitcord

### 4.1 Install Gitcord

```bash
# Clone the repository
git clone https://github.com/AOSSIE-Org/Gitcord-GithubDiscordBot.git
cd Gitcord-GithubDiscordBot

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install Gitcord
pip install -e .
```

### 4.2 Create Environment File

Create a `.env` file in the project root:

```bash
cp .env.example .env
```

Edit `.env` and add your tokens:

```env
GITHUB_TOKEN=your_github_token_here
DISCORD_TOKEN=your_discord_bot_token_here
```

**⚠️ SECURITY:** Never commit `.env` to git. It's already in `.gitignore`.

### 4.3 Create Configuration File

Copy the example config:

```bash
cp config/example.yaml config/my-org-config.yaml
```

Edit `config/my-org-config.yaml`:

```yaml
runtime:
  mode: "dry-run"  # Start with dry-run for safety
  log_level: "INFO"
  data_dir: "./data/my-org"
  github_adapter: "ghdcbot.adapters.github.rest:GitHubRestAdapter"
  discord_adapter: "ghdcbot.adapters.discord.api:DiscordApiAdapter"
  storage_adapter: "ghdcbot.adapters.storage.sqlite:SqliteStorage"

github:
  org: "your-org-name"  # Your GitHub organization name
  token: "${GITHUB_TOKEN}"  # From .env file
  api_base: "https://api.github.com"
  permissions:
    read: true
    write: true  # Enable for issue assignments and snapshots

discord:
  guild_id: "YOUR_DISCORD_GUILD_ID"  # Right-click server → Copy Server ID
  token: "${DISCORD_TOKEN}"  # From .env file
  permissions:
    read: true
    write: true
  notifications:
    enabled: true
    issue_assignment: true
    pr_review_requested: true
    pr_review_result: true
    pr_merged: true
    channel_id: null  # null = DM, or set channel ID for channel posting

scoring:
  period_days: 30  # Scoring period in days
  weights:
    issue_opened: 3
    pr_opened: 5
    pr_reviewed: 2
    comment: 1
    pr_merged: 10

role_mappings:
  - discord_role: "Contributor"
    min_score: 10
  - discord_role: "Maintainer"
    min_score: 40

assignments:
  review_roles:
    - "Maintainer"
  issue_assignees:
    - "Mentor"  # Roles that can use /assign-issue, /sync, /issue-requests

snapshots:
  enabled: true
  repo_path: "your-org/gitcord-data"  # Repository for GitHub snapshots
  branch: "main"
```

### 4.4 Find Discord Guild ID

**Method 1: Discord Desktop/Web**
1. Enable Developer Mode: **User Settings** → **Advanced** → **Developer Mode** ✅
2. Right-click your Discord server icon
3. Click **Copy Server ID**

**Method 2: Discord Mobile**
1. Enable Developer Mode in settings
2. Long-press server icon → **Copy Server ID**

---

## Step 5: Run Gitcord

### 5.1 Test Run (Dry-Run Mode)

**First, test in dry-run mode (safe, no changes):**

```bash
python -m ghdcbot.cli --config config/my-org-config.yaml run-once
```

**Expected Output:**
- ✅ Reads GitHub events
- ✅ Reads Discord members/roles
- ✅ Generates audit reports in `data/my-org/reports/`
- ✅ **No mutations** (no role changes, no issue assignments)

**Check Reports:**
- `data/my-org/reports/audit.json` - Machine-readable report
- `data/my-org/reports/audit.md` - Human-readable report

Review the reports to see what changes would be made.

### 5.2 Run Discord Bot

**Start the Discord bot (for slash commands):**

```bash
python -m ghdcbot.cli --config config/my-org-config.yaml bot
```

**Expected Output:**
```
Bot ready; slash commands synced for guild YOUR_GUILD_ID: ['link', 'verify-link', ...]
```

The bot will stay online and respond to slash commands.

**To run in background:**
```bash
nohup python -m ghdcbot.cli --config config/my-org-config.yaml bot > bot.log 2>&1 &
```

### 5.3 Enable Active Mode (After Testing)

**Once you've verified everything works in dry-run:**

1. Edit `config/my-org-config.yaml`
2. Change `mode: "dry-run"` to `mode: "active"`
3. Run `run-once` again to apply changes

**⚠️ WARNING:** Active mode will:
- Add/remove Discord roles
- Assign GitHub issues
- Request PR reviews

Only enable after reviewing dry-run reports!

---

## Security Best Practices

### ✅ DO:

- ✅ Use **fine-grained GitHub tokens** (not classic tokens)
- ✅ Store tokens in `.env` file (never commit to git)
- ✅ Use **minimal permissions** (only what's needed)
- ✅ Start with **dry-run mode** before going active
- ✅ Review audit reports before enabling writes
- ✅ Rotate tokens regularly (every 90 days recommended)
- ✅ Use **Server Members Intent** only (not all intents)
- ✅ Keep bot role **above** managed roles in Discord

### ❌ DON'T:

- ❌ **Never** commit tokens to GitHub
- ❌ **Never** use **Administrator** permission
- ❌ **Never** use classic GitHub tokens (use fine-grained)
- ❌ **Never** share tokens in screenshots or public channels
- ❌ **Never** enable active mode without testing dry-run first
- ❌ **Never** give bot more permissions than needed

### Token Security Checklist

- [ ] Tokens stored in `.env` file
- [ ] `.env` is in `.gitignore` (verify it's not tracked)
- [ ] Tokens not hardcoded in config files
- [ ] Fine-grained GitHub token (not classic)
- [ ] Minimal Discord permissions (no Administrator)
- [ ] Tokens have expiration dates set
- [ ] Tokens are rotated regularly

---

## Troubleshooting

### Bot Not Responding to Commands

**Problem:** Slash commands don't appear or show "application did not respond"

**Solutions:**
1. ✅ Verify bot is running: `ps aux | grep ghdcbot`
2. ✅ Check bot logs for errors
3. ✅ Wait 30 seconds after starting bot (commands need to sync)
4. ✅ Verify `applications.commands` scope is selected in invite URL
5. ✅ Re-invite bot with correct scopes

### Role Management Not Working

**Problem:** Bot can't add/remove roles

**Solutions:**
1. ✅ Verify bot role is **above** managed roles in Discord Role settings
2. ✅ Check bot has **Manage Roles** permission
3. ✅ Verify `discord.permissions.write: true` in config
4. ✅ Check bot is in **active** mode (not dry-run)
5. ✅ Check bot logs for permission errors

### GitHub API Errors

**Problem:** "GitHub permission or visibility issue"

**Solutions:**
1. ✅ Verify GitHub token has correct permissions
2. ✅ Check token hasn't expired
3. ✅ Verify token has access to organization repos
4. ✅ Check rate limits (GitHub API has rate limits)
5. ✅ Verify `github.permissions.read: true` in config

### Discord API Errors

**Problem:** "Discord permission issue" or 403 errors

**Solutions:**
1. ✅ Verify bot has required permissions (Manage Roles, View Channels)
2. ✅ Check Server Members Intent is enabled
3. ✅ Verify bot token is correct
4. ✅ Check bot is still in server (not kicked)
5. ✅ Verify guild_id is correct

### Identity Linking Not Working

**Problem:** `/link` or `/verify-link` commands fail

**Solutions:**
1. ✅ Verify user has verified Discord ↔ GitHub link
2. ✅ Check verification code is in GitHub bio or public gist
3. ✅ Verify code hasn't expired (10 minutes default)
4. ✅ Check storage is initialized: `storage.init_schema()`
5. ✅ Review bot logs for specific errors

### Snapshots Not Writing

**Problem:** GitHub snapshots not appearing in repo

**Solutions:**
1. ✅ Verify `snapshots.enabled: true` in config
2. ✅ Check GitHub token has **Contents: Write** permission
3. ✅ Verify `snapshots.repo_path` is correct format: `owner/repo`
4. ✅ Check bot has write access to snapshot repo
5. ✅ Review logs for snapshot errors (non-blocking, won't stop run-once)

---

## Quick Reference

### Essential Commands

```bash
# Test run (dry-run, safe)
python -m ghdcbot.cli --config config/my-org-config.yaml run-once

# Run Discord bot
python -m ghdcbot.cli --config config/my-org-config.yaml bot

# Check identity status
python -m ghdcbot.cli --config config/my-org-config.yaml identity status --discord-user-id USER_ID

# Export audit logs
python -m ghdcbot.cli --config config/my-org-config.yaml export-audit --format md
```

### Discord Slash Commands

**For Contributors:**
- `/link` - Link Discord to GitHub
- `/verify-link` - Verify GitHub link
- `/verify` - Check verification status
- `/status` - Show status and roles
- `/summary` - Show contribution metrics
- `/request-issue` - Request issue assignment

**For Mentors:**
- `/assign-issue` - Assign issue to user
- `/issue-requests` - Review pending requests
- `/sync` - Manually sync GitHub events

### Config File Locations

- **Example config:** `config/example.yaml`
- **Your config:** `config/my-org-config.yaml` (create your own)
- **Environment:** `.env` (create from `.env.example`)

---

## Next Steps

After installation:

1. ✅ Run first `run-once` in dry-run mode
2. ✅ Review `data/my-org/reports/audit.md`
3. ✅ Test identity linking with `/link` and `/verify-link`
4. ✅ Verify role mappings match your Discord roles
5. ✅ Test issue assignment flow
6. ✅ Enable active mode when ready
7. ✅ Set up cron job or scheduled task for `run-once`

---

## Getting Help

- **Documentation:** See [README.md](README.md) and [TECHNICAL_DOCUMENTATION.md](TECHNICAL_DOCUMENTATION.md)
- **Issues:** [GitHub Issues](https://github.com/AOSSIE-Org/Gitcord-GithubDiscordBot/issues)
- **Discord:** Join our Discord server for support

---

**🎉 Congratulations!** You've successfully installed Gitcord. Start with dry-run mode and review reports before enabling active mode.
