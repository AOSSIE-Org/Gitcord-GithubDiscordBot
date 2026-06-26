# GSoC 2026 – Week 5 Plan (Revised)

**Theme:** Contributor identity & notifications (not scoring)

**Mentor direction (Bruno):** Do **not** implement the transparent scoring system in AOSSIE/Gitcord for now. Pivot Week 5 to:

1. Ship the **notification expansion** PR (lifecycle events)
2. Add **social profile linking** so verified contributors can register **X (Twitter)** and **LinkedIn** from Discord

**Prerequisite:** Week 4 onboarding PRs [#23](https://github.com/AOSSIE-Org/Gitcord-GithubDiscordBot/pull/23) · [#24](https://github.com/AOSSIE-Org/Gitcord-GithubDiscordBot/pull/24) · [#25](https://github.com/AOSSIE-Org/Gitcord-GithubDiscordBot/pull/25) should merge or be actively in review before Week 5 ends.

---

## Spreadsheet row (copy to your tracker)

| Field | Value |
|-------|--------|
| **On schedule?** | yes |
| **Demo?** | Discord: new notifications + `/profile` commands |
| **Communicated with mentors?** | yes (Bruno — scoring deferred) |
| **Blockers?** | None |

### Planned goals (revised)

1. Open and merge **notification expansion** PR (`pr_closed`, `pr_reopened`, `issue_reopened`, `pr_review_comment`)
2. Add config flags, routing, templates, and tests for new notification types
3. Design **social profile linking** (X + LinkedIn) for verified contributors
4. Implement storage + Discord slash commands to set/view/remove social profiles
5. Show linked socials in `/status` (and optionally mentor-facing `identity list`)
6. Document new commands and notification options
7. Tests + manual Discord verification on AOSSIE guild

### Revised goals (if scope tightens)

- Drop mentor `identity list` social columns → ship set/view/unlink only
- LinkedIn: URL or vanity handle only (no API verification in v1)
- X: handle only (no OAuth in v1)

### Goals achieved

*(fill at end of week)*

---

## Why we changed the plan

| Original Week 5 | New Week 5 |
|-----------------|------------|
| Transparent scoring system | **Deferred** (not needed for AOSSIE deployment now) |
| Configurable weights / difficulty | Out of scope |
| Score storage & reporting | Out of scope |
| — | **Notifications** for more GitHub lifecycle events |
| — | **X + LinkedIn** on contributor profiles |

Scoring still exists in the codebase (`enable_scoring`); we are not extending it this week.

---

## Workstream A — Notification expansion PR

**Branch:** `feat/notification-lifecycle-events` (from `main` after Week 4 merges)

**Title:** `feat: expand verified notifications for PR/issue lifecycle events`

### What to build

| Event | Notify who | Config flag (default) |
|-------|------------|------------------------|
| `pr_closed` | PR author | `notifications.pr_closed` (true) |
| `pr_reopened` | PR author | `notifications.pr_reopened` (true) |
| `issue_reopened` | Assignee (if any) | `notifications.issue_reopened` (true) |
| `pr_reviewed` (COMMENT) | PR author | `notifications.pr_review_comment` (true) |

### Files (expected)

```
src/ghdcbot/config/models.py          # NotificationConfig flags
src/ghdcbot/engine/notifications.py   # routing + message templates
src/ghdcbot/engine/orchestrator.py    # include events in notification pass
src/ghdcbot/adapters/github/rest.py   # enrich payloads (pr_author, assignee)
config/example.yaml                   # document new flags
tests/test_notifications.py
tests/test_github_ingestion_lifecycle_events.py
event_coverage.md                     # optional doc
```

### PR checklist

- [ ] Only notification/ingestion files (no Week 4 onboarding mix)
- [ ] All notification tests pass
- [ ] `event_coverage.md` lists old + new events
- [ ] Example config comments for each flag
- [ ] Manual test: dry-run sync → trigger event → DM in Discord

### Note

This work was implemented locally during Week 4 but **not merged** (kept separate from onboarding PRs). Re-apply on a clean branch from `main`.

---

## Workstream B — Social profile linking (X + LinkedIn)

**Branch:** `feat/social-profile-linking` (stack after notifications PR or parallel if small)

**Title:** `feat: let verified contributors link X and LinkedIn profiles`

### User story

> As a verified contributor, I want to add my X and LinkedIn accounts in Discord so mentors and the org can see my public profiles alongside my linked GitHub.

### Design principles

- **Requires verified GitHub link** (`/link` + `/verify-link` completed) before setting socials
- **Self-reported v1** — no X/LinkedIn OAuth (same philosophy as GitHub bio verification)
- **Guild-scoped** — profiles stored per `discord_user_id`
- **Easy to fix mistakes** — update or remove without cooldown (unlike GitHub unlink)

### Proposed commands

| Command | Description |
|---------|-------------|
| `/profile` | Show GitHub link status + X + LinkedIn (if set) |
| `/profile set x <handle>` | Set X handle (`@user` or `user`, normalized) |
| `/profile set linkedin <url-or-handle>` | Set LinkedIn profile URL or `in/username` |
| `/profile remove x` | Clear X |
| `/profile remove linkedin` | Clear LinkedIn |

*Alternative naming:* `/social` group — pick one; document in `INSTALLATION.md`.

### Validation rules

| Field | Rules |
|-------|--------|
| **X handle** | 1–15 chars, `[A-Za-z0-9_]`; strip `@`; store without `@` |
| **LinkedIn** | Accept `https://linkedin.com/in/...`, `linkedin.com/in/...`, or bare `in/username` / `username`; normalize to canonical URL |

### Storage

New table (preferred over widening `identity_links`):

```sql
CREATE TABLE social_profiles (
  discord_user_id TEXT PRIMARY KEY,
  x_handle TEXT,
  linkedin_url TEXT,
  updated_at TEXT NOT NULL
);
```

### Files (expected)

```
src/ghdcbot/adapters/storage/sqlite.py     # schema + CRUD
src/ghdcbot/engine/social_profiles.py        # validation + service layer
src/ghdcbot/bot.py                         # slash commands
src/ghdcbot/cli.py                         # optional: profile status CLI
src/ghdcbot/config/models.py               # optional SocialProfileConfig
tests/test_social_profiles.py
docs/IDENTITY_VERIFICATION.md              # extend with social section
```

### Out of scope for Week 5 v1

- OAuth verification with X or LinkedIn APIs
- Auto-posting to social networks
- Public web “governance page” (can be Week 6+ if mentors want it)
- Scoring based on social activity

---

## Suggested week schedule

| Day | Focus | Deliverable |
|-----|-------|-------------|
| **Mon** | Re-create notification branch from `main`; config + models | PR draft open |
| **Tue** | Notification routing, templates, ingestion payloads | Tests green |
| **Wed** | Notification PR review fixes; merge if approved | Notifications live |
| **Thu** | Social profile schema + service + validation | Unit tests |
| **Fri** | Discord `/profile` commands + docs + AOSSIE manual test | PR #2 open |

---

## PR order (Week 5)

```text
1. feat: expand verified notifications for PR/issue lifecycle events
2. feat: let verified contributors link X and LinkedIn profiles
```

Keep **separate from** Week 4 PRs #23–#25 and **separate from each other** for review.

---

## Demo script (end of week)

1. Contributor with verified GitHub runs `/profile set x myhandle` and `/profile set linkedin in/myname`
2. `/profile` shows GitHub + X + LinkedIn links
3. Close/reopen a PR on a test repo → author gets Discord DM (if notifications enabled)
4. Mentor runs `ghdcbot identity list` (optional: show social column in follow-up)

---

## Risks & mitigations

| Risk | Mitigation |
|------|------------|
| Week 4 PRs not merged yet | Branch from `origin/main`; rebase when #23–#25 land |
| Notification code lost from local tree | Re-implement from `event_coverage` spec above (~1 day) |
| LinkedIn URL spam / bad input | Strict normalization + max length; reject non-LinkedIn domains |
| Scope creep into scoring | Explicitly out of scope per Bruno |

---

## Questions for Bruno / Bhavik (optional)

1. Should social profiles be visible to **all guild members** via `/profile @user` or **self-only**?
2. Should mentors see socials in `identity list` export?
3. Any required format for org governance page later (CSV export enough)?

---

## Success criteria

- [ ] Notification PR merged with tests
- [ ] Contributors can set/view/remove X and LinkedIn after GitHub verification
- [ ] Documentation updated
- [ ] No scoring system changes in either PR
- [ ] 245+ tests passing on `main` after merges
