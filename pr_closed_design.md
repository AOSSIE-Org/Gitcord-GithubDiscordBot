# PR Closed Notification Design (Week 5 Day 2)

## Ingestion source

`GitHubRestAdapter._collect_pull_request_events()` in `rest.py` paginates
`/repos/{owner}/{repo}/pulls` with `state=all`.

## Existing PR event logic

| Event | Condition | `github_user` |
|-------|-----------|---------------|
| `pr_opened` | `created_at >= since` | PR author |
| `pr_merged` | `merged_at >= since` | PR author |
| `pr_reviewed` | review `submitted_at >= since` | reviewer (notify author via payload) |

## New `pr_closed` event

Emit when **all** of the following hold:

1. `merged_at` is not in the `since` window (handled by `elif` after the merge branch)
2. `state == "closed"`
3. `merged_at` is null (not merged)
4. `closed_at >= since`

Merged PRs take the existing `pr_merged` branch first, so a merged close never emits `pr_closed`.

## Payload

```yaml
pr_number: int
pr_title: str
title: str          # alias for templates / consistency with other PR events
pr_author: str      # notification routing target
repository: str     # repo short name
html_url: str
closed_at: str      # ISO 8601 from GitHub
```

## Notification layer

| Step | Value |
|------|-------|
| Config flag | `notifications.pr_closed` (default `true`) |
| Recipient | `payload.pr_author` |
| Template key | `pr_closed` |
| Orchestrator filter | include `pr_closed` alongside `pr_merged`, `pr_reviewed`, `issue_assigned` |
| Dedupe | `pr_closed:{repo}:{pr_number}:{target_user}` |

## Non-goals (Day 2)

- `pr_reopened` / `issue_reopened` (timeline APIs — Day 3+)
- Notifying on PRs closed before the ingestion `since` cursor
