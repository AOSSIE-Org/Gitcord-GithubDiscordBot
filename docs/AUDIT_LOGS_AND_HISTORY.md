# Audit: Audit Logs & Activity History (Mentor Visibility)

This document audits existing audit and activity-history mechanisms, validates them, and records any additive changes. All changes are read-only, append-only, and non-breaking.

---

## STEP 1 — Audit Existing Implementation

### 1.1 Whether audit logs already exist (explicitly or implicitly)

| Source | Type | Persisted? | Format |
|--------|------|------------|--------|
| **audit.json / audit.md** | Report snapshot | Yes (file) | JSON overwritten each run; Markdown overwritten. **Not append-only.** |
| **activity.md** | Activity feed | Yes (file) | Markdown overwritten each run. **Not append-only.** |
| **contributions table** | GitHub events | Yes (SQLite) | Append-only INSERTs. Who (github_user), what (event_type), when (created_at UTC), where (repo). |
| **identity_links table** | Identity state | Yes (SQLite) | Mutable (upsert/update). Has created_at, verified_at. **Not an event log**; one row per (discord, github). |
| **Structured logging (stdout)** | Runtime logs | No (ephemeral) | JsonFormatter: ts (UTC), level, logger, message, extra. StreamHandler only; not written to file by code. |
| **Logger calls in code** | Implicit audit | No | IdentityLinkService, orchestrator, writers log to stdout. No dedicated persisted audit stream. |

**Classification:** **Partially implemented.** We have (1) snapshot reports (audit.json, audit.md) and activity.md, (2) append-only GitHub activity in `contributions`, (3) identity state with timestamps in `identity_links`, (4) structured logging to stdout. We do **not** have a dedicated, **append-only** audit log for identity verification events, role application results, or report-generation runs.

---

### 1.2 What is already being recorded

| Event / area | Recorded? | Where | Who / what / when / where |
|--------------|-----------|--------|---------------------------|
| **Identity claim created** | Log only | identity_linking.py, logger.info + extra | Who: discord_user_id, github_user. When: not in structured form (expires_at in extra). Not persisted as event. |
| **Identity verified** | State + log | identity_links.verified_at; logger | When: verified_at. Who: row. Log has discord_user_id, github_user, location. |
| **Identity verification expired / rejected** | Log only | identity_linking.py | "Identity claim expired", "Identity verification not found yet". Not persisted. |
| **Scoring computation** | State | scores table (upsert) | Per-user points; updated_at. Not append-only. |
| **GitHub activity ingestion** | Persisted | contributions table | Append-only. event_type, github_user, repo, created_at, payload. |
| **Discord role application** | Log only | discord writer _log_plan | "applied" / "skipped" / "failed" to logger. Not persisted. |
| **Report generation** | File output | audit.json, audit.md, activity.md | Files overwritten each run. audit.json has timestamp (UTC), org, mode, plans. |

---

### 1.3 Audit dimensions

| Dimension | Implemented | Where |
|-----------|-------------|--------|
| **Who** (GitHub user) | Yes | contributions.github_user; identity_links; audit payloads. |
| **Who** (Discord user) | In logs / identity_links | identity_links.discord_user_id; logger extra. No dedicated event log. |
| **Who** (system) | Implicit | Report generation has no explicit "system" actor in a log. |
| **What** | Partially | event_type in contributions; report has "discord_role_plans", "github_assignment_plans". No single "event_type" for identity/role/report. |
| **When** (UTC) | Yes | contributions.created_at; identity_links.created_at, verified_at; audit.json timestamp; JsonFormatter ts. All UTC. |
| **Where** (repo, org, guild) | Yes | contributions.repo; config.org in reports; guild_id in config. |

---

### 1.4 Output formats and persistence

| Output | Path / location | Mutability |
|--------|------------------|------------|
| audit.json | data_dir/reports/audit.json | Overwritten each run. |
| audit.md | data_dir/reports/audit.md | Overwritten each run. |
| activity.md | data_dir/reports/activity.md | Overwritten each run. |
| contributions | state.db (contributions table) | Append-only (INSERT only). |
| scores | state.db (scores table) | Upsert (mutable). |
| identity_links | state.db (identity_links) | Upsert/update (mutable). |
| Logs | stdout | Ephemeral unless redirected. |

