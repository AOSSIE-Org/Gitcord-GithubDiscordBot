# Retry Design — GitHub REST Ingestion

> **Status:** Implemented in `GitHubRestAdapter._execute_request_with_retries()` (Week 3 Day 2–3).

See also: `request_flow.md` for caller behavior, 403 flow, and `None` semantics.

---

## Goal

Transient GitHub API failures should not interrupt ingestion. Permanent failures should fail immediately. Rate-limit exhaustion should recover automatically instead of aborting pagination.

---

## What retries? (transient)

| Condition | Retries? | Max attempts | Backoff |
|-----------|----------|--------------|---------|
| `httpx.TimeoutException` | **Yes** | 4 | 1s → 2s → 4s + jitter |
| `httpx.ConnectError` | **Yes** | 4 | 1s → 2s → 4s + jitter |
| HTTP **502** Bad Gateway | **Yes** | 4 | 1s → 2s → 4s + jitter |
| HTTP **503** Service Unavailable | **Yes** | 4 | 1s → 2s → 4s + jitter |
| HTTP **504** Gateway Timeout | **Yes** | 4 | 1s → 2s → 4s + jitter |

### Backoff schedule

| Attempt | Delay before this attempt |
|---------|---------------------------|
| 1 | Immediate |
| 2 | 1 second + jitter (0–0.5s) |
| 3 | 2 seconds + jitter (0–0.5s) |
| 4 | 4 seconds + jitter (0–0.5s) |

Jitter: `random.uniform(0, 0.5)` added to base delay.

---

## What does NOT retry? (transient layer)

| Condition | Behavior | Why |
|-----------|----------|-----|
| HTTP **401** Unauthorized | Log permission issue → `None` | Bad/expired token |
| HTTP **404** Not Found | Log not found → `None` | Resource missing |
| HTTP **403** (permission, `Remaining > 0`) | Log permission issue → `None` | Missing scope / visibility |
| HTTP **403** + `Remaining == 0` + bad reset header | `github_rate_limit_missing_reset` → `None` | Cannot compute sleep |
| Other `httpx.HTTPError` | Log → `None` | Non-transport client errors |

---

## Rate-limit recovery strategy

### Why?

Partial ingestion caused by rate limits can advance cursors and skip events. When pagination hits `403` with exhausted quota, the sync previously returned `None` and stopped — leaving later repos/pages un-fetched.

### Flow

```text
GitHub Rate Limit (403 + Remaining == 0)
    ↓
Detect exhaustion (_is_rate_limit_exhausted)
    ↓
Read X-RateLimit-Reset
    ↓
sleep_seconds = reset_timestamp - now  (clamp negative → 0)
    ↓
Log github_rate_limit_exhausted
    ↓
sleep(sleep_seconds)
    ↓
Log github_rate_limit_recovered
    ↓
Retry same request (same transient attempt — does NOT consume backoff budget)
```

### Separate from transient retries

| Aspect | Transient retry | Rate-limit recovery |
|--------|-----------------|---------------------|
| Triggers | Timeout, ConnectError, 502/503/504 | 403 + `Remaining == 0` |
| Backoff | 1s → 2s → 4s + jitter | Sleep until `X-RateLimit-Reset` |
| Attempt budget | Max 4 transient attempts | Unlimited waits within same attempt |
| Logging | `github_request_retry` | `github_rate_limit_exhausted` / `recovered` |

### Structured logging

**Rate limit hit:**

```json
{
  "event": "github_rate_limit_exhausted",
  "remaining": 0,
  "reset_timestamp": 123456789,
  "sleep_seconds": 183,
  "path": "/repos/AOSSIE/Gitcord/issues"
}
```

**Rate limit recovery:**

```json
{
  "event": "github_rate_limit_recovered",
  "path": "/repos/AOSSIE/Gitcord/issues",
  "attempt": 2
}
```

**Missing/malformed reset header:**

```json
{
  "event": "github_rate_limit_missing_reset",
  "path": "/repos/AOSSIE/Gitcord/issues",
  "reset_header": null
}
```

---

## Implementation overview

```text
_execute_request_with_retries(method, path, params)
    for attempt in 1..4:
        while rate_limit_exhausted:
            sleep until reset (or return None if header invalid)
            retry request
        if Timeout/ConnectError → transient backoff, continue
        if 502/503/504 → transient backoff, continue
        return response
    return None

_request() wraps above:
    401/403-permission/404 → log + return None
    else → return response
```

---

## Example flows

### Rate limit recovery

```text
GET /repos/org/repo/issues
  → 403, Remaining=0, Reset=now+120
  → log github_rate_limit_exhausted, sleep 120s
  → log github_rate_limit_recovered
  → 200 ✓
```

### Permission 403 (no sleep)

```text
GET /repos/org/private-repo/issues
  → 403, Remaining=4521
  → _log_permission_issue, return None (1 request)
```

### Transient 503

```text
GET /repos/org/repo/issues
  → 503 → retry backoff
  → 503 → retry backoff
  → 200 ✓
```

---

## Testing

**Transient retries** — `tests/test_github_retry.py`:

| Scenario | Expected |
|----------|----------|
| 503, 503, 200 | Success; 3 requests |
| Timeout, Timeout, 200 | Success; retries logged |
| 404 / 401 | No retry; 1 request |
| 503 × 4 | Failure after 4 attempts |
| 403 permission | No sleep; permission log |

**Rate-limit recovery** — `tests/test_github_rate_limit.py`:

| Scenario | Expected |
|----------|----------|
| 403 Remaining=0 Reset=now+2, then 200 | Sleep called; success |
| 403 Remaining=0, missing reset | Failure; `missing_reset` log |
| 403 Remaining=5 | Permission flow; no sleep |
| 403 Remaining=0, malformed reset | Failure; `missing_reset` log |

Run:

```bash
pytest tests/test_github_retry.py tests/test_github_rate_limit.py -v
```

---

## Out of scope

- `Retry-After` header for secondary/abuse rate limits
- `tenacity` library (inline loop used)
- Discord API rate limits
- Direct `_client` mutation bypasses
- Per-repo cursor recovery on partial pagination failure

---

## Mentor talking point

> "I implemented resilient GitHub request handling with exponential backoff for transient failures, plus sleep-until-reset recovery for primary rate limits. Permission 403s still fail immediately. Rate-limit waits are separate from the transient retry budget so we don't burn attempts on quota resets."
