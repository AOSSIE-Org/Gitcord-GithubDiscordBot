# Week 6 Role Assignment — Readiness Assessment

**Date:** 2026-06-29  
**Branch investigated:** `feat/notification-lifecycle-events` (post-merge with `main`)  
**Scope:** Discord role assignment system only — no implementation changes in this document.

---

## Executive Summary

Gitcord already has a **working but narrow** Discord role assignment pipeline driven by `run-once` and `/sync`. Roles are granted from **merge count** (`merge_role_rules`) and **repo-specific merged PRs** (`repo_contributor_roles`). Score-based `role_mappings` is deprecated and **not wired** in the live orchestrator path.

There is **no distinct "Active Contributor" role**, no event-driven immediate role updates, no audit trail for applied role mutations, and **two divergent code paths** for planning vs applying (`plan_discord_roles` vs duplicated logic in `apply_discord_roles`). Week 6 should focus on unifying those paths, adding active-contributor semantics, hardening mutation safety, and improving observability.

---

## Phase 1 — Current Role Assignment Architecture

### How Discord roles are assigned today

1. **Ingest** GitHub contributions since cursor (`GitHubRestAdapter.list_contributions`).
2. **Persist** events to SQLite (`SqliteStorage.record_contributions`).
3. **Read** current Discord member→role mapping (`DiscordApiAdapter.list_member_roles`).
4. **Resolve** verified identity links (`_resolve_identity_mappings` in `orchestrator.py`).
5. **Compute** desired roles from stored `pr_merged` events + config rules.
6. **Apply** add/remove via `DiscordApiAdapter.add_role` / `remove_role` when all gates pass.

Roles are **not** assigned at GitHub webhook time. Assignment happens only on the next `run-once` or `/sync`.

### How GitHub events affect roles

| Event type | Stored? | Used for roles? | Mechanism |
|------------|---------|-----------------|-----------|
| `pr_merged` | Yes | **Yes** | `count_merged_prs_per_user()` (windowed) + `repos_with_merged_pr_per_user()` (all-time) |
| `pr_opened`, `pr_reviewed`, `comment`, etc. | Yes | **No** | Metrics only (`engine/metrics.py` — explicitly not used for roles) |
| Issue assignment events | Yes | **No** | Issue flow only |

### Synchronization flow

```
CLI: ghdcbot --config … run-once
  OR
Discord: /sync → asyncio.to_thread(orchestrator.run_once)
        ↓
Orchestrator._run_once_body()
```

### Role update pipeline (active mode)

| Step | Condition | Action |
|------|-----------|--------|
| 1 | `runtime.enable_discord_role_updates: true` | Proceed; else skip `apply_discord_roles` |
| 2 | `runtime.mode: active` | `MutationPolicy.allow_discord_mutations` |
| 3 | `discord.permissions.write: true` | Discord API calls allowed |
| 4 | Verified identity in storage | User included in iteration |
| 5 | Rule match | `add_role` for new roles; `remove_role` only for score-managed roles (currently none in live path) |

**Dry-run / observer:** `plan_discord_roles()` → `write_reports()` → `audit.md` / `audit.json`. No Discord mutations.

### Entry points

| Entry point | File | Function |
|-------------|------|----------|
| CLI sync | `src/ghdcbot/cli.py` | `run-once` → `Orchestrator.run_once()` |
| Discord sync | `src/ghdcbot/bot.py` | `sync_cmd` (~L1898) → `orchestrator.run_once()` in worker thread |
| Discord bot start | `src/ghdcbot/bot.py` | `run_bot()` — roles only when `/sync` or external `run-once` runs |
| Debug script | `scripts/debug_repo_contributor_roles.py` | Read-only inspection of repo-contributor eligibility |

There is **no scheduled/cron** built into the bot; operators use external scheduling for `run-once`.

### Files involved (complete list)

