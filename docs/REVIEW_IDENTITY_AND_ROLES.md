# Review: Identity Verification and Role Management

This document reviews the Gitcord codebase against two feature areas: **verified role assignment in Discord** and **re-verification and expiry handling**. It validates what exists, notes edge cases, and proposes additive improvements without changing current contracts or behavior.

---

## 1. Verified Role Assignment in Discord

### 1.1 Automatically assigning a specific Discord role after successful GitHub verification

**Status: Partially implemented (by design).**

**What exists:**

- **Role assignment is score-based, not “verified-only”.** The engine does not assign a dedicated “Verified” role at the moment a user completes `/verify-link`. Instead:
  1. Only **verified** identity mappings (or config fallback when none exist) are used.
  2. For each such mapping, the engine computes a **score** from GitHub contributions (issues, PRs, reviews, comments) over the scoring period.
  3. **Discord roles** (e.g. Contributor, Maintainer) are assigned from `role_mappings` when `score >= min_score` for that role.

So “verified” controls **who is in the identity set**; **which role** they get is determined by contribution score. There is no separate “Verified” role that is added immediately upon verification.

**Validation:**

- `_resolve_identity_mappings()` returns only `list_verified_identity_mappings()` when the list is non-empty, so unverified rows never affect role assignment.
- `plan_discord_roles()` and `apply_discord_roles()` use that same identity list and `role_mappings`; they never see unverified mappings.
- One-to-one is enforced in storage (one verified GitHub per Discord user, one verified Discord per GitHub user).

**Realistic scenario:**

- User verifies via `/verify-link` → row is `verified=1`.
- Next `run-once`: identity_mappings = verified only; scores computed for their `github_user`; if score ≥ e.g. 10, they get “Contributor”.
- If they have zero contributions in the window, they get 0 points and no role from `role_mappings` (they are still “verified” for future runs).

**Edge case:**

- A server that wants a **“Verified” role** given purely for completing verification (independent of score) cannot do so today. Workaround: add a role with `min_score: 0` in `role_mappings` and ensure only verified users are in the identity set (current behavior), so they get that role as long as they have 0+ points. That still ties the role to the scoring pipeline, not to the verification event itself.

---

### 1.2 Ensuring the role is applied only after one-to-one identity mapping is confirmed

**Status: Implemented and correct.**

- **Engine:** Only verified mappings (or config when no verified exist) are passed to planning and `apply_discord_roles`. So roles are applied only to Discord users who appear in that confirmed identity set.
- **Storage:** One-to-one is enforced in `create_identity_claim`:
  - `github_user` already verified for another `discord_user_id` → reject.
  - `discord_user_id` already verified for another `github_user` → reject.
- **Apply path:** `apply_discord_roles` iterates over `identity_mappings` and uses `mapping.discord_user_id` / `mapping.github_user`; no mixing of verified and unverified data.

**Test coverage:** `test_verified_mappings_used_unverified_ignored_in_planning` asserts that when storage has both verified and unverified rows, only the verified mapping is used for assignment (audit payload has assignee from verified, not from config/unverified).

**Edge case:** If storage fails and `_resolve_identity_mappings` falls back to config, roles are applied from config mappings (trusted file). That is a documented “fail open” choice; changing it would be a behavioral change, not covered here.

---

## 2. Re-verification and Expiry Handling

### 2.1 Support for verification expiry (time-based or policy-based)

**Status: Partial.**

**What exists:**

- **Claim (pending) expiry:** The **verification code** has a time-to-live (default 10 minutes). `IdentityLinkService` sets `expires_at = now + ttl` when creating a claim; `verify_claim()` checks `expires_at <= now` and returns `(False, "expired")` without marking verified. So pending claims expire; users must run `/link` again to get a new code.
- **Verified state:** There is **no** expiry of the verified link itself. Once `mark_identity_verified` is called, the row stays `verified=1` with `verified_at` set; `list_verified_identity_mappings()` does not filter by `verified_at` or any policy. So “verification” does not expire after N days or by policy.

**Validation:**

- Expiry of the **code** is correct: UTC-normalized, checked in `verify_claim`, and documented in the flow.
- No time-based or policy-based expiry of the **verified** state exists.

**Edge case:** Long-lived verified links (e.g. after a GitHub username change or compromise) cannot be automatically invalidated by time or policy; that would require new logic (see proposals below).

---

### 2.2 Ability for users to explicitly re-verify without silently overwriting an existing verified identity

**Status: Implemented and correct.**

- **Same pair (discord_user_id, github_user) already verified:** `create_identity_claim` checks for an existing row with `verified=1` for that pair and **raises** `ValueError("This Discord user and GitHub user are already verified; cannot create a new claim")`. So re-calling `/link` for the same pair does **not** overwrite or clear the verified state.
- **Different pair:** One Discord user cannot have two verified GitHub accounts (rejected). One GitHub account cannot be verified by two Discord users (rejected). So there is no silent overwrite of who is linked.

**Validation:** `test_create_claim_rejects_already_verified_same_pair` asserts that after verify, calling `create_claim(d1, octocat)` again raises and the row remains `verified=1`.

**Gap (additive):** There is no explicit “unlink” or “re-verify” flow. A user who wants to switch to another GitHub account (or re-prove the same one) cannot do so without an operator or future “unlink” feature; today they would need a manual DB change or a new, additive unlink command that clears the verified row and then allows a new claim.

---

### 2.3 Safe handling of edge cases (username changes, expired verification states)

**Status: Partially addressed.**

**Username changes (GitHub):**

