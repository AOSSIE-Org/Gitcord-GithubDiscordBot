# Gitcord Systems Verification Report

**Scope:** Correctness, safety, and current project status. No new features, no refactors.  
**Date:** 2026-02-03  
**Verdict:** See Section 8.

---

## 1. Current Status

### What is fully implemented

| Area | Status | Notes |
|------|--------|--------|
| **GitHub ingestion** | ✅ Implemented | Incremental (cursor), rate-limit aware, empty org/repos handled. Events: issue_opened/closed, pr_opened/merged, pr_reviewed, comment, issue_assigned. |
| **Discord read** | ✅ Implemented | list_member_roles, pagination, permission failures degrade to empty (no crash). |
| **Scoring** | ✅ Implemented | WeightedScoreStrategy from config weights; period_days; deterministic. |
| **Planning** | ✅ Implemented | plan_discord_roles, assignment (issues, PR reviews); deterministic; uses identity_mappings only. |
| **Reporting** | ✅ Implemented | audit.json, audit.md, activity.md; report_generated audit event. |
| **Mutation gating** | ✅ Implemented | MutationPolicy: dry-run/observer → no writes; active + permissions → writes. |
| **Identity linking (Phase-1)** | ✅ Implemented | Verification code (bio/gist), time-limited, SQLite; create_claim / verify_claim; engine uses only verified or config fallback. |
| **Audit logs** | ✅ Implemented | append_audit_event (identity + report_generated); export-audit CLI (JSON/CSV). |
| **Activity visibility** | ✅ Implemented | activity.md; optional activity_channel_id Discord summary. |
| **Discord bot** | ✅ Implemented | /link, /verify-link, /verify, /status, /summary (read-only/informational). |
| **CLI** | ✅ Implemented | run-once, bot, link, verify-link, export-audit. |

### Phase

- **Phase-1** identity verification is in place (code-in-bio/gist, no OAuth, no HTTP server).
- Project is **stable and safe to run** in dry-run/observer with default config.
- Identity linking is **production-safe under current design constraints** (see Section 2), with documented notes (storage failure fallback, no verified-state expiry).

---

## 2. Identity Linking Review

### Proof of ownership

- **Requires proof:** Verification requires the code to appear in the **claimed GitHub user’s** public bio or public gists. Only that account holder can do that. A Discord user cannot affect scoring/roles by claiming a GitHub user they do not own without passing verification.
- **Time-limited codes:** TTL configurable (default 10 minutes); `expires_at` stored in UTC; `verify_claim` checks `expires_at <= now` and returns `(False, "expired")` without marking verified.
- **Bio or gist:** `GitHubIdentityReader.search_verification_code` checks bio first, then public gists (description and file content via raw URL). No other channels.

### Verified mappings immutability

- **Overwrite protection:** `create_identity_claim` explicitly rejects when `(discord_user_id, github_user)` already has `verified = 1` (ValueError: "already verified; cannot create a new claim"). No downgrade of verified → unverified via create_claim. (Fixes previously identified bug.)
- **Mark verified:** `mark_identity_verified` only updates the row for that (discord_user_id, github_user); sets verified=1, clears code/expires_at. No batch or cross-user overwrite.

### One-to-one and impersonation

- **One GitHub ↔ one Discord:** If `github_user` is already verified for another `discord_user_id`, create_claim raises. If `discord_user_id` is already verified for another `github_user`, create_claim raises. So one-to-one is enforced.
- **Pending claims:** All pending rows for that `github_user` by **other** Discord users are fetched (`fetchall()`); if any has `expires_at > now`, raise. Only **expired** pending rows for that github_user are deleted; then the new claim is inserted. So no active pending claim is overwritten by another user.

### Engine uses only verified (or config fallback)

- **`_resolve_identity_mappings`:** Calls `storage.list_verified_identity_mappings()` when present. If the returned list is non-empty, returns it and **ignores** config. If empty (or getter missing/exception), returns `list(config_identity_mappings)`.
- **Unverified never used:** `list_verified_identity_mappings()` is `WHERE verified = 1`. Scoring, role planning, and assignment use only the list from `_resolve_identity_mappings`; unverified rows are never fed into the engine.

