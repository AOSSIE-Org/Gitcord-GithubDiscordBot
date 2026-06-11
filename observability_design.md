# Observability Design — Gitcord Sync Logging

> **Status:** Implemented (Week 3 Day 4).

---

## Current State (before)

- `JsonFormatter` emitted only `ts`, `level`, `logger`, `message`
- `extra={"repo": "..."}` fields were **dropped** from JSON output
- No correlation ID across a single `run-once` or `/sync` execution
- Limited visibility into cursor movement, API volume, per-repo timing, or event-type breakdown

---

## Improvements

| Feature | Purpose |
|---------|---------|
| **JsonFormatter fix** | Preserve all custom `extra` fields in JSON logs |
| **sync_id** | Correlate every log line from one sync run |
| **Sync lifecycle events** | `github_sync_started` / `completed` / `failed` |
| **API request counting** | `github_request_summary` per sync |
| **Repo-level logs** | `repo_ingestion_started` / `completed` with timing |
| **Event summary** | `github_event_summary` with counts by event type |
| **Cursor visibility** | `cursor_before` / `cursor_after` on sync lifecycle logs |

---

## sync_id

Format: `sync_YYYYMMDD_HHMMSS_ab12`

Generated at the start of each `Orchestrator.run_once()` (CLI `run-once` or Discord `/sync`).

Propagation:

- `contextvars.ContextVar` set by `SyncSession`
- `SyncContextFilter` attaches `sync_id` to every log record during the run
- Lifecycle events also include `sync_id` explicitly in `extra`

---

## Example Log Stream

```text
github_sync_started
    sync_id, cursor_before, repos_total
    ↓
repo_ingestion_started
    repo, sync_id (via filter)
    ↓
github_request_retry          (if transient failure)
    path, attempt, sleep_seconds, sync_id
    ↓
repo_ingestion_completed
    repo, events, duration_ms, sync_id
    ↓
github_event_summary
    issue_opened, pr_merged, …, sync_id
    ↓
github_request_summary
    requests_total, sync_id
    ↓
github_sync_completed
    cursor_before, cursor_after, repos_processed, events_fetched, duration_ms
```

On failure:

```text
github_sync_failed
    sync_id, error, duration_ms
```

---

## Structured Events

### Sync lifecycle (`ghdcbot.logging.sync_context.SyncSession`)

| Event | Key fields |
|-------|------------|
| `github_sync_started` | `sync_id`, `cursor_before`, `repos_total` |
| `github_sync_completed` | `sync_id`, `cursor_before`, `cursor_after`, `repos_processed`, `events_fetched`, `duration_ms` |
| `github_sync_failed` | `sync_id`, `error`, `duration_ms` |

### GitHub adapter (`GitHubRestAdapter`)

| Event | Key fields |
|-------|------------|
| `repo_ingestion_started` | `repo` |
| `repo_ingestion_completed` | `repo`, `events`, `duration_ms` |
| `github_request_summary` | `requests_total` (logged by orchestrator via `SyncSession`) |

### Event summary

| Event | Key fields |
|-------|------------|
| `github_event_summary` | `issue_opened`, `issue_closed`, `pr_opened`, `pr_merged`, `pr_reviewed` (0 if absent) |

---

## Implementation Map

| File | Responsibility |
|------|----------------|
| `src/ghdcbot/logging/setup.py` | `JsonFormatter` extra merge; `SyncContextFilter` on handler |
| `src/ghdcbot/logging/sync_context.py` | `sync_id` generation, `SyncSession` lifecycle logs |
| `src/ghdcbot/engine/orchestrator.py` | Wrap `run_once` in `SyncSession`; emit start/complete/fail |
| `src/ghdcbot/adapters/github/rest.py` | Request counting, repo cache, repo ingestion logs |

---

## Example JSON Lines

```json
{"ts": "...", "level": "INFO", "message": "Repository ingestion started", "repo": "AOSSIE/Gitcord", "event": "repo_ingestion_started", "sync_id": "sync_20260618_142300_ab12"}
```

```json
{"ts": "...", "level": "INFO", "message": "GitHub sync completed", "event": "github_sync_completed", "sync_id": "sync_20260618_142300_ab12", "cursor_before": "2026-06-01T00:00:00+00:00", "cursor_after": "2026-06-18T14:23:00+00:00", "repos_processed": 12, "events_fetched": 240, "duration_ms": 28451}
```

---

## Testing

```bash
pytest tests/test_observability.py -v
```

Covers:

- `JsonFormatter` preserves `extra` fields
- `sync_id` attached via filter
- `github_sync_started` / `github_sync_completed` field presence
- Orchestrator integration with mock GitHub reader

---

## Future Enhancements

- Propagate `sync_id` to Discord notification audit events
- Per-repo `requests_total` breakdown
- OpenTelemetry / metrics export from the same counters
- Include `sync_id` in `/sync` Discord ephemeral response for mentor cross-reference
