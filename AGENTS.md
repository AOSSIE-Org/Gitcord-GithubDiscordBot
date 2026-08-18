# Gitcord — AI Agent Guidelines

Instructions for AI coding agents (and humans) working on **Gitcord** (`AOSSIE-Org/Gitcord-GithubDiscordBot`).

---

## What Gitcord is

Offline-first Discord ↔ GitHub automation for open-source orgs:

- Verified Discord↔GitHub identity (`/link`, `/verify-link`)
- Contributor `/profile`, `/summary`, `/pr`, `/open-prs`
- Scheduler sync: ingest GitHub events → Discord notifications (verified users)
- Optional remote org config from `.github/gitcord.yaml`
- Safety: dry-run / observer modes, audit reports, no bulk mutations by default

Package name: `ghdcbot` (Python 3.11+).

---

## Stack

| Layer | Tech |
| ----- | ---- |
| Language | Python 3.11+ |
| Config | Pydantic + PyYAML (`src/ghdcbot/config/`) |
| Storage | SQLite (`SqliteStorage`) |
| Discord | `discord.py` slash commands + REST adapter |
| GitHub | REST + optional GitHub App auth |
| Tests | `pytest` |
| Deploy | Docker Compose (per-org isolated stacks) |

### Common commands

```bash
python -m pip install -e ".[dev]"
pytest
ruff check src tests
ghdcbot --config config/config.yaml validate
ghdcbot --config config/config.yaml run-once
docker compose up -d bot
```

---

## Repository map

```text
src/ghdcbot/
  adapters/     # github, discord, storage
  config/       # models, loader, remote gitcord.yaml
  engine/       # orchestrator, notifications, pr_list, snapshots
  bot.py        # Discord slash commands
  cli.py        # validate / run-once / bot entry
brand/          # logo, favicon, Brand.md
config/examples/# remote/bootstrap YAML examples
docs/           # Docker, identity, testing
tests/          # pytest suite
```

---

## Non-negotiables

1. **No secrets in git** — tokens only via `.env` / env expansion; never commit live org guild/channel maps if gitignored.
2. **Audit-first** — prefer dry-run; do not enable bulk `assignments.issue_assignees` / `review_roles` with write without mentor approval.
3. **Verified-only notifications** — DMs go to linked Discord users only.
4. **Keep changes scoped** — match existing patterns; update tests + docs when behavior changes.
5. **Remote config** — org settings may live in `org/.github/gitcord.yaml`; local bootstrap stays thin (`remote_config` + tokens + `data_dir`).
6. **Multi-org** — AOSSIE and Stability Nexus use **separate** Compose projects, env files, and volumes.
7. **Local handover** — use `./scripts/gitcord-handover pack|restore` and [`HANDOVER-EASY.txt`](HANDOVER-EASY.txt). AI paste prompt: [`docs/HANDOVER_AI_PROMPT.md`](docs/HANDOVER_AI_PROMPT.md). Never treat “git clone alone” as a full handover.

---

## Local handover (for AI agents)

When the user is **taking over** Gitcord or **moving bots to another PC**:

1. Prefer `./scripts/gitcord-handover` (`pack` / `restore FILE.tar.gz` / `check` / `stop-old`).
2. Follow [`HANDOVER-EASY.txt`](HANDOVER-EASY.txt) and [`docs/HANDOVER_AI_PROMPT.md`](docs/HANDOVER_AI_PROMPT.md).
3. Ask for the absolute path to `gitcord-handover-*.tar.gz` if missing.
4. Do not commit secrets; do not mix AOSSIE/SN; warn if both PCs might run the same Discord tokens.
5. After restore: `check` → user tests `/profile` + `/who-is` → `stop-old` on the previous PC.

---

## Testing expectations

- Add/adjust tests under `tests/` for engine and adapter behavior.
- CI: `.github/workflows/tests.yml` runs `pytest` on PRs.
- Do not commit credentials or production SQLite DBs.

---

## Branding

See [`brand/Brand.md`](brand/Brand.md). Prefer assets from `brand/` / `public/` rather than inventing new logos.
