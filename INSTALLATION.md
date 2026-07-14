# Gitcord Installation Guide

**Step-by-step setup for organizations deploying Gitcord**

Use this guide from start to finish. For a shorter overview, see [README.md](README.md). For Docker details, see [docs/DOCKER.md](docs/DOCKER.md). For environment variables, see [environment_variables.md](environment_variables.md).

---

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Step 1: Create GitHub Token (PAT)](#step-1-create-github-token-pat)
3. [Step 2: Create Discord Bot](#step-2-create-discord-bot)
4. [Step 3: Invite Bot to Discord Server](#step-3-invite-bot-to-discord-server)
5. [Step 4: Get the Code](#step-4-get-the-code)
6. [Step 5: Choose Installation Method](#step-5-choose-installation-method)
   - [Option A: Docker (recommended)](#option-a-docker-recommended)
   - [Option B: Local Python](#option-b-local-python)
   - [Windows and WSL](#windows-and-wsl)
7. [Step 6: Configure Gitcord](#step-6-configure-gitcord)
8. [Step 7: Run Gitcord](#step-7-run-gitcord)
9. [Step 8: Enable Active Mode (after testing)](#step-8-enable-active-mode-after-testing)
10. [Security Best Practices](#security-best-practices)
11. [Troubleshooting](#troubleshooting)
12. [Quick Reference](#quick-reference)

---

## Prerequisites

Before starting, ensure you have:

| Requirement | Docker install | Local install |
|-------------|----------------|---------------|
| **Git** | ✅ | ✅ |
| **Docker Engine + Docker Compose v2** | ✅ | — |
| **Python 3.11+** | — | ✅ |
| **GitHub org** access and a fine-grained PAT | ✅ | ✅ |
| **Discord server** admin access | ✅ | ✅ |
| **[Discord Developer Portal](https://discord.com/developers/applications)** access | ✅ | ✅ |

> **Windows users:** Use [WSL2](#windows-and-wsl) (Ubuntu) for the local Python path. Docker Desktop on Windows is supported when the project lives on the WSL filesystem.

---

## Step 1: Create GitHub Token (PAT)

### 1.1 Navigate to GitHub Settings

1. Go to **GitHub** → **Settings** → **Developer Settings**
2. Click **Personal Access Tokens** → **Fine-grained tokens**
3. Click **Generate new token**

### 1.2 Configure Token

**Token name:** `Gitcord Bot` (or your preferred name)

**Expiration:** Set appropriate expiration (recommended: 90 days or custom)

**Resource owner:** Select your **organization** (not only your personal account) if Gitcord will scan org repositories.

**Repository access:**

- **Only select repositories** (recommended), or
- **All repositories** (org-wide access)

### 1.3 Set Repository Permissions

| Permission | Access level | Why |
|------------|--------------|-----|
| **Contents** | Read (Write if using snapshots) | Repo metadata; Write for GitHub snapshots |
| **Issues** | Read & Write | Issue assignment |
| **Pull requests** | Read & Write | Review requests, merge status |
| **Metadata** | Read | Required automatically by GitHub |

For initial dry-run testing, **Read** on Contents/Issues/PRs is enough. Enable **Write** before active mode if you need assignments or snapshots.

### 1.4 Generate and Save Token

1. Click **Generate token**
2. **Copy the token immediately** (you will not see it again)
3. Save it securely — you will add it to `.env` in [Step 6](#step-6-configure-gitcord)

---

## Step 2: Create Discord Bot

### 2.1 Create Application

1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Click **New Application**
3. Enter application name: `Gitcord` (or your preferred name)
4. Click **Create**

### 2.2 Create Bot

1. Open the **Bot** section (left sidebar)
2. Click **Add Bot** → **Yes, do it!**

### 2.3 Configure Bot Settings

- **Public Bot:** off (unless you want others to invite it)
- **Requires OAuth2 Code Grant:** off

### 2.4 Set Privileged Gateway Intents

Under **Bot** → **Privileged Gateway Intents**:

| Intent | Required? |
|--------|-----------|
| **Server Members Intent** | ✅ **Yes** — member listing for role planning |
| Presence Intent | ❌ No |
| Message Content Intent | ❌ No (only if you enable `pr_preview_channels` in config) |

### 2.5 Copy Bot Token

1. Click **Reset Token** (or copy the existing token)
2. **Copy the token immediately**
3. Save it for `.env` in [Step 6](#step-6-configure-gitcord)

---

## Step 3: Invite Bot to Discord Server

### 3.1 Generate Invite URL

1. Go to **OAuth2** → **URL Generator**

### 3.2 Select Scopes

- ✅ `bot`
- ✅ `applications.commands`
- ❌ Do **not** use Administrator scope

### 3.3 Select Bot Permissions

**Required:**

- Manage Roles, View Channels
- Send Messages, Embed Links, Read Message History, Use Slash Commands

**Not needed:** Administrator, Manage Server, Kick/Ban Members, Manage Channels, Voice permissions

### 3.4 Authorize

1. Copy the generated URL and open it in a browser
2. Select your Discord server → **Authorize**

### 3.5 Set Bot Role Position

1. In Discord: **Server Settings** → **Roles**
2. Drag the **bot role above** any roles it should assign (e.g. above `Contributor`)

If the bot role is too low, role changes fail silently.

### 3.6 Copy Server ID (guild ID)

1. Enable **Developer Mode**: User Settings → Advanced → Developer Mode
2. Right-click your server icon → **Copy Server ID**

You will paste this into `discord.guild_id` in config.

---

## Step 4: Get the Code

```bash
git clone https://github.com/AOSSIE-Org/Gitcord-GithubDiscordBot.git
cd Gitcord-GithubDiscordBot
```

Forks: clone your fork URL instead; the rest of the guide is the same.

---

## Step 5: Choose Installation Method

### Option A: Docker (recommended)

Best if you want to skip Python setup. Requires Docker Engine and Compose v2.

```bash
cp .env.example .env
cp config/docker-example.yaml config/config.yaml
# Edit .env (tokens) and config/config.yaml (github.org, discord.guild_id)
docker compose up -d
docker compose logs -f bot
```

- **Config file:** `config/config.yaml` (from `config/docker-example.yaml`)
- **`data_dir` must stay** `/data` (Docker volume)
- **One-time sync:** `docker compose run --rm bot --config /app/config/config.yaml run-once`

See [docs/DOCKER.md](docs/DOCKER.md) for pitfalls and audit workflow.

### Option B: Local Python

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows WSL: same; native Windows: .venv\Scripts\activate
pip install -e .
cp .env.example .env
cp config/example.yaml config/config.yaml
# Edit .env and config/config.yaml (see Step 6)
ghdcbot --config config/config.yaml run-once
ghdcbot --config config/config.yaml bot
```

> Gitcord uses `pyproject.toml`, not `requirements.txt`. Install with `pip install -e .`.

**Set a local data directory** in `config/config.yaml`:

```yaml
runtime:
  data_dir: "./data"
```

Reports appear under `./data/reports/`.

### Windows and WSL

- **Recommended:** Install [WSL2](https://learn.microsoft.com/en-us/windows/wsl/install) with Ubuntu, clone the repo inside WSL (`~/projects/...`), and follow **Option B** or **Option A** there.
- **Docker Desktop:** Enable WSL2 integration; run `docker compose` from the WSL shell where the repo lives.
- **Volume mounting:** Keep the repo on the Linux filesystem (`/home/...`), not `C:\...`, so bind mounts and file permissions work reliably.
- **Native Windows Python** is possible but not officially tested; prefer WSL.

---

## Step 6: Configure Gitcord

### 6.1 Environment variables

```bash
cp .env.example .env
```

Edit `.env`:

```env
GITHUB_TOKEN=your_github_token_here
DISCORD_TOKEN=your_discord_bot_token_here
```

Details: [environment_variables.md](environment_variables.md)

### 6.2 Configuration file

Your active config is always **`config/config.yaml`** (gitignored — create it from a template):

| Install type | Copy from |
|--------------|-----------|
| Docker | `config/docker-example.yaml` |
| Local Python | `config/example.yaml` |

**Minimum edits:**

1. `github.org` — your GitHub organization name
2. `discord.guild_id` — server ID from [Step 3.6](#36-copy-server-id-guild-id)
3. `role_mappings` / `assignments.issue_assignees` — use **exact Discord role names** that exist in your server
4. Local only: `runtime.data_dir: "./data"`

Start with defaults in the template:

- `runtime.mode: "dry-run"`
- `discord.permissions.write: false`
- `github.permissions.write: false`

Optional blocks (`notifications`, `snapshots`, `repos` filter) are commented in `config/example.yaml` — enable only when needed.

**Reference configs** (not used automatically): `config/examples/` (e.g. AOSSIE sample).

### 6.3 Validate setup (recommended)

Before your first sync or bot start, run the read-only preflight check. It loads config, verifies tokens against GitHub and Discord, checks guild access, and (when `enable_discord_role_updates: true`) confirms configured Discord roles exist.

**Docker:**

```bash
docker compose run --rm bot --config /app/config/config.yaml validate
```

**Local:**

```bash
ghdcbot --config config/config.yaml validate
```

Example success output:

```text
✓ Config file loaded
✓ YAML valid and schema OK
✓ GITHUB_TOKEN configured
✓ DISCORD_TOKEN configured
✓ GitHub authentication successful
✓ Discord authentication successful
✓ Organization accessible
✓ Repositories visible
✓ Guild found
✓ Role "Contributor" found

Validation passed.
```

Exit code `0` means ready to run; `1` means fix the reported issues first. No data is written and the bot does not start.

---

## Step 7: Run Gitcord

### 7.1 One-time sync (dry-run)

Validates GitHub/Discord access and writes audit reports **without mutating** roles or issues.

**Docker:**

```bash
docker compose run --rm bot --config /app/config/config.yaml run-once
```

**Local:**

```bash
ghdcbot --config config/config.yaml run-once
```

**Expected:**

- GitHub events ingested (may be zero on a quiet org)
- Discord members/roles read (needs Server Members Intent)
- Reports written to `<data_dir>/reports/audit.json` and `audit.md`

Review `audit.md` before enabling active mode.

### 7.2 Run Discord bot

**Docker:**

```bash
docker compose up -d
docker compose logs -f bot
```

**Local:**

```bash
ghdcbot --config config/config.yaml bot
```

**Expected log line:**

```text
Bot ready; slash commands synced for guild YOUR_GUILD_ID: ['link', 'verify-link', ...]
```

Wait ~30 seconds after startup for slash commands to appear.

**Background (local Linux/macOS):**

```bash
nohup ghdcbot --config config/config.yaml bot > bot.log 2>&1 &
```

### 7.3 Smoke-test slash commands

**Contributors:** `/link`, `/verify-link`, `/profile` (optional Discord member), `/summary` (optional Discord member for another verified contributor), `/open-prs`, `/unlink`, `/connect-social`, `/disconnect-social`

**Mentors** (need `Mentor` role or `discord.command_permissions`): `/sync`

See [docs/TESTING_DISCORD.md](docs/TESTING_DISCORD.md) for a full test sequence.

---

## Step 8: Enable Active Mode (after testing)

Only after reviewing dry-run `audit.md`:

1. Edit `config/config.yaml`:

```yaml
runtime:
  mode: "active"
  enable_discord_role_updates: true

discord:
  permissions:
    write: true

github:
  permissions:
    write: true   # if you use issue assignment or snapshots
```

2. Run `run-once` again (Docker or local command from [§7.1](#71-one-time-sync-dry-run))
3. Confirm role changes in Discord and bot role hierarchy

`enable_discord_role_updates: false` prevents Discord role changes even if mode is mis-set — keep it `false` until you are ready.

---

## Security Best Practices

### Do

- Use fine-grained GitHub tokens
- Store tokens in `.env` only
- Start in **dry-run**; review audit reports
- Keep bot role above managed roles
- Enable minimal Discord permissions (no Administrator)

### Do not

- Commit `.env` or `config/config.yaml` with secrets
- Enable active mode without a dry-run review
- Share tokens in chat or screenshots

---

## Troubleshooting

### Bot not responding to slash commands

1. Confirm bot is running (`docker compose logs -f bot` or `ps aux | grep ghdcbot`)
2. Wait 30s after startup for command sync
3. Verify `applications.commands` scope on invite URL
4. Confirm `discord.guild_id` matches your server

### Role management not working

1. Bot role **above** target roles in Server Settings → Roles
2. `runtime.mode: active` and `enable_discord_role_updates: true`
3. `discord.permissions.write: true`
4. Server Members Intent enabled in Developer Portal

### Missing environment variable

```text
Missing required environment variable: GITHUB_TOKEN
```

Create `.env` in the project root with both tokens. Docker: same directory as `docker-compose.yml`.

### Config file not found

```text
Config file does not exist: ...
```

Run `cp config/docker-example.yaml config/config.yaml` (Docker) or `cp config/example.yaml config/config.yaml` (local).

### Validation command failures

Run `ghdcbot --config config/config.yaml validate` (or the Docker equivalent) after editing `.env` and config.

| Message | Fix |
|---------|-----|
| `GITHUB_TOKEN is missing` | Add token to `.env` |
| `GitHub authentication failed` | Regenerate PAT; confirm org access |
| `Discord authentication failed` | Reset bot token in Developer Portal |
| `Guild not found` | Re-invite bot; fix `discord.guild_id` |
| `Role "…" not found` | Create role in Discord or fix YAML spelling |

### Identity linking (`/link`, `/verify-link`)

1. Put verification code in GitHub **bio** or a **public gist**
2. Code expires in 10 minutes — run `/link` again if needed
3. See [docs/IDENTITY_VERIFICATION.md](docs/IDENTITY_VERIFICATION.md)

### Social profiles (`/connect-social`, `/disconnect-social`)

Contributors link their X or LinkedIn manually — no external setup required:

- `/connect-social` — pick a platform and enter your X username (e.g. `@name`) or LinkedIn URL
- `/disconnect-social` — remove a linked profile

Linked profiles show up in `/profile`.

### GitHub 403 / permission errors

1. Token not expired; org resource owner selected correctly
2. Token has access to target repositories
3. `github.permissions.read: true` in config

### Docker: state lost after restart

Keep `data_dir: "/data"` in config; do not remove the `gitcord_data` volume.

---

## Quick Reference

### Essential commands

| Action | Docker | Local |
|--------|--------|-------|
| Start bot | `docker compose up -d` | `ghdcbot --config config/config.yaml bot` |
| Logs | `docker compose logs -f bot` | terminal output / `bot.log` |
| Dry-run sync | `docker compose run --rm bot --config /app/config/config.yaml run-once` | `ghdcbot --config config/config.yaml run-once` |
| Validate setup | `docker compose run --rm bot --config /app/config/config.yaml validate` | `ghdcbot --config config/config.yaml validate` |
| Identity status | `docker compose run --rm bot --config /app/config/config.yaml identity status --discord-user-id ID` | `ghdcbot --config config/config.yaml identity status --discord-user-id ID` |

### Config files

| File | Purpose |
|------|---------|
| `config/config.yaml` | **Your** active config (create from template; gitignored) |
| `config/example.yaml` | Local template |
| `config/docker-example.yaml` | Docker template (`data_dir: /data`) |
| `config/examples/` | Reference configs only |
| `.env` | Tokens (from `.env.example`) |

### Discord slash commands

**Contributors:** `/link`, `/verify-link`, `/profile` (optional Discord member), `/summary` (optional Discord member), `/open-prs`, `/unlink`, `/connect-social`, `/disconnect-social`

**Mentors:** `/sync`

### Next steps

1. Dry-run `run-once` → review `audit.md`
2. Start bot → test `/link` and `/verify-link`
3. Match `merge_role_rules` / `repo_contributor_roles` to your Discord roles
4. Enable active mode when ready
5. Schedule periodic `run-once` (cron/systemd) if needed

---

## Getting Help

- [README.md](README.md) — overview and architecture
- [docs/DOCKER.md](docs/DOCKER.md) — Docker deployment
- [environment_variables.md](environment_variables.md) — env var reference
- [TECHNICAL_DOCUMENTATION.md](TECHNICAL_DOCUMENTATION.md) — architecture deep dive
- [GitHub Issues](https://github.com/AOSSIE-Org/Gitcord-GithubDiscordBot/issues)

---

**Start in dry-run mode, review reports, then go active.**
