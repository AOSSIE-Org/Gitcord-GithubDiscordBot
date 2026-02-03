# Audit: Contribution Metrics & Slash Commands (Mentor Visibility)

This document audits existing contribution metrics and Discord slash commands, validates behavior, and records any additive changes. All changes are read-only, informational, and non-breaking.

---

## FEATURE AREA A — Contribution Metrics

### STEP 1 — Audit Existing Metrics

#### 1.1 Data sources

| Source | Location | Contents |
|--------|----------|----------|
| **contributions table** | `SqliteStorage` / `state.db` | `github_user`, `event_type`, `repo`, `created_at`, `payload_json`. Append-only INSERTs. |
| **scores table** | same | `github_user`, `period_start`, `period_end`, `points`, `updated_at`. Upsert per (user, period). |
| **ContributionSummary** | `storage.list_contribution_summaries(period_start, period_end, weights)` | Aggregated counts and weighted score per user for a time window. |

**Event types stored (GitHub ingestion):**

| event_type | Meaning | Ingested in |
|------------|---------|-------------|
| `issue_opened` | User opened an issue | `adapters/github/rest.py` `_issue_events` |
| `issue_closed` | User closed an issue | same |
| `pr_opened` | User opened a PR | `_collect_pull_request_events` |
| `pr_merged` | User's PR was merged | same |
| `pr_reviewed` | User submitted a PR review | `_pull_request_reviews` |
| `comment` | User commented (issue or PR) | `_ingest_issue_comments`, `_ingest_pr_comments` |
| `issue_assigned` | User was assigned to issue | `_issue_assignment_events` |

**Time window:** Single config-driven window: `config.scoring.period_days` (default 30). Period is `[period_end - period_days, period_end]` with `period_end = now()` at run time.

#### 1.2 Target metrics — classification

| Metric | Status | Where | Notes |
|--------|--------|--------|--------|
| **PR count per user** | **Partially implemented** | `ContributionSummary.prs_opened`; raw events in `contributions` | Summary lumps `pr_opened` and `pr_merged` into one count. Raw events distinguish them; no separate "opened" vs "merged" in reports. |
| **Review participation** | **Implemented** | `ContributionSummary.prs_reviewed`; `event_type == "pr_reviewed"` | Counts reviews submitted (any state). Shown in audit report table as "reviews" column. |
| **Issue engagement score** | **Partially implemented** | `ContributionSummary`: `issues_opened`, `comments`, `total_score` | `total_score` is config-weighted (e.g. issue_opened: 3, comment: 1). No separate, documented "issue engagement" formula (e.g. 1 per issue, 0.5 per comment). |
| **Simple rankings (weekly / monthly)** | **Partially implemented** | `audit.md` "Contribution Summary (Last N days)" table; `get_scores()` ordered by `points DESC` | One window only (config `period_days`). No optional 7-day vs 30-day ranking view or dedicated ranking output. |

#### 1.3 Where metrics are used

- **Role scoring:** `WeightedScoreStrategy.compute_scores(contributions, period_end)` uses `config.scoring.weights`; results stored in `scores` and used for Discord roles and GitHub assignments.
- **Reports:** `write_reports()` uses `list_contribution_summaries()` for the Contribution Summary table in `audit.md`. `write_activity_report()` shows PR/issue events per repo (read-only feed).
- **Audit:** `audit.json` has `discord_role_plans` and `github_assignment_plans`; no separate metrics export.

---

### STEP 2 — Validation (Existing Metrics)

- **Identity:** Engine uses `_resolve_identity_mappings(storage, config)`: verified links from storage preferred; config fallback. Only verified (or config) mappings used for scoring and role plans. No unverified storage rows in output.
- **Determinism:** Scores and reports use sorted ordering (users, plans). Same inputs → same outputs.
- **Persisted GitHub activity:** Scores and summaries are computed from `contributions` (and `list_contributions` / `list_contribution_summaries`). No scoring from ephemeral data.
- **Edge cases (documented, not changed):**
  - User with PRs but no reviews: `prs_reviewed == 0`; they still get PR authorship in `prs_opened` and in activity feed.
  - User with reviews but no PRs: `prs_opened == 0`, `prs_reviewed` > 0; score uses `pr_reviewed` weight.
  - User with issue comments only: `comment` events; `comments` count and weight in `total_score`; no issue engagement formula applied separately.

---

### STEP 3 — Additions (Metrics, Additive Only)

Additions are **read-only**, use **existing tables and APIs**, and introduce **no schema or config changes**.

**Implementation:** `src/ghdcbot/engine/metrics.py`

1. **PR count per user (opened vs merged)**  
   - **Gap:** Summary only exposes combined `prs_opened`.  
   - **Addition:** `get_contribution_metrics(storage, period_start, period_end, weights)` aggregates from `storage.list_contributions(since)` filtered to `[period_start, period_end]`; returns `UserMetrics` per user with `prs_opened`, `prs_merged`, `reviews_submitted`, `issues_opened`, `comments`, `issue_engagement`, `total_score`. Used by `/summary`.  
   - **Label:** e.g. "PRs opened: 3, merged: 2 (last 7 days)".

