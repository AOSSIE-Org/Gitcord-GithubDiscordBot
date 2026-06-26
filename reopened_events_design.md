# Reopened Events Design (Week 5 Day 3)

## Overview

This document describes the ingestion and notification of **Issue Reopened** and **PR Reopened** events to complete the GitHub lifecycle notification coverage in Gitcord.

## Event Types

### `issue_reopened`

**Source:** GitHub issue timeline events (event type: `"reopened"`)

**Ingestion Logic:**
- Fetch issue timeline for each issue via `GET /repos/{owner}/{repo}/issues/{issue_number}/timeline`
- Filter for events with `event == "reopened"` and `created_at >= since`
- Extract assignee and reopened timestamp

**Payload:**
```python
{
    "issue_number": 123,
    "title": "Improve onboarding",
    "assignee": "alice",  # Current assignee at reopened time
    "repository": "Gitcord",
    "html_url": "https://github.com/AOSSIE/Gitcord/issues/123",
    "reopened_at": "2024-06-26T10:30:00Z",
}
```

**Recipient:** Current assignee (skip if unassigned)

**Target GitHub User:** `payload.assignee`

**Config Flag:** `notifications.issue_reopened` (default: `True`)

**Deduplication:** `issue_reopened:{repo}:{issue_number}:{assignee}`

### `pr_reopened`

**Source:** GitHub PR timeline events (event type: `"reopened"`)

**Ingestion Logic:**
- Fetch PR timeline for each PR via `GET /repos/{owner}/{repo}/pulls/{pr_number}/timeline`
- Filter for events with `event == "reopened"` and `created_at >= since`
- Extract PR author

**Payload:**
```python
{
    "pr_number": 456,
    "title": "Improve onboarding validation",
    "pr_author": "bob",
    "repository": "Gitcord",
    "html_url": "https://github.com/AOSSIE/Gitcord/pull/456",
    "reopened_at": "2024-06-26T11:00:00Z",
}
```

**Recipient:** PR author

**Target GitHub User:** `payload.pr_author`

**Config Flag:** `notifications.pr_reopened` (default: `True`)

**Deduplication:** `pr_reopened:{repo}:{pr_number}:{pr_author}`

## Implementation Details

### 1. Timeline Event Fetching

Both issues and PRs support the timeline API endpoint:
- Issues: `GET /repos/{owner}/{repo}/issues/{issue_number}/timeline`
- Pull Requests: `GET /repos/{owner}/{repo}/pulls/{pr_number}/timeline` (requires `is_pull_request` check)

The timeline returns events like:
```json
{
  "event": "reopened",
  "created_at": "2024-06-26T10:30:00Z",
  "actor": {
    "login": "someone"
  },
  "pull_request": {...} or "issue": {...}
}
```

### 2. Assignee Resolution

For `issue_reopened`:
- The assignee at the time of the reopened event is extracted from GitHub API issue data
- If no assignee, notification is skipped (unassigned issues)

For `pr_reopened`:
- The PR author is from `pr.user.login`
- This is immutable and always available

### 3. Notification Dispatch

Both events follow the same notification dispatch pattern:
1. Check config flag is enabled
2. Resolve target GitHub user to Discord ID (verified users only)
3. Deduplicate by key
4. Build message from template
5. Send via DM or channel (based on config)

### 4. Message Templates

#### Issue Reopened
```
📌 **Issue Reopened**

Issue #123 "Improve onboarding"

assigned to you has been reopened.

**Repository:** AOSSIE/Gitcord

Please review the latest discussion and continue working if needed.

**Link:** https://github.com/AOSSIE/Gitcord/issues/123
```

#### PR Reopened
```
🔄 **PR Reopened**

Your PR #456 "Improve onboarding validation"

has been reopened.

**Repository:** AOSSIE/Gitcord

Please review the discussion and continue updating your PR.

**Link:** https://github.com/AOSSIE/Gitcord/pull/456
```

## Files to Modify

1. **`src/ghdcbot/config/models.py`**
   - Add `issue_reopened: bool = True`
   - Add `pr_reopened: bool = True` to `NotificationConfig`

2. **`config/example.yaml`**
   - Uncomment/add `issue_reopened: true`
   - Uncomment/add `pr_reopened: true`

3. **`config/aussie.yaml`**
   - Add `issue_reopened: true`
   - Add `pr_reopened: true`

4. **`src/ghdcbot/adapters/github/rest.py`**
   - Add `_issue_reopened_events()` method (similar to `_issue_assignment_events()`)
   - Add `_pr_reopened_events()` method (similar pattern)
   - Call these methods from `_issue_events()` and `_collect_pull_request_events()`

5. **`src/ghdcbot/engine/notifications.py`**
   - Add `issue_reopened` and `pr_reopened` to `event_config_map`
   - Add templates for both event types in `_build_notification_message()`

6. **`tests/test_notifications.py`**
   - `test_send_notification_issue_reopened()`
   - `test_send_notification_pr_reopened()`
   - `test_issue_reopened_disabled()`
   - `test_pr_reopened_disabled()`
   - `test_issue_reopened_dedupe()`
   - `test_pr_reopened_dedupe()`

7. **`tests/test_github_ingestion_lifecycle_events.py`**
   - `test_issue_reopened_emitted_when_reopened()`
   - `test_pr_reopened_emitted_when_reopened()`

8. **Documentation**
   - Update `notification_flow_notes.md`
   - Create `event_coverage.md` if needed

## Success Criteria

✓ `issue_reopened` timeline events are fetched and ingested  
✓ `pr_reopened` timeline events are fetched and ingested  
✓ Notifications are sent to assignees for reopened issues  
✓ Notifications are sent to PR authors for reopened PRs  
✓ Config flags allow independent control  
✓ All tests pass  
✓ Documentation is updated
