# Week 4 PR Descriptions (Ready to Copy)

Three small PRs for easier review by maintainers.  
**Base branch:** `main` (or `week-4` if stacking on branch work).

---

## PR 1 — Improve installation and onboarding documentation

**Title:** `docs: unify installation guide and onboarding documentation (Week 4)`

### Summary

- Rewrites `INSTALLATION.md` with a single path for Docker and local Python installs
- Standardizes on `config/config.yaml` (replacing `my-org-config.yaml`)
- Adds `environment_variables.md` reference
- Syncs `README.md`, `docs/DOCKER.md`, and `.env.example` with the installation flow
- Documents `ghdcbot validate` as recommended preflight (Step 6.3)
- Adds troubleshooting for validate failures and active-mode promotion checklist
- Includes Week 4/5 audit artifacts (`installation_audit.md`, Day 5 test reports)

### Files

```
INSTALLATION.md
README.md
environment_variables.md
docs/DOCKER.md
docs/TESTING_DISCORD.md
.env.example
installation_audit.md
fresh_install_test.md
onboarding_test.md
deployment_readiness_report.md
validate_command_test.md
remaining_onboarding_issues.md
week4_summary.md
```

### Test plan

- [ ] Follow `INSTALLATION.md` Option A (Docker) steps from a fresh clone
- [ ] Follow Option B (local venv) steps
- [ ] Confirm all doc links resolve
- [ ] Confirm `config/config.yaml` convention matches `docker-compose.yml` and README

### Notes

No code changes. Safe to merge first.

---

## PR 2 — Add startup validation and validate command

**Title:** `feat: add config startup validation and ghdcbot validate preflight (Week 4)`

### Summary

- Adds `src/ghdcbot/config/validation.py` with human-readable env and active-mode checks
- Improves `load_config()` error messages (missing/empty tokens, YAML, schema)
- Adds **`ghdcbot validate`** — read-only preflight for GitHub, Discord, org, guild, and roles
- CLI prints `ConfigError` to stderr with exit code 1
- 14 new tests (`test_config_validation.py`, `test_validate.py`)

### Files

```
src/ghdcbot/config/validation.py
src/ghdcbot/config/setup_validate.py
src/ghdcbot/config/loader.py
src/ghdcbot/cli.py
tests/test_config_validation.py
tests/test_validate.py
validation_audit.md
```

### Test plan

- [ ] `pytest tests/test_config_validation.py tests/test_validate.py -v`
- [ ] `ghdcbot --config config/example.yaml validate` with missing `.env` → clear FAIL
- [ ] `ghdcbot validate` with valid tokens → PASS
- [ ] Wrong `guild_id` → FAIL with guild message
- [ ] `enable_discord_role_updates: true` + missing role → FAIL lists role names

### Depends on

PR 1 (docs reference validate command).

---

## PR 3 — Improve onboarding UX and configuration templates

**Title:** `fix: friendly bot login errors and clean example config (Week 4 Day 5)`

### Summary

- **Friendly Discord login failure:** replaces `LoginFailure` traceback with:
  ```
  Invalid DISCORD_TOKEN.

  Please update DISCORD_TOKEN in your .env file
  and restart Gitcord.
  ```
- **Validate output polish:** `GITHUB_TOKEN configured` / `DISCORD_TOKEN configured` (distinct from auth result); clearer GitHub/Discord/guild/role failure hints
- **Example config cleanup:**
  - `config/example.yaml`: `data_dir: "./data"` (matches INSTALLATION)
  - Remove `repo_contributor_roles: castro: "castro"` leftover; document as commented example
- New test: `tests/test_bot_login_failure.py`
- Updates `tests/test_readme_setup.py` for `./data`

### Files

```
src/ghdcbot/config/setup_validate.py   # message polish (if not fully in PR 2, cherry-pick)
src/ghdcbot/bot.py                       # LoginFailure handler only (split identity changes if needed)
config/example.yaml
tests/test_readme_setup.py
tests/test_bot_login_failure.py
tests/test_validate.py                   # updated assertions
INSTALLATION.md                          # validate sample output wording (if not in PR 1)
```

### Test plan

- [ ] `pytest tests/test_bot_login_failure.py tests/test_validate.py tests/test_readme_setup.py -v`
- [ ] Start bot with invalid `DISCORD_TOKEN` → friendly stderr, no traceback
- [ ] `ghdcbot validate` output shows `configured` then `authentication successful`
- [ ] Fresh copy of `config/example.yaml` → reports under `./data/reports/`

### Depends on

PR 2 (validate command exists).

---

## Optional PR 4 — Notification expansion (separate from onboarding)

**Title:** `feat: expand GitHub lifecycle notifications (pr_closed, pr_reopened, …)`

Not part of Week 4 onboarding scope. Merge after PR 1–3 or in parallel if reviewers prefer.

```
src/ghdcbot/adapters/github/rest.py
src/ghdcbot/config/models.py
src/ghdcbot/engine/notifications.py
src/ghdcbot/engine/orchestrator.py
config/examples/aussie.yaml
tests/test_notifications.py
tests/test_github_ingestion_lifecycle_events.py
event_coverage.md
```

---

## Merge order

```text
PR 1 (docs) [target: week-4]
  → PR 2 (validate) [target: week-4]
  → PR 3 (UX/templates) [target: week-4]
  → merge week-4 → main
        ↓
    PR 4 (notifications, optional) [target: main or rebased week-4]
```

After merge, re-run fresh clone simulation from [fresh_install_test.md](fresh_install_test.md).
