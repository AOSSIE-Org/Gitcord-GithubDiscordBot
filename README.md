<!-- Don't delete it -->
<div name="readme-top"></div>

<!-- Organization Logo -->
<div align="center" style="display: flex; align-items: center; justify-content: center; gap: 16px;">
  <img alt="AOSSIE" src="public/aossie-logo.svg" width="175">
  <img alt="Gitcord" src="public/gitcord-logo.svg" width="175" />
</div>

&nbsp;

<!-- Organization Name -->
<div align="center">

[![Static Badge](https://img.shields.io/badge/aossie.org/TODO-228B22?style=for-the-badge&labelColor=FFC517)](https://TODO.aossie.org/)

<!-- Correct deployed url to be added -->

</div>

<!-- Organization/Project Social Handles -->
<p align="center">
<!-- Telegram -->
<a href="https://t.me/StabilityNexus">
<img src="https://img.shields.io/badge/Telegram-black?style=flat&logo=telegram&logoColor=white&logoSize=auto&color=24A1DE" alt="Telegram Badge"/></a>
&nbsp;&nbsp;
<!-- X (formerly Twitter) -->
<a href="https://x.com/aossie_org">
<img src="https://img.shields.io/twitter/follow/aossie_org" alt="X (formerly Twitter) Badge"/></a>
&nbsp;&nbsp;
<!-- Discord -->
<a href="https://discord.gg/hjUhu33uAn">
<img src="https://img.shields.io/discord/1022871757289422898?style=flat&logo=discord&logoColor=white&logoSize=auto&label=Discord&labelColor=5865F2&color=57F287" alt="Discord Badge"/></a>
&nbsp;&nbsp;
<!-- Medium -->
<a href="https://news.stability.nexus/">
  <img src="https://img.shields.io/badge/Medium-black?style=flat&logo=medium&logoColor=black&logoSize=auto&color=white" alt="Medium Badge"></a>
&nbsp;&nbsp;
<!-- LinkedIn -->
<a href="https://www.linkedin.com/company/aossie/">
  <img src="https://img.shields.io/badge/LinkedIn-black?style=flat&logo=LinkedIn&logoColor=white&logoSize=auto&color=0A66C2" alt="LinkedIn Badge"></a>
&nbsp;&nbsp;
<!-- Youtube -->
<a href="https://www.youtube.com/@StabilityNexus">
  <img src="https://img.shields.io/youtube/channel/subscribers/UCZOG4YhFQdlGaLugr_e5BKw?style=flat&logo=youtube&logoColor=white&logoSize=auto&labelColor=FF0000&color=FF0000" alt="Youtube Badge"></a>
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
- **Discord Bot**: Interactive slash commands for identity linking, issue management, and contribution tracking.

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
2. [Installation Guide](INSTALLATION.md) - Complete setup instructions
3. [Technical Documentation](TECHNICAL_DOCUMENTATION.md) - Architecture and design

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

1. **Dry‑run review**
   - Configure tokens and org
   - Run `run-once` in dry‑run mode
   - Review audit reports

2. **Observer mode**
   - Run read‑only without write permissions
   - Produce audit output for reviewers

---

## 🍀 Getting Started

### Prerequisites
- Python 3.11+
- GitHub and Discord tokens with read permissions

### Installation

#### 1. Clone the Repository

```bash
git clone https://github.com/AOSSIE-Org/Gitcord-GithubDiscordBot.git
cd Gitcord-GithubDiscordBot
```

#### 2. Install Dependencies

```bash
python3 -m venv .venv
. .venv/bin/activate
# Use the venv's pip to avoid shell aliases or PATH issues
./.venv/bin/python -m pip install -e .
```

#### 3. Configure Environment Variables

Create a `.env` file in the root directory (you can copy `.env.example`):

```env
GITHUB_TOKEN=your_github_token
DISCORD_TOKEN=your_discord_token
```

Notes:
- Tokens must have read permissions for the org/repo and Discord server.

#### 4. Configure and Run (Safe Dry‑Run)

```bash
cp config/example.yaml /tmp/ghdcbot-config.yaml
# Use the venv python explicitly to avoid alias/PATH issues
./.venv/bin/python -m ghdcbot.cli --config /tmp/ghdcbot-config.yaml run-once
```

Expected output files (see `data_dir` in `/tmp/ghdcbot-config.yaml`):
```text
<data_dir>/reports/audit.json
<data_dir>/reports/audit.md
```

#### 5. Run Discord Bot

Start the Discord bot with slash commands:

```bash
./.venv/bin/python -m ghdcbot.cli --config config/your-config.yaml bot
```

**Bot Invitation:** Invite the bot with scopes `bot` and `applications.commands`, plus permissions `Manage Roles`, `View Channels`, and `Read Message History`.

#### 6. Testing in Discord

1. **Dry-run (default):** Run `run-once` with your config. The bot reads your guild’s members and roles, scores GitHub activity, and writes audit reports. No roles are changed in Discord; check `<data_dir>/reports/audit.md` to see planned role add/remove actions.
2. **Live role updates:** To have the bot actually add/remove roles in Discord, set in your config:
   - `runtime.mode: "active"`
   - `discord.permissions.write: true`
   Then run `run-once` again. Ensure the bot’s role in the server is **above** any roles it should assign (Server Settings → Roles). See [Testing in Discord](docs/TESTING_DISCORD.md) for details.

---

## 🤖 Discord Bot Commands

### Identity Linking
- `/link` - Link your Discord account to GitHub (creates verification code)
- `/verify-link` - Verify your GitHub link after adding code to bio/gist
- `/verify` - Check your verification status
- `/status` - Show verification state, activity window, and roles
- `/unlink` - Unlink your GitHub identity

### Contribution & Metrics
- `/summary` - Show your contribution metrics (7 and 30 days)
- `/pr-info` - Show PR context preview (repository, reviews, CI status)

### Issue Management
- `/request-issue` - Request to be assigned to a GitHub issue
- `/assign-issue` - Assign issue to Discord user (mentor-only)
- `/issue-requests` - Review pending issue requests (mentor-only)
- `/sync` - Manually sync GitHub events and notifications (mentor-only)

**Note:** Commands marked "mentor-only" require roles configured in `assignments.issue_assignees`. The bot also auto-detects PR URLs in configured channels and shows PR previews.

---

## 📱 App Screenshots

Not applicable (CLI automation engine).

---

## 🙌 Contributing

Thank you for considering contributing to this project! Contributions are highly appreciated and welcomed. To ensure smooth collaboration, please refer to our [Contribution Guidelines](./CONTRIBUTING.md).

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