| File | Role |
|------|------|
| `src/ghdcbot/engine/orchestrator.py` | Sync loop; calls `plan_discord_roles` (audit) and `apply_discord_roles` (live) |
| `src/ghdcbot/engine/planning.py` | `plan_discord_roles`, `plan_merge_based_roles`, `count_merged_prs_per_user`, `repos_with_merged_pr_per_user` |
| `src/ghdcbot/config/models.py` | `RoleMappingConfig`, `MergeRoleRulesConfig`, `repo_contributor_roles`, `enable_discord_role_updates` |
| `src/ghdcbot/adapters/discord/api.py` | `list_member_roles`, `add_role`, `remove_role`, `_resolve_role_id` |
| `src/ghdcbot/adapters/discord/writer.py` | `DiscordPlanWriter.apply_plans` — plan executor with dedupe (not used by orchestrator) |
| `src/ghdcbot/core/models.py` | `DiscordRolePlan` dataclass |
| `src/ghdcbot/core/interfaces.py` | `DiscordReader` / `DiscordWriter` protocols |
| `src/ghdcbot/core/modes.py` | `MutationPolicy`, `RunMode` |
| `src/ghdcbot/adapters/storage/sqlite.py` | `contributions` table, `list_contributions`, `append_audit_event` |
| `src/ghdcbot/engine/reporting.py` | Audit report rendering (`_render_discord_section`) |
| `src/ghdcbot/engine/metrics.py` | Activity metrics (informational; not role input) |
| `src/ghdcbot/engine/snapshots.py` | Exports `roles.json` snapshot after sync |
| `src/ghdcbot/engine/assignment.py` | Role→GitHub mapping for issue/review assignment (read roles, not grant) |
| `src/ghdcbot/engine/issue_request_flow.py` | `compute_eligibility` — low-activity heuristic for mentors (not role grant) |
| `src/ghdcbot/bot.py` | `/sync`, `/status` (displays roles) |
| `src/ghdcbot/cli.py` | `run-once` command |
| `config/example.yaml`, `config/docker-example.yaml`, `config/examples/aussie.yaml` | Role config templates |

### Database interactions

- **Read:** `storage.list_contributions(since)` — all `pr_merged` events for merge/repo rules.
- **Read:** `storage.list_verified_identity_mappings()` — who is eligible.
- **Read:** `storage.get_cursor("github")` — ingestion watermark (not per-user role state).
- **Write:** `record_contributions` — append GitHub events (no role state table).
- **No table** stores "roles already granted by Gitcord" — Discord guild state is the source of truth for current roles.

### Discord API interactions

- `GET /guilds/{id}/roles` + `GET /guilds/{id}/members` — member role snapshot.
- `PUT /guilds/{id}/members/{user}/roles/{role_id}` — add role.
- `DELETE` same path — remove role.
- Failures: log warning, **no retry**, **no audit event**.

### Architecture gaps

1. **`plan_discord_roles` vs `apply_discord_roles`** — logic duplicated; `plan_merge_based_roles()` exists but is **never called** (inlined elsewhere).
2. **Orchestrator passes `scores=[]`, `role_mappings=[]`** — score path dead in production.
3. **`DiscordPlanWriter`** has dedupe and structured apply logging; orchestrator bypasses it.
4. **No role-specific audit events** when mutations succeed/fail in active mode.

---

## Phase 2 — Configuration Audit

### Existing role configuration

| Key | Location | Purpose | Default in templates |
|-----|----------|---------|----------------------|
| `runtime.mode` | `RuntimeConfig` | `dry-run` / `observer` / `active` | `dry-run` |
| `runtime.enable_discord_role_updates` | `RuntimeConfig` | Master switch for role mutations | `true` in model; `false` in `config/examples/aussie.yaml` |
| `runtime.activity_period_days` | `RuntimeConfig` | Window for merge-count rules | `30` (example), `7` (aussie example) |
| `discord.permissions.write` | `PermissionConfig` | Required for Discord mutations | `false` in example templates |
| `merge_role_rules` | `BotConfig` | `{enabled, rules: [{discord_role, min_merged_prs}]}` | Commented in `config/example.yaml` |
| `repo_contributor_roles` | `BotConfig` | `repo_name → discord_role` | `{}` or commented examples |
| `role_mappings` | `BotConfig` | Deprecated score thresholds | Empty / omitted |
| `assignments.issue_assignees` | `AssignmentConfig` | Who can run mentor slash commands | `["Mentor"]` |
| `assignments.issue_request_eligible_roles` | `AssignmentConfig` | Eligibility for issue requests | `[]` (any verified user) |

Notification settings (`discord.notifications`) are **orthogonal** — they do not affect role assignment.

### Missing configuration (for Week 6 goals)

