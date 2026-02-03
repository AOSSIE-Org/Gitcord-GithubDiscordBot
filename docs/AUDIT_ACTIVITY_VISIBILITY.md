# Audit: Activity Visibility (Read-Only, Mentor Visibility)

This document audits the four requested activity-visibility features, validates what exists, and records what was added. All enhancements are additive and read-only; no existing APIs, schemas, or behavior were changed.

---

## STEP 1 — Audit Existing Implementation

### 1. PR opened / merged notifications

| Question | Answer |
|----------|--------|
| **Implemented?** | **Partially implemented** (data only). |
| **Where it lives** | **Data:** `src/ghdcbot/adapters/github/rest.py` — `_collect_pull_request_events()` emits `ContributionEvent` with `event_type="pr_opened"` or `"pr_merged"`, with payload `pr_number`, `title`, `created_at`/`merged_at`. **Storage:** events stored in `contributions` table via `record_contributions()`. **Visibility:** No dedicated “PR notifications” product. `audit.md` has a “Contribution Summary” table with **aggregate counts** (e.g. “PRs opened” per user), not a feed of “PR #5 opened by alice in repo X”. |
| **Current behavior** | PR opened/merged events are ingested, stored, and used for scoring and contribution summaries. There is no batched “N PRs opened/merged” notification (file or Discord). |
| **Safe/correct?** | Yes. Ingestion and storage are correct; payload has author, title, repo, link-building info. |

---

### 2. Issue opened / closed notifications

| Question | Answer |
|----------|--------|
| **Implemented?** | **Partially implemented** (data only). |
| **Where it lives** | **Data:** `src/ghdcbot/adapters/github/rest.py` — `_issue_events()` yields `ContributionEvent` with `event_type="issue_opened"` or `"issue_closed"`, payload from `_issue_payload(issue)` (issue_number, title, state, labels). **Storage:** same `contributions` table. **Visibility:** Again only aggregate counts in “Contribution Summary”; no issue-level feed. Issues that are pull requests are excluded in `_list_repo_open_issues()` (skip when `"pull_request" in issue`). |
| **Current behavior** | Issue opened/closed events are ingested and stored; used for scoring and contribution summaries. No per-issue notification or feed. |
| **Safe/correct?** | Yes. PRs are excluded from issue lists; event data is correct. |

---

### 3. Commit summaries (daily / weekly)

| Question | Answer |
|----------|--------|
| **Implemented?** | **Not implemented.** |
| **Where it lives** | N/A. No commit ingestion in the GitHub adapter; no `event_type="commit"`; no commit aggregation. |
| **Current behavior** | N/A. |
| **Safe/correct?** | N/A. |

**Note:** Adding commit summaries would require: (1) optional commit ingestion in the GitHub REST adapter (e.g. `GET /repos/{owner}/{repo}/commits?since=...`), (2) storing as contribution events (existing schema supports any `event_type`), (3) excluding commits from scoring unless a new weight is added (current code uses `weights.get(event_type, 0)`, so `"commit"` would be 0 by default). This is left as a future, additive enhancement.

---

### 4. Repo-wise activity feed (in Discord or file)

| Question | Answer |
|----------|--------|
| **Implemented?** | **Not implemented** (before this audit). |
| **Where it lives** | **Data:** Same `contributions` table; events have `repo`, `event_type`, `created_at`, `payload`. **Output:** No repo-wise feed. Reports are `audit.json` and `audit.md` (summary counts, role plans, assignment plans). **Discord:** The Discord adapter has no “send message to channel”; it only lists members/roles and adds/removes roles. |
| **Current behavior** | No single message or section “per repo” summarizing PRs opened/merged, issues opened/closed, etc. No Discord channel posting. |
| **Safe/correct?** | N/A. |

---

## STEP 2 — Validation (Existing Features)

- **Contribution ingestion:** PR and issue events are fetched with correct filters (since cursor, repo filter). Stored with UTC timestamps. Scoring and contribution summaries use the same stored events; identity used for role/assignment is from verified mappings or config (no unverified leak in plans).
- **Contribution Summary in audit.md:** Renders per-user counts (issues, PRs, reviews, comments, score) for the scoring period. Read-only; accurate for the data in the period. Does not list individual PRs/issues; mentor sees aggregates only.
- **Edge case:** “Exclude pull requests masquerading as issues” — already done: `_list_repo_open_issues` skips issues that have `"pull_request"` in the payload.

