<!-- Don't delete it -->
<div name="readme-top"></div>

<!-- Organization Logo -->
<div align="center" style="display: flex; align-items: center; justify-content: center; gap: 16px;">
  <img alt="AOSSIE" src="public/aossie-logo.svg" width="175">
  <img alt="Gitcord" src="public/gitcord.svg" width="175" />  <!-- Gitcord logo (Discord + GitHub fusion) -->
</div>

&nbsp;

<!-- Organization Name -->
<div align="center">

[![Static Badge](https://img.shields.io/badge/aossie.org/Gitcord-228B22?style=for-the-badge&labelColor=FFC517)](https://github.com/AOSSIE-Org/Gitcord-GithubDiscordBot)
<br/>
[![Best Practices](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/AOSSIE-Org/Gitcord-GithubDiscordBot/main/checklist-status.json)](./BestPracticesChecklist.md)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/AOSSIE-Org/Gitcord-GithubDiscordBot/badge)](https://scorecard.dev/viewer/?uri=github.com/AOSSIE-Org/Gitcord-GithubDiscordBot)

</div>

<!-- Organization/Project Social Handles -->
<p align="center">
<!-- X (formerly Twitter) -->
<a href="https://x.com/aossie_org">
<img src="https://img.shields.io/twitter/follow/aossie_org" alt="X (formerly Twitter) Badge"/></a>
&nbsp;&nbsp;
<!-- Discord -->
<a href="https://discord.gg/hjUhu33uAn">
<img src="https://img.shields.io/discord/1022871757289422898?style=flat&logo=discord&logoColor=white&logoSize=auto&label=Discord&labelColor=5865F2&color=57F287" alt="Discord Badge"/></a>
&nbsp;&nbsp;
<!-- LinkedIn -->
<a href="https://www.linkedin.com/company/aossie/">
  <img src="https://img.shields.io/badge/LinkedIn-black?style=flat&logo=LinkedIn&logoColor=white&logoSize=auto&color=0A66C2" alt="LinkedIn Badge"></a>
&nbsp;&nbsp;
<!-- Website -->
<a href="https://aossie.org/">
  <img src="https://img.shields.io/badge/Website-black?style=flat&logo=globe&logoColor=white&logoSize=auto&color=228B22" alt="AOSSIE Website Badge"></a>
</p>

---

<div align="center">
<h1>Gitcord (Discord–GitHub Automation Engine)</h1>
</div>

Gitcord is a local, offline‑first automation engine that reads GitHub activity and Discord state, then plans role changes and GitHub assignments in a deterministic, reviewable way. It is designed for safety: dry‑run and observer modes produce audit reports without mutating anything.

---

## 🚀 Features

- **Offline‑first execution**: run locally on demand, no daemon required.
- **Audit‑first workflow**: JSON + Markdown reports before any writes.
- **Deterministic planning**: identical inputs produce identical plans.
- **Permission‑aware IO**: readers degrade safely on missing permissions.
- **Discord Bot**: Interactive slash commands for identity linking, contributor profiles, metrics, and notifications.

---

## 💻 Tech Stack

### Backend

- Python 3.11+
- SQLite (local state)
- Pydantic + PyYAML

---

## ✅ Project Checklist

- [x] **Audit-first workflow**: reports generated for review.
- [x] **Dry-run default**: writes gated by mode and permissions.
- [x] **Permission-limited operation**: safe under missing permissions.

---

## 🔗 Repository Links

1. [Main Repository](https://github.com/AOSSIE-Org/Gitcord-GithubDiscordBot)
2. [Installation Guide](INSTALLATION.md) - Complete setup instructions (Docker and local)
3. [Environment Variables](environment_variables.md) - `.env` reference
4. [Brand kit](brand/Brand.md) - Logo, colors, typography, icons
5. [Maintainers](MAINTAINERS.md) · [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md) · [Best Practices](BestPracticesChecklist.md)
6. [AOSSIE Discord — Gitcord thread](https://discord.com/channels/1022871757289422898/1465995983791063140)
7. [Technical Documentation](TECHNICAL_DOCUMENTATION.md) - Architecture and design
8. [Docker Guide](docs/DOCKER.md) - Docker setup and mentor-friendly deployment
9. [Handover (easy)](HANDOVER-EASY.txt) - One USB file: `gitcord-handover pack` → `restore`
10. [Handover AI prompt](docs/HANDOVER_AI_PROMPT.md) - Paste into Cursor on the new PC

---

## 🏗️ Architecture Diagram

```text
Read -> Plan -> Report -> Apply
```

Core boundaries:

- Readers are read‑only (GitHub/Discord ingestion).
- Planners are pure, deterministic logic.
- Writers are thin executors gated by `MutationPolicy`.

---

## 🔄 User Flow

```text
Load config -> Ingest -> Score -> Plan -> Audit -> (Optional) Apply
```

### Key User Journeys

1. **Preflight check**
   - Configure tokens and org
   - Run `ghdcbot --config config/config.yaml validate`
   - Fix any ✗ failures before continuing

2. **Dry‑run review**
   - Run `run-once` in dry‑run mode
   - Review audit reports

3. **Observer mode**
   - Run read‑only without write permissions
   - Produce audit output for reviewers

---

## 🍀 Getting Started

> **📖 New to Gitcord?** For complete step-by-step setup instructions including Discord bot creation and GitHub token setup, see **[INSTALLATION.md](INSTALLATION.md)**.

### Prerequisites

Before installing Gitcord, you need:

- ✅ **GitHub Organization** access
- ✅ **Discord Server** with admin permissions
- ✅ **GitHub Personal Access Token** (fine-grained PAT) - [How to create](INSTALLATION.md#step-1-create-github-token-pat)
- ✅ **Discord Bot Token** - [How to create](INSTALLATION.md#step-2-create-discord-bot)

### Quick Start with Docker (recommended for mentors)

If you have Docker installed, you can skip Python setup and run Gitcord in one go:

```bash
git clone https://github.com/AOSSIE-Org/Gitcord-GithubDiscordBot.git
cd Gitcord-GithubDiscordBot
cp .env.example .env          # Add your GITHUB_TOKEN and DISCORD_TOKEN
cp config/docker-example.yaml config/config.yaml   # Set github.org and discord.guild_id
docker compose up -d
docker compose logs -f bot
```

The Discord bot stays running; SQLite data and reports persist in a Docker volume. To run a one-off sync (e.g. dry-run):  
`docker compose run --rm bot --config /app/config/config.yaml run-once`

See **[docs/DOCKER.md](docs/DOCKER.md)** for details, pitfalls, and audit-first workflow.

### Quick Deployment (always-on)

For development and user testing, run Gitcord locally with Docker as described above. When the bot must stay online while your computer is off, deploy the same Compose setup to an always-on Linux host.

#### Linux VPS (recommended)

This works with Oracle Cloud Free Tier, a small VM from another provider, or your own always-on server. Gitcord does not accept inbound web traffic, so the VM only needs outbound HTTPS access plus SSH for administration.

1. Create an Ubuntu/Debian VM, install Git, [Docker Engine and the Compose plugin](https://docs.docker.com/engine/install/), then clone this repository.
2. Create `.env` and `config/config.yaml` exactly as in the Docker quick start. Keep `runtime.data_dir: "/data"` so SQLite survives restarts.
3. Validate the configuration, then start the bot and scheduled sync:

   ```bash
   docker compose run --rm bot --config /app/config/config.yaml validate
   docker compose --profile scheduler up -d --build
   docker compose ps
   docker compose logs -f bot sync-scheduler
   ```

Both services use `restart: unless-stopped`, and the named `gitcord_data` volume preserves identities, notification history, and sync cursors across container or VM restarts. The scheduler runs every six hours by default; set `GITCORD_SYNC_INTERVAL_SECONDS` in `.env` to change it.

For updates:

```bash
git pull
docker compose --profile scheduler up -d --build
```

Protect `.env`, restrict SSH access, and back up the `gitcord_data` volume. Never commit production tokens or `config/config.yaml`.

#### Fly.io and similar container platforms

Fly.io is possible, but the repository is not currently a one-command Fly deployment: its Compose file uses host networking and a local named volume. A Fly deployment must remove `network_mode: host`, provide secrets through `fly secrets`, mount a persistent volume at `/data`, and run the bot and scheduler without allowing overlapping syncs. Use a Linux VPS for the supported copy-and-run path until platform-specific deployment files are added.

### Quick Setup Overview (local Python install)

**1. Create GitHub Token** ([Detailed Guide](INSTALLATION.md#step-1-create-github-token-pat))

- Go to GitHub → Settings → Developer Settings → Fine-grained tokens
- Permissions: Contents (Read & Write), Issues (Read & Write), Pull requests (Read & Write)

**2. Create Discord Bot** ([Detailed Guide](INSTALLATION.md#step-2-create-discord-bot))

- Go to [Discord Developer Portal](https://discord.com/developers/applications)
- Create Application → set **App Icon** + **Bot Icon** from [`public/gitcord-discord-icon-large.png`](public/gitcord-discord-icon-large.png) → Add Bot → Enable **Server Members Intent**

**3. Invite Bot to Server** ([Detailed Guide](INSTALLATION.md#step-3-invite-bot-to-discord-server))

- OAuth2 → URL Generator
- Scopes: `bot`, `applications.commands`
- Permissions: `Manage Roles`, `View Channels`, `Send Messages`, `Embed Links`, `Read Message History`
- ⚠️ **Never** use Administrator permission

**4. Install Gitcord**

#### 1. Clone the Repository

```bash
git clone https://github.com/AOSSIE-Org/Gitcord-GithubDiscordBot.git
cd Gitcord-GithubDiscordBot
```

#### 2. Install Dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Full walkthrough: **[INSTALLATION.md](INSTALLATION.md)**.

**5. Configure Environment Variables**

Create a `.env` file (copy from `.env.example`):

```env
GITHUB_TOKEN=your_github_token_here
DISCORD_TOKEN=your_discord_bot_token_here
```

**6. Create Configuration File**

Copy and edit:

```bash
cp config/example.yaml config/config.yaml
```

Edit `config/config.yaml`: set `github.org`, `discord.guild_id`, and `runtime.data_dir: "./data"`. Match `role_mappings` to role names in your Discord server.

**7. Test Run (Dry-Run Mode)**

```bash
ghdcbot --config config/config.yaml run-once
```

This generates audit reports without making changes. Review `data/reports/audit.md`.

**8. Run Discord Bot**

```bash
ghdcbot --config config/config.yaml bot
```

Wait 30 seconds for commands to sync.

**9. Enable Active Mode** (After Testing)

1. **Dry-run (default):** Run `run-once` with your config. The bot reads your guild’s members and roles, ingests GitHub activity, and writes audit reports. No roles are changed in Discord; check `<data_dir>/reports/audit.md` to see planned role add/remove actions.
2. **Live role updates:** To have the bot actually add/remove roles in Discord, set in `config/config.yaml`:
   - `runtime.mode: "active"`
   - `runtime.enable_discord_role_updates: true`
   - `discord.permissions.write: true`
   Then run `run-once` again. Ensure the bot’s role in the server is **above** any roles it should assign (Server Settings → Roles). See [Testing in Discord](docs/TESTING_DISCORD.md) and [INSTALLATION.md](INSTALLATION.md) for details.

---

## 🤖 Discord Bot Commands

Contributor-facing cheat sheet: [`QUICK_START_GUIDE.txt`](QUICK_START_GUIDE.txt).

### Identity Linking

- `/link` - Link your Discord account to GitHub (creates verification code)
- `/verify-link` - Verify your GitHub link after adding code to bio/gist
- `/help-link` - Help a tagged Discord member start the linking flow (anyone; DM preferred; channel fallback is visible but target-only)
- `/profile` - Show contributor profile (GitHub, verification, socials, roles); optional Discord member
- `/who-is` - Look up a GitHub username, find the verified Discord account, and see whether verification is current or stale
- `/unlink` - Unlink your GitHub identity
- `/connect-social` - Connect X or LinkedIn (enter your username or profile URL)
- `/disconnect-social` - Disconnect X or LinkedIn from Gitcord

### Contribution & Metrics

- `/summary` - Show contribution metrics (7 and 30 days); optional Discord member for another verified contributor
- `/open-prs` - List a contributor's currently open PRs in configured repos
- `/pr` - List a contributor's recent PRs grouped by closed / merged / open (`count` N, optional `skip` M)
- `/pr-status` - Show PR health (CI, CodeRabbit review threads, merge conflicts, approval state) for a single PR or multi-PR org dashboard (`show_all:True`, optional `skip` M). Accessible to all server members by default (with command cooldown); optionally gateable via `discord.command_permissions.pr-status`. Dashboard requests are capped at 25 PRs per page. Each PR uses a small number of REST calls (pull request, reviews, check runs) plus paginated GraphQL queries for review threads, so a full dashboard page costs roughly 100 or more API requests. When querying a single PR, omitting `repo:` probes each configured repository with one additional REST call to auto-detect the repository (capped at 25 candidate repositories, resulting in a maximum of 25 probe calls).

### Sync (mentor-only)

- `/sync` - Manually sync GitHub events and send notifications

**Note:** `/sync` requires a mentor role configured in `discord.command_permissions`. The bot can also auto-detect PR URLs in configured channels and post passive previews.

---

## 📱 App Screenshots

Not applicable (CLI automation engine).

---

## 🙌 Contributing

Thank you for considering contributing to this project! Contributions are highly appreciated and welcomed. To ensure smooth collaboration, please refer to our [Contribution Guidelines](CONTRIBUTING.md).

---

## ✨ Maintainers

See [contributors](https://github.com/AOSSIE-Org/Gitcord-GithubDiscordBot/graphs/contributors).

---

## 📍 License

This project is licensed under the GNU General Public License v3.0.
See the [LICENSE](LICENSE) file for details.

---

## 💪 Thanks To All Contributors

Thanks a lot for spending your time helping Gitcord grow. Keep rocking 🥂

[![Contributors](https://contrib.rocks/image?repo=AOSSIE-Org/Gitcord-GithubDiscordBot)](https://github.com/AOSSIE-Org/Gitcord-GithubDiscordBot/graphs/contributors)

© 2026 AOSSIE
