# Gitcord Docker Guide

Docker support is designed for **mentor-friendly deployment** and **reproducible runs** without changing Gitcord’s offline-first architecture. The bot and `run-once` both work; SQLite and reports persist across restarts.

---

## Why Docker?

- **No local Python/setup**: Mentors run `docker compose up` after adding `.env` and config.
- **Same behavior as CLI**: Same code paths; only the runtime is containerized.
- **Persistent state**: Named volume keeps SQLite, reports, and identity links across restarts.
- **Audit-first unchanged**: Dry-run and reports work the same; config and mutation policy are unchanged.

---

## Quick Start (3 steps)

1. **Create `.env`** in the project root (copy from `.env.example`):

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
   ```

   The Discord bot runs in the background. Slash commands sync within ~30 seconds.

**Run a one-off sync (dry-run or active):**

```bash
docker compose run --rm bot --config /app/config/config.yaml run-once
```
(Do not pass `ghdcbot` — the image default command is `ghdcbot`.)

---

## Recommended Folder Structure

```
Gitcord-GithubDiscordBot/
├── .env                    # Tokens (never commit; not in image)
├── .env.example
├── config/
│   ├── config.yaml         # Your active config (data_dir: /data for Docker)
│   ├── docker-example.yaml # Template for Docker
│   └── example.yaml        # Template for local install
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml
├── src/
└── ...
```

**Inside the container:**

- `/app` = app root (code, config mount at `/app/config`).
- `/data` = persistent volume (SQLite `state.db`, `reports/`, `audit_events.jsonl`). Set `data_dir: "/data"` in config.

---

## Dockerfile Design (Why Each Part)

| Section | Purpose |
|--------|--------|
| `FROM python:3.11-slim` | Matches `requires-python = ">=3.11"`; slim reduces image size and attack surface. |
| `PYTHONDONTWRITEBYTECODE=1` | Avoids writing `.pyc` in the image; cleaner and slightly faster. |
| `PYTHONUNBUFFERED=1` | Logs show up immediately in `docker compose logs`. |
| Copy `pyproject.toml` + `src/` then `pip install -e .` | Dependency layer is cached; only code/setup changes trigger reinstall. |
| `useradd appuser` / `USER appuser` | Process runs as non-root; no gosu/entrypoint at runtime. |
| `CMD ["ghdcbot", "--config", "/app/config/config.yaml", "bot"]` | Default is Discord bot; override with `docker compose run ... run-once` etc. |

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
| **Running both bot and run-once** | Need two invocations. | Bot: `docker compose up -d`. Run-once: `docker compose run --rm bot ghdcbot --config /app/config/config.yaml run-once`. |

---

## Audit-First Workflow in Docker

1. Keep `runtime.mode: "dry-run"` in config.
2. Run once:  
   `docker compose run --rm bot --config /app/config/config.yaml run-once`
3. Inspect reports in the volume (e.g. copy out or run a temporary container that mounts the same volume and cats the file):  
   Reports are under `/data/reports/` (e.g. `audit.md`, `audit.json`).
4. When satisfied, set `runtime.mode: "active"` and `discord.permissions.write: true` in config, then run `run-once` again or let the bot apply changes on the next sync.

---

## Production and Maintainability Notes

- **Reproducibility**: Same image and config produce the same behavior; use tagged images if you need to pin versions.
- **Secrets**: Never bake tokens into the image; use `.env` or a secrets manager and `env_file` / env.
- **Updates**: Rebuild with `docker compose build --no-cache` after dependency or code changes; config and data are unchanged.
- **Logs**: Use `docker compose logs -f bot` for live logs; log level is controlled by config `runtime.log_level`.