---

## STEP 2 — Validation (Existing Mechanisms)

- **Determinism:** audit.json and scoring use deterministic ordering (sorted keys, sorted plans). Same inputs → same report output. Contributions are ordered by created_at.
- **UTC:** contributions, identity_links, and report timestamp use UTC (storage helpers _ensure_utc / _parse_utc; datetime.now(timezone.utc)).
- **Identity:** Reports and planning use identity_mappings from _resolve_identity_mappings (verified or config fallback). No unverified storage rows in engine output. Logs may contain discord_user_id and github_user for verification events; these are expected for audit.
- **Limitations (documented, not changed):** (1) No append-only event log for identity/role/report events. (2) audit.json/audit.md are snapshots, not history. (3) Export is "cat audit.json" only; no dedicated export-audit CLI or CSV.

---

## STEP 3 — Additions Made (Additive Only)

All of the following are additive. No existing APIs, storage schema (no new tables), or CLI contract changed. Default behavior unchanged if the new code paths are not used.

### 3.1 Append-only audit event log (file-based)

- **File:** `data_dir/audit_events.jsonl`  
  One JSON object per line (JSON Lines). Append-only; no update or delete. Created on first event.

- **Storage (SqliteStorage only):**  
  **`append_audit_event(event: dict) -> None`**  
  Appends one line (JSON dump of event) to `data_dir/audit_events.jsonl`. Not added to the Storage Protocol so other adapters are unchanged. Callers use `getattr(storage, "append_audit_event", None)` and call only if present.

- **Event shape (recommended):**  
  `actor_type` (e.g. "discord_user", "system"), `actor_id` (e.g. discord_user_id or ""), `event_type` (e.g. "identity_claim_created", "identity_verified", "report_generated"), `timestamp` (UTC ISO), `context` (dict: org, repo, github_user, etc.). Optional `correlation_id` / `run_id` when available.

- **Call sites:**  
  - **IdentityLinkService:** After create_claim success → append identity_claim_created. After verify_claim success → append identity_verified. On expired → append identity_verification_expired. On not found → append identity_verification_not_found.  
  - **Orchestrator:** After write_reports (and write_activity_report) → append report_generated with context org, mode, paths.  
  No change to existing logic; only additional append when the method exists.

### 3.2 Immutable activity log

- **audit_events.jsonl** is append-only (open in append mode; no read-modify-write). Repeated events (e.g. multiple runs) produce multiple lines. No overwrite or delete. Immutability boundary: this file only; existing tables (scores, identity_links) remain as today.

### 3.3 Exportable audit data (CLI)

- **New CLI subcommand (optional):** **`export-audit`**  
  **Usage:** `ghdcbot --config <path> export-audit [--format json|csv] [--output <file>]`  
  Reads `data_dir/audit_events.jsonl` (if present).  
  - **--format json:** Outputs a JSON array of events (default).  
  - **--format csv:** Outputs flattened CSV with stable columns (timestamp, actor_type, actor_id, event_type, plus context keys).  
  - **--output:** Writes to file; otherwise stdout.  
  If the file does not exist or is empty, outputs empty array or empty CSV. Does not modify any data. Does not change default behavior of run-once, link, verify-link, or bot.

---

## Confirmation Checklist

- [x] No existing behavior changed (reports, scoring, identity, role application unchanged).
- [x] No storage schema change (no new tables; one new file audit_events.jsonl).
- [x] No change to Storage Protocol or existing methods.
- [x] Append-only audit log; no update/delete of audit_events.jsonl.
- [x] Export is read-only and optional; triggered only by export-audit command.
- [x] All new code is additive (optional method on storage; optional CLI command).

---

## Summary

| Capability | Before | After |
|------------|--------|--------|
| Who did what, when, where (event records) | Logs + tables; no dedicated event log | Append-only audit_events.jsonl for identity and report events. |
| Immutable activity logs | contributions append-only; reports overwritten | audit_events.jsonl append-only; no mutation. |
| Exportable audit (JSON/CSV) | Manual (cat audit.json) | Optional `export-audit --format json|csv` from audit_events.jsonl. |

Existing audit-related behavior (audit.json, audit.md, activity.md, contributions, logging) is unchanged and remains valid.