| Needed config | Why |
|---------------|-----|
| `active_contributor_rules` (or extend `merge_role_rules`) | Distinguish Contributor vs Active Contributor by recency/activity |
| `contributor_role` baseline rule | Explicit "first merge" role if not using generic merge ladder |
| `role_sync` / `role_audit` flags | Control audit logging and dry-run preview in active mode |
| Per-repo role groups / prefixes | Scale beyond flat `repo → role` map |
| `role_removal_policy` | Today merge/repo roles are add-only by design — may need documented opt-in demotion |

### Configuration limitations

- **Role names are strings** — must match Discord exactly; no `role_id` in merge/repo rules (IDs only in `command_permissions`).
- **`activity_period_days` is shared** — used for merge rules, reports, and issue-request eligibility; changing it affects multiple subsystems.
- **No validate subcommand in `cli.py`** on this branch — role name preflight documented in INSTALLATION but not enforced in code here.

---

## Phase 3 — Contributor Role Assignment Audit

### Is contributor role assignment implemented?

**Partially.**

| Mechanism | Status | Discord role | Trigger |
|-----------|--------|--------------|---------|
| Generic "Contributor" via `merge_role_rules` | Implemented (config-gated) | Config-defined (e.g. `Contributor` at `min_merged_prs: 1`) | `pr_merged` count in `activity_period_days` |
| Repo-specific contributor | Implemented (config-gated) | e.g. `Contributor-frontend` | Any all-time `pr_merged` in mapped repo |
| Score-based `role_mappings` | **Deprecated / unwired** | N/A in live path | N/A |

### GitHub events that trigger contributor roles

Only **`pr_merged`** (after sync stores it and next `run-once`/`/sync` runs).

### Persistence

- Events: SQLite `contributions` table.
- Role grants: **Discord guild state only** — not recorded in SQLite.

### Edge cases handled

| Case | Behavior |
|------|----------|
| Unverified GitHub user | Excluded (not in `identity_mappings`) |
| GitHub username case mismatch | Normalized via lowercase map in planning helpers |
| User already has role | Skip add (set difference) |
| Merge role promotion | Highest threshold only (not cumulative ladder of all roles) |
| Repo role after merge | Add-only, never removed |
| Missing Discord role name | Log warning, skip (`add_role` returns early) |
| Bot below role in hierarchy | Discord API error, log warning |

### Edge cases NOT handled

- User leaves guild then rejoins (re-apply on next sync if rules still match).
- PR merged then reverted (role not revoked).
- Multiple merges same PR / duplicate events (counts may inflate if duplicates stored).
- Member not in guild (404 on add — logged only).

---

## Phase 4 — Active Contributor Logic

### Does Gitcord distinguish Contributor vs Active Contributor?

**No.** There is no config key, plan function, or Discord role name convention for "Active Contributor" in the codebase.

### Related existing logic (not role assignment)

| Location | What it does |
|----------|--------------|
| `engine/issue_request_flow.py` | `LOW_ACTIVITY_DAYS = 30`; `compute_eligibility()` returns `eligible_low_activity` if no merge in 30 days — **mentor UI only** |
| `engine/metrics.py` | `get_contribution_metrics()` — PR/issue counts per window; docstring: **not used for roles** |
| `bot.py` `/summary` | Shows 7/30-day activity to user |

### Data available to implement Active Contributor

From `storage.list_contributions(period_start)`:

- `pr_merged` timestamps → last activity date
- `pr_opened`, `pr_reviewed`, `comment` → engagement signals
- `list_contribution_summaries()` → aggregated counts per user per window

### Recommendations

1. **Define Active Contributor** as: verified user with ≥1 `pr_merged` in `activity_period_days` (or stricter: merge + review/comment threshold).
2. **Define Contributor** as: ≥1 all-time `pr_merged` OR first-time merge role (promotion-only).
3. Add config block, e.g.:

```yaml
contributor_roles:
  contributor:
    min_merged_prs_all_time: 1
  active_contributor:
    min_merged_prs_in_period: 1
    period_days: 30  # or inherit activity_period_days
```

4. Implement in **`plan_discord_roles` only**, then route `apply_discord_roles` through `DiscordPlanWriter.apply_plans` for single source of truth.
5. **Removal policy:** Active Contributor could be removed when activity drops (unlike merge/repo promotion-only rules) — requires explicit design decision.

---

## Phase 5 — Merge-Based Assignment

### What happens after a PR is merged?