- The stored `github_user` is the login at verification time. If the user renames their GitHub account, the stored link still points to the old username. Contributions and API calls (e.g. for assignments) use that stored name; GitHub redirects renames for many API endpoints, but not all. So:
  - **Current behavior:** No automatic update of `github_user`; no “refresh” or “re-verify” that updates the stored name.
  - **Edge case:** Renamed users might have contributions under the new username while the link still has the old one; scoring could be wrong until the link is updated (which today requires manual DB change or a future unlink + re-verify flow).

**Expired verification states:**

- There is no concept of “verified state expired.” Only the **claim code** expires. So there is nothing to “handle” for an expired verified state today; the only expiry is the pending code.

**Other edge cases:**

- **Code removed from bio after verify:** Verified state remains; no re-check. By design (no polling).
- **Multiple pending claims (other users):** Fixed earlier: we fetch all pending rows for other users, reject if any is unexpired, and delete only expired pending rows.

---

## 3. Summary Table

| Feature | Status | Notes |
|--------|--------|--------|
| Role assigned only after identity is confirmed (one-to-one) | ✅ Implemented | Verified mappings only; storage enforces one-to-one. |
| Role applied only to verified (or config) mappings | ✅ Implemented | Engine uses only verified set for scoring/roles. |
| Dedicated “Verified” role at verification time | ❌ Not implemented | Roles are score-based; no immediate “Verified” role. |
| Claim (code) expiry | ✅ Implemented | 10 min TTL; verify_claim returns "expired". |
| Verified-state expiry (time/policy) | ❌ Not implemented | Verified link never expires. |
| No silent overwrite of verified pair | ✅ Implemented | create_claim raises for already-verified same pair. |
| Explicit re-verify / unlink | ❌ Not implemented | No way to unlink and re-verify without DB/operator. |
| GitHub username change handling | ❌ Not implemented | Stored github_user not updated. |

---

## 4. Proposals (Additive, Non-Breaking)

The following are opt-in or additive. They do not change existing APIs, storage contracts, or current behavior of verification or role assignment.

### 4.1 Optional “verified role” (opt-in config)

**Goal:** Allow a server to grant a specific Discord role solely for having a verified link (e.g. “Verified” with no score requirement).

**Idea:**

- Add an optional config field, e.g. `discord.verified_role: Optional[str] = None`.
- In `apply_discord_roles` (or in planning), when this is set: for each `identity_mapping` (already only verified or config), if the member does not have that role, add it (or plan it). Keep all existing `role_mappings` (score-based) as-is.
- If `verified_role` is unset, behavior is unchanged.

**Contract:** Existing configs remain valid; no change to `identity_links` or verification flow.

---

### 4.2 Optional verified-state expiry (policy-based, opt-in)

**Goal:** Allow verified links to be considered “expired” after a configured duration (e.g. 365 days) for role assignment only.

**Idea:**

- Add optional `identity.verified_max_age_days: Optional[int] = None` (or under `runtime`).
- In `list_verified_identity_mappings()` (or a wrapper used only when the option is set): when the option is set, filter out rows where `verified_at` is older than `now - timedelta(days=verified_max_age_days)`. When unset, return all `verified=1` rows as today.
- Do **not** delete or update the row; only exclude it from the list so that user falls back to config (if any) or gets no role from that mapping until they re-verify. Re-verification would require an unlink flow (see 4.3).

**Contract:** Storage schema unchanged; existing callers of `list_verified_identity_mappings()` get the same list when the option is unset.

---

### 4.3 Explicit unlink / re-verify (additive CLI and optional Discord command)

**Goal:** Let a user (or admin) break the verified link so they can run `/link` + verify again (e.g. after username change or to re-prove).

**Idea:**

- Add a storage method, e.g. `clear_identity_verification(discord_user_id: str, github_user: str) -> bool`, which updates the row to `verified=0`, clears `verified_at`/`verification_code`/`expires_at`, or deletes the row. Idempotent; return True if a verified row was cleared.
- Add CLI subcommand, e.g. `unlink --discord-user-id X GITHUB_USER`, guarded by config or flag so it’s opt-in (e.g. only when `identity.allow_unlink: true`).
- Optionally add Discord slash command `/unlink` that clears the link for the invoking user (and optionally requires a role like “Admin”). Only register when enabled by config.

**Contract:** No change to `create_claim` or `verify_claim`; unlink is a separate path. After unlink, the user can create a new claim and verify again.

---

### 4.4 Auditability (additive)

**Goal:** Improve trust and auditability without changing behavior.

**Ideas:**

- Log when `_resolve_identity_mappings` uses verified vs config (e.g. “Using N verified identity mappings” or “Using config identity_mappings (no verified)”).
- In reports (e.g. audit.md), optionally list which identity mappings were used (e.g. “Identity set: verified” and count, or “Identity set: config” and count). No PII needed; just source and count.
- Keep existing logging for create_claim, verify_claim, and mark_identity_verified as-is.

---

## 5. Conclusion

- **Verified role assignment:** Role assignment is applied only to confirmed one-to-one identity mappings (verified or config fallback). Roles are score-based; there is no dedicated “Verified” role at verification time. Behavior is correct and test-covered; optional “verified role” can be added via config.
- **Re-verification and expiry:** Claim code expiry is implemented and correct; verified state does not expire. Re-verification does not silently overwrite (create_claim raises for already-verified same pair). Explicit unlink and optional verified-state expiry are not present and can be added incrementally as above.
- **Edge cases:** Username changes and “expired verified state” are not handled today; optional verified-state expiry and an unlink/re-verify flow would address them in an additive way.

All proposed changes are additive or opt-in and preserve current contracts and behavior.
