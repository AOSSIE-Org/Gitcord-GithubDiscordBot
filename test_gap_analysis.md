# Week 6 — Role Assignment Test Gap Analysis

Assessment of existing test coverage and recommendations for Week 6 implementation.

---

## 1. Existing Tests

### Role planning (unit)

| File | What it covers |
|------|----------------|
| `tests/test_merge_role_rules.py` | `merge_role_rules` enabled/disabled; threshold selection; highest role; determinism; config validation |
| `tests/test_repo_contributor_roles.py` | Repo role grant; multiple repos; add-only (no remove); disabled config |
| `tests/test_role_planning_correctness.py` | Score-based add/remove via `plan_discord_roles` |
| `tests/test_planning_determinism.py` | Identical inputs → identical `plan_discord_roles` output |

### Role application (unit)

| File | What it covers |
|------|----------------|
| `tests/test_role_congratulations.py` | `apply_discord_roles`: add, skip, remove, DM gating, dry-run |
| `tests/test_mutation_policy_gating.py` | `DiscordPlanWriter` / `GitHubPlanWriter` skip in dry-run, observer, write-disabled |

### Related (partial)

| File | Relevance |
|------|-----------|
| `tests/test_identity_linking.py` | Orchestrator mocks `add_role`/`remove_role`; not merge/repo rules |
| `tests/test_observability.py` | `enable_discord_role_updates=False` sync logging |
| `tests/test_empty_org_behavior.py` | Empty org `plan_discord_roles` |
| `tests/test_config.py` | `role_mappings` optional default |
| `tests/test_snapshots.py` | `roles.json` snapshot fixture |
| `tests/test_discord_command_permissions.py` | Slash command role checks (not assignment) |

### Debug / manual

| File | Purpose |
|------|---------|
| `scripts/debug_repo_contributor_roles.py` | Manual inspection of repo-contributor eligibility |

---

## 2. Coverage Map vs Week 6 Goals

| Goal | Covered? | Test file(s) | Gap |
|------|----------|--------------|-----|
| Contributor via merge rules | **Plan only** | `test_merge_role_rules.py` | No orchestrator e2e apply |
| Active contributor | **No** | — | New test module needed |
| Merge rule improvements | **Partial** | `test_merge_role_rules.py` | No `plan_merge_based_roles` tests (dead code) |
| Repo-specific roles | **Plan only** | `test_repo_contributor_roles.py` | No apply + Discord mock integration |
| Deterministic sync | **Plan only** | `test_planning_determinism.py` | `apply_discord_roles` order not tested |
| Mutation safety | **Partial** | `test_mutation_policy_gating.py`, `test_role_congratulations.py` | No retry, no partial-failure summary |
| Audit visibility | **Partial** | `test_observability.py` | No role mutation audit events |

---

## 3. Missing Tests (recommended)

### Priority 1 — Must have for Week 6

| ID | Test | Rationale |
|----|------|-----------|
| T1 | `test_apply_matches_plan_for_merge_rules` | Assert `plan_discord_roles` and `apply_discord_roles` produce same add set |
| T2 | `test_orchestrator_applies_merge_role_in_active_mode` | Full `run_once` with mock Discord writer; verify `add_role` called |
| T3 | `test_active_contributor_role_granted_and_revoked` | New rules: merge in period → active; no activity → removed (if policy allows) |
| T4 | `test_role_mutation_audit_event_appended` | After apply, `audit_events.jsonl` contains `discord_role_mutation` |

### Priority 2 — Should have

| ID | Test | Rationale |
|----|------|-----------|
| T5 | `test_add_role_missing_discord_role_logs_and_skips` | `api.py` returns early when role name not found |
| T6 | `test_discord_plan_writer_dedupes_duplicate_plans` | Already partially in writer tests; wire orchestrator to use it |
| T7 | `test_repo_contributor_roles_orchestrator_integration` | `run_once` with `pr_merged` in storage + config map |
| T8 | `test_enable_discord_role_updates_false_skips_apply` | Extend `test_observability` pattern |

### Priority 3 — Nice to have