1. GitHub ingestion emits `pr_merged` `ContributionEvent` (`rest.py` `_collect_pull_request_events`).
2. `run-once` stores event in SQLite.
3. On same run (after storage), `apply_discord_roles` reads all `pr_merged` in period / all-time.
4. If thresholds met → `add_role`.

**No immediate role update at merge time** separate from sync cycle.

### Files processing merge events

| File | Function |
|------|----------|
| `src/ghdcbot/adapters/github/rest.py` | Ingestion |
| `src/ghdcbot/adapters/storage/sqlite.py` | Persistence |
| `src/ghdcbot/engine/planning.py` | `count_merged_prs_per_user`, merge role planning |
| `src/ghdcbot/engine/orchestrator.py` | `apply_discord_roles` |

### Missing behavior

- No notification-to-role coupling (by design).
- No demotion when merge count drops (impossible without event deletion).
- No handling of merge in period boundary edge (clock skew).
- `apply_discord_roles` does not sort `identity_mappings` — planning does; minor determinism gap.

---

## Phase 6 — Repository-Specific Roles

### Current support

**Implemented** via `repo_contributor_roles: dict[str, str]`:

```yaml
repo_contributor_roles:
  Gitcord-GithubDiscordBot: "Gitcord Contributor"
  docs-site: "Documentation Contributor"
```

- Matching uses **short repo name** (not `org/repo`).
- **All-time** `pr_merged` in storage (`REPO_CONTRIBUTOR_EPOCH = 2000-01-01`).
- **Add-only** — never auto-removed.

### Schema

No dedicated DB table — derived from `contributions` at runtime.

### Permission implications

- Bot needs **Manage Roles** + role hierarchy above target roles.
- Repo role names must exist in Discord guild.
- Large orgs: N repos × M contributors → many role adds over time (no cap).

### Recommended architecture (Week 6)

1. Keep flat `repo → role` map for simple cases.
2. Optional **role prefix** + auto-naming convention for scale.
3. Consider **role group** config: `repo_groups: {gitcord: [repo-a, repo-b], docs: [docs-site]}`.
4. Document that repo roles are **sticky** unless demotion policy added later.

---

## Phase 7 — Role Synchronization

### How `/sync` updates roles

Same as `run-once`: full GitHub ingestion + `apply_discord_roles` if gates pass. Runs in **background thread** (`bot.py`) so Discord heartbeats continue.

### Determinism

| Aspect | Deterministic? |
|--------|----------------|
| `plan_discord_roles` | **Yes** — sorted users, sorted rules, stable plan order |
| `apply_discord_roles` | **Mostly** — iteration order over `identity_mappings` may vary if unsorted |
| Duplicate adds in one run | Prevented by set diff (`desired - current`) |
| `DiscordPlanWriter` dedupe | **Yes** — but unused in orchestrator |

### Weaknesses

1. **Global single cursor** — partial ingestion failure can skew events (documented in `week3_notes.md`).
2. **No per-repo or per-user sync state** for roles.
3. **No idempotency ledger** — relies on Discord state; re-run safe for adds but noisy for logs/DMs.
4. **Congratulation DM on every new role** (`_send_role_congratulation`) — re-link edge cases may re-notify if role was manually removed.
5. **Full re-read of all contributions** each run — scales with history size.

---

## Phase 8 — Mutation Safety

### Existing protections

| Mechanism | Location |
|-----------|----------|
| `MutationPolicy` | `core/modes.py` — active + write required |
| `enable_discord_role_updates` | `orchestrator.py` — config master switch |
| Promotion-only merge/repo rules | `planning.py` — no auto-remove |
| Graceful degrade on missing role | `api.py` `add_role` |
| Dry-run audit reports | `reporting.py` |

### Gaps

| Risk | Current behavior | Recommendation |
|------|------------------|----------------|
| Accidental role removal | Live path: `managed_roles` empty → **no removals** | Explicit removal allowlist if score rules return |
| Partial failure mid-loop | Some users updated, others not; no rollback | Per-user try/except + summary; optional transaction log |
| Discord API transient errors | Log and return | Retry with backoff (like GitHub adapter) |
| Missing guild member | 404, logged | Skip + audit entry |
| Duplicate apply in one run | Safe for adds | Route through `DiscordPlanWriter` dedupe |
| `apply` without `plan` in active mode | Active skips audit plan generation | Generate plan in active mode too for diff logging |

---

## Phase 9 — Audit Logging

### Current state