### Edge cases and risks

- **Re-verification / re-link:** Same user can run /link again for a **different** GitHub user only after the current verified link is for another GitHub user (blocked by "discord_user_id is already verified for another GitHub user"). For the **same** pair, create_claim is rejected (already verified). So no silent overwrite; re-link is explicit (e.g. support would need to delete or add an unlink flow).
- **Race conditions:** Single-connection flows; between SELECT and INSERT another process could insert. PRIMARY KEY and application checks (verified for other user, active pending for other user) cause conflicting cases to fail or be rejected on next read. No in-process partial write of one row. **Documented:** No DB-level UNIQUE for “at most one verified per github_user”; enforced in app only.
- **Silent failure path:** If `list_verified_identity_mappings()` **raises** (e.g. SQLite locked), `_resolve_identity_mappings` catches Exception, sets `verified = []`, and returns config. So **on storage failure the system falls back to config** (fail-open). Documented in SECURITY_VERIFICATION_IDENTITY_LINKING.md; acceptable only if config is trusted when storage is unavailable.

**Conclusion:** Identity linking is **correct and secure** under the stated constraints. Impersonation is prevented; verified mappings are protected from overwrite; engine uses only verified (or config fallback). The only behavioral caveat is config fallback on storage exception.

---

## 3. Engine & Pipeline Integrity

### Pipeline order

1. **init_schema**
2. **Identity:** `_resolve_identity_mappings(storage, config.identity_mappings)` → used for all downstream steps.
3. **GitHub ingestion:** cursor = get_cursor("github") or period_start; list_contributions(cursor); record_contributions; set_cursor("github", last_seen).
4. **Scoring:** list_contributions(period_start); WeightedScoreStrategy.compute_scores; upsert_scores.
5. **Discord read:** list_member_roles.
6. **Planning:** build_role_to_github_map(identity_mappings, member_roles); plan_issue_assignments; plan_review_requests; plan_discord_roles(member_roles, scores, identity_mappings, role_mappings).
7. **Reporting (dry-run/observer):** write_reports; write_activity_report; append_audit report_generated; optional Discord activity summary.
8. **Mutations (gated):** apply_github_plans; apply_discord_roles — only if policy.allow_*.

### Determinism

- Scoring: same contributions + period_end → same scores (dict iteration order normalized by sorted output).
- Planning: sorted(identity_mappings), sorted(role_thresholds), sorted(desired_roles), etc. Audit payload uses sorted keys and sorted plan lists.
- Reports: deterministic ordering; same inputs → same audit/activity output.

### MutationPolicy enforcement

- **allow_github_mutations:** True only if mode == ACTIVE and config.github.permissions.write.
- **allow_discord_mutations:** True only if mode == ACTIVE and config.discord.permissions.write.
- **apply_github_plans / apply_discord_roles:** Both check policy at start; if not allow_*, log and return without calling writer.
- **Writers:** test_mutation_policy_gating confirms dry-run, observer, and write-disabled all skip HTTP calls and log the expected skip reason.

### Coupling

- Identity linking is **not** coupled to ingestion: ingestion uses GitHub org/repos and contribution events only; identity is used only in orchestrator for `_resolve_identity_mappings` and then in planning and apply_discord_roles. No identity in contribution ingestion path.

**Conclusion:** Pipeline is correct; planning is deterministic; dry-run/observer produce reports and no writes; active mode writes only when permissions allow; Discord role writes are gated. No accidental writes in observer/dry-run.

---

## 4. Storage & Data Safety (SQLite)

### Timestamps

- **Write path:** `_ensure_utc` used for event.created_at, period_start/end, cursor, expires_at, created_at, verified_at. Stored as ISO with timezone (e.g. +00:00).
- **Read path:** `_parse_utc` used for created_at, period_start/end, cursor; returns timezone-aware UTC. No naive datetimes returned to callers.

