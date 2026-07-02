# Deployment Readiness Report (Week 4 Day 5)

**Question:** Can another organization install and run Gitcord successfully without maintainer help?

**Assessment date:** 2026-06-20  
**Branch tested:** `week-4` @ `15e0c30` (clone simulation) + workspace (uncommitted Week 4 work)

---

## Executive summary

| Audience | Verdict |
|----------|---------|
| Clone from **`HEAD` today** | **Not ready** — documentation split, no `validate`, false-success `run-once`, Docker crash loops on bad tokens |
| **Workspace** (local Week 4 work) | **Nearly ready** — unified docs, startup validation, `validate` command; needs commit/push and minor polish |
| **Docker infrastructure** (Day 1) | **Ready** — build, volume, config path, entrypoint all correct |

**Bottom line:** Gitcord is **deployment-ready for mentors who already know the project**. It is **not yet release-ready for cold-start organizations** until Week 4 changes land on the default branch.

---

## Scoring (1–10)

| Area | Day 1 (audit) | Day 5 (`HEAD` clone) | Day 5 (workspace) | Notes |
|------|---------------|----------------------|-------------------|-------|
| **Docker** | 7 | **8** | **8** | Day 1 fixed paths/volumes; bot still crashes noisily on bad token |
| **Documentation** | 4 | **5** | **8** | `HEAD` still has `my-org-config.yaml`; workspace INSTALLATION is strong |
| **Validation** | 2 | **3** | **8** | `validate` + Day 3 loader exist only in workspace |
| **Fresh install** | 5 | **5** | **7** | Mechanical install works; feedback loop weak on `HEAD` |
| **Organization onboarding** | 5 | **5** | **7** | Discord/GitHub steps documented; template config still noisy |

**Weighted average**

| Snapshot | Score |
|----------|-------|
| Day 1 (from [installation_audit.md](installation_audit.md)) | **4.6 / 10** |
| Day 5 — `HEAD` clone | **5.2 / 10** |
| Day 5 — workspace (if shipped) | **7.6 / 10** |

---

## Area-by-area assessment

### Docker (8/10)

**Strengths**

- `config/config.yaml` + `/data` volume consistent (Day 1)
- `init_data` permission fix works
- `docker compose build` ~21 s warm / 2–5 min cold
- `run-once` and `validate` invocations documented in workspace `docs/DOCKER.md`

**Gaps**

- Bot restart loop + traceback on invalid `DISCORD_TOKEN`
- No healthcheck in compose file
- `validate` not in image until code committed

### Documentation (8/10 workspace / 5/10 `HEAD`)

**Strengths (workspace)**

- Single `INSTALLATION.md` for Docker + local
- `environment_variables.md`, WSL section, active-mode checklist
- Validate step 6.3 with sample output
- Troubleshooting table for validate failures

**Gaps**

- `HEAD` not updated — primary blocker
- `config/example.yaml` still diverges from INSTALLATION (`data_dir`, example roles)
- No cron/systemd scheduling example

### Validation (8/10 workspace / 3/10 `HEAD`)

**Strengths (workspace)**

- `load_config()` rejects missing/empty tokens with actionable messages
- `ghdcbot validate` checks GitHub, Discord, org, repos, guild, roles
- 14 automated tests
- ~3 s live API preflight

**Gaps**

- Not shipped on `HEAD`
- “TOKEN present” wording vs auth result
- Log noise from Discord adapter on stdout
- `run-once` does not yet require prior validate pass

### Fresh install (7/10 workspace / 5/10 `HEAD`)

**Strengths**

- `pip install -e .` and Docker build reproducible
- Templates copy cleanly
- Validate → run-once → bot path clear in workspace docs

**Gaps**

- `HEAD`: false-success `run-once`, config name mismatch
- First-time token setup still 20–45 min external to repo

### Organization onboarding (7/10 workspace / 5/10 `HEAD`)

**Strengths**

- GitHub fine-grained PAT steps detailed
- Discord intents, invite scopes, role hierarchy documented
- Identity linking flow documented separately

**Gaps**

- GitHub org PAT approval — external friction
- Template role names ≠ real server
- Active mode still multi-flag

---

## Day 1 → Day 5 progression

```text
Day 1  ████████░░  Docker consistency fixed
Day 2  ████████░░  Docs rewritten (uncommitted)
Day 3  ████████░░  Startup validation (uncommitted)
Day 4  ████████░░  ghdcbot validate (uncommitted)
Day 5  ███████░░░  Real-world test proves gap: ship ≠ local
```

**Largest delta:** Documentation + Validation (+3–5 points) — **locked in workspace, not in clone**.

---

## Release checklist (before claiming “org-ready”)

- [ ] Commit + push Week 4 Days 2–4 to `week-4` / `main`
- [ ] Run fresh clone test against remote branch (repeat Day 5 simulation)
- [ ] Friendly `LoginFailure` message in bot entrypoint
- [ ] Remove or comment `castro` from `config/example.yaml`
- [ ] Align `config/example.yaml` `data_dir` with INSTALLATION (`./data`)
- [ ] Add `docker compose build` to INSTALLATION Option A
- [ ] Optional: CI job `pytest` + smoke `validate` with test credentials

---

## Installation timing reference

See [fresh_install_test.md](fresh_install_test.md#installation-timing-measured).

| Milestone | Typical duration |
|-----------|------------------|
| External setup (PAT + Discord bot) | 20–45 min |
| Clone + configure | 5–10 min |
| Build (Docker) or venv (local) | 0.5–5 min |
| Validate + first dry-run | 3–10 min |
| **Cold-start org (total)** | **~35–70 min** |

---

## Related deliverables

| File | Purpose |
|------|---------|
| [fresh_install_test.md](fresh_install_test.md) | Docker + local clone results |
| [onboarding_test.md](onboarding_test.md) | Discord/GitHub org simulation |
| [validate_command_test.md](validate_command_test.md) | Preflight command matrix |
| [remaining_onboarding_issues.md](remaining_onboarding_issues.md) | Prioritized friction list |
| [installation_audit.md](installation_audit.md) | Day 2 baseline |
| [validation_audit.md](validation_audit.md) | Day 3 baseline |

---

## Final recommendation

**Merge Week 4 work to the default branch**, then re-run this Day 5 simulation against the published repo. Until then, score **5.2/10** for cold-start organizations; after merge and minor bot/template fixes, **~7.6/10** — acceptable for mentor-guided org onboarding with the `validate` → `run-once` → `bot` sequence.
