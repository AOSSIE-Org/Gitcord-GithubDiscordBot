# Live PR DM lifecycle cards

Gitcord keeps one verified-only Discord DM per Pull Request instead of sending a
new message for every milestone. The first milestone creates the card; later
milestones edit the same message in place.

## What the card shows

- Current status badge: ⏳ In Review, ⚠️ Changes Requested, ✅ Approved,
  🟣 Merged, 🔴 Closed, or 🔄 Reopened.
- A lifecycle breadcrumb line.
- Repository, PR title (linked), and author.
- An incremental activity timeline (`by` the reviewer/merger and when).

Tracked message IDs are stored in the `pr_dm_messages` SQLite table as
`(repo, pr_number, discord_user_id, channel_id, message_id)`. If Discord no
longer has the tracked message (for example, the user deleted it), Gitcord
posts a fresh card and updates the mapping.

Channel announcements are unchanged and keep using their own tracking tables.

## Post-merge cleanup (opt-in)

Cleanup is **off by default**. To delete merged PR cards after a retention
window, enable it under `discord.notifications`:

```yaml
discord:
  notifications:
    enabled: true
    enable_merged_dm_cleanup: true   # default: false
    merged_dm_cleanup_hours: 24      # default: 24
```

The documented uppercase spelling is also accepted:

```yaml
    ENABLE_MERGED_DM_CLEANUP: true
    MERGED_DM_CLEANUP_HOURS: 24
```

The same names work as environment variables (for example in `.env`):

```dotenv
ENABLE_MERGED_DM_CLEANUP=true
MERGED_DM_CLEANUP_HOURS=24
```

Cleanup runs once per sync after notifications. It only removes cards whose PR
status is `merged` and whose merge time is older than the retention window.
Rows are kept when Discord deletion fails so a later sync can retry; a `404`
(message already gone) is treated as a successful cleanup.

In `dry-run` and `observer` modes nothing is deleted. Intended deletions are
logged and appended to `audit_events.jsonl` as `pr_dm_cleanup` with
`dry_run: true`.
