# Week 5 Readiness Assessment

**Date:** 2026-06-20  
**Branch audited:** `week-4-pr3-ux` (stacked on Week 4 PRs #23–#25, **not yet merged to `main`**)  
**Method:** Read-only codebase investigation — no implementation changes.

---

## Executive summary

| Week 5 goal | Current state | Readiness |
|-------------|---------------|-----------|
| Notification expansion PR | **Not implemented** on current branch; template comments only | **Ready to implement** (~2–3 days) |
| Config / routing / tests for new types | Partial foundation exists | **Extend existing system** |
| Social profile linking (X + LinkedIn) | **Missing entirely** | **Greenfield** (~2–3 days) |
| `/status` + `identity list` social display | Identity system mature; no social storage | **Low complexity add-on** |
| Documentation | Strong identity docs; notification docs lag planned flags | **Updates required** |
| Manual AOSSIE verification | Config exists in `config/examples/aussie.yaml` | **Blocked on PR merge + feature work** |

**Bottom line:** Week 5 is feasible without scoring work. The notification pipeline is production-quality for four event types today, but the four **planned expansion events are missing end-to-end**. Social linking can follow the existing identity patterns. Fix two pre-existing notification gaps (`pr_review_requested`, orchestrator duplicate stub) while touching notification code.

---

## 1. Current state summary

### What already exists

| Area | Status |
|------|--------|
| **Notification engine** | `src/ghdcbot/engine/notifications.py` — routing, templates, dedupe, DM/channel delivery, audit |
| **Notification dispatch** | `Orchestrator.run_once()` → `_send_notifications_for_new_events()` |
| **Notification storage** | `notifications_sent` table in `sqlite.py` |
| **Notification config** | `NotificationConfig` in `models.py` — 5 toggles + CodeRabbit |
| **Notification tests** | 16 tests in `tests/test_notifications.py` |
| **Identity linking** | Full `/link`, `/verify-link`, `/unlink`, CLI parity, SQLite `identity_links` |
| **Status commands** | `/status`, `/identity status`, `/summary`, CLI `identity list` |
| **GitHub ingestion** | Rich event set for scoring; `pr_reviewed` includes `pr_author` in payload |
| **Week 4 onboarding** | Open PRs #23, #24, #25 (docs, validate, config cleanup) |

### What is partially complete

| Item | Detail |
|------|--------|
| **`pr_review_requested` notifications** | Config flag + message template in `notifications.py`, documented in `TECHNICAL_DOCUMENTATION.md` — but **no ingestion event** and **not in orchestrator filter** (`week3_notes.md` L256) |
| **`pr_reviewed` COMMENT** | Ingested via `_pull_request_reviews()`; test asserts COMMENT **does not** notify (`test_send_notification_pr_reviewed_comment`) — opposite of Week 5 goal for `pr_review_comment` |
| **Planned notification flags in `config/example.yaml`** | Commented lines for `pr_closed`, `pr_reopened`, `issue_reopened`, `pr_review_comment` (L54–58) — **not in `NotificationConfig` model** |
| **Orchestrator notification helper** | Duplicate function signature at `orchestrator.py` L312–320 and L321–328 (dead stub + real body); messy but Python uses second definition |
| **Week 4 PRs** | Implemented locally, **open on GitHub**, not on `main` |

### What is missing entirely

| Item | Detail |
|------|--------|
| `pr_closed` | No ingestion event, no config flag, no template, no tests |
| `pr_reopened` | No ingestion event, no config flag, no template, no tests |
| `issue_reopened` | No ingestion event (`issue_closed` exists; no reopen) |
| `pr_review_comment` as notify-on | Explicitly skipped in `notifications.py` L61–72 |
| **Social profiles** | No table, service, commands, or tests |
| **`test_github_ingestion_lifecycle_events.py`** | Was planned in Week 4; **not present** on current branch (only `test_github_ingestion_comments.py`) |
| **`event_coverage.md`** | Not in repo |

---

# Phase 1 — Notification system audit

## Architecture flow

```text
GitHubRestAdapter.ingest_events()
  → ContributionEvent list (this sync window)
  → Orchestrator.run_once() stores contributions
  → _send_notifications_for_new_events(contributions, ...)
       filter: issue_assigned | pr_reviewed | pr_merged   ← orchestrator.py L336
  → send_notification_for_event() per event
       → resolve verified Discord user (identity_links)
       → dedupe (notifications_sent)
       → _build_notification_message()
       → _send_discord_notification() → send_dm() or send_message()
       → audit event github_notification_sent
```

## What notification types exist today?

| Event (ingested) | Notifies? | Config flag | Recipient |
|------------------|-----------|-------------|-----------|
| `issue_assigned` | ✅ | `issue_assignment` | Assignee (`event.github_user`) |
| `pr_reviewed` APPROVED | ✅ | `pr_review_result` | PR author (`payload.pr_author`) |
| `pr_reviewed` CHANGES_REQUESTED | ✅ | `pr_review_result` | PR author |
| `pr_reviewed` COMMENT | ❌ skipped | — | — |
| `pr_merged` | ✅ | `pr_merged` | Merger / `event.github_user` |
| `pr_review_requested` | ❌ never ingested | `pr_review_requested` (unused) | — |
| CodeRabbit reminder | ✅ separate path | `coderabbit_reminders` | PR author |

**Ingested but never notified:** `issue_opened`, `issue_closed`, `pr_opened`, `comment`, `helpful_comment`, `pr_reverted`, etc. (scoring/planning only).

## Files involved

| File | Role |
|------|------|
| `src/ghdcbot/engine/notifications.py` | Core: `send_notification_for_event`, templates, dedupe, delivery, CodeRabbit |
| `src/ghdcbot/engine/orchestrator.py` | Calls notification pass after ingestion (L125–152) |
| `src/ghdcbot/config/models.py` | `NotificationConfig` (L73–91) |
| `src/ghdcbot/adapters/github/rest.py` | Ingestion: issues, PRs, reviews, assignments |
| `src/ghdcbot/adapters/discord/api.py` | `send_dm()`, `send_message()` (L171–196) |
| `src/ghdcbot/adapters/storage/sqlite.py` | `notifications_sent`, `was_notification_sent`, `mark_notification_sent` |
| `tests/test_notifications.py` | 16 unit tests |

## How notifications are generated

1. Events are created during GitHub REST ingestion with `event_type` + `payload`.
2. Orchestrator passes **this run's** `contributions` list (not historical DB) to the notification pass.
3. `send_notification_for_event()` maps event → config flag → target GitHub user → Discord ID via `list_verified_identity_mappings()`.
4. `_build_notification_message()` returns markdown string.

## How notifications are delivered

| Mode | Condition | API |
|------|-----------|-----|
| **DM** (default) | `config.channel_id is None` | `DiscordApiAdapter.send_dm()` |
| **Channel** | `channel_id` set | `DiscordApiAdapter.send_message()` |
| **Skipped** | `policy.allow_discord_mutations is False` | dry-run / observer — no send |

## Config toggles (`NotificationConfig`)

```python
# src/ghdcbot/config/models.py L73-84
enabled, issue_assignment, pr_review_requested, pr_review_result,
pr_merged, coderabbit_reminders, coderabbit_reminder_after_hours,
coderabbit_bot_logins, channel_id
```

## Deduplication

- Key built in `_build_dedupe_key()` — for `pr_reviewed` includes `review_id` + `state`.
- Stored in `notifications_sent.dedupe_key` (PRIMARY KEY).
- Prevents re-notify on re-sync of same event.

## Tests

- **16 tests** in `tests/test_notifications.py` — message building, verified-only, dedupe, dry-run, channel mode, audit, COMMENT skip.
- **No orchestrator integration test** for notification dispatch.
- **No ingestion tests** for PR close/reopen lifecycle (file does not exist).

---

# Phase 2 — Notification expansion readiness

## Per-event assessment

### `pr_closed`

| Aspect | Status |
|--------|--------|
| Ingestion | ❌ **Missing** — `_collect_pull_request_events()` emits `pr_opened`, `pr_merged`, not close-without-merge |
| Config flag | ❌ Not in `NotificationConfig` (only in commented `example.yaml`) |
| Routing | ❌ |
| Template | ❌ |
| Orchestrator filter | ❌ |
| Tests | ❌ |

**Implementation approach:** In `_collect_pull_request_events()`, when `pr["state"]=="closed"` and no `merged_at`, and `closed_at >= since`, emit `pr_closed` with `pr_author`, `pr_number`, `title` in payload.

| Field | Estimate |
|-------|----------|
| Complexity | **Medium** |
| Files | `rest.py`, `models.py`, `notifications.py`, `orchestrator.py`, `config/example.yaml`, `aussie.yaml`, tests |
| Risk | Distinguish close vs merge (must not double-notify with `pr_merged`) |

**Verdict:** Ready to implement — not blocked.

---

### `pr_reopened`

| Aspect | Status |
|--------|--------|
| Ingestion | ❌ **Missing** |
| Config flag | ❌ |
| Routing / template | ❌ |
| Tests | ❌ |

**Implementation approach:** Use PR timeline or issues timeline `reopened` events, or compare `updated_at` with state transitions. GitHub Pull Request Timeline: `GET /repos/{owner}/{repo}/issues/{pr_number}/timeline` with `event: reopened` (PRs are issues in GitHub API).

| Field | Estimate |
|-------|----------|
| Complexity | **Medium–High** (timeline API, pagination) |
| Dependencies | Same timeline pattern as `issue_assigned` (`_issue_assignment_events`) |
| Risk | Extra API calls per PR; rate limits |

**Verdict:** Ready to implement — design timeline fetch similar to assignments.

---

### `issue_reopened`

| Aspect | Status |
|--------|--------|
| Ingestion | ❌ **Missing** — `issue_closed` exists (`rest.py` L1006–1015), no reopen |
| Config flag | ❌ |
| Routing / template | ❌ |
| Tests | ❌ |

**Implementation approach:** Extend `_issue_events()` or add `_issue_timeline_events()` for `reopened` on issue timeline; notify **current assignee** from issue payload or timeline.

| Field | Estimate |
|-------|----------|
| Complexity | **Medium** |
| Risk | No assignee → skip (per Week 5 plan) |

**Verdict:** Ready to implement.

---

### `pr_review_comment` (`pr_reviewed` + `COMMENT`)

| Aspect | Status |
|--------|--------|
| Ingestion | ✅ **Already ingested** — `_pull_request_reviews()` with `state: COMMENT`, `pr_author` in payload |
| Config flag | ❌ Need `pr_review_comment: bool` |
| Routing | ⚠️ **Explicitly rejected** — `notifications.py` L61–72 returns False for COMMENT |
| Template | ❌ |
| Test | ⚠️ `test_send_notification_pr_reviewed_comment` expects **no** notification — must **invert** when flag enabled |

| Field | Estimate |
|-------|----------|
| Complexity | **Low** (routing + template only) |
| Risk | Spam if many review comments — dedupe by `review_id` already helps |

**Verdict:** Easiest of the four — ingestion done; change routing + add template.

---

## Summary table

| Event | Ingested? | Notified? | Work remaining |
|-------|-----------|-----------|----------------|
| `pr_closed` | ❌ | ❌ | Ingestion + full notify stack |
| `pr_reopened` | ❌ | ❌ | Timeline ingestion + full stack |
| `issue_reopened` | ❌ | ❌ | Timeline ingestion + full stack |
| `pr_review_comment` | ✅ | ❌ (intentional skip) | Config + routing + template + test update |

## Bonus fix (pre-existing)

| Issue | Action |
|-------|--------|
| `pr_review_requested` never fires | Add ingestion OR remove from docs; add to orchestrator filter if ingested |
| Duplicate `_send_notifications_for_new_events` stub | Clean up `orchestrator.py` L312–332 while editing |

## Estimated total effort (notifications PR)

| Task | Days |
|------|------|
| `pr_review_comment` | 0.5 |
| `pr_closed` | 0.5–1 |
| `issue_reopened` + `pr_reopened` | 1–1.5 |
| Tests + docs + orchestrator cleanup | 0.5–1 |
| Manual AOSSIE test | 0.5 |
| **Total** | **~2.5–4 days** |

---

# Phase 3 — Identity linking audit

## Commands

| Surface | Commands |
|---------|----------|
| **Discord** | `/link`, `/verify-link`, `/verify` (button), `/identity status`, `/status`, `/unlink`, `/summary` |
| **CLI** | `ghdcbot link`, `verify-link`, `unlink`, `identity status`, `identity list` |

## Database: `identity_links`

```sql
-- sqlite.py L47-55 (+ migrations)
discord_user_id, github_user, verified, verification_code,
expires_at, created_at, verified_at, unlinked_at, github_user_normalized
PRIMARY KEY (discord_user_id, github_user)
```

## Verification flow

1. `IdentityLinkService.create_claim()` — 10-char code, 10 min TTL  
2. User puts code in GitHub bio or public gist  
3. `GitHubIdentityReader.search_verification_code()` — bio + gists  
4. `mark_identity_verified()` — `verified=1`, audit `identity_verified`  
5. Unlink with cooldown (`identity.unlink_cooldown_hours`, default 24h)

## Key files

| File | Purpose |
|------|---------|
| `src/ghdcbot/engine/identity_linking.py` | `IdentityLinkService`, `LinkClaim` |
| `src/ghdcbot/adapters/github/identity.py` | GitHub bio/gist search |
| `src/ghdcbot/adapters/storage/sqlite.py` | CRUD, `get_identity_status`, `list_verified_identity_mappings` |
| `src/ghdcbot/bot.py` | Slash commands, `IdentityVerificationView` |
| `docs/IDENTITY_VERIFICATION.md` | 680+ lines end-to-end doc |
| `tests/test_identity_linking.py` | **32 tests** |

## GitHub ↔ Discord mapping

- Verified rows in `identity_links` used by notifications, planning, `/summary`, issue assignment.
- `list_verified_identity_mappings()` is the canonical read API.

---

# Phase 4 — Social profile linking feasibility

## Current state

**Zero implementation** — no `social_profiles` table, no commands, no models.

## Recommended architecture

### Database (new table)

```sql
CREATE TABLE social_profiles (
  discord_user_id TEXT PRIMARY KEY,
  x_handle TEXT,
  linkedin_url TEXT,
  updated_at TEXT NOT NULL
);
```

Keep separate from `identity_links` — one GitHub link, optional socials, independent lifecycle.

### Service layer

`src/ghdcbot/engine/social_profiles.py`:

- `require_verified_github(discord_user_id)` — gate all writes  
- `set_x_handle()`, `set_linkedin_url()`, `remove_x()`, `remove_linkedin()`, `get_profile()`  
- Normalization: X `[A-Za-z0-9_]{1,15}`; LinkedIn URL canonicalization  

### Recommended commands

Prefer **`/profile` group** over `/set-social` (matches `/identity` pattern):

| Command | Behavior |
|---------|----------|
| `/profile` | Show GitHub (from identity) + X + LinkedIn |
| `/profile set x:<handle>` | Set X (verified users only) |
| `/profile set linkedin:<url>` | Set LinkedIn |
| `/profile remove x` | Clear X |
| `/profile remove linkedin` | Clear LinkedIn |

**Alternative:** `/social` — functionally equivalent; pick one for consistency with `docs/IDENTITY_VERIFICATION.md`.

### Validation (v1 — no OAuth)

| Field | Validation |
|-------|------------|
| X | Strip `@`, regex, length 1–15 |
| LinkedIn | Must match `linkedin.com/in/` or normalize bare username to `https://www.linkedin.com/in/{user}` |

### Privacy

| Decision needed | Options |
|-----------------|---------|
| Visibility | Self-only (ephemeral `/profile`) vs guild-visible |
| Mentor export | Add columns to `identity list` CLI? |
| Public governance page | Out of scope Week 5 |

**Recommendation:** Self-only ephemeral by default; mentors use CLI export if approved.

### UI/UX flow

```text
/link + verify GitHub
  → /profile set x myhandle
  → /profile set linkedin https://linkedin.com/in/myname
  → /profile  (confirms all links)
  → /status   (optional: add social lines here too)
```

---

# Phase 5 — Status command audit

## Commands investigated

| Command | Location | Shows today | Social-ready? |
|---------|----------|-------------|---------------|
| `/status` | `bot.py` L416–446 | Activity window, linked GitHub, roles | ✅ Add 2 lines from `social_profiles` |
| `/identity status` | `bot.py` L510–549 | GitHub, verification state, stale warning | Optional add socials |
| `/summary` | `bot.py` L453–499 | Contribution metrics (requires verified GitHub) | Keep separate; socials not needed |
| `identity list` (CLI) | `cli.py` L185–196 | Discord ID ↔ GitHub | Optional mentor columns |

## Files to modify (social display)

- `src/ghdcbot/bot.py` — `/status` and/or `/profile`  
- `src/ghdcbot/cli.py` — optional `identity list` extension  
- `src/ghdcbot/adapters/storage/sqlite.py` — read social_profiles  

## Permission concerns

- **Low risk** — self-service profile data, no mutations to others' records.
- If guild-visible `/profile @user` added later, need permission check (mentor roles or same as `/assign-issue`).

---

# Phase 6 — Documentation audit

| Doc | Notification coverage | Week 5 updates needed |
|-----|----------------------|------------------------|
| `TECHNICAL_DOCUMENTATION.md` | §4.2 lists 4 types; omits expansion | Add new flags + events |
| `docs/IDENTITY_VERIFICATION.md` | Comprehensive GitHub only | New § Social profiles |
| `INSTALLATION.md` | Week 4 validate; not notifications | Optional slash command table |
| `config/example.yaml` | Commented future flags L54–58 | Uncomment + align with model |
| `config/examples/aussie.yaml` | Live notifications L54–62; no expansion flags | Add after implementation |
| `README.md` | High-level | Optional feature bullet |
| `week5_plan.md` | Planning doc | Keep as companion |

**Gap:** `example.yaml` documents flags that **do not exist** in `NotificationConfig` — confusing until implemented or comments removed.

---

# Phase 7 — Testing audit

## Current test coverage

| Area | File | Count | Notes |
|------|------|-------|-------|
| Notifications | `tests/test_notifications.py` | 16 | Unit tests only; COMMENT = no notify |
| Identity | `tests/test_identity_linking.py` | 32 | Service, CLI, Discord status mocks |
| Bot login UX | `tests/test_bot_login_failure.py` | 1 | Week 4 PR |
| GitHub ingestion | `tests/test_github_ingestion_comments.py` | comments only | No lifecycle PR/issue tests |
| Discord slash E2E | — | **0** | No live Discord tests |
| Social profiles | — | **0** | — |

**Total tests on branch:** ~245 (per Week 4 run)

## Recommended tests for Week 5

### Notifications PR

- `test_build_notification_message_pr_closed` / `pr_reopened` / `issue_reopened` / `pr_review_comment`
- `test_send_notification_*` for each (verified, dedupe, disabled flag)
- **New:** `tests/test_github_ingestion_lifecycle_events.py` — mock timeline/PR responses
- **Update:** `test_send_notification_pr_reviewed_comment` — notify when `pr_review_comment=True`
- Optional: orchestrator integration test with mock contributions

### Social profiles PR

- `tests/test_social_profiles.py` — validation, verified gate, CRUD
- Bot command tests via mock `Interaction` (pattern in `test_identity_linking.py`)

### Manual

- AOSSIE guild: link account → trigger PR close → DM received  
- `/profile set x` → `/profile` shows handle  

---

# 4. Week 5 risk analysis

| Risk | Severity | Mitigation |
|------|----------|------------|
| Week 4 PRs #23–#25 not merged | Medium | Branch from `main`; rebase when merged |
| Notification work was lost from Week 4 local branch | Low | Re-implement from this assessment (~2–3 days) |
| `pr_reopened` / `issue_reopened` timeline API cost | Medium | Reuse pagination helpers; limit to repos in cursor window |
| `pr_closed` vs `pr_merged` double notify | Medium | Only emit `pr_closed` when `merged_at` is null |
| COMMENT review spam | Low | Dedupe by `review_id`; config default `true` with mentor opt-out |
| Social profile abuse (fake URLs) | Low | URL validation only; no claim of verification |
| Mentor visibility undecided | Low | Ship self-only; defer guild-visible |
| Orchestrator duplicate stub | Low | Clean up while editing notifications |
| `pr_review_requested` doc/code drift | Low | Fix or document as future work |

## Mentor decisions required

1. Social profiles: **self-only** or **guild-visible**?  
2. Show socials in **`identity list`** CLI for mentors?  
3. Fix **`pr_review_requested`** in same PR or separate?  
4. Confirm **`pr_review_comment`** should notify (changes current behavior/test)?

---

# 5. Week 5 recommended plan

Assumes Week 4 PRs merge early in the week. No coding on Saturday unless catching up.

| Day | Goals | Deliverable |
|-----|-------|-------------|
| **Monday** | Branch `feat/notification-lifecycle-events` from `main`; fix orchestrator stub; add `NotificationConfig` flags; implement `pr_review_comment` routing + template; update COMMENT test | PR draft opened |
| **Tuesday** | Ingest `pr_closed` in `rest.py`; templates + routing; ingestion tests | `pr_closed` complete |
| **Wednesday** | Ingest `issue_reopened` + `pr_reopened` via timeline; templates + routing; expand `test_notifications.py` | All 4 events coded |
| **Thursday** | `event_coverage.md` + doc updates (`TECHNICAL_DOCUMENTATION.md`, `aussie.yaml`); PR review fixes; **merge notifications PR** | Notifications live on `main` |
| **Friday** | Branch `feat/social-profile-linking`; schema + service + validation tests | Storage layer done |
| **Saturday** | `/profile` Discord commands; `/status` integration; `IDENTITY_VERIFICATION.md`; open social PR; AOSSIE manual smoke test | Social PR open |

### Week 5 goal checklist mapping

| # | Goal | Day |
|---|------|-----|
| 1 | Open/merge notification PR | Mon–Thu |
| 2 | Config, routing, templates, tests | Mon–Wed |
| 3 | Design social linking | Fri (design doc in PR description) |
| 4 | Storage + slash commands | Fri–Sat |
| 5 | Show in `/status` | Sat |
| 6 | Documentation | Thu + Sat |
| 7 | Tests + AOSSIE manual | Wed–Sat |

---

# Appendix — Code references

### Notification orchestrator filter (current)

```336:336:src/ghdcbot/engine/orchestrator.py
        if event.event_type in {"issue_assigned", "pr_reviewed", "pr_merged"}:
```

### COMMENT reviews skipped (current)

```61:72:src/ghdcbot/engine/notifications.py
        else:
            # COMMENT, DISMISSED, or other states - no notification
            logger.debug(
                "Skipping notification: PR review state is not APPROVED or CHANGES_REQUESTED",
                ...
            )
            return False
```

### `pr_reviewed` ingestion includes `pr_author`

```761:768:src/ghdcbot/adapters/github/rest.py
                payload = {
                    "pr_number": pr_number,
                    "review_id": review.get("id"),
                    "state": review.get("state"),
                    ...
                }
                if pr_author:
                    payload["pr_author"] = pr_author
```

### `/status` GitHub display (extension point)

```424:437:src/ghdcbot/bot.py
            verified_row = next((r for r in links if int(r.get("verified") or 0) == 1), None)
            if verified_row:
                lines.append(f"**Linked GitHub:** {verified_row.get('github_user', '?')}.")
```

### Open Week 4 PRs (dependency)

- [#23](https://github.com/AOSSIE-Org/Gitcord-GithubDiscordBot/pull/23) docs  
- [#24](https://github.com/AOSSIE-Org/Gitcord-GithubDiscordBot/pull/24) validate  
- [#25](https://github.com/AOSSIE-Org/Gitcord-GithubDiscordBot/pull/25) config UX  

---

**Assessment complete.** Implementation can proceed Monday with notification PR first, social profiles second. No scoring work required.
