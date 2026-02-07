# Pre-push audit summary

**Date:** 2026-02-07

## 1. Test suite
- **Result:** 161 tests passed, 1 unrelated warning (audioop deprecation in discord.py).
- **Command:** `PYTHONPATH=src python -m pytest tests/ -v`
- **Relevant tests:** `test_issue_request_flow.py` (21), `test_issue_assignment_from_discord.py`, others unchanged.

## 2. Linting
- **Result:** No linter errors in `bot.py`, `issue_request_flow.py`, `sqlite.py`, `config/models.py`.

## 3. Feature scope (issue request & mentor flow)
- **Contributor:** `/request-issue <url>` — validate URL, org, open issue, verified identity; store request; audit `issue_request_created`.
- **Storage:** `issue_requests` table; `insert_issue_request`, `list_pending_issue_requests`, `get_issue_request`, `update_issue_request_status`.
- **Mentor Step 1:** `/issue-requests` → repo selection embed + select menu (no GitHub calls, no mutations).
- **Mentor Step 2:** On repo select → audit `issue_request_viewed_repo`; show only that repo’s requests; Approve / Replace / Reject / Back to Repo List.
- **Audit:** `issue_request_viewed_repo`, `issue_request_approved`, `issue_request_rejected`, `issue_request_reassigned` with repo, issue_number, mentor_discord_id, contributor_discord_id, timestamp.
- **Safety:** Re-validation on every button click; MutationPolicy respected; no writes in dry-run/observer.

## 4. Files changed (for commit)
- `config/example.yaml` — `issue_request_eligible_roles` documented.
- `src/ghdcbot/adapters/storage/sqlite.py` — issue_requests schema and methods.
- `src/ghdcbot/bot.py` — `/request-issue`, `/issue-requests` (repo selection), `RepoSelectView`, `IssueRequestReviewView` (with Back).
- `src/ghdcbot/config/models.py` — `issue_request_eligible_roles` on AssignmentConfig.
- `src/ghdcbot/engine/issue_request_flow.py` — grouping, eligibility, embed builders, repo selection embed.
- `tests/test_issue_request_flow.py` — tests for flow and repo selection.
- Other project files: `adapters/github/rest.py`, `engine/issue_assignment.py`, `engine/pr_context.py`, `tests/test_issue_assignment_from_discord.py`, `tests/test_pr_context.py` (as applicable).

## 5. Excluded from commit
- `bot.log` — runtime log; do not commit.
- `config/shubh-olrd.yaml` — local/user config; keep untracked unless intended for repo.
