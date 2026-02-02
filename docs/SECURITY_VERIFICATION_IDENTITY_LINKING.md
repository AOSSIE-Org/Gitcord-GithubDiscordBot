# Security Verification: GitHub ↔ Discord Identity Linking

**Scope:** Identity linking system only. No new features, no refactors. Strict review for correctness, security, non-breaking behavior, and production-safety in an offline-first, run-once architecture.

**Constraints respected:** No OAuth, no HTTP servers, no background polling, no always-on daemons. Linking is time-limited codes, GitHub bio/gist verification, SQLite-backed, CLI or Discord-triggered only.

---

## 1. Identity Linking Flow

### Can a Discord user claim a GitHub username they do NOT own?

**✅ Correct.** Claiming only creates a pending (unverified) row. The engine uses only **verified** mappings (`list_verified_identity_mappings()`). Verification requires the code to appear in that GitHub user’s **public** bio or public gists, which only the real account holder can do. So a fake claim never affects scoring, roles, or assignments.

### Can two Discord users race to claim the same GitHub username?

**✅ Correct.** In `create_identity_claim`:
- If `github_user` is already **verified** for another `discord_user_id` → `ValueError("github_user is already verified for another Discord user")`.
- If `github_user` has an **unexpired** pending claim by another `discord_user_id` → `ValueError("github_user has an active pending claim by another Discord user")`.
- Expired pending claims for that `github_user` by others are deleted, then the new claim is inserted. So the second racer either gets rejected or (if the first claim expired) replaces it. No double-verified state.

### What happens if verification expires?

**✅ Correct.** `verify_claim` compares `expires_at <= now` (UTC). If expired, returns `(False, "expired")`. No verification is performed; the row stays `verified=0`. Engine never sees it. User must run `create_claim` again to get a new code.

### What happens if the code is removed after verification?

**✅ Correct.** After `mark_identity_verified`, the row has `verification_code = NULL` and `verified = 1`. There is no re-validation of the GitHub bio/gist on later runs. Verified state is persistent; removal of the code from GitHub does not revert the link. (By design: no polling.)

### Are verified identities immutable and protected from overwrite?

**❌ Actual bug.** In `create_identity_claim`, when `(discord_user_id, github_user)` **already exists with `verified=1`**:
- The check “discord_user_id is already verified for another GitHub user” only raises when the **existing** verified `github_user` is **different** from the one requested. So for the **same** pair (e.g. d1 + octocat already verified), we do **not** raise.
- The code then runs `INSERT ... ON CONFLICT(discord_user_id, github_user) DO UPDATE SET verified = 0, verification_code = excluded.verification_code, verified_at = NULL, ...`, which **overwrites** the verified row and sets it back to unverified.

**Impact:** Anyone who can call `create_claim(discord_user_id, github_user)` (CLI or Discord `/link` with that pair) can **un-verify** an existing link. In Discord, only the same user can call `/link` with their own ID (from `interaction.user.id`), so they could un-verify themselves and get a new code. But via CLI, an operator (or attacker with CLI access) who knows a verified (discord_id, github_user) pair can run `link --discord-user-id <victim_id> <github_user>` and **downgrade** that mapping to unverified. Verified state is therefore **not** immutable.

**Required fix:** Before the `INSERT ... ON CONFLICT`, if a row exists for `(discord_user_id, github_user)` with `verified = 1`, **reject** (raise) or no-op. Do not run the `ON CONFLICT DO UPDATE` for already-verified pairs.

---

## 2. Storage Safety (SQLite)

### Schema and constraints

**✅ Correct.** `identity_links` has `PRIMARY KEY (discord_user_id, github_user)`, `verified INTEGER NOT NULL DEFAULT 0`, and indexes on `github_user` and `verified`. No duplicate (discord_user_id, github_user). UTC handling: `_ensure_utc` / `_parse_utc` used for `expires_at`, `created_at`, `verified_at` in this code path; `mark_identity_verified` uses `datetime.now(timezone.utc).isoformat()` for `verified_at`.

**⚠️ Potential risk (low).** There is no DB-level `UNIQUE` constraint like “at most one row per `github_user` where `verified=1`”. The application logic (reject create when github_user already verified for someone else) prevents duplicate verified owners. A future bug could theoretically insert a second verified row for the same github_user; a partial mitigation would be a unique partial index if the SQLite version supports it. Not critical given current checks.

### Race conditions / partial writes

**✅ Correct.** Each of `create_identity_claim`, `mark_identity_verified`, `get_identity_link`, `list_verified_identity_mappings` uses a single `with self._connect() as conn` and one or more executes in that context. Default `sqlite3` isolation is DEFERRED; multi-statement flows run without explicit BEGIN, so they are autocommit-per-statement. For `create_identity_claim`, the critical sequence (SELECTs then INSERT/UPDATE) runs in one connection; a concurrent process could still insert between our SELECT and INSERT, but the PRIMARY KEY and the “github_user already verified” / “pending claim by another” checks make conflicting inserts fail or be rejected on the next read. No in-process partial write of a single row.