| Event | Logged? | Where |
|-------|---------|-------|
| Planned role changes (dry-run) | **Yes** | `data_dir/reports/audit.md`, `audit.json` |
| Applied role add/remove | **Partial** | Python logger only (`DiscordMutations`, `DiscordApiAdapter`) |
| Skipped (gated) | **Partial** | `DiscordPlanWriter._log_plan` — not used by orchestrator |
| Failures | **Partial** | Warning logs in `api.py` |
| Mentor-queryable history | **No** | `audit_events.jsonl` has identity/issue events, not role mutations |

### Recommendations

1. `append_audit_event` for each `add`/`remove`/`skip`/`fail` with `event_type: discord_role_mutation`.
2. Include `github_user`, `discord_user_id`, `role`, `action`, `decision_reason`, `sync_id`.
3. Surface last role sync summary in `/sync` response embed.
4. Export via existing `ghdcbot export-audit --event-type discord_role_mutation`.

---

## Phase 10 — Tests (summary)

See `test_gap_analysis.md` for full detail.

**Existing:** `test_merge_role_rules.py`, `test_repo_contributor_roles.py`, `test_role_planning_correctness.py`, `test_role_congratulations.py`, `test_planning_determinism.py`, `test_mutation_policy_gating.py`.

**Missing:** Orchestrator integration for merge/repo rules in active mode, `apply_discord_roles` parity with `plan_discord_roles`, active contributor rules, Discord API failure/retry, audit event assertions, `/sync` role summary.

---

## Week 6 Readiness Matrix

| Planned goal | Status | Complexity | Notes |
|--------------|--------|------------|-------|
| Contributor role assignment | **Partial** | S | Exists via `merge_role_rules`; needs wiring/docs + possible default config |
| Active contributor role assignment | **Missing** | M | Needs new rules + removal policy decision |
| Improve merge-based role assignment | **Partial** | M | Unify plan/apply; use `plan_merge_based_roles`; fix orchestrator wiring |
| Repo-specific contributor roles | **Implemented** | S | Extend docs/tests; optional grouping |
| Deterministic role synchronization | **Partial** | M | Route apply through `DiscordPlanWriter`; sort mappings |
| Mutation safety checks | **Partial** | M | Retries, audit, active-mode plan diff |
| Improve role audit visibility | **Missing** | S–M | `audit_events.jsonl` + `/sync` feedback |

**Complexity:** S = ≤1 day, M = 2–3 days, L = 4+ days

---

## Potential Blockers

| Blocker | Impact | Mitigation |
|---------|--------|------------|
| Discord role hierarchy | Silent add failures | INSTALLATION checklist; validate role existence |
| Server Members Intent | Empty `list_member_roles` | Document in DOCKER.md / TESTING_DISCORD |
| Large org sync duration | Stale role updates | Scheduled `run-once`; future webhook architecture |
| Add-only repo roles | Role bloat over time | Document; optional demotion in Week 6+ |
| No "Active Contributor" definition | Scope creep | Agree product definition with mentors before Tuesday |

---

## Recommended Implementation Order

| Day | Focus |
|-----|-------|
| **Monday (today)** | This assessment; agree Contributor vs Active Contributor definitions with mentors |
| **Tuesday** | Unify `plan_discord_roles` + `apply_discord_roles` → single plan-then-apply path via `DiscordPlanWriter` |
| **Wednesday** | Active contributor rules + config schema; tests |
| **Thursday** | Mutation safety: audit events, retries, `/sync` summary |
| **Friday** | Repo role polish (groups/docs); deterministic ordering hardening |
| **Saturday** | Integration tests, manual checklist, update INSTALLATION + TECHNICAL_DOCUMENTATION |

---

## Key Code References

```349:457:src/ghdcbot/engine/orchestrator.py
def apply_discord_roles(...):
    # Live role application — duplicated logic vs plan_discord_roles
```

```126:324:src/ghdcbot/engine/planning.py
def plan_discord_roles(...):
    # Authoritative planning with audit-friendly DiscordRolePlan output
```

```196:211:src/ghdcbot/engine/orchestrator.py
        if enable_discord_role_updates:
            apply_discord_roles(..., scores=[], role_mappings=[], ...)
```

```94:120:src/ghdcbot/adapters/discord/api.py
    def add_role(self, discord_user_id: str, role_name: str) -> None:
        # PUT role — warns and returns on failure, no retry
```