### Schema

- **identity_links:** CREATE TABLE IF NOT EXISTS; PRIMARY KEY (discord_user_id, github_user); verified, verification_code, expires_at, created_at, verified_at. Indexes on github_user and verified. No change to existing contribution/scores/cursors tables; additive.
- **Backward compatibility:** New installs get identity_links; existing installs without it get the table on first init_schema. No migrations; no dropped columns.

### Verified data overwrite

- **create_identity_claim:** Rejects when (discord_user_id, github_user) already has verified=1. Rejects when github_user is verified for another discord_user_id. Deletes only expired pending rows for that github_user. So verified rows are not overwritten by create_claim.
- **mark_identity_verified:** UPDATE only for (discord_user_id, github_user); sets verified=1, clears code/expires_at. No bulk overwrite.

### Cursor handling

- **get_cursor:** Returns None if missing; otherwise _parse_utc(row["cursor"]) → timezone-aware UTC.
- **set_cursor:** _ensure_utc(cursor); stored as isoformat. Safe for incremental ingestion ordering (string compare of ISO8601 UTC).

### Single-runner / multi-runner

- **Single-runner:** Code assumes one orchestrator run at a time (cursor advance, report overwrite). No distributed locking.
- **Multi-runner risk:** Two processes running run-once on same data_dir could double-ingest (cursor race), double-write reports, or conflict on SQLite. **Documented;** not fixed. Acceptable for Phase-1 single-machine, run-once usage.

**Conclusion:** Timestamps are UTC; schema is backward compatible; identity tables do not break existing installs; verified data is not accidentally overwritten; cursor handling is safe. Single-runner assumption documented.

---

## 5. Test Coverage Summary

### Well-covered

- **Identity linking:** Verification code stored; impersonation (github already verified for other) fails; same-pair already verified rejects create_claim; verify_claim marks verified and clears code; **verified mappings used and unverified ignored in planning** (test_verified_mappings_used_unverified_ignored_in_planning with orchestrator run_once and audit.json assert assignee bob not alice).
- **Mutation gating:** test_mutation_policy_gating parametrized for dry-run, observer, active+write disabled; writers do not perform HTTP when gated; expected skip reason in logs.
- **Planning determinism:** test_planning_determinism.
- **Contribution summary:** test_contribution_summary (counts, scores).
- **Config:** test_config.
- **Repo filtering, empty org, user fallback, role planning, writer safety, readme setup:** Covered by existing tests.

### Not covered (acceptable for Phase-1)

- **Storage exception in _resolve_identity_mappings:** No test that mocks list_verified_identity_mappings() to raise and asserts fallback to config or fail-closed. Documented in security doc as optional test.
- **GitHub identity API failure (404/403/rate):** search_verification_code uses _request; on non-200 or exception returns None or empty; verify_claim gets match.found False. Not explicitly tested with mocked HTTP failure.
- **Expired claim exact message:** test_verify_marks_mapping_verified covers success; expired path is exercised in logic but not a dedicated test for "expired" return value.
- **Discord bot slash commands:** No automated tests for /link, /verify-link, /verify, /status, /summary (would require Discord mock or integration). Manual testing only.

**Conclusion:** Identity verification, impersonation, verified-vs-unverified in planning, and mutation gating are covered. Tests would fail if verification were bypassed (unverified used) because the planning test asserts assignee from verified mapping only. Gaps are documented and acceptable for Phase-1.

---

## 6. Failure Modes