### Verified vs unverified separation

**✅ Correct.** `list_verified_identity_mappings()` returns only `WHERE verified = 1`. The engine only consumes this list (when available). Unverified rows are never returned here and are never used for scoring, role planning, or assignments.

---

## 3. Engine Integration

### Orchestrator only consumes verified identity mappings

**✅ Correct.** `_resolve_identity_mappings(storage, config_identity_mappings)`:
- Calls `storage.list_verified_identity_mappings()` when the method exists.
- If the returned list is **non-empty**, it **returns that list** and ignores config.
- If the list is empty (or the getter is missing/throws), it returns `list(config_identity_mappings)`.

So when any verified mappings exist, the engine sees **only** verified mappings. It never receives unverified rows from storage.

### Unverified mappings ignored everywhere

**✅ Correct.** Unverified rows exist only in SQLite; they are never returned by `list_verified_identity_mappings()`. Scoring, role planning, and assignment logic all use the single `identity_mappings` list from `_resolve_identity_mappings`, so they never see unverified data.

### Fallback to config only when no verified mappings

**✅ Correct.** Fallback to config happens only when `verified` is empty (or the getter fails and we set `verified = []`). So config is used only when there are **no** verified mappings.

### No scoring/role/assignment logic with unverified data

**✅ Correct.** All of those paths use `identity_mappings` from `_resolve_identity_mappings`. There is no code path that feeds unverified storage rows into scoring, planning, or assignment.

---

## 4. Failure Modes

### GitHub API (identity reader)

**✅ Fail closed.** `_request` returns `None` on 401/403/404 and on `httpx.HTTPError`. `_fetch_bio` / `_search_public_gists` treat `None` or non-200 as “no data”; `search_verification_code` then returns `VerificationMatch(found=False)`, so verification is not granted.

**⚠️ Potential risk (low).** 429 and 5xx are not explicitly handled: `_request` returns the response. Callers may then call `.json()` or use the body; for 429/5xx the content may be an error body. In practice, `_fetch_bio` uses `response.json()` only when status was not checked (we only check `response is None or response.status_code != 200`). So 429/5xx are treated as “success” and we might parse an error JSON as profile data; `data.get("bio")` would typically be None, so we still tend to fail closed. Explicit handling of 429/5xx (e.g. return None or treat as failure) would make the contract clear.

### Discord API failure

**✅ Correct.** Bot and CLI only use Discord for reading members/roles (run-once) or for slash commands (bot). Identity linking state is in SQLite; Discord API failures do not corrupt verified/unverified state. If Discord is down, link/verify-link can’t be used in Discord; CLI link/verify-link still work.

### SQLite locked / read-only

**✅ Correct.** If SQLite is locked or read-only, `_connect()` or `execute` will raise. That propagates: `create_claim` / `verify_claim` fail, CLI/bot get an exception, no silent success. `_resolve_identity_mappings` catches `Exception` and sets `verified = []`, so the engine falls back to config (see below).

### Missing or malformed config

**✅ Correct.** `load_config` raises `ConfigError` for missing file, empty file, invalid YAML, or validation failure. No silent fallback; process exits.

### Partial verification state (claim exists, never verified)

**✅ Correct.** Such rows stay `verified=0`. They are never returned by `list_verified_identity_mappings()`, so the engine never uses them. They only consume space; they do not affect behavior.

---

## 5. Security & Abuse

### Impersonation (claiming someone else’s GitHub)

**✅ Correct.** Storage rejects “github_user already verified for another Discord user” and “github_user has an active pending claim by another Discord user.” Verification requires the code on that GitHub user’s public bio/gists, so only the real owner can verify. Impersonation cannot produce a verified mapping for a GitHub account the claimant does not control.

### Replay of old codes

**✅ Correct.** After verification, `verification_code` is set to NULL and `verified_at` is set. Re-running `verify_claim` with the same pair returns `(True, "already-verified")`. There is no path that re-uses an old code to change or overwrite a verified link. Old codes cannot be replayed to grant verification again.

### Brute-force verification

**⚠️ Potential risk (low).** Code is 10 chars from 36 (uppercase + digits), so 36^10 possibilities. There is no rate limiting in `verify_claim` (or in the Discord bot) per (discord_user_id, github_user). Theoretically an attacker who knows the pair could try many codes; the code expires in 10 minutes, making brute-force impractical in practice. For high-security deployments, rate limiting or backoff per (discord_user_id, github_user) would strengthen guarantees.

### One Discord ID → multiple GitHub accounts

**✅ Correct.** Before insert, storage checks “discord_user_id is already verified for another GitHub user” and raises. So one Discord user can have only one verified GitHub link.

### One GitHub account → multiple Discord IDs

**✅ Correct.** Storage checks “github_user is already verified for another Discord user” and raises. So one GitHub account can be linked to only one Discord user.

---

## 6. UX & Safety Guarantees

### Fail closed vs open

**✅ Correct.** Verification fails closed (no verification without code on GitHub). Use of unverified data fails closed (engine never sees unverified rows).

