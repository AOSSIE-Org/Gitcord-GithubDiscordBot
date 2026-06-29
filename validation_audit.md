# Gitcord Startup Validation Audit (Week 4 Day 3)

**Theme:** Catch configuration mistakes before Gitcord starts.

**Scope:** `src/ghdcbot/config/loader.py`, `src/ghdcbot/config/validation.py`, `src/ghdcbot/config/models.py`, CLI/bot entry points.

---

## What is validated today (after Day 3)

| Check | When | Result on failure |
|-------|------|-------------------|
| Config path exists and is a file | `load_config()` | `Config file not found: …` with copy-template hint |
| YAML parses | `load_config()` | `Invalid YAML syntax detected in …` |
| Config not empty | `load_config()` | `Config file is empty: …` |
| `GITHUB_TOKEN` / `DISCORD_TOKEN` present | `${VAR}` expansion | `… is missing` + setup hint |
| `GITHUB_TOKEN` / `DISCORD_TOKEN` non-empty | `${VAR}` expansion | `… is configured but empty` |
| Pydantic schema (sections, types) | `BotConfig.model_validate()` | `Invalid configuration in …` with field list |
| `role_mappings` non-empty | Pydantic | Included in formatted validation error |
| Active mode readiness | `validate_active_mode()` | `Active mode requires:` when `enable_discord_role_updates: true` with bad guild or permissions |
| CLI / bot `ConfigError` | `cli.py`, `bot.py` | Message printed to stderr, exit code 1 |

---

## What was validated before Day 3

| Check | Behavior |
|-------|----------|
| Missing env var | `Missing required environment variable: GITHUB_TOKEN` |
| Empty env var | **Accepted** — caused late API 401 errors |
| Config missing | `Config file does not exist: path` |
| YAML errors | `Failed to parse YAML: …` (less clear path context) |
| Pydantic errors | Raw `Invalid configuration: …` ValidationError dump |
| Active mode | **Not validated** — `mode: active` alone could run with `enable_discord_role_updates: false` |
| Guild placeholder | **Not validated** — `000000000000000000` accepted in active mode |
| data_dir writable | **Not validated** at startup (fails on first SQLite write) |
| Discord role names exist | **Not validated** at startup (fails at runtime / empty plans) |
| GitHub org reachable | **Not validated** at startup (fails on first API call) |
| Database path | **Not validated** beyond `data_dir` string in config; directory created on first storage access |

---

## What fails silently or late

| Issue | Symptom | When detected |
|-------|---------|---------------|
| Empty tokens | GitHub/Discord 401 in logs | First API call |
| Wrong `guild_id` | Slash commands on wrong server or `CommandNotFound` | Discord interaction |
| Role name mismatch | No role changes in audit / permission denied | `run-once` or `/sync` |
| `mode: active` without `enable_discord_role_updates` | No role changes despite “active” | `run-once` (pre–Day 3) |
| Server Members Intent off | Empty member list, warning in logs | `run-once` |
| Invalid `repos.names` filter | Skipped repos, partial sync | GitHub ingestion |
| Snapshot repo 403 | Warning logs, non-fatal | `run-once` |

---

## Confusing errors (pre–Day 3 → Day 3 fix)

| Before | After (Day 3) |
|--------|----------------|
| `Missing required environment variable: GITHUB_TOKEN` | `GITHUB_TOKEN is missing.` + PAT hint |
| Empty token → silent pass | `GITHUB_TOKEN is configured but empty.` |
| `Config file does not exist` | `Config file not found` + template copy hint |
| `Failed to parse YAML` | `Invalid YAML syntax detected in config/config.yaml` |
| Pydantic traceback-style blob | `Invalid configuration in path:` with bullet list |
| Active mode with no role updates | `Active mode requires: enable_discord_role_updates…` |

---

## Out of scope (Day 3)

- Live GitHub/Discord API preflight (`ghdcbot validate` — later in Week 4)
- Health checks / Docker CI
- Cron / deployment automation
- Validating Discord role names against live guild

---

## Files changed (Day 3)

| File | Purpose |
|------|---------|
| `src/ghdcbot/config/validation.py` | Env messages, active-mode checks |
| `src/ghdcbot/config/loader.py` | Wired validation, clearer errors |
| `src/ghdcbot/cli.py` | Print `ConfigError` to stderr |
| `src/ghdcbot/bot.py` | Print `ConfigError` to stderr |
| `tests/test_config_validation.py` | Startup validation tests |
