# Gitcord Technical Documentation

**Version:** 1.0  
**Last Updated:** May 2026  
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
8. [Migration Plan](#8-migration-plan)
9. [Future Improvements](#9-future-improvements)

---

## 1. Project Overview

### 1.1 Problem Statement

Gitcord solves the challenge of automating contributor recognition and task assignment in open-source organizations that use both GitHub and Discord. Organizations need:

- **Automated role management** based on GitHub contribution activity
- **Identity verification** between Discord and GitHub accounts without OAuth complexity
- **Issue assignment workflows** that respect contributor eligibility and mentor oversight
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
7. It plans role changes, issue assignments, review requests, notifications, reports, and snapshots.
8. It applies GitHub or Discord changes only when the runtime mode and write permissions allow it.

The default operating style is safe by design. In `dry-run` and `observer` modes, Gitcord reads data and writes audit reports, but it does not change GitHub or Discord. In `active` mode, it can assign issues, request reviews, add or remove Discord roles, send Discord messages, and write snapshots, but only if the relevant write permissions are enabled in config.

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
- **Verified-only notifications:** GitHub activity notifications are sent only to verified Discord users and are deduplicated.
- **CodeRabbit reminders:** Optional reminders can notify PR authors about old CodeRabbit review comments.
- **Audit reports:** Dry runs generate JSON and Markdown reports of planned Discord and GitHub actions.
- **Activity reports:** Gitcord writes a human-readable activity feed for mentor visibility.
- **Audit event export:** CLI export supports JSON, CSV, and Markdown with filters for user, event type, and date range.
- **SQLite local state:** Contributions, scores, cursors, identity links, issue requests, notifications, and audit events are stored locally.
- **GitHub snapshots:** Optional snapshot writing exports identities, scores, contributors, roles, issue requests, and notifications to a GitHub repository.
- **Docker support:** The project includes Docker and Docker Compose files for deployment.

### 1.6 Main Code Areas

| Area | Files | Responsibility |
| --- | --- | --- |
| CLI | `src/ghdcbot/cli.py`, `src/ghdcbot/__main__.py` | Command-line entry points and command routing. |
| Config | `src/ghdcbot/config/loader.py`, `src/ghdcbot/config/models.py` | YAML loading, env expansion, Pydantic validation. |
| Core models | `src/ghdcbot/core/models.py`, `src/ghdcbot/core/interfaces.py`, `src/ghdcbot/core/modes.py` | Shared dataclasses, protocols, and mutation policy. |
| GitHub adapter | `src/ghdcbot/adapters/github/rest.py` | GitHub ingestion, assignments, review requests, snapshots file writes. |
| Discord adapter | `src/ghdcbot/adapters/discord/api.py` | Discord role reads/writes, DMs, channel messages. |
| Storage | `src/ghdcbot/adapters/storage/sqlite.py` | SQLite schema and persistence methods. |
| Orchestration | `src/ghdcbot/engine/orchestrator.py` | Main sync pipeline. |
| Scoring | `src/ghdcbot/engine/scoring.py` | Weighted contributor scoring. |
| Planning | `src/ghdcbot/engine/planning.py`, `src/ghdcbot/engine/assignment.py` | Role plans, assignment plans, review plans. |
| Discord bot | `src/ghdcbot/bot.py` | Slash commands and Discord UI workflows. |
| Identity | `src/ghdcbot/engine/identity_linking.py`, `src/ghdcbot/adapters/github/identity.py` | GitHub account verification. |
| Reports | `src/ghdcbot/engine/reporting.py`, `src/ghdcbot/engine/audit_export.py` | Audit report rendering and export. |
| Notifications | `src/ghdcbot/engine/notifications.py` | GitHub-to-Discord notification logic. |
| Snapshots | `src/ghdcbot/engine/snapshots.py` | GitHub-backed JSON snapshot export. |

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
│              SQLite (Primary) + GitHub Snapshots            │
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
- Handles identity linking (`/link`, `/verify-link`, `/verify`, `/unlink`)
- Provides contribution metrics (`/summary`, `/status`)
- Issue management (`/request-issue`, `/assign-issue`, `/issue-requests`)
- PR previews (`/pr-info` and passive URL detection)
- Mentor-only commands (`/sync`, `/assign-issue`, `/issue-requests`)

#### **Orchestrator (`src/ghdcbot/engine/orchestrator.py`)**
- Core execution engine for `run-once` cycle
- Coordinates: ingestion → scoring → planning → reporting → mutation
- Manages notification sending (verified-only)
- Writes GitHub snapshots (additive, non-blocking)

#### **Storage (`src/ghdcbot/adapters/storage/sqlite.py`)**
- SQLite database for local state (`state.db`)
- Tables: `contributions`, `scores`, `cursors`, `identity_links`, `issue_requests`, `notifications_sent`
- Append-only audit log (`audit_events.jsonl`)
- Schema migrations via additive `ALTER TABLE` (backward compatible)

#### **GitHub Adapter (`src/ghdcbot/adapters/github/rest.py`)**
- Reads: contributions, issues, PRs via REST API
- Writes: issue assignments, review requests, file commits (snapshots)
- Handles pagination, rate limiting, error recovery
- Filters repos based on config (`repos.mode`, `repos.names`)

#### **Discord Adapter (`src/ghdcbot/adapters/discord/api.py`)**
- Reads: guild members, roles via REST API
- Writes: role additions/removals, DMs, channel messages
- Handles permission degradation gracefully

#### **Snapshot Engine (`src/ghdcbot/engine/snapshots.py`)**
- Writes periodic snapshots to GitHub repo as JSON files
- Additive-only: timestamped directories, never overwrites
- Non-blocking: failures don't stop `run-once`
- Schema versioned (`SCHEMA_VERSION = "1.0.0"`)

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

9. Write Snapshots (additive, non-blocking)
   └─> Collect: identities, scores, contributors, roles, issue_requests, notifications
   └─> Write to GitHub repo: `snapshots/YYYY-MM-DDTHH-MM-SS-runid/*.json`
   └─> Never blocks run-once completion
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

**Issue Request Flow:**
```
Contributor: /request-issue <issue_url>
  └─> Parse issue URL
  └─> Verify user is linked (Discord → GitHub)
  └─> Store in SQLite `issue_requests` (status='pending')
  └─> Audit event: issue_request_created

Mentor: /issue-requests
  └─> List pending requests from SQLite
  └─> Group by repo
  └─> Show embed with eligibility info

Mentor: Clicks "Approve & Assign"
  └─> Fetch issue context (GitHub API)
  └─> Assign issue via GitHubWriter.assign_issue()
  └─> Update `issue_requests` (status='approved')
  └─> Send DM to contributor
  └─> Audit event: issue_request_approved
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

**Purpose:** Historical record of all GitHub activity. Used for scoring, reports, and snapshots.

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

### 3.2 GitHub Snapshots

#### **What is Written**

Snapshots are written to a GitHub repository (configured via `snapshots.repo_path`) as JSON files in timestamped directories:

```
snapshots/
  └─ 2026-02-16T23-20-25-abc12345/
      ├─ meta.json              # Schema version, timestamps, run_id
      ├─ identities.json        # Verified Discord ↔ GitHub mappings
      ├─ scores.json            # Current scores per user
      ├─ contributors.json      # Contribution summaries (counts + scores)
      ├─ roles.json             # Discord member roles
      ├─ issue_requests.json    # Pending issue requests
      └─ notifications.json     # Recent sent notifications (last 1000)
```

#### **When Snapshots are Created**

- After `run-once` completes successfully (all processing done)
- Only if `snapshots.enabled = true` in config
- Non-blocking: failures are logged but don't stop `run-once`
- Each snapshot gets a unique `run_id` (UUID) for traceability

#### **Snapshot Structure**

All snapshot files follow this schema:

```json
{
  "schema_version": "1.0.0",
  "generated_at": "2026-02-16T23:20:25.123456+00:00",
  "org": "example-org",
  "run_id": "abc12345-def6-7890-ghij-klmnopqrstuv",
  "period_start": "2026-01-17T23:20:25+00:00",
  "period_end": "2026-02-16T23:20:25+00:00",
  "data": [ /* array of records */ ]
}
```

**Example: `identities.json`**
```json
{
  "schema_version": "1.0.0",
  "generated_at": "2026-02-16T23:20:25+00:00",
  "org": "example-org",
  "run_id": "abc12345...",
  "data": [
    {
      "discord_user_id": "123456789",
      "github_user": "alice"
    }
  ]
}
```

**Example: `scores.json`**
```json
{
  "schema_version": "1.0.0",
  "generated_at": "2026-02-16T23:20:25+00:00",
  "org": "example-org",
  "run_id": "abc12345...",
  "data": [
    {
      "github_user": "alice",
      "period_start": "2026-01-17T23:20:25+00:00",
      "period_end": "2026-02-16T23:20:25+00:00",
      "points": 42
    }
  ]
}
```

#### **Why Snapshots are Additive**

1. **Never overwrite:** Each snapshot is in a unique timestamped directory
2. **Historical record:** All snapshots remain accessible for analysis
3. **Org Explorer compatibility:** External tools can consume snapshots without SQLite
4. **GitHub as source of truth:** Aligns with Bruno's requirement (no Supabase)

#### **How This Aligns with Bruno's Requirement**

Bruno's requirement: **"No Supabase, use GitHub for data persistence"**

Gitcord's approach:
- ✅ SQLite remains primary source of truth (for now)
- ✅ Snapshots are **additive** (Phase 1: write-only)
- ✅ Future phases will migrate to GitHub-first (see Migration Plan)
- ✅ No external databases required
- ✅ All data eventually backed to GitHub repo

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
- `/verify-link` - Verify claim
- `/verify` - Check status
- `/status` - Show status + roles
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

### 4.3 Issue Assignment Flow

**Two Flows:**

#### **Flow 1: Contributor Requests Assignment**
1. Contributor runs `/request-issue <issue_url>`
2. Bot verifies user is linked (Discord → GitHub)
3. Bot stores request in `issue_requests` (status='pending')
4. Mentor runs `/issue-requests`
5. Bot shows list grouped by repo
6. Mentor selects repo → sees requests with eligibility info
7. Mentor approves/rejects/replaces assignee
8. Bot assigns issue on GitHub (if approved)
9. Bot sends DM to contributor

#### **Flow 2: Mentor Direct Assignment**
1. Mentor runs `/assign-issue <issue_url> <discord_user>`
2. Bot resolves Discord user → GitHub username
3. Bot shows confirmation embed with issue details
4. Mentor confirms → Bot assigns issue on GitHub
5. Bot sends DM to assignee (if notifications enabled)

**Eligibility Check:**
- Role-based: contributor must have role in `assignments.issue_request_eligible_roles`
- Activity-based: shows merged PR count and last merged time
- Verdict: "eligible", "eligible_low_activity", or "not_eligible"

### 4.4 PR Info Previews

**Two Modes:**

#### **Mode 1: Slash Command**
- User runs `/pr-info <pr_url>`
- Bot fetches PR context: title, status, reviews, CI status, last commit
- Bot shows rich embed with all details
- Includes Discord mention if PR author is linked

#### **Mode 2: Passive Detection**
- Bot monitors configured channels (`discord.pr_preview_channels`)
- When PR URL detected in message → auto-fetch and post embed
- No command needed, just paste URL

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

### 4.6 Issue Requests

**Purpose:** Contributors request issue assignment, mentors review with full context.

**Storage:** `issue_requests` table

**Statuses:**
- `pending` - Awaiting mentor review
- `approved` - Mentor approved, issue assigned
- `rejected` - Mentor rejected
- `cancelled` - Cancelled (timeout or manual)

**Mentor Review UI:**
- Shows contributor Discord mention, roles, merged PR count
- Shows issue title, labels, assignees
- Shows eligibility verdict and reason
- Buttons: Approve & Assign, Replace Existing Assignee, Reject, Cancel

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
- `snapshot_written`
- `report_generated`

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

### 4.8 Snapshot System

**Purpose:** Write Gitcord state to GitHub repo for external consumption (Org Explorer).

**When:** After `run-once` completes successfully

**What:** 7 JSON files per snapshot:
1. `meta.json` - Schema version, timestamps, run_id
2. `identities.json` - Verified Discord ↔ GitHub mappings
3. `scores.json` - Current scores per user
4. `contributors.json` - Contribution summaries (counts + scores)
5. `roles.json` - Discord member roles
6. `issue_requests.json` - Pending issue requests
7. `notifications.json` - Recent notifications (last 1000)

**Non-Blocking:** Failures are logged but don't stop `run-once`

**Schema Versioning:** `SCHEMA_VERSION = "1.0.0"` (increment on breaking changes)

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
- `issue_opened` - New issue created
- `issue_assigned` - Issue assigned to user
- `pr_opened` - New PR created
- `pr_merged` - PR merged
- `pr_reviewed` - PR review submitted (approved/changes_requested/comment)
- `comment` - Comment on issue/PR

**Incremental Ingestion:**
- Uses `cursors` table to track last-seen timestamp
- Only fetches events since cursor
- Prevents re-processing old events

**Repo Filtering:**
- Config: `repos.mode` (`allow` or `deny`)
- Config: `repos.names` (list of repo names)
- Applied before ingestion

### 6.2 File Writing (Snapshots)

**API Used:**
- `GET /repos/{owner}/{repo}/contents/{path}` - Check if file exists (get SHA)
- `PUT /repos/{owner}/{repo}/contents/{path}` - Create/update file

**Process:**
1. Check if file exists (get SHA for update)
2. Base64 encode content
3. Create commit with message
4. Use default branch if not specified

**Error Handling:**
- Network errors → log warning, return False
- Permission errors → log warning, return False
- Never raises exceptions (non-blocking)

### 6.3 Snapshot Schema

**Current Schema Version:** `1.0.0`

**Files:**
- `meta.json` - Metadata (schema_version, generated_at, org, run_id, period_start, period_end)
- `identities.json` - Array of `{discord_user_id, github_user}`
- `scores.json` - Array of `{github_user, period_start, period_end, points}`
- `contributors.json` - Array of `{github_user, period_start, period_end, issues_opened, prs_opened, prs_reviewed, comments, total_score}`
- `roles.json` - Array of `{discord_user_id, roles: [string]}`
- `issue_requests.json` - Array of `{request_id, discord_user_id, github_user, owner, repo, issue_number, issue_url, created_at, status}`
- `notifications.json` - Array of `{dedupe_key, event_type, github_user, discord_user_id, repo, target, channel_id, sent_at}`

**Schema Evolution:**
- Increment `SCHEMA_VERSION` on breaking changes
- Consumers should check `schema_version` before parsing
- Additive changes (new fields) don't require version bump

---

## 7. Current Limitations

### 7.1 What is Not Yet Implemented

**Raw Event History in Snapshots:**
- Snapshots contain aggregated data (scores, summaries)
- **Not included:** Raw `contributions` table events
- **Reason:** File size concerns (could be large)
- **Future:** Optional raw event export (see Future Improvements)

**SQLite Still Primary Source of Truth:**
- Snapshots are additive (write-only)
- SQLite remains authoritative for reads
- **Future:** Dual-write phase, then gradual SQLite downgrade (see Migration Plan)

**No Event Replay:**
- Cannot rebuild state from snapshots alone
- Requires SQLite for full history
- **Future:** Snapshot-based state reconstruction

**Limited Snapshot Frequency:**
- Snapshots written once per `run-once`
- No configurable frequency (e.g., hourly, daily)
- **Future:** Configurable snapshot schedule

### 7.2 Raw Event History Missing from Snapshots

**Current State:**
- Snapshots contain: identities, scores, contributors (aggregated), roles, issue_requests, notifications
- **Missing:** Raw `contributions` events (issue_opened, pr_opened, pr_merged, etc.)

**Impact:**
- Cannot reconstruct full event timeline from snapshots
- Cannot analyze event patterns without SQLite
- Org Explorer cannot show detailed activity feed

**Why:**
- File size concerns (could be thousands of events per snapshot)
- Schema not yet designed for event export
- Prioritized aggregated data for initial use case

### 7.3 SQLite Still Primary Source of Truth

**Current State:**
- All reads come from SQLite (`state.db`)
- Snapshots are write-only (additive)
- No read path from GitHub snapshots

**Impact:**
- System requires SQLite for operation
- Cannot run Gitcord from snapshot-only data
- Migration to GitHub-first requires code changes

**Why:**
- Incremental migration strategy (see Migration Plan)
- SQLite provides fast local queries
- Snapshots are Phase 1 (additive)

---

## 8. Migration Plan

### 8.1 Phase 1: Additive Snapshots (Current)

**Status:** ✅ Implemented

**What:**
- Snapshots written after each `run-once`
- Never overwrite previous snapshots
- SQLite remains primary source of truth
- Snapshots are audit output, not input

**Goal:**
- Establish snapshot schema
- Build Org Explorer compatibility
- Validate snapshot format

### 8.2 Phase 2: Dual-Write

**Status:** 🔄 Planned

**What:**
- Continue writing to SQLite (backward compatibility)
- **Also** write to GitHub snapshots (additive)
- Reads still from SQLite
- Snapshots become authoritative for external tools

**Goal:**
- Validate snapshot reliability
- Ensure no data loss
- Build confidence in GitHub-backed storage

**Implementation:**
- No code changes needed (already dual-write)
- Focus on validation and monitoring

### 8.3 Phase 3: Gradual SQLite Downgrade

**Status:** 🔮 Future

**What:**
- Option 1: Read from latest snapshot, fallback to SQLite
- Option 2: Reconstruct SQLite from snapshots on startup
- Option 3: Remove SQLite entirely, read from GitHub API

**Goal:**
- GitHub becomes primary source of truth
- SQLite becomes optional cache
- Eventually: SQLite-free operation

**Challenges:**
- Performance (GitHub API rate limits)
- Offline operation (requires cache)
- State reconstruction (from snapshots)

**Timeline:**
- TBD based on Phase 2 validation

---

## 9. Future Improvements

### 9.1 Raw Event Export

**Description:**
- Add optional raw event export to snapshots
- Include all `contributions` events in snapshot directory
- Format: `events.jsonl` (JSON Lines, one event per line)

**Benefits:**
- Full event timeline in GitHub
- Org Explorer can show detailed activity feed
- Enables event replay and state reconstruction

**Challenges:**
- File size (could be large)
- Schema design (event format, deduplication)
- Performance (writing large files)

**Priority:** Medium

### 9.2 Org Explorer Compatibility

**Description:**
- Ensure snapshot schema matches Org Explorer expectations
- Add metadata fields for Org Explorer consumption
- Document snapshot format for external tools

**Benefits:**
- Seamless integration with Org Explorer
- Standardized data format
- External tool compatibility

**Challenges:**
- Schema coordination with Org Explorer
- Versioning strategy
- Backward compatibility

**Priority:** High (if Org Explorer is target consumer)

### 9.3 Snapshot Schema Stabilization

**Description:**
- Finalize snapshot schema (v1.0.0 → v1.0.0 stable)
- Document all fields and types
- Establish versioning policy

**Benefits:**
- Stable API for consumers
- Clear migration path for schema changes
- Reduced breaking changes

**Challenges:**
- Balancing flexibility vs. stability
- Handling schema migrations
- Consumer coordination

**Priority:** Medium

### 9.4 Configurable Snapshot Frequency

**Description:**
- Allow snapshots on schedule (hourly, daily) vs. per-run
- Config: `snapshots.frequency: "per-run" | "hourly" | "daily"`
- Skip snapshots if no changes since last snapshot

**Benefits:**
- Reduced snapshot volume
- More predictable snapshot timing
- Better for external tool consumption

**Challenges:**
- Change detection (what counts as "change"?)
- Scheduling (requires daemon or cron)
- Deduplication logic

**Priority:** Low

### 9.5 Snapshot-Based State Reconstruction

**Description:**
- Reconstruct SQLite state from snapshots
- Command: `ghdcbot --config config.yaml rebuild-from-snapshots`
- Useful for disaster recovery or migration

**Benefits:**
- Disaster recovery (if SQLite lost)
- Migration to new instance
- Validation of snapshot completeness

**Challenges:**
- Handling missing snapshots (gaps)
- Event ordering (if raw events added)
- Performance (processing many snapshots)

**Priority:** Low

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

github:
  org: "example-org"
  token: "${GITHUB_TOKEN}"  # From env var
  api_base: "https://api.github.com"
  permissions:
    read: true
    write: true

discord:
  guild_id: "123456789"
  token: "${DISCORD_TOKEN}"  # From env var
  permissions:
    read: true
    write: true
  notifications:
    enabled: true
    issue_assignment: true
    pr_review_requested: true
    pr_review_result: true
    pr_merged: true
    channel_id: null

scoring:
  period_days: 30
  weights:
    issue_opened: 3
    pr_opened: 5
    pr_reviewed: 2
    comment: 1
    pr_merged: 10

role_mappings:
  - discord_role: "Contributor"
    min_score: 10
  - discord_role: "Maintainer"
    min_score: 40

merge_role_rules:
  enabled: true
  rules:
    - discord_role: "apprentice"
      min_merged_prs: 1

snapshots:
  enabled: true
  repo_path: "org/gitcord-data"
  branch: "main"
```

---

## Appendix B: Glossary

- **Adapter:** Plugin component (GitHub reader/writer, Discord reader/writer, Storage)
- **Claim:** Pending identity link (before verification)
- **Contribution Event:** Raw GitHub activity (issue_opened, pr_opened, etc.)
- **Cursor:** Timestamp tracking for incremental ingestion
- **Deduplication:** Preventing duplicate operations (assignments, notifications)
- **Identity Mapping:** Verified Discord ↔ GitHub link
- **Mutation:** Write operation (role change, issue assignment)
- **Orchestrator:** Core execution engine for `run-once` cycle
- **Plan:** Precomputed change (role add/remove, issue assignment)
- **Snapshot:** GitHub-backed JSON state export
- **Verified User:** Discord user with verified GitHub link

---

**End of Technical Documentation**
