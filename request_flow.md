# GitHubRestAdapter._request() — Request Flow

> Documents `src/ghdcbot/adapters/github/rest.py` as of Week 3 Day 3 (retry + rate-limit recovery).

---

## Signature

```python
def _request(self, method: str, path: str, params: dict) -> httpx.Response | None
```

| Argument | Type | Description |
|----------|------|-------------|
| `method` | `str` | HTTP method (`"GET"`, `"POST"`, etc.) |
| `path` | `str` | GitHub REST path relative to `api_base` (e.g. `/repos/owner/repo/issues`) |
| `params` | `dict` | Query parameters passed to `httpx.Client.request()` |

## Return value

| Return | Meaning |
|--------|---------|
| `httpx.Response` | Request succeeded (after retries / rate-limit recovery if needed). Caller checks `status_code` (typically `200`). |
| `None` | Request failed permanently, exhausted retries, unrecoverable rate limit, or permission/not-found status. |

`_request_with_status()` shares the same retry layer (`_execute_request_with_retries`) but **returns the response** for 401/403/404 instead of converting to `None` — used by org repo listing to detect 401/403 for user-fallback.

---

## Execution flow (current)

```text
_request(method, path, params)
    │
    ▼
_execute_request_with_retries()     ← transient retry loop (max 4 attempts)
    │
    ├─ inner while (rate-limit recovery — does NOT consume transient attempts)
    │     ├─ client.request()
    │     ├─ 403 + Remaining == 0 ?
    │     │     ├─ missing/malformed X-RateLimit-Reset → log, return None
    │     │     ├─ sleep until reset_timestamp - now (clamp negative → 0)
    │     │     └─ retry same request (same transient attempt)
    │     └─ break when response is not rate-limit exhausted
    │
    ├─ httpx.TimeoutException / ConnectError
    │     → transient retry with backoff (1s, 2s, 4s + jitter) or return None
    │
    ├─ HTTP 502 / 503 / 504
    │     → transient retry with backoff or return None
    │
    ├─ other httpx.HTTPError
    │     → log warning, return None (no retry)
    │
    └─ other status codes (incl. permission 403)
          → return response to _request()
    │
    ▼
_request() post-processing
    │
    ├─ response is None → return None
    ├─ rate limit remaining <= 1 → log warning (no sleep)
    ├─ 401 → log permission issue, return None
    ├─ 403 (permission) → log permission issue, return None
    ├─ 404 → log not found, return None
    └─ else → return response
```

---

## 403 handling flow

```text
HTTP 403 received
    │
    ▼
_parse_rate_limit(headers)
    │
    ├─ X-RateLimit-Remaining == 0 ?
    │     │
    │     YES ──► Rate limit exhausted (in _execute_request_with_retries)
    │     │         ├─ X-RateLimit-Reset valid?
    │     │         │     NO  → event: github_rate_limit_missing_reset → return None
    │     │         │     YES → sleep_seconds = reset_ts - now (min 0)
    │     │         │           event: github_rate_limit_exhausted
    │     │         │           sleep(sleep_seconds)
    │     │         │           event: github_rate_limit_recovered
    │     │         │           retry same request (same transient attempt)
    │     │
    │     NO ──► Permission / visibility issue
    │               return response to _request()
    │               _log_permission_issue() → return None
    │
    └─ Remaining is None on 403 → treated as permission issue (not rate limit)
```

### Rate-limit vs permission 403

| Signal | Classification | Behavior |
|--------|----------------|----------|
| `403` + `Remaining == 0` | Rate limit exhausted | Sleep until `X-RateLimit-Reset`, retry (no transient attempt consumed) |
| `403` + `Remaining > 0` | Permission / visibility | `_log_permission_issue()`, return `None` via `_request()` |
| `403` + `Remaining` missing | Permission (not exhausted) | Same as permission |
| `403` + `Remaining == 0` + bad/missing reset | Unrecoverable | `github_rate_limit_missing_reset`, return `None` |

### Header parsing

`_parse_rate_limit()` reads:

- `X-RateLimit-Remaining` — integer; `None` if missing/non-digit
- `X-RateLimit-Reset` — Unix timestamp; parsed to UTC `datetime`

`_rate_limit_sleep_seconds()` computes `reset_timestamp - time.time()`, clamping negative values to `0.0`.

---

## Failure handling summary

| Condition | Behavior |
|-----------|----------|
| `TimeoutException` / `ConnectError` | Transient retry up to 4× with backoff |
| HTTP 502/503/504 | Transient retry up to 4× with backoff |
| HTTP 403 + `Remaining == 0` | Rate-limit sleep + retry (separate from transient budget) |
| HTTP 403 + `Remaining != 0` | Permission log → `None` |
| HTTP 401 | Permission log → `None` |
| HTTP 404 | Not-found log → `None` |
| Other `HTTPError` | Log → `None` |

---

## Callers that depend on `None`

When `_request()` returns `None`, callers stop processing that path. Key dependents:

| Caller | File | Behavior on `None` |
|--------|------|-------------------|
| `_paginate()` | `rest.py` | Stops pagination for that endpoint |
| `_paginate_from_page()` | `rest.py` | Stops pagination |
| `_list_repos_from_path()` | `rest.py` | Returns `[], None` — no repos discovered |
| `get_pull_request()` | `rest.py` | Returns `None` |
| `get_issue()` | `rest.py` | Returns `None` |
| `get_pull_request_check_runs()` | `rest.py` | Returns `[]` |
| `write_file()` | `rest.py` | Falls back to default branch or fails update |

Rate-limit recovery reduces partial-ingestion failures that previously stopped pagination mid-sync when GitHub returned `403` with exhausted quota.

---

## Related functions

| Function | Purpose |
|----------|---------|
| `_execute_request_with_retries()` | Transient retry loop + rate-limit recovery inner loop |
| `_is_rate_limit_exhausted()` | `403` + `Remaining == 0` |
| `_rate_limit_sleep_seconds()` | Compute sleep from `X-RateLimit-Reset` |
| `_rate_limit_reset_timestamp()` | Parse reset header as Unix timestamp |
| `_request_with_status()` | Same retry layer; preserves 401/403/404 responses |
| `_log_github_rate_limit_exhausted()` | `event=github_rate_limit_exhausted` |
| `_log_github_rate_limit_recovered()` | `event=github_rate_limit_recovered` |
| `_log_github_rate_limit_missing_reset()` | `event=github_rate_limit_missing_reset` |
| `_log_github_request_retry()` | Transient retry log |
| `_log_github_request_failed()` | Transient retry exhaustion log |
| `_log_permission_issue()` | 401/403 permission logging |
| `_log_not_found()` | 404 logging |
| `_parse_rate_limit()` | Parses rate-limit headers |

---

## Direct `_client` bypasses (not covered)

Some mutation helpers in `rest.py` call `self._client.post/get` directly. These do **not** use `_request()` and have no retry or rate-limit recovery — future refactor candidate.
