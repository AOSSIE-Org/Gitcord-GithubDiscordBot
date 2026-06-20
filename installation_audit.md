# Gitcord Installation Audit (Week 4 Day 2)

**Question:** Can a completely new contributor install Gitcord without asking for help?

**Method:** Walk through `INSTALLATION.md` step by step as a new user; cross-check `README.md`, `docs/DOCKER.md`, `config/*.yaml`, and `.env.example`.

**Status:** Issues below reflect the **pre–Day 2** guide. Fixes are tracked in the Day 2 deliverables (`INSTALLATION.md` update, doc sync, `environment_variables.md`).

---

## Step-by-step audit

| Step | Expected | Actual (pre–Day 2) | Issues |
|------|----------|-------------------|--------|
| **Prerequisites** | Clear list of what to install before starting | Python 3.11+ only | No Docker prerequisite; no Git; no Windows/WSL note |
| **Python version** | Match `pyproject.toml` (`>=3.11`) | Stated correctly | OK |
| **Docker prerequisites** | Docker Engine + Compose documented | Not in INSTALLATION.md | Docker only in README; split brain for Docker-first users |
| **GitHub PAT** | Fine-grained token steps | Detailed, accurate | Write perms marked optional for testing but snapshots example enables writes; org resource owner selection not mentioned |
| **Discord bot setup** | Portal steps, intents, token | Detailed, accurate | OK |
| **Discord invite** | Scopes, permissions, role hierarchy | Detailed, accurate | OK |
| **Clone repository** | `git clone` + `cd` | Documented | Hard-coded AOSSIE-Org URL (fine for upstream; forks need adjustment — not explained) |
| **Local install** | Reproducible dependency install | `pip install -e .` | No `requirements.txt` (project uses `pyproject.toml`) — some docs implied otherwise |
| **Environment file** | `.env` from `.env.example` | Documented | `.env.example` had no comments; empty tokens not rejected at load |
| **Config file name** | One obvious active config path | `config/my-org-config.yaml` in INSTALLATION | README/Docker use `config/config.yaml` — **three different names** |
| **Config YAML content** | Match shipped `config/example.yaml` | Inline example diverged | Stale scoring weights, enabled `snapshots` by default, missing `command_permissions`, `enable_discord_role_updates` |
| **Local `data_dir`** | Persistent, obvious path | `./data/my-org` in guide | `config/example.yaml` uses `/tmp/ghdcbot-state` — confusing for beginners |
| **Docker setup** | Same flow as README/DOCKER.md | **Missing entirely** from INSTALLATION | Docker users had to discover README |
| **Run `run-once`** | Dry-run, reports generated | Documented | Command used `python -m ghdcbot.cli` vs `ghdcbot` entry point inconsistently |
| **Run bot** | Bot stays up, commands sync | Documented | Same command inconsistency; no Docker equivalent in INSTALLATION |
| **Enable active mode** | Safe promotion from dry-run | Only `runtime.mode: active` | Missing `enable_discord_role_updates` and `discord.permissions.write` (documented elsewhere in `docs/TESTING_DISCORD.md`) |
| **Troubleshooting** | Actionable operator steps | Broad coverage | `storage.init_schema()` is internal — not an operator action; no Docker log commands |
| **Next steps** | Cron / scheduling hint | Mentioned with no example | No crontab or systemd sample |
| **Windows** | Guidance for Windows users | One-line venv activate | No WSL recommendation; native Windows paths fragile |

---

## Documentation consistency audit (pre–Day 2)

| Topic | README.md | INSTALLATION.md | docs/DOCKER.md |
|-------|-----------|-----------------|----------------|
| Active config path | `config/config.yaml` (Docker) | `config/my-org-config.yaml` (local) | `config/config.yaml` |
| Local config template | `config/example.yaml` | Inline YAML (stale) | N/A |
| Docker config template | `config/docker-example.yaml` | Not mentioned | `config/docker-example.yaml` |
| Install command | `pip install -e .` | `pip install -e .` | N/A (image build) |
| Run bot | `python -m ghdcbot.cli … bot` | `python -m ghdcbot.cli … bot` | `docker compose up -d` |
| Run once | `docker compose run … run-once` | `python -m ghdcbot.cli … run-once` | `docker compose run … run-once` |
| Env vars | `GITHUB_TOKEN`, `DISCORD_TOKEN` | Same | Same |
| Active mode flags | `mode` + `discord.permissions.write` | `mode` only | `mode` + `discord.permissions.write` |

**Conclusion:** Docker path was aligned on Day 1; INSTALLATION.md lagged behind and used a different config filename and stale YAML.

---

## Fresh install simulation (docs-only)

**Persona:** New contributor, Ubuntu 22.04, has Docker, fine-grained PAT, Discord admin access. Reads **only** `INSTALLATION.md` (pre–Day 2).

| Stage | Friction |
|-------|----------|
| Prerequisites | Assumes Python; if they prefer Docker, no path forward in INSTALLATION |
| Step 4 config | Copies `my-org-config.yaml` while Docker docs say `config.yaml` — confusion if they switch methods later |
| Config editing | Inline YAML doesn't match repo template; role names (`Mentor`, `Contributor`) may not exist in their server — no warning |
| `run-once` | Works if tokens valid; report path depends on `data_dir` they chose |
| Active mode | User sets `active` only; roles may not change — **hidden knowledge** from TESTING_DISCORD.md |
| AOSSIE familiarity | Clone URL, support Discord link, and `config/examples/aussie.yaml` imply AOSSIE context; not harmful but not explained for other orgs |
| Hidden knowledge | `command_permissions` vs `assignments.issue_assignees`; Server Members Intent in portal vs code; mentor role must exist in Discord |

**Post–Day 2 expectation:** INSTALLATION.md presents Docker and local paths with the same `config/config.yaml` convention, links to `environment_variables.md`, and documents full active-mode flags.

---

## Day 2 remediation checklist

- [x] `installation_audit.md` (this file)
- [x] `INSTALLATION.md` — Docker section, unified `config/config.yaml`, WSL note, active-mode flags
- [x] `README.md` — aligned local paths with INSTALLATION
- [x] `docs/DOCKER.md` — cross-link to INSTALLATION; flow already consistent
- [x] `environment_variables.md`
- [x] `.env.example` — commented descriptions

---

## Residual gaps (out of scope for Day 2)

- No `requirements.txt` (by design — use `pip install -e .`)
- No `ghdcbot validate` preflight command (planned later in Week 4)
- No cron/systemd examples
- `config/example.yaml` still uses `/tmp/ghdcbot-state` in the tracked template (INSTALLATION tells users to set `./data` in their copy)