| ID | Test | Rationale |
|----|------|-----------|
| T9 | `test_congratulation_dm_not_sent_twice_same_role` | Idempotency across syncs |
| T10 | `test_apply_sorts_identity_mappings_deterministically` | Ordering guarantee |
| T11 | `test_github_username_case_insensitive_role_grant` | Storage has different case than identity |

---

## 4. Recommended New Test Files

```text
tests/test_role_apply_parity.py      # plan vs apply equivalence
tests/test_active_contributor_roles.py
tests/test_role_audit_events.py
tests/test_orchestrator_role_sync.py  # integration-style with mocks
```

---

## 5. Manual Testing Checklist

Use before/after Week 6 changes on a test Discord server.

### Setup

- [ ] Test guild with roles: `Contributor`, `Active Contributor`, `Gitcord Contributor`, `Mentor`
- [ ] Bot role **above** target roles in hierarchy
- [ ] Bot has **Manage Roles** + **Server Members Intent**
- [ ] Config: `mode: dry-run` first, then `active`
- [ ] `enable_discord_role_updates: false` until dry-run reviewed

### Dry-run

- [ ] `ghdcbot --config config/config.yaml run-once`
- [ ] Open `data/reports/audit.md` — verify **Discord Role Changes** section
- [ ] Confirm `decision_reason` shows `merge_role_rules` or `repo_contributor_roles`
- [ ] No roles changed in Discord

### Active apply

- [ ] Set `mode: active`, `enable_discord_role_updates: true`, `discord.permissions.write: true`
- [ ] Link test user: `/link` + `/verify-link`
- [ ] Merge a PR as test user (or seed SQLite with `pr_merged` event)
- [ ] Run `run-once` or `/sync`
- [ ] Verify role appears in Discord and `/status`

### Merge rules

- [ ] User with 0 merges → no Contributor role
- [ ] User with 1 merge in period → Contributor (if `min_merged_prs: 1`)
- [ ] User with 5 merges → highest configured role only (not all ladder roles)

### Repo roles

- [ ] Configure `repo_contributor_roles: {test-repo: "Gitcord Contributor"}`
- [ ] Merge PR in `test-repo` → role granted
- [ ] Merge in other repo → repo role not granted

### Active contributor (after Week 6 implementation)

- [ ] Recent merge → Active Contributor granted
- [ ] No merges in 30 days → Active Contributor removed (if designed)

### Safety

- [ ] Wrong role name in config → sync completes; warning in logs; no crash
- [ ] `enable_discord_role_updates: false` → no Discord role API calls
- [ ] `dry-run` → no Discord role API calls

### Audit

- [ ] `audit_events.jsonl` records role mutations (after Week 6)
- [ ] `ghdcbot export-audit --event-type discord_role_mutation` filters correctly

---

## 6. Test Utilities Needed

| Utility | Purpose |
|---------|---------|
| `make_contribution_event(event_type, user, repo, at)` | Already scattered in tests — consolidate fixture |
| `seed_merged_pr(storage, user, repo, count)` | Quick eligibility setup |
| `MockDiscordWriter` with call tracking | Assert add/remove sequences |
| `read_audit_events(path)` | Parse jsonl for assertions |

---

## 7. Regression Risks (watch during Week 6)

| Change | Risk | Test to run |
|--------|------|-------------|
| Unify plan/apply | Behavior drift | T1 parity test |
| Active contributor removal | Accidental mass role strip | T3 + manual safety |
| Route through DiscordPlanWriter | Double-apply or skipped apply | T2, T6 |
| Audit logging | Performance / disk growth | T4 |

---

## 8. Current CI Coverage

Workflow: `.github/workflows/tests.yml` runs full `pytest` suite.

Role-specific tests today: **~6 files**, **~40+ test cases** (planning-heavy, apply-light).

**Estimated new tests for Week 6:** 15–25 cases across 3–4 new files.

---

## 9. Summary

| Category | Count |
|----------|-------|
| Existing role-related test files | 8 |
| Missing P1 tests | 4 |
| Missing P2 tests | 4 |
| Manual checklist items | 20+ |

The largest gap is **integration between orchestrator and Discord apply** for merge/repo rules — planning is well tested, but production `apply_discord_roles` diverges from `plan_discord_roles` without parity tests.
