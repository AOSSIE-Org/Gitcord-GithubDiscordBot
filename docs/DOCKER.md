# Gitcord Docker Guide

Docker support is designed for **mentor-friendly deployment** and **reproducible runs** without changing Gitcord’s offline-first architecture. The bot and `run-once` both work; SQLite and reports persist across restarts.

For GitHub PAT setup, Discord bot creation, and first-time configuration, start with **[INSTALLATION.md](../INSTALLATION.md)**. This document covers Docker-specific details.

---

## Why Docker?

- **No local Python/setup**: Mentors run `docker compose up` after adding `.env` and config.
- **Same behavior as CLI**: Same code paths; only the runtime is containerized.
- **Persistent state**: Named volume keeps SQLite, reports, and identity links across restarts.
- **Audit-first unchanged**: Dry-run and reports work the same; config and mutation policy are unchanged.

---

## Quick Start

1. **Create `.env`** in the project root (copy from `.env.example`):

   ```bash
   cp .env.example .env
   ```

   Edit `.env` and set your tokens:

   ```env
   GITHUB_TOKEN=your_fine_grained_pat
   DISCORD_TOKEN=your_discord_bot_token
   ```

2. **Create config** (use Docker-specific data dir):

   ```bash
   cp config/docker-example.yaml config/config.yaml
   ```

   Edit `config/config.yaml`: set `github.org`, `discord.guild_id`, and any other options. **Do not change `data_dir`**; it must stay `/data` so the mounted volume is used.

3. **Start the bot**:

   ```bash
   docker compose up -d
   docker compose logs -f bot
   ```

   The Discord bot runs in the background. Slash commands sync within ~30 seconds.

**Validate setup (read-only, before first sync):**

```bash
docker compose run --rm bot --config /app/config/config.yaml validate
```

Checks config, tokens, GitHub/Discord API access, guild, and role names. Exit code `0` = ready; `1` = fix reported issues.

**Run a one-off sync (dry-run or active):**

```bash
docker compose run --rm bot --config /app/config/config.yaml run-once
```

The image `ENTRYPOINT` is `ghdcbot`, so you only pass subcommand arguments after the service name.

---

## Recommended Folder Structure

```
Gitcord-GithubDiscordBot/
├── .env                    # Tokens (never commit; not in image)
├── .env.example
├── config/
│   ├── config.yaml         # Your active config (create from docker-example.yaml; gitignored)
│   ├── docker-example.yaml # Template for Docker
│   ├── example.yaml        # Template for local install
│   └── examples/           # Reference configs (not used by compose)
├── docker-compose.yml
├── docker-compose.instance.example.yml  # Template for a second org instance
├── Dockerfile
├── pyproject.toml
├── src/
└── ...
```

**Inside the container:**

- `/app` = app root (code, config mount at `/app/config`).
- `/data` = persistent volume (SQLite `state.db`, `reports/`, `audit_events.jsonl`). Set `data_dir: "/data"` in config.

---

## Multiple organizations (separate instances)

Gitcord is designed for **any** open-source org. To run more than one org/server on the same host, use **isolated** stacks — never share `.env`, config, or the SQLite volume.

For each org:

