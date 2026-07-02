# Week 4 Commit Audit

**Branch:** `week-4`  
**Last commit on branch:** `15e0c30` — *Fix Docker deployment defaults for fresh installs.* (Day 1)  
**Audit date:** 2026-06-20 (end of Week 4)

---

## Summary

| Category | Count |
|----------|-------|
| **Committed to `week-4`** | 1 commit (Day 1 Docker) |
| **Modified, unstaged** | 17 files |
| **Untracked (new)** | 22 files |
| **Tests passing (workspace)** | **262** |

Almost all Week 4 value (Days 2–5) is **local only** until PRs merge.

---

## What is committed (`15e0c30`)

Day 1 Docker consistency only:

| File | Change |
|------|--------|
| `Dockerfile` | `ENTRYPOINT ["ghdcbot"]`, default `--config /app/config/config.yaml bot` |
| `docker-compose.yml` | Config path `/app/config/config.yaml`; `init_data` entrypoint fix |
| `config/examples/aussie.yaml` | Moved from `config/aussie.yaml` |
| `config/config.yaml` | **Removed from git** (gitignored active config) |
| `.gitignore` / `.dockerignore` | Ignore `config/config.yaml` |
| `docs/DOCKER.md` | Partial sync (pre–Day 2 full rewrite) |
| `README.md` | Minor Docker path note |

**Not in commit:** validate command, startup validation, INSTALLATION rewrite, notification expansion, Day 5 UX fixes.

---

## What is still local (modified tracked files)

### PR 1 — Documentation & onboarding

| File | Week 4 relevance |
|------|------------------|
| `INSTALLATION.md` | Day 2 — unified Docker + local, validate step, troubleshooting |
| `README.md` | Day 2 — preflight journey, aligned paths |
| `docs/DOCKER.md` | Day 2/4 — validate before sync, cross-links |
| `docs/TESTING_DISCORD.md` | Minor sync |
| `.env.example` | Day 2 — commented token descriptions |

### PR 2 — Startup validation & validate command

| File | Week 4 relevance |
|------|------------------|
| `src/ghdcbot/config/validation.py` | Day 3 — env messages, active-mode checks |
| `src/ghdcbot/config/setup_validate.py` | Day 4/5 — `ghdcbot validate` |
| `src/ghdcbot/config/loader.py` | Day 3 — empty env rejection, YAML errors |
| `src/ghdcbot/cli.py` | Day 3/4 — `ConfigError` stderr, `validate` subcommand |
| `tests/test_config_validation.py` | Day 3 — 9 tests |
| `tests/test_validate.py` | Day 4/5 — 5 tests |
| `validation_audit.md` | Day 3 audit artifact |

### PR 3 — Onboarding UX & config templates

| File | Week 4 relevance |
|------|------------------|
| `config/example.yaml` | Day 5 — `data_dir: ./data`, remove `castro` example |
| `src/ghdcbot/bot.py` | Day 5 — friendly `LoginFailure`; also identity async fixes (see note) |
| `tests/test_readme_setup.py` | Day 5 — `./data` template alignment |
| `tests/test_bot_login_failure.py` | Day 5 — friendly Discord token message |
| `installation_audit.md` | Day 2 audit |
| Day 5 reports | `fresh_install_test.md`, `onboarding_test.md`, etc. |

### Separate PR recommended — not Week 4 onboarding core

These are modified locally but belong to **notification / ingestion** work (parallel to Week 4):

| File | Purpose |
|------|---------|
| `src/ghdcbot/adapters/github/rest.py` | Lifecycle event payloads |
| `src/ghdcbot/config/models.py` | Notification config flags |
| `src/ghdcbot/engine/notifications.py` | New notification types |
| `src/ghdcbot/engine/orchestrator.py` | Notification routing |
| `config/examples/aussie.yaml` | Notification flags |
| `tests/test_notifications.py` | Expanded notification tests |
| `tests/test_github_ingestion_lifecycle_events.py` | Ingestion tests |
| `tests/test_identity_linking.py` | Minor additions |
| `event_coverage.md` | Event documentation |
| `scripts/benchmark_ingestion.py` | Benchmark script |
| `benchmark_*.md/json` | Benchmark artifacts |

**`src/ghdcbot/bot.py`** also contains identity-verification async/defer UX (not onboarding). Consider splitting into PR 4 or keeping with PR 3 if reviewers prefer one bot PR.

---

## Untracked files (new, never committed)

### Must include in PRs

| File | Suggested PR |
|------|--------------|
| `environment_variables.md` | PR 1 |
| `src/ghdcbot/config/validation.py` | PR 2 |
| `src/ghdcbot/config/setup_validate.py` | PR 2 |
| `tests/test_config_validation.py` | PR 2 |
| `tests/test_validate.py` | PR 2 |
| `tests/test_bot_login_failure.py` | PR 3 |
| `installation_audit.md` | PR 1 |
| `validation_audit.md` | PR 2 |

### Day 5 audit artifacts (include in PR 1 or docs-only follow-up)

| File |
|------|
| `fresh_install_test.md` |
| `onboarding_test.md` |
| `validate_command_test.md` |
| `remaining_onboarding_issues.md` |
| `deployment_readiness_report.md` |
| `week4_summary.md` |
| `week4_commit_audit.md` (this file) |
| `week4_pr_descriptions.md` |

### Optional / do not commit without review

| File | Reason |
|------|--------|
| `benchmark_report.md`, `benchmark_results*.json` | Generated data |
| `scripts/benchmark_ingestion.py` | Separate performance work |
| `event_coverage.md` | Tied to notification PR |

---

## Verification checklist (pre-PR)

| Item | Status |
|------|--------|
| `INSTALLATION.md` | ✅ Rewritten locally |
| `README.md` | ✅ Updated locally |
| `environment_variables.md` | ✅ New, untracked |
| `docs/DOCKER.md` | ✅ Updated locally |
| `validation_audit.md` | ✅ Untracked |
| `installation_audit.md` | ✅ Untracked |
| `ghdcbot validate` | ✅ Works in workspace |
| Startup validation (`loader.py`) | ✅ Works in workspace |
| Tests | ✅ **262 passed** |
| Friendly `LoginFailure` | ✅ Fixed Day 5 |
| Example config cleanup | ✅ Fixed Day 5 |
| Validate output polish | ✅ `configured` wording |

---

## What must be included before claiming Week 4 complete

1. **Merge PR 1 + PR 2 + PR 3** (minimum) to `week-4` or `main`
2. **Decide on notification PR** — merge separately or after onboarding PRs
3. **Re-run Day 5 fresh clone test** against remote after push
4. **Do not commit** `config/config.yaml`, `.env`, or benchmark JSON without review

---

## Git commands reference (for maintainer)

```bash
# See full local diff vs last commit
git diff --stat HEAD

# See untracked
git ls-files --others --exclude-standard

# Suggested first PR (docs only)
git add INSTALLATION.md README.md environment_variables.md docs/DOCKER.md \
  docs/TESTING_DISCORD.md .env.example installation_audit.md \
  fresh_install_test.md onboarding_test.md deployment_readiness_report.md \
  remaining_onboarding_issues.md week4_summary.md validate_command_test.md
```

(Only add `week4_commit_audit.md` and `week4_pr_descriptions.md` if you intentionally want to version planning/meta docs. Split adds per [week4_pr_descriptions.md](week4_pr_descriptions.md).)
