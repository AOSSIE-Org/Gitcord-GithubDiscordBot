# Remaining Onboarding Issues (Week 4 Day 5)

Ranked by impact on a **new organization** installing without maintainer help.

**Evidence:** [fresh_install_test.md](fresh_install_test.md), [onboarding_test.md](onboarding_test.md), [validate_command_test.md](validate_command_test.md), fresh clone of `HEAD` (`15e0c30`).

---

## High priority — blocks or falsely signals success

| # | Issue | Impact | Suggested fix |
|---|-------|--------|---------------|
| H1 | **Week 4 Days 2–4 not committed to `HEAD`** | Clone gets old INSTALLATION (`my-org-config.yaml`), no `validate`, weak startup checks | Commit + push `week-4` before GSoC milestone |
| H2 | **`run-once` exits 0 with invalid tokens** (`HEAD`) | User believes install succeeded; empty audit | Ship Day 3 loader validation; consider non-zero exit if GitHub auth fails |
| H3 | **Docker bot shows stack trace on bad Discord token** | Crash loop; scary logs | Catch `LoginFailure` in `bot.py`; print one-line hint to fix `DISCORD_TOKEN` |
| H4 | **No documented preflight on `HEAD`** | Users skip straight to `up -d` / `run-once` | INSTALLATION Step 6.3: run `validate` before any runtime command |
| H5 | **Config filename split on `HEAD`** | `my-org-config.yaml` vs `config/config.yaml` | Already fixed in workspace INSTALLATION — needs commit |
| H6 | **Template `guild_id` placeholder `000…`** | Silent wrong-server behavior | Fail at validate (workspace does); warn in template comments |

---

## Medium priority — confusing, wastes time

| # | Issue | Impact | Suggested fix |
|---|-------|--------|---------------|
| M1 | **`config/example.yaml` `data_dir: /tmp/ghdcbot-state`** | Reports not where INSTALLATION says (`./data`) | Change template default to `./data` or add prominent comment |
| M2 | **Template roles include `Maintainer`, `castro`** | `validate` fails until user hunts YAML | Remove `castro` example; comment that all role names must exist in Discord |
| M3 | **JSON logs on stdout for `run-once`** | Non-developers cannot read output | Document “ignore JSON” or add human summary line at end |
| M4 | **`validate` prints “TOKEN present” before auth** | Implies success before API check | Rename to “token configured” or merge with auth result |
| M5 | **Adapter warnings on stdout during validate** | `Unable to list roles` before ✓/✗ block | Route adapter logs to stderr only during validate |
| M6 | **INSTALLATION Docker path missing on `HEAD`** | Docker users must discover README/DOCKER.md | Commit unified INSTALLATION |
| M7 | **Active mode requires 4 flags** | “Active” with no role changes | Keep validate active-mode check; add checklist in INSTALLATION Step 8 |
| M8 | **Fine-grained PAT org approval** | Hours of “broken” token | Add troubleshooting: “pending organization approval” |
| M9 | **No cron/systemd example** | Ops teams don’t schedule `run-once` | Add one crontab line to INSTALLATION or TECHNICAL_DOCUMENTATION |
| M10 | **`docker compose build` omitted from INSTALLATION Option A** | First-time users may need explicit build step | Add `docker compose build` before `up -d` |

---

## Low priority — nice-to-have

| # | Issue | Impact | Suggested fix |
|---|-------|--------|---------------|
| L1 | **Invite URL generator helper** | Manual permission bitmask | Script or doc table with permission integer |
| L2 | **Fork clone URL** | Minor confusion for forks | One sentence (already in workspace INSTALLATION) |
| L3 | **PyNaCl / voice warnings in Docker logs** | Harmless noise | Document as ignorable |
| L4 | **`requirements.txt` absent** | Some users expect it | Note in INSTALLATION (already present) |
| L5 | **AOSSIE examples in `config/examples/`** | Could imply wrong copy source | Label as “reference only” in README table |
| L6 | **Health check / CI smoke test** | No automated fresh-install gate in CI | Future: `docker compose run validate` in CI with secrets |
| L7 | **Bot role order API check** | Cannot fully verify via API | Link to Discord screenshot in docs |

---

## Priority summary

```text
HIGH:   6 issues — 1 meta (uncommitted work), 5 user-facing
MEDIUM: 10 issues — mostly docs + UX polish
LOW:    7 issues — polish and automation opportunities
```

**If only one thing is fixed:** commit and release Week 4 work (H1) — owner: onboarding/docs maintainer, target: PR 1.

**If two things:** H1 + H3 (friendly bot token failure) — owners: onboarding/docs maintainer (H1) + validation/CLI maintainer (H3), targets: PR 1 + PR 2.

**If three things:** H1 + H3 + M2 (clean example config template) — owners: onboarding/docs maintainer (H1), validation/CLI maintainer (H3), config owner (M2), targets: PR 1 + PR 2 + PR 3 before Week 4 closure.
