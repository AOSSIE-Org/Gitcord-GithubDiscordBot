# Notification Flow Notes (Week 5 Day 1)

Architecture review of verified-only GitHub → Discord notifications before adding `pr_review_comment`.

## Entry point

`Orchestrator.run_once()` fetches contributions from GitHub, then calls `_send_notifications_for_new_events()` when `discord.notifications.enabled` is true.

**Orchestrator filter** (`orchestrator.py`): only these ingested event types are passed to the notification layer:

- `issue_assigned`
- `pr_reviewed`
- `pr_merged`

Other configured types (e.g. `pr_review_requested`) are not in this filter yet.

## Core function

`send_notification_for_event()` in `notifications.py`:

1. Check `config.enabled`
2. Map event type → config flag and resolve **target GitHub user** (who receives the DM/channel message)
3. Resolve target via `storage.list_verified_identity_mappings()` (must be linked + verified)
4. Build dedupe key → skip if already sent
5. Build message → send via DM or `channel_id`
6. Mark sent + append audit event

## Event flows

### `issue_assigned`

| Step | Behavior |
|------|----------|
| Target | `event.github_user` (assignee) |
| Config flag | `notifications.issue_assignment` |
| Template key | `issue_assigned` |
| Dedupe | `issue_assigned:{repo}:{issue_number}:{target_user}` |

### `pr_reviewed` (APPROVED / CHANGES_REQUESTED / COMMENT)

| Step | Behavior |
|------|----------|
| Target | `payload.pr_author` (not the reviewer in `event.github_user`) |
| Config flags | `pr_review_result` (APPROVED, CHANGES_REQUESTED); `pr_review_comment` (COMMENT) |
| Template keys | `pr_approved`, `pr_changes_requested`, `pr_review_comment` |
| Dedupe | `pr_reviewed:{repo}:{pr_number}:{target_user}:{review_id}:{state}` |

Ingestion already emits `pr_reviewed` with `state`, `review_id`, `pr_author`, `pr_number`, and `title` in the payload.

### `pr_merged`

| Step | Behavior |
|------|----------|
| Target | `event.github_user` (PR author) |
| Config flag | `notifications.pr_merged` |
| Template key | `pr_merged` |
| Dedupe | `pr_merged:{repo}:{pr_number}:{target_user}` |

## Deduplication

- Storage methods: `was_notification_sent(dedupe_key)`, `mark_notification_sent(...)`
- Keys are case-insensitive on GitHub username (lowercased)
- PR reviews include `review_id` + `state` so the same review is not notified twice, but different states on the same review id are distinct keys

## Dry-run / observer

`_send_discord_notification()` returns false when `policy.allow_discord_mutations` is false (no actual Discord write).

## Week 5 Day 1 change

Enable `COMMENT` state on `pr_reviewed` events behind `notifications.pr_review_comment`, notifying the PR author with a new template.