**⚠️ Potential risk (medium).** In `_resolve_identity_mappings`, if `list_verified_identity_mappings()` **raises** (e.g. DB error), we catch `Exception` and set `verified = []`, then return `list(config_identity_mappings)`. So **on storage failure we fall back to config**. That is “fail open” with respect to config: config mappings (possibly unverified-by-design or legacy) are used when storage is broken. If config is trusted and storage is transiently failing, this avoids a full outage. If the goal is “never use anything but verified when storage exists but is failing,” the safer behavior would be to re-raise or return [] on storage exception so the engine uses no identity mappings. Document or change as appropriate for your threat model.

### No silent success on failure

**✅ Correct.** GitHub identity request failures lead to `found=False`. Expired or missing claim leads to explicit return values or ValueError. SQLite errors propagate. No silent “verified” when verification did not succeed.

### Logging for audit

**✅ Correct.** IdentityLinkService and storage paths log create, verify, expire, and “not found yet” with relevant IDs. Enough for audit trails.

### Dry-run / observer modes

**✅ Correct.** MutationPolicy disallows GitHub and Discord mutations when mode is not ACTIVE. Dry-run and observer only read and plan; they do not mutate Discord or GitHub. Identity linking is independent of mode; linking only updates SQLite.

---

## 7. Tests

### Existing coverage

**✅ Correct.** Tests cover:
- Verification code generation and storage (and UTC in expires_at).
- Impersonation: second Discord user cannot create_claim for already-verified github_user.
- Verify flow: verify_claim marks verified, clears code and expires_at, sets verified_at.
- Engine: verified mappings used, unverified and config mapping (when verified exist) ignored for assignment; audit payload shows only verified assignee.

### Missing test cases (recommended)

**🧪 Missing test (recommended).** **Verified row must not be overwritten by create_identity_claim.** Add a test: create claim for (d1, octocat), verify it, then call `create_identity_claim(d1, octocat, ...)` again; assert either ValueError or that the row still has `verified=1`. Currently the implementation would overwrite; the test would document the intended invariant and fail until the bug is fixed.

**🧪 Missing test (optional).** **Storage exception in _resolve_identity_mappings:** Mock storage so `list_verified_identity_mappings()` raises; assert that the orchestrator either re-raises or returns [] (depending on desired “fail closed” behavior), and does not silently use config without the test explicitly expecting it.

---

## 8. Summary

| Area                         | Result |
|-----------------------------|--------|
| Claim vs ownership          | ✅ Only real GitHub owner can verify. |
| Race for same GitHub        | ✅ Rejected or replace expired; no double-verified. |
| Expiry                      | ✅ Checked in UTC; expired claims not verified. |
| Code removed after verify  | ✅ No re-check; verified state persists. |
| Verified immutable          | ✅ Fixed: create_claim rejects already-verified same pair. |
| SQLite schema / UTC         | ✅ Correct; verified/unverified separated. |
| Engine uses only verified  | ✅ Yes; fallback to config only when verified empty. |
| Unverified never used       | ✅ Never returned or fed to engine. |
| GitHub/Discord/Config fail  | ✅ Fail closed or explicit; config fallback on storage exception. |
| Impersonation / replay      | ✅ Blocked; one-to-one enforced. |
| Brute-force                 | ⚠️ Theoretically possible; 10-min expiry makes it impractical. |
| Dry-run / observer          | ✅ No mutations. |
| Tests                       | ✅ Good coverage; 🧪 add test for “verified not overwritable”. |

---

## 9. Security Guarantee Summary

- **Verified-only usage:** The engine uses only verified identity mappings when present; unverified storage rows never affect scoring, roles, or assignments.
- **Verification proof:** Verification requires the code to appear in the GitHub user’s public bio or public gists (only the account holder can do that).
- **One-to-one:** One Discord user ↔ one verified GitHub account; one GitHub account ↔ one verified Discord user.
- **No silent verification:** Verification only succeeds when the code is found and not expired; failures do not mark as verified.
- **Immutable verified state:** Enforced; create_claim rejects when (discord_user_id, github_user) is already verified, so verified rows cannot be overwritten.

---

## 10. Final Verdict

**SAFE WITH NOTES** (verified-overwrite bug fixed in storage; see “Fix applied” above).

- **Note 1:** On storage exception, `_resolve_identity_mappings` falls back to config; decide whether that is acceptable or whether to fail closed (return [] or re-raise).
- **Note 2:** GitHub 429/5xx in the identity reader are not explicitly handled; behavior is effectively fail-closed but could be clarified.
- **Note 3:** No rate limiting on verify_claim; acceptable for typical deployments given 10-minute code expiry and 36^10 code space; optional hardening for high-security environments.

**Fix applied:** In `create_identity_claim`, before the INSERT, we now check for an existing row `(discord_user_id, github_user)` with `verified = 1` and raise `ValueError("This Discord user and GitHub user are already verified; cannot create a new claim")` instead of overwriting. Regression test: `test_create_claim_rejects_already_verified_same_pair`.