1. Create a **new Discord application** (own bot token) and upload [`public/gitcord-discord-icon-large.png`](../public/gitcord-discord-icon-large.png) as App Icon + Bot Icon ([INSTALLATION.md](../INSTALLATION.md#22-set-app-icon-recommended)).
2. Create a **separate GitHub PAT** with access to that org.
3. Copy templates and fill placeholders:

   ```bash
   cp .env.example .env.myorg
   cp config/docker-example.yaml config/myorg.local.yaml
   cp docker-compose.instance.example.yml docker-compose.myorg.local.yml
   ```

4. Edit `config/myorg.local.yaml` (`github.org`, `discord.guild_id`, channels, notifications) and the copied compose file (`env_file`, config path, volume name).
5. Start with a unique project name:

   ```bash
   docker compose -p gitcord-myorg -f docker-compose.myorg.local.yml --env-file .env.myorg up -d --build
   docker compose -p gitcord-myorg -f docker-compose.myorg.local.yml --env-file .env.myorg --profile scheduler up -d
   ```

`*.local.yaml` and `docker-compose.*.local.yml` are gitignored so live org IDs stay off the public repo.

---

## Dockerfile Design (Why Each Part)

| Section | Purpose |
|--------|--------|
| `FROM python:3.11-slim` | Matches `requires-python = ">=3.11"`; slim reduces image size and attack surface. |
| `PYTHONDONTWRITEBYTECODE=1` | Avoids writing `.pyc` in the image; cleaner and slightly faster. |
| `PYTHONUNBUFFERED=1` | Logs show up immediately in `docker compose logs`. |
| Copy `pyproject.toml` + `src/` then `pip install -e .` | Dependency layer is cached; only code/setup changes trigger reinstall. |
| `useradd appuser` / `USER appuser` | Process runs as non-root. |
| `ENTRYPOINT ["ghdcbot"]` | Lets `docker compose run bot --config … run-once` work without repeating the binary name. |
| `CMD ["--config", "/app/config/config.yaml", "bot"]` | Default is Discord bot; override args for `run-once` etc. |

---

## docker-compose.yml Design

| Section | Purpose |
|--------|--------|
| `init_data` service | Runs once as root to `chown` the volume to `appuser` so the bot (non-root) can write; then exits. Bot starts after it completes. |
| `env_file: .env` | Loads `GITHUB_TOKEN` and `DISCORD_TOKEN`; config YAML uses `${GITHUB_TOKEN}` etc. |
| `./config:/app/config:ro` | Host config dir mounted read-only; edit YAML on host without rebuilding. |
| `gitcord_data:/data` | Named volume for SQLite and reports; survives `docker compose down`. |
| `command: ["--config", "/app/config/config.yaml", "bot"]` | Ensures config path is correct and default is bot. |
| `restart: unless-stopped` | Bot comes back after reboot or Docker restart. |

---

## Common Pitfalls and How to Avoid Them

| Pitfall | Cause | Fix |
|--------|--------|-----|
| **"Config file does not exist"** | No `config/config.yaml` or wrong path. | Copy `config/docker-example.yaml` to `config/config.yaml` and keep `data_dir: "/data"`. |
| **"Missing required environment variable: GITHUB_TOKEN"** | `.env` missing or not loaded. | Create `.env` in project root (same dir as `docker-compose.yml`) with `GITHUB_TOKEN` and `DISCORD_TOKEN`. |
| **State lost after restart** | `data_dir` pointed at a non-persistent path. | Use `data_dir: "/data"` and the provided `docker-compose` volume; do not override `/data` with a host path unless you intend to. |
| **Bot doesn’t respond / "application did not respond"** | Same as non-Docker: slow storage or missing intents. | Ensure Server Members Intent is enabled; check logs with `docker compose logs -f`. |
| **Permission errors on `/data`** | Container user cannot write. | Dockerfile already runs as `appuser`; the volume is writable by the container. If you use a host bind mount for `data`, ensure the host dir is writable (e.g. `chown` to the same UID as `appuser`). |
| **Running both bot and run-once** | Need two invocations. | Bot: `docker compose up -d`. Run-once: `docker compose run --rm bot --config /app/config/config.yaml run-once`. |

---

## Audit-First Workflow in Docker

1. Keep `runtime.mode: "dry-run"` in config.
2. Run once:  
   `docker compose run --rm bot --config /app/config/config.yaml run-once`
3. Inspect reports in the volume (e.g. copy out or run a temporary container that mounts the same volume and cats the file):  
   Reports are under `/data/reports/` (e.g. `audit.md`, `audit.json`).
4. When satisfied, set `runtime.mode: "active"`, `runtime.enable_discord_role_updates: true`, and `discord.permissions.write: true` in config, then run `run-once` again or let the bot apply changes on the next sync.

---

## Scheduled sync (`run-once`)

The Discord bot handles slash commands; **background sync** ingests GitHub activity, sends notifications, and (when enabled) updates roles. Run it on a schedule so mentors do not need `/sync` every time.

**Prerequisites:** for a large first ingest, set `discord.notifications.enabled: false` once, run `run-once`, then re-enable notifications in `config/config.yaml` (from `config/docker-example.yaml`).

**Safety:** every `run-once` (CLI, `/sync`, scheduler) runs a **preflight** that aborts if `assignments.issue_assignees` or `assignments.review_roles` are set while `github.permissions.write: true` — this blocks the bulk auto-assign incident. Check manually:

```bash
./scripts/preflight-sync.sh
```

### Option A — Docker scheduler (recommended)

Runs `run-once` every **6 hours** in a sidecar container (same SQLite volume as the bot):

```bash
cp config/aussie.yaml config/config.yaml   # if not already done
docker compose up -d                        # bot only
docker compose --profile scheduler up -d    # bot + sync-scheduler
docker compose logs -f sync-scheduler
```

Change interval in `.env` (seconds):

```env
GITCORD_SYNC_INTERVAL_SECONDS=43200   # 12 hours
```

Stop scheduler only: `docker compose --profile scheduler stop sync-scheduler`

### Option B — Host cron

For VPS hosts that already use cron:

```bash
chmod +x scripts/scheduled-run-once.sh
# Edit path in deploy/cron/gitcord-sync.crontab, then:
crontab -e
```

Example line (every 6 hours):

```cron
0 */6 * * * cd /path/to/Gitcord-GithubDiscordBot && ./scripts/scheduled-run-once.sh >> /var/log/gitcord-sync.log 2>&1
```

Overlapping runs are skipped via a lock file on the `/data` volume.

### Manual one-off

```bash
docker compose run --rm bot --config /app/config/config.yaml run-once
```

---

## Production and Maintainability Notes

- **Reproducibility**: Same image and config produce the same behavior; use tagged images if you need to pin versions.
- **Secrets**: Never bake tokens into the image; use `.env` or a secrets manager and `env_file` / env.
- **Updates**: Rebuild with `docker compose build --no-cache` after dependency or code changes; config and data are unchanged.
- **Logs**: Use `docker compose logs -f bot` for live logs; log level is controlled by config `runtime.log_level`.
- **Scheduled sync**: Use `sync-scheduler` profile or host cron; with 15 AOSSIE repos expect **10–30+ minutes** per run.
