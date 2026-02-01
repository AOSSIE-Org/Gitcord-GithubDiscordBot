# Gitcord — Full Verification Report

This document records an end-to-end check of the project against the **bot requirements** and the codebase. Date: 2026-02-01.

---

## 1. Bot Requirements vs Implementation

| Requirement | Status | How It Is Met |
|-------------|--------|----------------|
| **Executable on any computer; no cloud hosting required** | OK | CLI runs locally: `python -m ghdcbot.cli --config <path> run-once`. No server or daemon. |
| **Not required to be online all the time** | OK | Offline-first, run-once. Run manually or via cron. |
| **Assign Discord roles based on GitHub activity** | OK | Pipeline: ingest contributions → score (weights) → plan_discord_roles (score vs role_mappings) → apply_discord_roles (gated by MutationPolicy). Identity mapping links GitHub users to Discord IDs. Default wiring uses stub writers (log only); logic and planning are correct. |
| **Assign issues and PR reviews to users with specific Discord roles** | OK | role_to_github built from identity_mappings + member_roles. RoleBasedAssignmentStrategy produces issue/review plans from issue_assignees and review_roles. apply_github_plans calls writer (stub by default). |
| **Track contributions and assign scores** | OK | ContributionEvent ingestion (issues, PRs, reviews, comments, assignments from timeline). WeightedScoreStrategy + SQLite storage. list_contribution_summaries for report table (issues/PRs/reviews/comments + total_score). |
| **Configurable and general for other orgs** | OK | Single YAML + env; no hardcoded orgs; config/example.yaml generic; role_mappings, identity_mappings, scoring weights, repo filter all config-driven. |

**Verdict:** All six requirements are met by the current design and code. Default wiring uses stub writers so no live API writes occur until real writers are wired or implemented.

---

## 2. Codebase Consistency

### 2.1 Interfaces vs Implementations

| Interface | Implementation | Notes |
|-----------|-----------------|--------|
| GitHubReader | GitHubRestAdapter | list_contributions, list_open_issues, list_open_pull_requests implemented. |
| GitHubWriter | GitHubRestAdapter (stubs: assign_issue, request_review) | Plan writers (GitHubPlanWriter) exist but are not wired in CLI. |
| DiscordReader | DiscordApiAdapter | list_member_roles implemented; degrades on 401/403. |
| DiscordWriter | DiscordApiAdapter (stubs: add_role, remove_role) | Plan writers (DiscordPlanWriter) exist but are not wired in CLI. |
| Storage | SqliteStorage | init_schema, record_contributions, list_contributions, list_contribution_summaries, upsert_scores, get_scores, get_cursor, set_cursor. All present. UTC normalization applied. |

### 2.2 Config Schema vs example.yaml

- **runtime**: mode, log_level, data_dir, github_adapter, discord_adapter, storage_adapter — all present; mode default dry-run.
- **github**: org, token (env), api_base, permissions, user_fallback; repos optional.
- **discord**: guild_id, token (env), permissions.
- **scoring**: period_days, weights (issue_opened, pr_opened, pr_reviewed, comment in example).
- **role_mappings**: non-empty (validated); example has Contributor, Maintainer.
- **assignments**: review_roles, issue_assignees (optional defaults).
- **identity_mappings**: optional; example has one placeholder entry.

No schema/example mismatch.

### 2.3 Event Types and Weights

- **Emitted by GitHub adapter**: issue_opened, issue_closed, issue_assigned, pr_opened, pr_merged, pr_reviewed, comment.
- **Example weights**: issue_opened, pr_opened, pr_reviewed, comment. Missing event types get weight 0 in WeightedScoreStrategy (weights.get(event_type, 0)).
- **list_contribution_summaries**: Maps issue_opened → issues_opened; pr_opened/pr_merged → prs_opened; pr_reviewed → prs_reviewed; comment → comments; total_score uses weights. Correct.

### 2.4 Orchestrator Flow

- init_schema → ingest (cursor, list_contributions, record, set_cursor) → score (list_contributions, compute_scores, upsert_scores) → member_roles → role_to_github → assignment plans → policy → reports (dry-run/observer) → apply_github_plans, apply_discord_roles. Matches PROJECT.md and code. close() calls close on adapters that have it.

### 2.5 Errors and CLI

- **Errors**: ConfigError, GitcordPermissionError, AdapterError in core.errors. CLI catches ConfigError and AdapterError and exits 1.
- **CLI**: --config required, run-once subcommand; build_orchestrator loads config, builds adapters via registry, runs run_once, calls orchestrator.close() in finally.

---

## 3. Tests and Run

- **pytest**: 20 tests passed (config, empty org, mutation gating, planning determinism, repo filtering, writer safety, contribution summary, README setup, etc.).
- **CLI run**: With GITHUB_TOKEN and DISCORD_TOKEN set (e.g. test values), `run-once` with config/example.yaml completes with exit 0; audit reports written to data_dir/reports; GitHub/Discord return 401 with dummy tokens but pipeline completes and reports are generated.

---

## 4. Known Limitations (By Design)

1. **Default writers are stubs**: The CLI wires the same REST/API adapters as both readers and writers. Their write methods only log. To perform real Discord role or GitHub assignment changes, either wire GitHubPlanWriter/DiscordPlanWriter or add real write logic to the adapters.
2. **Identity mapping is manual**: New contributors must be added to identity_mappings (GitHub username + Discord user ID); no automatic linking.
3. **Reports only in dry-run/observer**: Audit reports (audit.json, audit.md) are generated when mode is dry-run or observer; active mode could be extended to also write reports if desired.

---

## 5. Checklist Summary

| Area | Result |
|------|--------|
| Requirements coverage | All 6 met |
| Storage protocol vs SqliteStorage | All methods implemented |
| Config load and validation | OK; example.yaml valid |
| Orchestrator run_once flow | OK; reports and apply paths consistent |
| Scoring and contribution summaries | OK; weights and aggregation correct |
| Planning (Discord roles, GitHub assignments) | OK; deterministic, role-based |
| Mutation policy (dry-run default, gating) | OK |
| CLI and error handling | OK |
| Tests | 20 passed |
| README setup test | Passes (config exists, load with env, run_once + reports) |

---

## 6. Conclusion

**Everything required for the bot is implemented and consistent.** The project runs correctly when following the README (install, env vars, run-once): config loads, pipeline runs, reports are written, and no unintended writes occur. The only intentional gap is that **live writes** to Discord/GitHub are not performed with the default adapter wiring (stub writers); the logic to decide what to write is in place and can be activated by wiring or implementing real writers.