**Gaps (documented, not “fixed” as behavior):**  
- No per-event or per-repo feed for PR/issue activity.  
- No commit data.  
- No Discord channel output for activity.

---

## STEP 3 — Additions Made (Additive Only)

All of the following are additive. No existing APIs, data models, storage schemas, or CLI/Discord commands were changed. No new required config; optional Discord posting is behind an optional config key.

### 3.1 Activity feed (file-based, read-only)

- **New:** `src/ghdcbot/engine/reporting.py`
  - **`build_activity_feed_markdown(events, period_start, period_end, org)`**  
    Filters `ContributionEvent` list to `period_start <= created_at <= period_end`, groups by `repo`, then by event type. Renders:
    - **PRs opened:** title, author, repo, link (using `config.github.org`).
    - **PRs merged:** same.
    - **Issues opened:** issue number, title, author, repo, link.
    - **Issues closed:** same.
    One section per repo; batched (e.g. “3 PRs opened”) with short detail lines. Mentor-friendly, low noise.
  - **`write_activity_report(storage, period_start, period_end, config)`**  
    Calls `storage.list_contributions(period_start)`, filters events to `[period_start, period_end]`, builds markdown via `build_activity_feed_markdown`, writes **`<data_dir>/reports/activity.md`**. Uses existing `Storage` interface only; no schema change.
- **Orchestrator:** Inside the existing `if policy.mode in {RunMode.DRY_RUN, RunMode.OBSERVER}` block, after `write_reports(...)`, calls **`write_activity_report(self.storage, period_start, period_end, self.config)`**. Same run cycle; no new commands or scheduling.

Result: **PR opened/merged** and **issue opened/closed** visibility (and **repo-wise activity feed**) are now available as a read-only file report. Commits are not included (no commit ingestion).

### 3.2 Optional Discord activity notification

- **New optional config:** `DiscordConfig` in `src/ghdcbot/config/models.py`  
  - **`activity_channel_id: str | None = None`**  
  If set, the orchestrator may post a short activity summary to that channel after writing reports. Optional; not required.
- **New adapter method (optional use):** `src/ghdcbot/adapters/discord/api.py`  
  - **`send_message(channel_id: str, content: str) -> bool`**  
  POST to `/channels/{channel_id}/messages` with content (max 2000 chars). Returns True on success. Only used when `activity_channel_id` is set.
- **Orchestrator:** After `write_activity_report`, if `config.discord.activity_channel_id` is set, builds a short summary (e.g. first 1900 chars of the activity feed or a one-line “Activity report: N PRs, M issues …”) and calls `discord_writer.send_message(...)`. No message is sent if the config is unset; existing behavior unchanged.

Config loader and validation: `activity_channel_id` is optional (default None); existing configs remain valid.

### 3.3 Commit summaries

- **Not added.** Documented in this audit as “Not implemented”. Can be added later as optional commit ingestion + optional inclusion in the activity feed, without changing existing behavior.

---

## Confirmation Checklist

- [x] No existing behavior changed (reports, scoring, role/assignment logic unchanged).
- [x] No contracts broken (Storage, GitHub/Discord adapters: existing methods unchanged; new methods or optional parameters only).
- [x] No storage schema changes.
- [x] No new required config; Discord activity posting is opt-in via `activity_channel_id`.
- [x] Additions are read-only (observe and report; no actions, automation, or side effects beyond writing a file and optionally posting one message).
- [x] Reuses existing abstractions (Storage.list_contributions, existing report dir, existing run_once cycle).
- [x] Identity: activity feed lists GitHub users and events only; it does not expose Discord IDs or rely on identity mapping for the feed content. Verified identity is only used where it already was (role/assignment planning).

---

## Summary

| Feature | Before | After |
|--------|--------|--------|
| PR opened/merged notifications | Data only; no feed | **File:** `reports/activity.md` with PR opened/merged per repo. **Optional:** Discord message if `activity_channel_id` set. |
| Issue opened/closed notifications | Data only; no feed | **File:** same `activity.md` with issues opened/closed per repo. **Optional:** Discord. |
| Commit summaries | Not implemented | Still not implemented (documented for future work). |
| Repo-wise activity feed | Not implemented | **File:** `activity.md` is repo-wise. **Optional:** one Discord summary when `activity_channel_id` set. |

All additions are additive and safe; no breaking changes.