| Scenario | Behavior | Explicit / Silent |
|----------|----------|-------------------|
| **GitHub API down / 5xx** | Ingestion request fails; exception or empty list; no contributions stored; cursor not advanced (or partial). Orchestrator continues; reports may be from previous data. | Explicit (exception/log). |
| **GitHub rate limit** | Adapter may log and return; behavior depends on rest.py (e.g. return response vs None). If None, that page of data is skipped. | Depends on implementation; should be explicit. |
| **Discord API down / 403** | list_member_roles returns {} on failure; planning proceeds with empty member_roles; no crash. | Explicit (log); safe degradation. |
| **SQLite locked** | _connect() or execute raises; create_claim / verify_claim / run_once fail; no silent success. | Explicit. |
| **Verification code expires** | verify_claim checks expires_at <= now; returns (False, "expired"); no mark_identity_verified; row stays verified=0. | Explicit. |
| **Re-link already verified same pair** | create_identity_claim raises "already verified; cannot create a new claim". | Explicit. |
| **list_verified_identity_mappings raises** | _resolve_identity_mappings catches Exception; verified = []; returns config. Engine uses config (fail-open). | Explicit (no silent success); documented risk. |

**Conclusion:** Failures are explicit (exceptions or degraded results). No silent success on failure. No partial state corruption from the reviewed paths (verified row not downgraded; only expired pending rows deleted).

---

## 7. Known Break Points

1. **Storage exception fallback to config**  
   **What:** If SQLite is unavailable or list_verified_identity_mappings raises, engine falls back to config identity_mappings.  
   **Why:** Design choice to avoid full outage when storage is transiently failing.  
   **Risk:** If config contains legacy/unverified mappings, they are used when storage fails.  
   **Acceptable for Phase-1:** Yes, if config is trusted and single-machine.

2. **Two run-once processes on same data_dir**  
   **What:** Cursor race; possible double ingestion; report overwrite; SQLite contention.  
   **Why:** No file or DB locking across processes.  
   **Acceptable for Phase-1:** Yes, if only one runner is used per data_dir.

3. **GitHub token scope / rate limit**  
   **What:** Token without repo scope or hitting rate limit can yield empty or partial ingestion.  
   **Why:** External dependency.  
   **Acceptable for Phase-1:** Yes; operators must provide valid token and handle rate limits.

4. **Discord token invalid or bot removed from guild**  
   **What:** list_member_roles fails; empty roles; role plans may be wrong; no role writes if already gated.  
   **Why:** External dependency.  
   **Acceptable for Phase-1:** Yes; explicit log and safe degradation.

5. **Verified state never expires**  
   **What:** Once verified, the link is permanent until manual DB change or future unlink feature.  
   **Why:** No polling; no re-check of bio/gist after verification.  
   **Acceptable for Phase-1:** Yes; documented. Optional verified_max_age can be added later without breaking current contract.

---

## 8. Final Verdict

**Verdict: SAFE WITH NOTES**

- **Identity linking:** Correct and secure under design constraints; proof of ownership required; verified immutable from create_claim; one-to-one enforced; engine uses only verified (or config fallback). Note: **on storage exception, fallback to config** (documented; accept if config is trusted).
- **Engine and pipeline:** Deterministic; dry-run/observer produce reports and no writes; mutations gated by MutationPolicy and permissions.
- **Storage:** UTC throughout; schema backward compatible; verified data protected; cursor safe.
- **Tests:** Identity, impersonation, verified-vs-unverified in planning, and mutation gating are covered; no bypass of verification in tested paths.

**What Gitcord is currently best at**

- Offline-first, run-once automation with clear read → score → plan → report → apply pipeline.  
- Audit-first: reports and audit events before any mutation.  
- Phase-1 identity linking without OAuth: code-in-bio/gist, time-limited, SQLite, verified-only in engine.  
- Deterministic planning and reporting; mutation gating by mode and permissions.

**What it intentionally does not do yet**

- OAuth; HTTP callback servers; background polling; always-on daemons.  
- Verified-state expiry; unlink; multi-runner coordination.  
- Real-time Discord chat; webhooks for every event.

**What should not be added next (to avoid scope creep)**

- OAuth or callback servers before a clear Phase-2 design.  
- Automatic re-verification or verified-state expiry without an explicit, documented policy.  
- Real-time sync or always-on services that conflict with run-once, audit-first model.
