# Fresh Installation Test (Week 4 Day 5)

**Persona:** New organization, zero prior Gitcord knowledge, follows documentation only.

**Method:** Two isolated installs from a clean `git clone` of `HEAD` on branch `week-4` (commit `15e0c30`). No maintainer help. Timestamps: 2026-06-20.

> **Critical context:** Week 4 Days 2–4 improvements (updated `INSTALLATION.md`, startup validation, `ghdcbot validate`) exist in the **working tree but are not committed to `HEAD`**. This test reflects what a real `git clone` from GitHub receives today. See [deployment_readiness_report.md](deployment_readiness_report.md).

---

## Objective 1 – Fresh Docker Installation

### Steps followed (from `docs/DOCKER.md` Quick Start + `INSTALLATION.md` Option A)

```bash
git clone <repo> && cd Gitcord-GithubDiscordBot
cp .env.example .env          # left placeholder token values
cp config/docker-example.yaml config/config.yaml   # left example-org / 000… guild
docker compose build
docker compose up -d
docker compose logs -f bot
```

### What worked

| Step | Result |
|------|--------|
| `git clone` | ✅ Succeeds |
| `cp .env.example .env` | ✅ File created with commented placeholders |
| `cp config/docker-example.yaml config/config.yaml` | ✅ Correct Docker path; `data_dir: /data` pre-set |
| `docker compose build` | ✅ Builds `gitcord:latest` and `init_data` (~21 s with cache) |
| `docker compose up -d` | ✅ `init_data` chowns volume, bot container starts |
| Config mount | ✅ Bot reads `/app/config/config.yaml` |
| Volume persistence | ✅ `gitcord_data` volume created at `/data` |

Docker **infrastructure** from Day 1 is solid: consistent config path, entrypoint, volume, init container.

### What failed or confused

| Issue | Severity | Detail |
|-------|----------|--------|
| Bot crashes on placeholder tokens | **High** | Container restarts in a loop. Logs show a full Python traceback (`discord.errors.LoginFailure`) instead of a one-line operator message. |
| No preflight before `up -d` | **High** | Docs on `HEAD` do not mention `validate`. User discovers bad tokens only after container crash. |
| Must edit config before run | **Medium** | Quick Start says “edit” in a comment but many users will copy-paste through to `up -d` with `example-org` and `000000000000000000`. |
| `docker compose build` not in INSTALLATION Option A on `HEAD` | **Medium** | `INSTALLATION.md` on `HEAD` has no Docker section; user must find `docs/DOCKER.md`. |
| Restart loop noise | **Medium** | `docker compose ps` shows `Up` while logs are full of stack traces — looks “running” but bot is not healthy. |

### Prior knowledge still required

- Fine-grained PAT and Discord bot must exist **before** clone (Steps 1–3 in updated guide; on `HEAD` these exist but Docker path is missing).
- `discord.guild_id` — Developer Mode + Copy Server ID (documented in Step 3.6 of updated guide; absent from `HEAD` INSTALLATION Docker flow).
- Bot role must sit above managed roles (not checked until runtime).
- Org must approve fine-grained PAT if org is resource owner.

### Sample log (placeholder `.env`, unedited config)

```text
discord.errors.LoginFailure: Improper token has been passed.
```

No pointer to `.env` or `DISCORD_TOKEN`.

---

## Objective 2 – Fresh Local Installation

### Steps followed (`INSTALLATION.md` Option B — **workspace version**; `HEAD` differs)

```bash
git clone <repo> && cd Gitcord-GithubDiscordBot
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
cp config/example.yaml config/config.yaml
ghdcbot --config config/config.yaml run-once
```

### What worked

| Step | Result |
|------|--------|
| `python3 -m venv .venv` | ✅ |
| `pip install -e .` | ✅ (~44 s; pulls httpx, discord.py, pydantic, etc.) |
| `ghdcbot` entry point | ✅ Available after install |
| `run-once` with bad tokens | ⚠️ **Exits 0** — completes “successfully” with empty audit |

### Unexpected failures / gaps

| Issue | Severity | Detail |
|-------|----------|--------|
| `run-once` succeeds with invalid tokens | **High** | On `HEAD`, placeholder `your_github_token` yields 401 warnings but exit code 0 and empty `audit.md`. New user thinks setup worked. |
| Empty tokens not rejected on `HEAD` | **High** | `GITHUB_TOKEN=` in `.env` → `Illegal header value b'Bearer '` in logs, not a clear startup error. (Fixed in uncommitted Day 3 loader.) |
| JSON log wall | **Medium** | `run-once` prints structured JSON to stdout; intimidating for non-developers. |
| Report path surprise | **Medium** | `config/example.yaml` uses `data_dir: /tmp/ghdcbot-state`. Reports land in `/tmp/...`, not `./data` as INSTALLATION Option B instructs. |
| `ghdcbot validate` missing on `HEAD` | **High** | Command not in committed CLI. |
| `HEAD` INSTALLATION uses `my-org-config.yaml` | **High** | Differs from README/Docker `config/config.yaml` — name collision across docs on clone. |
| `HEAD` uses `python -m ghdcbot.cli` | **Low** | Works but inconsistent with `ghdcbot` after `pip install -e .`. |

### Missing dependencies

None for local install on Ubuntu — `pip install -e .` resolves all packages. Python **3.11+** required (stated in `pyproject.toml`).

### Confusing instructions (committed vs intended)

| Topic | On `HEAD` clone | In workspace (uncommitted) |
|-------|-----------------|----------------------------|
| Config filename | `config/my-org-config.yaml` | `config/config.yaml` |
| Docker in INSTALLATION | Missing | Full Option A |
| Preflight validate | N/A | `ghdcbot validate` |
| Empty token check | Late / none | Fail at `load_config()` |
| `data_dir` in template | `/tmp/ghdcbot-state` | INSTALLATION says set `./data` |

---

## Installation timing (measured)

| Step | Docker path | Local path |
|------|-------------|------------|
| Clone repo | 0.08 s (local mirror) / ~30–90 s (GitHub, network) | same |
| Copy `.env` + config | 0.004 s | 0.004 s |
| Docker build | **20.8 s** (warm cache) / **2–5 min** (cold, no cache) | — |
| `pip install -e .` | — | **44.2 s** |
| First run | **0.99 s** to container start (then crash loop on bad tokens) | **~2.3 s** `run-once` (misleading success) |
| `ghdcbot validate` (workspace only) | — | **~3.3 s** (live APIs) |

**Estimated onboarding cost (competent operator, valid tokens, uncommitted docs):**

| Phase | Time |
|-------|------|
| GitHub PAT + Discord bot setup | 20–45 min (first time) |
| Clone + config edit | 5–10 min |
| Docker build + validate + first dry-run | 5–15 min |
| **Total to first meaningful audit** | **~35–70 min** |

**With placeholder tokens and `HEAD` only:** additional 15–60 min debugging cryptic logs.

---

## Summary

| Path | Can complete without help? | Blocker |
|------|---------------------------|---------|
| Docker (`HEAD`) | **Partial** | Build/up works; bot unhealthy without real tokens; scary tracebacks |
| Local (`HEAD`) | **Partial** | Installs cleanly; `run-once` false success masks bad config |
| Docker + workspace (uncommitted) | **Yes** (with tokens) | Needs commit/push to be real for others |

**Top recommendation:** Commit and push Week 4 Days 2–4 before claiming onboarding-ready.