2. **Review participation**  
   - **No change.** Already implemented and attributed to GitHub user. New output uses wording "Reviews submitted" (e.g. in `format_metrics_summary`).

3. **Issue engagement score**  
   - **Formula (documented in code):** `issues_opened * 1.0 + comments * 0.5` (constants `ISSUE_ENGAGEMENT_ISSUE_WEIGHT`, `ISSUE_ENGAGEMENT_COMMENT_WEIGHT`). Non-competitive, informational only.  
   - **Addition:** Computed in `UserMetrics.issue_engagement`; displayed as "Issue engagement (informational): X" in `/summary`.

4. **Simple rankings (weekly / monthly)**  
   - **Addition:** `rank_by_activity(metrics)` and `get_rank_for_user(ranked, github_user)`; used in `/summary` as "Top contributors by activity (last 30 days): you're #N". No "leaderboard" or "winners" wording.

---

## FEATURE AREA B — Slash Commands

### STEP 4 — Audit Existing Commands

| Command | Status | Current behavior | Side effects |
|---------|--------|------------------|--------------|
| **/link** | **Implemented** | Create identity claim; show verification code and instructions | Writes claim to `identity_links` (user-initiated). |
| **/verify-link** | **Implemented** | Verify GitHub link using code in bio/gist | Updates `identity_links` (verified_at); user-initiated. |
| **/verify** | **Not implemented** | — | — |
| **/status** | **Not implemented** | — | — |
| **/summary** | **Not implemented** | — | — |
| **/unlink** | **Not implemented** | — | Not designed; not added per constraints. |

**Implementation location:** `src/ghdcbot/bot.py` (Discord bot with `link`, `verify-link` only).

---

### STEP 5 — Additions (Commands, Additive Only)

New commands are **read-only or explicitly user-initiated**, and **do not trigger role changes or modify contribution data**.

**Implementation:** `src/ghdcbot/bot.py`. Optional storage method: `get_identity_links_for_discord_user(discord_user_id)` on `SqliteStorage` (not on Storage Protocol).

1. **/verify**  
   - **Behavior:** Show verification status for the invoking user. Does not re-verify.  
   - **Output:** Linked (verified) → "Linked to GitHub: **username**". Pending claim → "Pending: link to **github_user** (expires: …). Run /verify-link to complete." Not linked → "Not linked. Use /link to start."  
   - **Implementation:** Uses `storage.get_identity_links_for_discord_user(discord_user_id)` when available; else falls back to `list_verified_identity_mappings` filtered by discord_user_id.

2. **/status**  
   - **Behavior:** Show verification state, activity window (from config), and the user's Discord roles.  
   - **Output:** "Activity window: last N days" + "Linked GitHub: …" + "Your roles: …" (from `discord_reader.list_member_roles()`).  
   - **Implementation:** Bot builds Discord adapter (same as config) for read-only `list_member_roles()`; no writes.

3. **/summary**  
   - **Behavior:** Show contribution metrics for the invoking user (if linked), for last 7 and last 30 days.  
   - **Output:** Per-window: PRs opened/merged, reviews submitted, issues opened/comments, issue engagement; then "Top contributors by activity (last 30 days): you're #N." If not linked: "Link your account with /link and /verify-link to see your summary."  
   - **Implementation:** Resolve GitHub user from verified link; call `get_contribution_metrics(storage, start, now, config.scoring.weights)` for 7- and 30-day windows; `rank_by_activity` and `get_rank_for_user` for ranking. No writes.

4. **/unlink**  
   - **Not added.** Only if already designed or explicitly enabled; current codebase has no unlink design.

---

## Confirmation Checklist

- [x] No existing behavior changed (scoring, reports, identity, role application unchanged).
- [x] No storage schema changes (no new tables).
- [x] No changes to Storage Protocol or existing method contracts.
- [x] New metrics are derived from existing contributions/summaries; no new required config.
- [x] New commands are read-only or user-initiated (link/verify-link unchanged); no role or contribution writes from /verify, /status, /summary.
- [x] All additions are additive and informational; wording is contextual and non-gamified.

---

## Summary

| Area | Before | After |
|------|--------|--------|
| PR opened vs merged | Only combined count in summary | Optional read-only breakdown (e.g. "PRs opened: X, merged: Y") from existing events. |
| Review participation | Already counted and reported | Unchanged; any new output labels "Reviews submitted". |
| Issue engagement score | Only config-weighted total_score | Optional documented formula (1×issues + 0.5×comments) in metrics helper. |
| Rankings | Single-window table in audit.md | Optional time-scoped "Top contributors by activity (last N days)" in new surfaces only. |
| /verify, /status, /summary | Not present | Added as informational/read-only; /link and /verify-link unchanged. /unlink not added. |

Existing metrics, scoring, and commands remain valid and unchanged.
