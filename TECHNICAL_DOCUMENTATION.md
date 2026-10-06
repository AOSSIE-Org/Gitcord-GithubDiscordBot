# Gitcord Technical Documentation

**Version:** 1.0  
**Last Updated:** July 2026  
**Author:** Technical Architecture Review

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Architecture](#2-architecture)
3. [Storage Design](#3-storage-design)
4. [Features](#4-features)
5. [Safety & Policies](#5-safety--policies)
6. [GitHub Integration](#6-github-integration)
7. [Current Limitations](#7-current-limitations)
8. [Future GitHub-Backed Persistence](#8-future-github-backed-persistence)

---

## 1. Project Overview

### 1.1 Problem Statement

Gitcord solves the challenge of automating contributor recognition and task assignment in open-source organizations that use both GitHub and Discord. Organizations need:

- **Automated role management** based on GitHub contribution activity
- **Identity verification** between Discord and GitHub accounts without OAuth complexity
- **Contributor visibility** — profiles, metrics, open PRs, and verified-only notifications
- **Transparent audit trails** for all automated actions
- **GitHub-backed data persistence** without external databases (per Bruno's requirement)

### 1.2 Core Design Philosophy

Gitcord is built on four foundational principles:

#### **Offline-First Execution**
- Runs locally on-demand; no daemon or server required
- All processing happens in a single execution cycle (`run-once`)
- No external dependencies beyond GitHub and Discord APIs
- Suitable for cron jobs, manual runs, or CI/CD pipelines

#### **Audit-First Workflow**
- All planned changes are written to JSON and Markdown reports **before** any mutations
- Reports are generated in `data_dir/reports/audit.json` and `audit.md`
- Reviewers can inspect planned role changes, issue assignments, and scoring decisions
- Mutations only occur in `active` mode with explicit write permissions

#### **Deterministic Planning**
- Identical inputs (same GitHub events, Discord state, config) produce identical plans
- No randomness or time-dependent logic in planning
- Enables reproducible testing and debugging
- Plans are pure functions of input data

#### **Permission-Aware IO**
- Readers degrade gracefully when API permissions are missing
- Writers check `MutationPolicy` before executing any changes
- Failed reads don't crash the system; they produce partial results
- Safe to run with read-only tokens for observation

### 1.3 How Gitcord Works

Gitcord connects a GitHub organization and a Discord server so open-source communities can recognize contributors, review activity, and manage assignment workflows with less manual work. The project is designed around a simple loop:

```text
Load config -> Read GitHub and Discord -> Store activity -> Score contributors -> Plan actions -> Report -> Apply if allowed
```

The code starts from the CLI in `src/ghdcbot/cli.py`. The CLI loads a YAML configuration file, expands environment variables such as `GITHUB_TOKEN` and `DISCORD_TOKEN`, builds the configured adapters, and then either runs a one-time sync or starts the Discord bot.

For a one-time sync, `src/ghdcbot/engine/orchestrator.py` coordinates the whole system:

1. It initializes SQLite storage.
2. It resolves verified Discord-to-GitHub identity mappings.
3. It reads GitHub contribution activity from the configured organization.
4. It stores contribution events locally.
5. It computes contributor scores for the configured activity window.
6. It reads Discord member roles.
7. It plans role changes, issue assignments, review requests, notifications, and reports.
8. It applies GitHub or Discord changes only when the runtime mode and write permissions allow it.

The default operating style is safe by design. In `dry-run` and `observer` modes, Gitcord reads data and writes audit reports, but it does not change GitHub or Discord. In `active` mode, it can assign issues, request reviews, add or remove Discord roles, and send Discord messages, but only if the relevant write permissions are enabled in config.

### 1.4 Main Runtime Modes

Gitcord uses `RunMode` and `MutationPolicy` from `src/ghdcbot/core/modes.py` to decide whether actions may be applied.

| Mode | What Gitcord Does |
| --- | --- |
| `dry-run` | Reads data, computes plans, writes audit reports, skips mutations. |
| `observer` | Read-only observation mode, also skips mutations. |
| `active` | Applies GitHub and Discord changes when write permissions are enabled. |

This means a maintainer can test the complete workflow safely before allowing the bot to mutate GitHub or Discord.

### 1.5 Feature Summary

The current codebase provides these major Gitcord features:

- **Discord-GitHub identity linking:** Users verify GitHub ownership through a temporary code placed in their GitHub bio or public gist.
- **Contribution ingestion:** Gitcord reads GitHub issues, pull requests, merges, reviews, comments, helpful comments, reverted PRs, and failed-CI merge signals.
- **Merge-focused scoring:** Scores are based primarily on merged PRs, with optional difficulty labels and quality adjustments.
- **Discord role automation:** Roles can be added or removed from score thresholds, merge-count rules, and repo-contributor rules.
- **Issue assignment planning:** Eligible Discord roles can be mapped to GitHub users and used for deterministic issue assignment.
- **PR review assignment planning:** Gitcord can plan review requests for eligible reviewers.
- **Mentor-controlled issue assignment:** Contributors can request an issue, and mentors can approve, reject, or replace the assignee from Discord.
- **PR context previews:** The bot can show PR status, author, reviews, CI state, idle time, and mentor signal from a PR URL.
- **GitHub notifications:** Nine notification types cover the full lifecycle (issue assigned, PR review states, review comments, PR merged, PR closed, issue reopened, PR reopened), each gated by its own `NotificationConfig` flag except PR Approved and Changes Requested, which share `pr_review_result`
- **Verified-only notifications:** All notifications are sent only to verified Discord users and are deduplicated to prevent duplicates
- **CodeRabbit reminders:** Optional reminders can notify PR authors about old CodeRabbit review comments
- **Audit reports:** Dry runs generate JSON and Markdown reports of planned Discord and GitHub actions
- **Activity reports:** Gitcord writes a human-readable activity feed for mentor visibility.
- **Audit event export:** CLI export supports JSON, CSV, and Markdown with filters for user, event type, and date range.
- **SQLite local state:** Contributions, cursors, identity links, issue requests, notifications, social profiles, and audit events are stored locally.
- **Docker support:** The project includes Docker and Docker Compose files for deployment.

### 1.6 Notification Types (GitHub → Discord)

Gitcord supports **9 notification types** that cover the full GitHub contribution lifecycle. All notifications are:
- **Verified-only:** Sent only to users who have verified their Discord-GitHub identity
- **Configurable:** Each type can be enabled/disabled via `NotificationConfig` (`pr_review_result` covers both PR Approved and Changes Requested)
- **Deduplicated:** Prevents duplicate notifications for the same event using deduplication keys
- **Recipient-aware:** Each event type routes to the appropriate GitHub user (assignee, author, or reviewer)

| Notification Type | Config Flag | Trigger | Recipient | Message | Dedup Key |
|---|---|---|---|---|---|
| **Issue Assigned** | `issue_assignment` | Issue assigned to a user | Assignee | 📋 Assigned to you: `[title]` | `issue_assigned:{repo}:{issue_number}:{user}` |
| **PR Review Requested** | `pr_review_requested` | Reviewer requested on PR | Reviewer | 👀 Review requested: `[title]` | `pr_review_requested:{repo}:{pr_number}:{user}:{reviewer_id}` |
| **PR Approved** | `pr_review_result` | PR review approved | PR Author | ✅ Approved: `[title]` | `pr_reviewed:{repo}:{pr_number}:{user}:{review_id}:APPROVED` |
| **Changes Requested** | `pr_review_result` | Changes requested on PR | PR Author | 🔁 Changes requested: `[title]` | `pr_reviewed:{repo}:{pr_number}:{user}:{review_id}:CHANGES_REQUESTED` |
| **Review Comment** | `pr_review_comment` | Comment posted on PR review | PR Author | 💬 Review comment: `[title]` | `pr_reviewed:{repo}:{pr_number}:{user}:{review_id}:COMMENT` |
| **PR Merged** | `pr_merged` | PR merged to main | PR Author | 🎉 Merged: `[title]` | `pr_merged:{repo}:{pr_number}:{user}` |
| **PR Closed** | `pr_closed` | PR closed without merge | PR Author | 🚫 Closed: `[title]` | `pr_closed:{repo}:{pr_number}:{user}:{closed_at}` |
| **Issue Reopened** | `issue_reopened` | Issue reopened | Current Assignee | 📌 Reopened: `[title]` | `issue_reopened:{repo}:{issue_number}:{user}:{reopened_at}` |
| **PR Reopened** | `pr_reopened` | PR reopened | PR Author | 🔄 Reopened: `[title]` | `pr_reopened:{repo}:{pr_number}:{user}:{reopened_at}` |

**Delivery Method:**
- Default: DM to verified Discord user
- Fallback: Send to configured `channel_id` if DM fails and channel is specified

**Configuration:**

```yaml
discord:
  notifications:
    enabled: true                    # Master switch for all notifications
    issue_assignment: true           # When issue is assigned
    pr_review_requested: true        # When reviewer is requested
    pr_review_result: true           # When review is approved or changes requested
    pr_review_comment: true          # When comment posted on review
    pr_merged: true                  # When PR is merged
    pr_closed: true                  # When PR is closed without merge
    issue_reopened: true             # When issue is reopened
    pr_reopened: true                # When PR is reopened
    channel_id: null                 # Optional fallback channel for notifications
```

### 1.6.1 PR Channel Status Timeline (optional)

With `notifications.pr_channel_timeline: true` (default `false`), each tracked PR channel post (from `pr_opened`) shows a status dot and a newest-first timeline, and is edited in place as the PR moves. It replaces the merge/close/reopen edit from `update_pr_channel_on_lifecycle`; it never posts new messages or sends DMs (review DMs are unchanged).

| Dot | Status | When |
| --- | --- | --- |
| 🔴 | Closed | PR closed without merge |
| 🔵 | Merged | PR merged |
| 🟢 | Approved | Latest review is an approval |
| 🟠 | Not approved | Latest review requested changes or only commented |
| 🟡 | Revised | New commits (or a force-push / merge of main) after the latest review |
| ⚪ | Created | No reviews yet |

Rules: anyone's review counts (people and bots alike, no role assumptions); the author's own replies, pending reviews and dismissed reviews do not change the colour; commits from bot accounts and commits before the first review are not shown as revisions. Drafts keep their colour and get a `· Draft` label. Dates use Discord timestamps (each reader's local time); repeated steps collapse to `(×N)` and long histories show the 6 newest lines plus `Created`.

Each sync, Gitcord records the head commit of open tracked PRs (to spot pushes), queues posts whose PR had new activity, rebuilds each queued post from GitHub (PR, issue timeline, commits; read-only) and edits it only if the rendered post changed. Posts are never rebuilt from partial GitHub data; a post that fails 5 times in a row stops retrying until its PR has new activity. In dry-run / observer mode (or with Discord writes disabled) queued posts are neither read nor edited; they stay queued until a run that can edit them (use `preview-pr-timeline` to see what they would show). Existing posts switch to the timeline style the next time their PR has activity.

Preview the post for real PRs without editing anything:

```bash
ghdcbot --config config/config.yaml preview-pr-timeline --repo Gitcord-GithubDiscordBot --pr 106 --pr 107
```

### 1.7 Main Code Areas

| Area | Files | Responsibility |
| --- | --- | --- |
| CLI | `src/ghdcbot/cli.py`, `src/ghdcbot/__main__.py` | Command-line entry points and command routing. |
| Config | `src/ghdcbot/config/loader.py`, `src/ghdcbot/config/models.py` | YAML loading, env expansion, Pydantic validation. |
| Core models | `src/ghdcbot/core/models.py`, `src/ghdcbot/core/interfaces.py`, `src/ghdcbot/core/modes.py` | Shared dataclasses, protocols, and mutation policy. |
| GitHub adapter | `src/ghdcbot/adapters/github/rest.py` | GitHub ingestion, assignments, review requests. |
| Discord adapter | `src/ghdcbot/adapters/discord/api.py` | Discord role reads/writes, DMs, channel messages. |
| Storage | `src/ghdcbot/adapters/storage/sqlite.py` | SQLite schema and persistence methods. |
| Orchestration | `src/ghdcbot/engine/orchestrator.py` | Main sync pipeline. |
| Scoring | `src/ghdcbot/engine/scoring.py` | Weighted contributor scoring. |
| Planning | `src/ghdcbot/engine/planning.py`, `src/ghdcbot/engine/assignment.py` | Role plans, assignment plans, review plans. |
| Discord bot | `src/ghdcbot/bot.py` | Slash commands and Discord UI workflows. |
| Identity | `src/ghdcbot/engine/identity_linking.py`, `src/ghdcbot/adapters/github/identity.py` | GitHub account verification. |
| Reports | `src/ghdcbot/engine/reporting.py`, `src/ghdcbot/engine/audit_export.py` | Audit report rendering and export. |
| Notifications | `src/ghdcbot/engine/notifications.py` | GitHub-to-Discord notification logic. |
| PR timeline | `src/ghdcbot/engine/pr_timeline.py`, `src/ghdcbot/engine/pr_timeline_refresh.py` | PR status/timeline rules and the per-sync PR channel post refresh. |
---

## 2. Architecture

### 2.1 High-Level Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        CLI Entry Point                      │
│              (ghdcbot.cli or Discord Bot)                   │
└───────────────────────┬─────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│                      Orchestrator                           │
│  Coordinates: Read → Score → Plan → Report → Apply          │
└───────┬───────────────┬───────────────┬──────────────────────┘
        │               │               │
        ▼               ▼               ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│   Readers    │ │   Planners   │ │   Writers    │
│              │ │              │ │              │
│ GitHubReader │ │ ScoreStrategy│ │ GitHubWriter │
│ DiscordReader│ │ RolePlanner │ │ DiscordWriter│
└──────┬───────┘ └──────┬───────┘ └──────┬───────┘
       │                 │                 │
       └─────────────────┴─────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│                      Storage Layer                          │
│                     SQLite (local state)                    │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 Main Components

#### **CLI (`src/ghdcbot/cli.py`)**
- Entry point for `run-once`, `bot`, `link`, `verify-link`, `identity`, `export-audit`
- Parses command-line arguments and YAML config
- Builds adapters via plugin registry
- Constructs `Orchestrator` and executes commands

#### **Discord Bot (`src/ghdcbot/bot.py`)**
- Long-running Discord bot with slash commands
- Handles identity linking (`/link`, `/verify-link`, `/unlink`)
- Shows contributor profiles (`/profile` — GitHub, verification, socials, roles)
- Social profile linking (`/connect-social`, `/disconnect-social` — manual username/URL)
- Contribution metrics (`/summary`, `/open-prs`, `/pr`)
- Passive PR URL previews in configured channels
- Mentor-only sync (`/sync`)
- Mentor-only repo → channel routing (`/pr-channel set|remove|list`): routes stored in SQLite (`repo_channel_routes`) and layered on top of `discord.pr_open_channels` / `github.repos` by `engine/channel_routes.py` at bot startup, after each change, and at the start of every `run-once`

#### **Orchestrator (`src/ghdcbot/engine/orchestrator.py`)**
- Core execution engine for `run-once` cycle
- Coordinates: ingestion → scoring → planning → reporting → mutation
- Manages notification sending (verified-only)

#### **Storage (`src/ghdcbot/adapters/storage/sqlite.py`)**
- SQLite database for local state (`state.db`)
- Tables: `contributions`, `scores`, `cursors`, `identity_links`, `issue_requests`, `notifications_sent`
- Append-only audit log (`audit_events.jsonl`)
- Schema migrations via additive `ALTER TABLE` (backward compatible)

#### **GitHub Adapter (`src/ghdcbot/adapters/github/rest.py`)**
- Reads: contributions, issues, PRs via REST API
- Writes: issue assignments, review requests (never commits files to repos)
- Handles pagination, rate limiting, error recovery
- Filters repos based on config (`repos.mode`, `repos.names`)

#### **Discord Adapter (`src/ghdcbot/adapters/discord/api.py`)**
- Reads: guild members, roles via REST API
- Writes: role additions/removals, DMs, channel messages
- Handles permission degradation gracefully

### 2.3 Data Flow During `run-once`

```
1. Load Config
   └─> Parse YAML, load env vars, validate

2. Initialize Storage
   └─> Create/upgrade SQLite schema
   └─> Load identity mappings (verified from storage, fallback to config)

3. Ingest GitHub Events
   └─> Get cursor (last seen timestamp) or use period_start
   └─> List repos (filtered by config)
   └─> For each repo: fetch issues, PRs, events since cursor
   └─> Store events in SQLite `contributions` table
   └─> Update cursor to max(created_at)

4. Compute Scores
   └─> Load contributions from period_start to period_end
   └─> Apply WeightedScoreStrategy (configurable weights)
   └─> Support difficulty-based scoring (PR labels)
   └─> Upsert scores to SQLite `scores` table

5. Plan Changes
   └─> Load Discord member roles
   └─> Plan Discord role changes (score-based + merge-based)
   └─> Plan GitHub issue assignments (role-based round-robin)
   └─> Plan review requests (role-based round-robin)

6. Send Notifications (if enabled)
   └─> For each new contribution event:
       └─> Check if user is verified (Discord ↔ GitHub)
       └─> Check event type matches config
       └─> Deduplicate (check `notifications_sent` table)
       └─> Send DM or channel message
       └─> Record in `notifications_sent`

7. Generate Reports (dry-run/observer modes)
   └─> Write `audit.json` (machine-readable)
   └─> Write `audit.md` (human-readable)
   └─> Write `activity.md` (event feed per repo)

8. Apply Mutations (active mode only)
   └─> Apply Discord role plans (add/remove)
   └─> Apply GitHub assignment plans (assign issues, request reviews)
   └─> All gated by MutationPolicy
```

### 2.4 Discord Commands Interaction

Discord bot commands interact with the system as follows:

**Identity Linking Flow:**
```
User: /link github_username
  └─> IdentityLinkService.create_claim()
      └─> Generate verification code
      └─> Store in SQLite `identity_links` (verified=0)
      └─> Return code to user

User: Adds code to GitHub bio/gist

User: /verify-link github_username
  └─> IdentityLinkService.verify_claim()
      └─> Fetch GitHub bio/gist via GitHubIdentityReader
      └─> Check for code match
      └─> Update `identity_links` (verified=1, verified_at=now)
      └─> Audit event: identity_verified
```

**Profile & Metrics:**

```text
User: /profile [contributor]
  └─> get_identity_status() from SQLite
  └─> social_service.get_profiles()
  └─> list_roles_for_member() via Discord API (offloaded to thread)
  └─> Return ephemeral profile message

User: /summary [contributor]
  └─> Resolve verified GitHub user
  └─> build_contribution_summary_message() (offloaded to thread)
  └─> Return 7-day and 30-day metrics + rank

User: /open-prs contributor
  └─> Resolve Discord member → GitHub user
  └─> List open PRs across configured repos (offloaded to thread)
```

**Sync Command:**
```
Mentor: /sync
  └─> Build Orchestrator (same as run-once)
  └─> Execute orchestrator.run_once()
  └─> Ingests events, sends notifications, updates roles
  └─> Returns success message
```

---

## 3. Storage Design

### 3.1 SQLite Tables

#### **`contributions`**
Stores raw GitHub contribution events.

```sql
CREATE TABLE contributions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    github_user TEXT NOT NULL,
    event_type TEXT NOT NULL,  -- 'issue_opened', 'pr_opened', 'pr_merged', 'pr_reviewed', 'comment'
    repo TEXT NOT NULL,
    created_at TEXT NOT NULL,  -- ISO-8601 UTC
    payload_json TEXT NOT NULL  -- JSON blob with event-specific data
);
```

**Purpose:** Historical record of all GitHub activity. Used for scoring and reports.

**Indexes:** None (queries filter by `created_at` range).

#### **`scores`**
Stores computed contribution scores per user per period.

```sql
CREATE TABLE scores (
    github_user TEXT NOT NULL,
    period_start TEXT NOT NULL,  -- ISO-8601 UTC
    period_end TEXT NOT NULL,     -- ISO-8601 UTC
    points INTEGER NOT NULL,
    updated_at TEXT NOT NULL,     -- ISO-8601 UTC
    PRIMARY KEY (github_user, period_start, period_end)
);
```

**Purpose:** Cached scores for role planning. Period boundaries match config `scoring.period_days`.

#### **`cursors`**
Tracks last-seen timestamp per data source.

```sql
CREATE TABLE cursors (
    source TEXT PRIMARY KEY,  -- e.g., 'github'
    cursor TEXT NOT NULL      -- ISO-8601 UTC timestamp
);
```

**Purpose:** Incremental ingestion. Prevents re-processing old events.

#### **`identity_links`**
Stores Discord ↔ GitHub identity mappings.

```sql
CREATE TABLE identity_links (
    discord_user_id TEXT NOT NULL,
    github_user TEXT NOT NULL,
    verified INTEGER NOT NULL DEFAULT 0,  -- 0=pending, 1=verified
    verification_code TEXT,
    expires_at TEXT,                       -- ISO-8601 UTC (for pending claims)
    created_at TEXT NOT NULL,              -- ISO-8601 UTC
    verified_at TEXT,                      -- ISO-8601 UTC (when verified)
    unlinked_at TEXT,                      -- ISO-8601 UTC (for unlink history)
    PRIMARY KEY (discord_user_id, github_user)
);
```

**Purpose:** Identity verification system. One verified mapping per Discord user. Supports stale refresh.

**Indexes:**
- `idx_identity_links_github_user` (for reverse lookup)
- `idx_identity_links_verified` (for filtering verified)

#### **`issue_requests`**
Stores contributor issue assignment requests.

```sql
CREATE TABLE issue_requests (
    request_id TEXT PRIMARY KEY,      -- UUID
    discord_user_id TEXT NOT NULL,
    github_user TEXT NOT NULL,
    owner TEXT NOT NULL,               -- GitHub org
    repo TEXT NOT NULL,
    issue_number INTEGER NOT NULL,
    issue_url TEXT NOT NULL,
    created_at TEXT NOT NULL,         -- ISO-8601 UTC
    status TEXT NOT NULL DEFAULT 'pending'  -- 'pending', 'approved', 'rejected', 'cancelled'
);
```

**Purpose:** Issue request workflow. Mentors review and approve/reject.

**Indexes:**
- `idx_issue_requests_status` (for filtering pending)
- `idx_issue_requests_created` (for sorting)

#### **`notifications_sent`**
Deduplication table for notifications.

```sql
CREATE TABLE notifications_sent (
    dedupe_key TEXT PRIMARY KEY,      -- Composite: event_type + github_user + repo + target
    event_type TEXT NOT NULL,
    github_user TEXT NOT NULL,
    discord_user_id TEXT NOT NULL,
    repo TEXT NOT NULL,
    target TEXT NOT NULL,              -- Issue/PR number or identifier
    channel_id TEXT,                   -- NULL = DM, else channel ID
    sent_at TEXT NOT NULL              -- ISO-8601 UTC
);
```

**Purpose:** Prevents duplicate notifications for same event.

**Indexes:**
- `idx_notifications_sent_github_user`
- `idx_notifications_sent_discord_user`

### 3.2 GitHub Snapshots (removed)

Gitcord used to commit JSON snapshots (`roles.json`, `identities.json`, `notifications.json`, …) to the org's `.gitcord` repo after each sync. Those repos were public, so the snapshots published every Discord member's ID and roles (including people who never used Gitcord), Discord ↔ GitHub identity links, and per-user notification logs. Nothing consumed them, so the feature was removed: Gitcord no longer commits files to any repository, and a `snapshots:` config block is ignored (a warning is logged when `snapshots.enabled` is true).

All persisted data now stays local in `data_dir`: SQLite (`state.db`, application state), the audit log (`audit_events.jsonl`) and generated reports (`reports/`). Back up the whole `data_dir` with the handover script or a volume backup. Any future GitHub-backed persistence (the original "no Supabase, use GitHub" direction) must write to a private repository and must not include Discord member data or identity links.

---

## 4. Features

### 4.1 Identity Linking

**Purpose:** Verify Discord users own their claimed GitHub accounts without OAuth.

**Flow:**
1. User runs `/link github_username` in Discord
2. Bot generates 10-character verification code (alphanumeric)
3. Code expires in 10 minutes (configurable via `ttl_minutes`)
4. User adds code to GitHub profile bio or a public gist
5. User runs `/verify-link github_username`
6. Bot fetches GitHub bio/gist via REST API
7. If code found → mark as verified, store `verified_at` timestamp
8. If code not found → return error, user can retry

**Security:**
- Impersonation protection: one GitHub user can only be verified by one Discord user
- Expired claims are cleaned up automatically
- Stale verification detection (configurable `identity.verified_max_age_days`)
- Unlink with cooldown (24 hours default)

**Storage:** `identity_links` table in SQLite

**Commands:**
- `/link` - Create claim
- `/verify-link` - Verify claim (GitHub bio/gist check; offloaded to thread)
- `/profile` - Show GitHub, verification, socials, and roles (self or another member)
- `/connect-social` / `/disconnect-social` - Link or remove X/LinkedIn (manual entry)
- `/summary` - Contribution metrics (self or another verified contributor)
- `/open-prs` - List a contributor's open PRs
- `/pr` - List a contributor's recent PRs grouped by closed / merged / open (`count` N, optional `skip` M)
- `/unlink` - Remove verified link (cooldown applies)

### 4.2 Notifications (Verified-Only)

**Purpose:** Send Discord notifications for GitHub events, but only to verified users.

**Event Types:**
- `issue_assigned` - User assigned to issue
- `pr_review_requested` - User requested as reviewer
- `pr_review_result` - PR review approved/changes requested (notifies PR author)
- `pr_merged` - User's PR merged

**Verification Requirement:**
- User must have verified Discord ↔ GitHub link
- Unverified users receive no notifications (anti-spam)

**Deduplication:**
- Uses `notifications_sent` table
- Dedupe key: `event_type + github_user + repo + target`
- Prevents duplicate notifications for same event

**Delivery:**
- DM (default): `channel_id = null`
- Channel posting: `channel_id` configured in `discord.notifications.channel_id`

**Configuration:**
```yaml
discord:
  notifications:
    enabled: true
    issue_assignment: true
    pr_review_requested: true
    pr_review_result: true
    pr_merged: true
    channel_id: null  # null = DM, or set channel ID
```

### 4.3 Contributor Profiles & Social Links

**`/profile`** shows for a Discord member (or self):
- GitHub username and verification status (verified, stale, pending, not linked)
- Linked social profiles (X, LinkedIn)
- Discord roles

**`/connect-social`** accepts a platform (`x` or `LinkedIn`) and username/URL. Re-running the command for the same platform updates the stored value (upsert). No OAuth or external app registration is required.

**`/disconnect-social`** removes a linked social profile.

### 4.4 Passive PR Previews

When `discord.pr_preview_channels` is configured, the bot monitors those channels. When a PR URL is detected in a message, it auto-fetches and posts an embed preview. No slash command is required.

**PR Context Includes:**
- Repository, PR number, title, state (open/closed/merged)
- Author (with Discord mention if linked)
- Review status (approved/changes_requested/pending)
- CI status (passing/failing/pending)
- Last commit time (relative: "2 hours ago")
- Mentor signal (ready/needs_review/blocked)

### 4.5 Role Automation

**Two Rule Types:**

#### **Score-Based Roles**
- Roles assigned based on contribution score thresholds
- Config: `role_mappings` (list of `discord_role` + `min_score`)
- Example: `Contributor` role at 10 points, `Maintainer` at 40 points
- Scoring period: `scoring.period_days` (default: 30 days)

#### **Merge-Based Roles**
- Roles assigned based on merged PR count
- Config: `merge_role_rules` (list of `discord_role` + `min_merged_prs`)
- Example: `apprentice` at 1 merged PR, `testing_role` at 2 merged PRs
- Only highest eligible role is assigned (deterministic)

**Role Removal:**
- Score-based roles removed if score drops below threshold
- Merge-based roles persist (never removed automatically)
- Final desired roles = `max(score_based, merge_based)`

**Congratulatory Messages:**
- When role is added → Bot sends DM congratulating user
- Only in active mode (mutations allowed)
- Fails gracefully if DMs disabled

### 4.6 Legacy Storage (`issue_requests`)

The `issue_requests` SQLite table remains for historical data. Discord slash commands for issue requests (`/request-issue`, `/issue-requests`) were removed. The `/assign-issue` command allows authorized Discord members to directly assign issues on GitHub.

### 4.7 Audit Logs

**Purpose:** Append-only log of all system actions.

**Storage:** `data_dir/audit_events.jsonl` (JSON Lines format)

**Event Types:**
- `identity_claim_created`
- `identity_verified`
- `identity_unlinked`
- `issue_request_created`
- `issue_request_approved`
- `issue_request_rejected`
- `issue_assigned_from_discord`
- `report_generated`
- `snapshot_written` (older logs only; snapshots were removed)

**Format:**
```json
{
  "event_type": "identity_verified",
  "actor_type": "discord_user",
  "actor_id": "123456789",
  "timestamp": "2026-02-16T23:20:25+00:00",
  "context": {
    "github_user": "alice",
    "location": "bio"
  }
}
```

**Export:**
- CLI: `ghdcbot --config config.yaml export-audit --format json|csv|md`
- Filters: `--user`, `--event-type`, `--from`, `--to`

---

## 5. Safety & Policies

### 5.1 MutationPolicy

**Purpose:** Gate all mutations (Discord roles, GitHub assignments) behind explicit policy.

**Structure:**
```python
@dataclass(frozen=True)
class MutationPolicy:
    mode: RunMode  # DRY_RUN, OBSERVER, ACTIVE
    github_write_allowed: bool
    discord_write_allowed: bool
    
    @property
    def allow_github_mutations(self) -> bool:
        return mode == ACTIVE and github_write_allowed
    
    @property
    def allow_discord_mutations(self) -> bool:
        return mode == ACTIVE and discord_write_allowed
```

**Usage:**
- All writers check `policy.allow_*_mutations` before executing
- Plans are always generated (for reports)
- Mutations only applied if policy allows

### 5.2 Run Modes

#### **DRY_RUN (Default)**
- Reads GitHub and Discord state
- Computes scores and plans
- Generates audit reports
- **No mutations** (Discord or GitHub)
- Safe for testing and review

#### **OBSERVER**
- Same as DRY_RUN
- Intended for read-only tokens
- Produces reports without write permissions

#### **ACTIVE**
- Full execution: reads, plans, **and applies mutations**
- Requires explicit config:
  ```yaml
  runtime:
    mode: "active"
  github:
    permissions:
      write: true
  discord:
    permissions:
      write: true
  ```

### 5.3 Deduplication

**Plans:**
- Dedupe key: `(repo, target_type, target_number, action, assignee)`
- Prevents duplicate assignments in same run

**Notifications:**
- Dedupe key: `event_type + github_user + repo + target`
- Stored in `notifications_sent` table
- Prevents duplicate notifications for same event

**Identity Claims:**
- One verified mapping per Discord user
- One verified mapping per GitHub user
- Pending claims expire after 10 minutes

### 5.4 Verified-Only Behavior

**Notifications:**
- Only sent to users with verified Discord ↔ GitHub link
- Unverified users receive no notifications (anti-spam)

**Issue Requests:**
- Contributors must be verified to request assignment
- Mentors can assign to any Discord user (they resolve to GitHub)

**Scoring:**
- Scores computed for all GitHub users (not just verified)
- But role assignment only applies to verified Discord users

---

## 6. GitHub Integration

### 6.1 Event Ingestion

**APIs Used:**
- `GET /orgs/{org}/repos` - List organization repositories
- `GET /repos/{owner}/{repo}/issues` - List open issues
- `GET /repos/{owner}/{repo}/pulls` - List open pull requests
- `GET /repos/{owner}/{repo}/issues/{issue_number}/events` - Issue events (assignments, labels)
- `GET /repos/{owner}/{repo}/pulls/{pr_number}/reviews` - PR reviews
- `GET /repos/{owner}/{repo}/commits` - Commit history (for PR context)

**Event Types Ingested:**
- `issue_opened` - New issue created (ingested for reports, not scored)
- `issue_assigned` - Issue assigned to user (triggers notification)
- `pr_opened` - New PR created (ingested for reports, not scored)
- `pr_merged` - PR merged (**only event that affects scores** - merge-only scoring)
- `pr_reviewed` - PR review submitted (approved/changes_requested/comment)
- `comment` - Comment on issue/PR (ingested for reports, not scored)
- `pr_reverted` - Reverted PR (quality penalty if configured)
- `pr_merged_with_failed_ci` - PR merged with failing CI (quality penalty if configured)
- `helpful_comment` - Comment marked as helpful (quality bonus if configured)

**Note on Scoring:** Gitcord uses merge-only scoring. Only `pr_merged` events contribute to contributor scores. Other events (issue_opened, pr_opened, comment) are ingested for audit trails and activity reports but do not affect scoring. This prevents spam and gaming while keeping the system simple and mentor-approved.

**Incremental Ingestion:**
- Uses `cursors` table to track last-seen timestamp
- Only fetches events since cursor
- Prevents re-processing old events

**Repo Filtering:**
- Config: `repos.mode` (`allow` or `deny`)
- Config: `repos.names` (list of repo names)
- Applied before ingestion

---

## 7. Current Limitations

### 7.1 Local Data Only

- Application state lives in SQLite (`state.db` in `data_dir`); losing it loses identity links, cursors and notification history.
- The audit log (`audit_events.jsonl`) and reports (`reports/`) sit next to it in `data_dir`; reports are regenerated each run, the audit log is not.
- `./scripts/gitcord-handover pack` and Docker volume backups copy the whole `data_dir` (database, audit log, reports); take one before rebuilds.
- GitHub snapshots were removed (see §3.2), so there is no off-machine copy unless you make one.

---

## 8. Future GitHub-Backed Persistence

The earlier plan (snapshots → dual-write → GitHub as primary store) stopped at its first phase when snapshots were removed for publishing member data. If GitHub-backed persistence is revisited, it must:

- write only to a private repository,
- exclude Discord member IDs/roles and Discord ↔ GitHub identity links, and
- have a concrete consumer before anything is written.

---

## Appendix A: Configuration Reference

### Key Config Sections

```yaml
runtime:
  mode: "dry-run" | "observer" | "active"
  log_level: "INFO"
  data_dir: "/path/to/data"
  github_adapter: "ghdcbot.adapters.github.rest:GitHubRestAdapter"
  discord_adapter: "ghdcbot.adapters.discord.api:DiscordApiAdapter"
  storage_adapter: "ghdcbot.adapters.storage.sqlite:SqliteStorage"
  # Optional: Disable scoring while keeping ingestion (default: true)
  enable_scoring: true
  # Optional: Disable Discord role updates while keeping notifications (default: true)
  enable_discord_role_updates: true

github:
  org: "example-org"
  token: "${GITHUB_TOKEN}"  # From env var
  api_base: "https://api.github.com"
  permissions:
    read: true
    write: true
  # Optional: If true, fallback to user repos when org access fails (default: false)
  user_fallback: false
  # Optional: Filter repos to include/exclude
  repos:
    mode: "allow"  # or "deny"
    names:
      - "repo-a"
      - "repo-b"

discord:
  guild_id: "123456789"
  token: "${DISCORD_TOKEN}"  # From env var
  permissions:
    read: true
    write: true
  # Optional: Channel ID for activity feed summary (default: null)
  activity_channel_id: null
  # Optional: weekly maintainer digest (auto-only, once per ISO week; default off).
  # Distinct from activity_channel_id (per-sync dump). See docs/DOCKER.md.
  # digest:
  #   enabled: true
  #   channel_id: "..."
  #   weekday_utc: 6
  #   hour_utc: 12
  # Optional: Channel names where PR URLs trigger passive preview (requires message content intent)
  pr_preview_channels: []
  # Optional: Per-command permission rules (if omitted, falls back to assignments.issue_assignees)
  command_permissions:
    sync:
      role_ids: []
      role_names: ["Mentor"]
      allow_discord_administrators: true
    # pr-channel: optional; when omitted /pr-channel uses the sync rule above
  # TESTING ONLY: Allow any guild member to run restricted commands (default: false)
  unrestricted_slash_commands: false
  notifications:
    enabled: true
    issue_assignment: true
    pr_review_requested: true
    pr_review_result: true
    pr_merged: true
    # Optional: CodeRabbit reminder for old review comments
    coderabbit_reminders: false
    coderabbit_reminder_after_hours: 48
    coderabbit_bot_logins: ["coderabbitai", "coderabbitai[bot]"]
    channel_id: null  # null = DM; set to channel ID to post there

scoring:
  period_days: 30
  # Note: Only "pr_merged" events affect scores (merge-only scoring to prevent spam)
  weights:
    pr_merged: 10
  # Optional: Difficulty-aware scoring via PR labels (e.g., "easy", "medium", "hard")
  difficulty_weights:
    easy: 5
    medium: 10
    hard: 20
  # Optional: Quality adjustments (penalties and bonuses)
  quality_adjustments:
    penalties:
      reverted_pr: 5
      failed_ci_merge: 3
    bonuses:
      pr_review: 2
      helpful_comment: 1

role_mappings:
  - discord_role: "Contributor"
    min_score: 10
  - discord_role: "Maintainer"
    min_score: 40

assignments:
  review_roles:
    - "Maintainer"
  issue_assignees:
    - "Mentor"
  # Optional: Roles required for issue request eligibility (empty = any verified user)
  issue_request_eligible_roles: []

merge_role_rules:
  enabled: true
  rules:
    - discord_role: "apprentice"
      min_merged_prs: 1

# Optional: Grant Discord roles when contributor has PR merged in specific repo
repo_contributor_roles:
  repo-name: "discord-role-name"

# Optional: Identity linking settings
identity:
  unlink_cooldown_hours: 24
  verified_max_age_days: null  # null = no stale check; or set days (e.g., 90)
```

---

## Appendix B: Glossary

- **Adapter:** Plugin component (GitHub reader/writer, Discord reader/writer, Storage)
- **Claim:** Pending identity link (before verification)
- **Contribution Event:** Raw GitHub activity ingested (issue_opened, pr_opened, pr_merged, pr_reviewed, comment, pr_reverted, pr_merged_with_failed_ci, helpful_comment). Note: Only `pr_merged` events affect scores (merge-only scoring); others are tracked for reports and audit.
- **Cursor:** Timestamp tracking for incremental ingestion
- **Deduplication:** Preventing duplicate operations (assignments, notifications)
- **Identity Mapping:** Verified Discord ↔ GitHub link
- **Merge-Only Scoring:** Score calculation uses only `pr_merged` events to prevent spam and gaming. Other events are ingested but don't contribute to scores.
- **Mutation:** Write operation (role change, issue assignment)
- **Orchestrator:** Core execution engine for `run-once` cycle
- **Plan:** Precomputed change (role add/remove, issue assignment)
- **Quality Adjustments:** Optional scoring bonuses/penalties for PR reviews, helpful comments, reverted PRs, and failed CI merges.
- **Repo Contributor Roles:** Discord roles granted when a user has a PR merged in a specific repository.
- **Verified User:** Discord user with verified GitHub link

---

**End of Technical Documentation**
