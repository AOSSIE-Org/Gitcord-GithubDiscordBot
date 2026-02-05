# Gitcord Bot: Deep Technical Report

**Report Date:** 2026-02-05  
**Version:** Current HEAD  
**Report Type:** Comprehensive Technical Deep-Dive

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Architecture Overview](#architecture-overview)
3. [Core Components Deep Dive](#core-components-deep-dive)
4. [Data Flow & Processing Pipeline](#data-flow--processing-pipeline)
5. [Configuration System](#configuration-system)
6. [Storage & Persistence Layer](#storage--persistence-layer)
7. [API Integrations](#api-integrations)
8. [Scoring System](#scoring-system)
9. [Role Management System](#role-management-system)
10. [Identity Linking System](#identity-linking-system)
11. [Safety & Security Mechanisms](#safety--security-mechanisms)
12. [Reporting & Audit System](#reporting--audit-system)
13. [Extension Points & Plugin Architecture](#extension-points--plugin-architecture)
14. [Performance Considerations](#performance-considerations)
15. [Deployment & Operations](#deployment--operations)

---

## Executive Summary

**Gitcord** is a sophisticated, audit-first automation engine that bridges GitHub contributions and Discord role management. It operates as a **run-once, offline-first, deterministic** system designed for transparency, safety, and organizational flexibility.

### Key Characteristics

- **Architecture Pattern:** Adapter-based, protocol-driven design
- **Execution Model:** Run-once orchestration (no background polling)
- **Safety Model:** Multi-layer mutation gating (dry-run, observer, active modes)
- **Data Model:** Event-driven, append-only audit trail
- **Scoring Model:** Merge-only contribution scoring with quality adjustments
- **Role Model:** Dual-strategy (score-based + merge-based) with promotion-only merge rules

### Core Capabilities

1. **GitHub Contribution Ingestion:** Pulls issues, PRs, reviews, comments, and quality signals
2. **Contribution Scoring:** Computes user scores from merged PRs with optional difficulty/quality adjustments
3. **Discord Role Management:** Assigns/removes roles based on scores and merged PR counts
4. **Identity Verification:** Links GitHub accounts to Discord users via verification codes
5. **Assignment Planning:** Plans GitHub issue/PR review assignments based on roles
6. **Comprehensive Reporting:** Generates JSON and Markdown audit reports

---

## Architecture Overview

### High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         CLI / Bot Entry                         │
│                    (ghdcbot.cli / bot.py)                      │
└────────────────────────────┬──────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Orchestrator                               │
│              (engine/orchestrator.py)                           │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐        │
│  │   Ingest     │→ │    Score     │→ │    Plan      │        │
│  └──────────────┘  └──────────────┘  └──────────────┘        │
│         │                 │                 │                   │
│         ▼                 ▼                 ▼                   │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐        │
│  │   Storage    │  │   Scoring    │  │   Planning   │        │
│  └──────────────┘  └──────────────┘  └──────────────┘        │
└─────────────────────────────────────────────────────────────────┘
         │                    │                    │
         ▼                    ▼                    ▼
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│  GitHub Adapter │  │  Discord Adapter│  │  Storage Adapter │
│  (REST API)     │  │  (REST API)     │  │  (SQLite)       │
└─────────────────┘  └─────────────────┘  └─────────────────┘
```

### Design Principles

1. **Protocol-Based Interfaces:** All adapters implement typed protocols (`GitHubReader`, `DiscordWriter`, `Storage`, etc.)
2. **Separation of Concerns:** Clear boundaries between ingestion, scoring, planning, and application
3. **Deterministic Execution:** Same inputs always produce same outputs
4. **Audit-First:** All decisions are logged before execution
5. **Fail-Safe Defaults:** Dry-run mode by default, explicit permission flags required

---

## Core Components Deep Dive

### 1. Orchestrator (`engine/orchestrator.py`)

**Purpose:** Central coordination hub that executes the complete automation cycle.

**Key Responsibilities:**
- Manages the complete run-once lifecycle
- Coordinates adapters (GitHub, Discord, Storage)
- Applies mutation policy gating
- Generates audit reports
- Handles resource cleanup

**Execution Flow:**

```python
def run_once(self) -> None:
    1. Initialize storage schema
    2. Calculate scoring period (period_start, period_end)
    3. Resolve identity mappings (verified > config fallback)
    4. Ingest GitHub contributions (since cursor)
    5. Store contributions and update cursor
    6. Compute scores (WeightedScoreStrategy)
    7. Store scores
    8. Load Discord member roles
    9. Build role-to-GitHub user mapping
    10. Plan GitHub assignments (issues, PR reviews)
    11. Create mutation policy from config
    12. If dry-run/observer: Generate audit reports
    13. Apply GitHub plans (if mutations allowed)
    14. Apply Discord role plans (if mutations allowed)
```

**Key Methods:**

- `run_once()`: Main orchestration method
- `close()`: Resource cleanup (closes adapters)
- `build_role_to_github_map()`: Maps Discord roles to GitHub users
- `_resolve_identity_mappings()`: Prefers verified mappings over config

**Dependencies:**
- `GitHubReader` / `GitHubWriter`: Contribution ingestion and assignments
- `DiscordReader` / `DiscordWriter`: Role management
- `Storage`: Persistence layer
- `WeightedScoreStrategy`: Scoring engine
- `RoleBasedAssignmentStrategy`: Assignment planning
- `plan_discord_roles()`: Role planning

---

### 2. Scoring Engine (`engine/scoring.py`)

**Purpose:** Computes user contribution scores from events.

**Strategy:** `WeightedScoreStrategy`

**Core Design:**

1. **Merge-Only Scoring:** Only `pr_merged` events contribute to base scores
   - Prevents spam/gaming
   - Aligns incentives with mentor-approved work
   - Other events (PR opens, comments, reviews) are ingested but not scored

2. **Difficulty-Aware Scoring:** Optional difficulty labels from linked issues
   - Labels matched case-insensitively
   - Uses maximum weight if multiple labels match
   - Falls back to base `pr_merged` weight if no labels

3. **Quality Adjustments:** Optional penalties and bonuses
   - **Penalties:** Applied once per PR
     - `pr_reverted`: Detected via commit/PR title/body keywords
     - `pr_merged_with_failed_ci`: Detected via GitHub Checks API
   - **Bonuses:** Additive to base score
     - `pr_review`: Only for `APPROVED` reviews, once per PR per reviewer
     - `helpful_comment`: Capped at 5 per PR/issue per user

**Scoring Algorithm:**

```python
for event in contributions:
    if event.created_at not in [period_start, period_end]:
        continue
    
    # Apply penalties (once per PR)
    if event.event_type == "pr_reverted":
        apply_penalty_once(event)
    
    # Apply bonuses (additive)
    if event.event_type == "pr_reviewed" and APPROVED:
        add_bonus(event)
    
    # Base scoring (merge-only)
    if event.event_type == "pr_merged":
        if difficulty_labels:
            score = max(difficulty_weights[matching_labels])
        else:
            score = weights["pr_merged"]
        totals[user] += score
```

**Key Features:**

- **Deterministic:** Same events → same scores
- **Order-Independent:** Results don't depend on event order
- **Idempotent:** Re-running with same data produces same scores
- **Period-Based:** Only events within `period_days` window count

**Configuration:**

```yaml
scoring:
  period_days: 30
  weights:
    pr_merged: 1  # Only this weight is used for base scoring
  difficulty_weights:  # Optional
    easy: 1
    medium: 3
    hard: 5
  quality_adjustments:  # Optional
    penalties:
      reverted_pr: -8
      failed_ci_merge: -5
    bonuses:
      pr_review: 2
      helpful_comment: 1
```

---

### 3. Planning Engine (`engine/planning.py`)

**Purpose:** Plans Discord role changes and GitHub assignments.

**Key Functions:**

#### `plan_discord_roles()`

Plans Discord role additions/removals based on:
1. **Score-Based Roles:** From `role_mappings` config
2. **Merge-Based Roles:** From `merge_role_rules` config (optional)

**Planning Logic:**

```python
# Score-based desired roles
score_desired = {
    role for role, threshold in role_mappings
    if user_score >= threshold
}

# Merge-based desired roles (highest eligible only)
if merge_role_rules.enabled:
    merged_count = count_merged_prs_per_user(...)
    eligible_roles = [
        role for role, min_prs in merge_rules
        if merged_count >= min_prs
    ]
    merge_desired = {highest_eligible_role}

# Final desired = union of both
final_desired = score_desired | merge_desired

# Plan additions
for role in final_desired - current_roles:
    plan.add(role)

# Plan removals (score-based only, merge-based is promotion-only)
for role in (current_roles & managed_roles) - score_desired:
    plan.remove(role)
```

**Key Features:**

- **Promotion-Only Merge Rules:** Merge-based roles never removed
- **Union Logic:** User gets roles from both systems
- **Deterministic:** Sorted inputs ensure consistent output
- **Audit Trail:** Each plan includes `reason` and `source` metadata

#### `count_merged_prs_per_user()`

Counts `pr_merged` events per verified GitHub user within period.

**Implementation:**

```python
def count_merged_prs_per_user(storage, identity_mappings, period_start, period_end):
    all_events = storage.list_contributions(period_start)
    verified_users = {m.github_user for m in identity_mappings}
    counts = {}
    
    for event in all_events:
        if (event.event_type == "pr_merged" and
            event.github_user in verified_users and
            period_start <= event.created_at <= period_end):
            counts[event.github_user] += 1
    
    return counts
```

**Key Features:**

- Only counts verified users
- Respects period boundaries
- Computed once per run (efficient)

---

### 4. Assignment Strategy (`engine/assignment.py`)

**Purpose:** Plans GitHub issue assignments and PR review requests.

**Strategy:** `RoleBasedAssignmentStrategy`

**Logic:**

1. Maps Discord roles to GitHub users via identity mappings
2. Filters eligible users by role names from config
3. Assigns based on score ranking (highest score first)

**Configuration:**

```yaml
assignments:
  issue_assignees:
    - "Contributor"
    - "Maintainer"
  review_roles:
    - "Maintainer"
```

**Assignment Algorithm:**

```python
# Build role → GitHub users map
role_to_github = {
    "Contributor": ["user1", "user2"],
    "Maintainer": ["user3"]
}

# Get eligible users for issue assignment
eligible = role_to_github["Contributor"] + role_to_github["Maintainer"]

# Sort by score (descending)
eligible_sorted = sorted(eligible, key=lambda u: scores[u], reverse=True)

# Assign to highest scorer
assignee = eligible_sorted[0]
```

---

## Data Flow & Processing Pipeline

### Complete Pipeline

```
┌─────────────────────────────────────────────────────────────────┐
│ PHASE 1: INGESTION                                              │
└─────────────────────────────────────────────────────────────────┘
GitHub API → GitHubRestAdapter → ContributionEvent → Storage

Events Ingested:
- issue_opened, issue_closed, issue_assigned
- pr_opened, pr_merged, pr_reviewed
- comment (issue/PR comments)
- pr_reverted (detected)
- pr_merged_with_failed_ci (detected)
- helpful_comment (non-author comments)

┌─────────────────────────────────────────────────────────────────┐
│ PHASE 2: SCORING                                                │
└─────────────────────────────────────────────────────────────────┘
Storage → list_contributions() → WeightedScoreStrategy → Score

Scoring Process:
1. Filter events by period [period_start, period_end]
2. Apply penalties (once per PR)
3. Apply bonuses (capped where applicable)
4. Apply base scores (merge-only)
5. Aggregate per user

┌─────────────────────────────────────────────────────────────────┐
│ PHASE 3: PLANNING                                               │
└─────────────────────────────────────────────────────────────────┘
Scores + Identity Mappings + Discord Roles → Planning → Plans

Planning Outputs:
- DiscordRolePlan[] (add/remove actions)
- GitHubAssignmentPlan[] (assign/request_review actions)

┌─────────────────────────────────────────────────────────────────┐
│ PHASE 4: REPORTING (Dry-Run/Observer)                          │
└─────────────────────────────────────────────────────────────────┘
Plans + Config → Reporting → audit.json + audit.md

┌─────────────────────────────────────────────────────────────────┐
│ PHASE 5: APPLICATION (Active Mode)                             │
└─────────────────────────────────────────────────────────────────┘
Plans + MutationPolicy → GitHubWriter/DiscordWriter → API Calls

Application:
- GitHub: assign_issue(), request_review()
- Discord: add_role(), remove_role(), send_dm()
```

### Event Lifecycle

1. **Ingestion:** GitHub API → `ContributionEvent` → Storage
2. **Storage:** SQLite `contributions` table (append-only)
3. **Scoring:** Events → `Score` objects → `scores` table
4. **Planning:** Scores + Config → `DiscordRolePlan` / `GitHubAssignmentPlan`
5. **Audit:** Plans → `audit.json` / `audit.md`
6. **Application:** Plans → API calls (if mutations allowed)

---

## Configuration System

### Configuration Model (`config/models.py`)

**Structure:**

```python
BotConfig:
  runtime: RuntimeConfig
    - mode: RunMode (dry-run | observer | active)
    - log_level: str
    - data_dir: str
    - adapters: str (dotted paths)
  
  github: GitHubConfig
    - org: str (required)
    - token: str (required, env var)
    - api_base: HttpUrl (default: api.github.com)
    - permissions: PermissionConfig
    - repos: RepoFilterConfig | None
    - user_fallback: bool
  
  discord: DiscordConfig
    - guild_id: str (required)
    - token: str (required, env var)
    - permissions: PermissionConfig
    - activity_channel_id: str | None
  
  scoring: ScoringConfig
    - period_days: int (default: 30)
    - weights: dict[str, int]
    - difficulty_weights: dict[str, int] | None
    - quality_adjustments: QualityAdjustmentsConfig | None
  
  role_mappings: list[RoleMappingConfig]
    - discord_role: str
    - min_score: int
  
  merge_role_rules: MergeRoleRulesConfig | None
    - enabled: bool
    - rules: list[MergeRoleRuleConfig]
      - discord_role: str
      - min_merged_prs: int
  
  assignments: AssignmentConfig
    - review_roles: list[str]
    - issue_assignees: list[str]
  
  identity_mappings: list[IdentityMapping]
    - github_user: str
    - discord_user_id: str
```

### Configuration Loading (`config/loader.py`)

**Process:**

1. Load YAML file
2. Expand environment variables (`${VAR_NAME}`)
3. Validate against Pydantic models
4. Set as active config (for adapter access)

**Environment Variable Support:**

```yaml
github:
  token: "${GITHUB_TOKEN}"  # Expands from env
discord:
  token: "${DISCORD_TOKEN}"  # Expands from env
```

**Validation:**

- Pydantic models enforce types
- Required fields validated
- Custom validators (e.g., `min_merged_prs >= 0`)
- Field validators (e.g., log level enum)

---

## Storage & Persistence Layer

### Storage Protocol (`core/interfaces.py`)

**Interface:**

```python
class Storage(Protocol):
    def init_schema() -> None
    def record_contributions(events) -> int
    def list_contributions(since) -> Sequence[ContributionEvent]
    def list_contribution_summaries(period_start, period_end, weights) -> Sequence[ContributionSummary]
    def upsert_scores(scores) -> None
    def get_scores() -> Sequence[Score]
    def get_cursor(source) -> datetime | None
    def set_cursor(source, cursor) -> None
    def create_identity_claim(...) -> None
    def get_identity_link(...) -> dict | None
    def verify_identity_link(...) -> None
    def list_verified_identity_mappings() -> Iterable[IdentityMapping]
    def append_audit_event(event) -> None
    def list_audit_events() -> Iterable[dict]
```

### SQLite Implementation (`adapters/storage/sqlite.py`)

**Schema:**

```sql
-- Contributions (append-only)
CREATE TABLE contributions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    github_user TEXT NOT NULL,
    event_type TEXT NOT NULL,
    repo TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

-- Scores (period-based)
CREATE TABLE scores (
    github_user TEXT NOT NULL,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    points INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (github_user, period_start, period_end)
);

-- Cursors (sync state)
CREATE TABLE cursors (
    source TEXT PRIMARY KEY,
    cursor TEXT NOT NULL
);

-- Identity Links (verification)
CREATE TABLE identity_links (
    discord_user_id TEXT NOT NULL,
    github_user TEXT NOT NULL,
    verified INTEGER NOT NULL DEFAULT 0,
    verification_code TEXT,
    expires_at TEXT,
    created_at TEXT NOT NULL,
    verified_at TEXT,
    unlinked_at TEXT,
    PRIMARY KEY (discord_user_id, github_user)
);

CREATE INDEX idx_identity_links_github_user ON identity_links (github_user);
CREATE INDEX idx_identity_links_verified ON identity_links (verified);
```

**Key Features:**

- **Append-Only Contributions:** No updates/deletes, full audit trail
- **Period-Based Scores:** Scores stored per period for historical tracking
- **Cursor Tracking:** Tracks last sync time per source (e.g., "github")
- **Identity Verification:** Supports claim/verify/unlink flow
- **Audit Events:** Optional append-only audit log (JSONL)

**Data Directory Structure:**

```
data_dir/
├── state.db              # SQLite database
├── audit_events.jsonl    # Optional audit log
└── reports/
    ├── audit.json        # Machine-readable audit
    ├── audit.md          # Human-readable audit
    └── activity.md        # Activity feed
```

---

## API Integrations

### GitHub REST API Adapter (`adapters/github/rest.py`)

**Class:** `GitHubRestAdapter`

**Key Methods:**

- `list_contributions(since)`: Ingests all contribution events
- `list_open_issues()`: Lists open issues for assignment
- `list_open_pull_requests()`: Lists open PRs for review assignment
- `assign_issue(repo, issue_number, assignee)`: Assigns issue
- `request_review(repo, pr_number, reviewer)`: Requests PR review

**Ingestion Process:**

1. **List Repositories:**
   - `/orgs/{org}/repos` (or `/user/repos` if fallback enabled)
   - Applies repo filter (allow/deny list)

2. **Per Repository:**
   - Issues: `/repos/{owner}/{repo}/issues?state=all&since={since}`
   - PRs: `/repos/{owner}/{repo}/pulls?state=all&sort=updated`
   - Comments: `/repos/{owner}/{repo}/issues/{number}/comments`
   - PR Comments: `/repos/{owner}/{repo}/pulls/{number}/comments`
   - Check Runs: `/repos/{owner}/{repo}/commits/{sha}/check-runs` (CI status)

3. **Event Detection:**
   - Reverted PRs: Keyword matching in commits/PR title/body
   - Failed CI: Checks API status on merged PRs
   - Helpful Comments: Non-author, non-bot comments

**Rate Limiting:**

- Monitors `X-RateLimit-Remaining` header
- Logs warnings when limit < 1
- Handles 403/404 gracefully
- No automatic retry (fails fast)

**Pagination:**

- Automatic pagination via `Link` header
- Processes all pages until empty
- Respects `per_page=100` limit

---

### Discord REST API Adapter (`adapters/discord/api.py`)

**Class:** `DiscordApiAdapter`

**Key Methods:**

- `list_member_roles()`: Returns `dict[discord_user_id, list[role_names]]`
- `add_role(discord_user_id, role_name)`: Assigns role
- `remove_role(discord_user_id, role_name)`: Removes role
- `send_dm(discord_user_id, content)`: Sends direct message
- `send_message(channel_id, content)`: Posts to channel

**Role Resolution:**

1. Lists all guild roles: `/guilds/{guild_id}/roles`
2. Lists all members: `/guilds/{guild_id}/members?limit=1000` (paginated)
3. Maps role IDs to names
4. Builds `user_id → [role_names]` mapping

**DM Sending:**

1. Creates DM channel: `POST /users/@me/channels` with `recipient_id`
2. Sends message: `POST /channels/{channel_id}/messages`
3. Handles privacy errors gracefully (user may have DMs disabled)

**Rate Limiting:**

- Monitors `X-RateLimit-Remaining` header
- Handles 429 (rate limit) responses
- Logs warnings for permission issues (401/403)

**Error Handling:**

- Degrades gracefully if roles/members cannot be listed
- Returns empty dict if permissions insufficient
- Logs warnings but doesn't crash

---

## Scoring System

### Scoring Strategy Details

**Base Scoring (Merge-Only):**

```python
# Only pr_merged events contribute to base score
if event.event_type == "pr_merged":
    if difficulty_labels:
        score = max(difficulty_weights[matching_labels])
    else:
        score = weights.get("pr_merged", 0)
    totals[user] += score
```

**Rationale:**
- Prevents spam (opening PRs doesn't increase score)
- Aligns with mentor approval (only merged PRs count)
- Prevents gaming (comments/reviews don't inflate scores)

**Quality Adjustments:**

**Penalties (Applied Once Per PR):**

```python
# Reverted PR penalty
if event.event_type == "pr_reverted":
    key = (user, pr_number)
    if key not in reverted_prs:
        totals[user] += penalties["reverted_pr"]
        reverted_prs.add(key)

# Failed CI merge penalty
if event.event_type == "pr_merged_with_failed_ci":
    key = (user, pr_number)
    if key not in failed_ci_prs:
        totals[user] += penalties["failed_ci_merge"]
        failed_ci_prs.add(key)
```

**Bonuses (Additive, Capped):**

```python
# PR review bonus (APPROVED only, once per PR)
if event.event_type == "pr_reviewed" and state == "APPROVED":
    key = (user, pr_number)
    if key not in pr_reviews:
        totals[user] += bonuses["pr_review"]
        pr_reviews[key] = True

# Helpful comment bonus (capped at 5 per target)
if event.event_type == "helpful_comment":
    key = (user, target_number)
    if helpful_comment_counts[key] < 5:
        totals[user] += bonuses["helpful_comment"]
        helpful_comment_counts[key] += 1
```

**Difficulty-Aware Scoring:**

- Links PRs to issues via `Closes #123` syntax
- Fetches issue labels
- Matches labels case-insensitively to `difficulty_weights`
- Uses maximum weight if multiple labels match

**Period Calculation:**

```python
period_end = datetime.now(timezone.utc)
period_start = period_end - timedelta(days=period_days)

# Only events in [period_start, period_end] count
if period_start <= event.created_at <= period_end:
    # Score event
```

---

## Role Management System

### Dual-Strategy Role Assignment

**Strategy 1: Score-Based Roles**

- Based on contribution scores
- Configurable thresholds (`min_score`)
- Roles can be added and removed based on score changes

**Strategy 2: Merge-Based Roles**

- Based on merged PR counts
- Promotion-only (never removed)
- Highest eligible role assigned

**Combination Logic:**

```python
# Final desired roles = union of both strategies
final_desired = score_desired | merge_desired

# Additions: roles in final_desired but not in current_roles
for role in final_desired - current_roles:
    plan.add(role)

# Removals: only from score-based (merge-based is promotion-only)
for role in (current_roles & managed_roles) - score_desired:
    plan.remove(role)
```

**Example:**

```yaml
role_mappings:
  - discord_role: "Contributor"
    min_score: 10

merge_role_rules:
  enabled: true
  rules:
    - discord_role: "apprentice"
      min_merged_prs: 1
    - discord_role: "senior"
      min_merged_prs: 5
```

**User with:**
- Score: 15 → Gets "Contributor" (score-based)
- Merged PRs: 3 → Gets "senior" (merge-based, highest eligible)
- **Final roles:** `{"Contributor", "senior"}`

**If score drops to 5:**
- Score-based: "Contributor" removed
- Merge-based: "senior" kept (promotion-only)
- **Final roles:** `{"senior"}`

### Congratulatory Messages

**Trigger:** When a new role is assigned (not already present)

**Conditions:**
- `MutationPolicy.allow_discord_mutations == True`
- Role action is "add"
- Role was newly added (not already present)

**Message Format:**

```
🎉 Congratulations!

Hi <@discord_user_id>,

Your recent merged pull request has earned you the **{role_name}** role in the server.

Thank you for your contribution — keep building 🚀
```

**Delivery:**
- Sent via DM (direct message)
- Fails gracefully if user has DMs disabled
- Logged but doesn't crash on failure

---

## Identity Linking System

### Verification Flow

**Phase 1: Claim Creation**

1. User runs `/link` or `ghdcbot link` command
2. System generates random verification code
3. Stores claim in `identity_links` table:
   - `discord_user_id`
   - `github_user`
   - `verification_code`
   - `expires_at` (TTL: 10 minutes default)
   - `verified = 0`

**Phase 2: Verification**

1. User adds code to GitHub bio or public gist
2. User runs `/verify-link` or `ghdcbot verify-link`
3. System checks:
   - GitHub bio (via `/users/{username}`)
   - Public gists (via `/users/{username}/gists`)
4. If code found:
   - Sets `verified = 1`
   - Sets `verified_at = now()`
   - Returns success

**Phase 3: Usage**

- Verified mappings preferred over config fallback
- Only verified users counted for role assignment
- One-to-one enforcement (one GitHub per Discord, one Discord per GitHub)

### Identity Resolution (`_resolve_identity_mappings()`)

```python
def _resolve_identity_mappings(storage, config_mappings):
    # Try verified mappings first
    verified = storage.list_verified_identity_mappings()
    if verified:
        return verified
    
    # Fallback to config
    return config_mappings
```

**Priority:**
1. Verified mappings from storage (preferred)
2. Config-based mappings (legacy fallback)

---

## Safety & Security Mechanisms

### Mutation Policy (`core/modes.py`)

**Policy Creation:**

```python
policy = MutationPolicy(
    mode=RunMode.ACTIVE,  # or DRY_RUN, OBSERVER
    github_write_allowed=config.github.permissions.write,
    discord_write_allowed=config.discord.permissions.write,
)
```

**Policy Logic:**

```python
@property
def allow_github_mutations(self) -> bool:
    return (
        self.mode == RunMode.ACTIVE and
        self.github_write_allowed
    )

@property
def allow_discord_mutations(self) -> bool:
    return (
        self.mode == RunMode.ACTIVE and
        self.discord_write_allowed
    )
```

**Modes:**

1. **DRY_RUN:** Read-only, generates reports, no mutations
2. **OBSERVER:** Read-only, generates reports, no mutations
3. **ACTIVE:** Mutations allowed if permissions enabled

**Gating:**

```python
if not policy.allow_discord_mutations:
    logger.info("Discord mutations disabled")
    return

# Apply mutations
for plan in plans:
    discord_writer.add_role(...)
```

### Safety Features

1. **Explicit Permissions:** Both mode and permission flags required
2. **Audit-First:** Reports generated before mutations
3. **Fail-Safe Defaults:** Dry-run by default
4. **Graceful Degradation:** API failures don't crash system
5. **Idempotent Operations:** Re-running is safe

---

## Reporting & Audit System

### Report Generation (`engine/reporting.py`)

**Reports Generated:**

1. **audit.json:** Machine-readable audit (JSON)
2. **audit.md:** Human-readable audit (Markdown)
3. **activity.md:** Activity feed (read-only, mentor visibility)

**Audit JSON Structure:**

```json
{
  "timestamp": "2026-02-05T15:12:20.758849+00:00",
  "runtime_mode": "dry-run",
  "org": "example-org",
  "repo_filter": null,
  "summary": {
    "discord_role_changes": 2,
    "github_assignments": 1
  },
  "discord_role_plans": [
    {
      "action": "add",
      "discord_user_id": "123456789",
      "role": "Contributor",
      "reason": "Score 15 meets threshold for Contributor",
      "source": {
        "github_user": "user1",
        "decision_reason": "score_role_rules",
        "score": 15,
        "score_threshold": 10
      }
    }
  ],
  "github_assignment_plans": [...]
}
```

**Audit Markdown Structure:**

```markdown
## Summary
- Runtime mode: `dry-run`
- Organization: `example-org`
- Discord role changes: `2`
- GitHub assignments: `1`

## Contribution Summary (Last 30 days)
| User | Issues | PRs | Reviews | Comments | Score |
|------|-------|-----|---------|----------|-------|
| user1 | 5 | 3 | 2 | 10 | 15 |

## Discord Role Changes
- `add` `Contributor` for `123456789` (reason: Score 15 meets threshold...)

## GitHub Issue Assignments
- Assign issue #42 in `repo-a` to `user1`
```

**Activity Feed:**

- Read-only summary of PR/issue events
- Optional posting to Discord channel
- Truncated to 1900 chars for Discord

---

## Extension Points & Plugin Architecture

### Adapter Registry (`plugins/registry.py`)

**Loading:**

```python
adapter = build_adapter(
    "ghdcbot.adapters.github.rest:GitHubRestAdapter",
    token=token,
    org=org,
    api_base=api_base,
)
```

**Process:**

1. Parse dotted path (`module:class`)
2. Import module
3. Get class
4. Instantiate with kwargs

**Custom Adapters:**

Users can implement custom adapters by:
1. Implementing protocol interfaces (`GitHubReader`, `Storage`, etc.)
2. Registering via config (`github_adapter: "my.module:MyAdapter"`)

### Protocol Interfaces

All adapters implement typed protocols:

- `GitHubReader`: Contribution ingestion
- `GitHubWriter`: Issue/PR assignments
- `DiscordReader`: Role/member listing
- `DiscordWriter`: Role management
- `Storage`: Persistence layer
- `ScoreStrategy`: Scoring algorithms

**Benefits:**

- Easy to swap implementations
- Type-safe interfaces
- Testable with mocks

---

## Performance Considerations

### Optimization Strategies

1. **Cursor-Based Ingestion:**
   - Only fetches events since last cursor
   - Reduces API calls on subsequent runs

2. **Efficient Counting:**
   - `count_merged_prs_per_user()` computed once per run
   - Reused throughout planning

3. **Batch Operations:**
   - Contributions stored in batch
   - Scores upserted in batch

4. **Lazy Evaluation:**
   - Generators used for large datasets
   - Events processed on-demand

### Scalability

**Limitations:**

- Single-threaded execution
- SQLite database (single writer)
- No distributed processing

**Scaling Options:**

1. **Horizontal:** Run multiple instances with different `data_dir`
2. **Vertical:** Increase resources (CPU, memory)
3. **Database:** Migrate to PostgreSQL for concurrent writes

**Typical Performance:**

- Small org (< 10 repos): ~5-10 seconds per run
- Medium org (10-50 repos): ~30-60 seconds per run
- Large org (50+ repos): ~2-5 minutes per run

---

## Deployment & Operations

### Deployment Models

**1. Cron-Based (Recommended):**

```bash
# Run every hour
0 * * * * cd /path/to/gitcord && .venv/bin/python -m ghdcbot.cli --config config.yaml run-once
```

**2. Systemd Service:**

```ini
[Unit]
Description=Gitcord Automation Bot
After=network.target

[Service]
Type=oneshot
ExecStart=/path/to/.venv/bin/python -m ghdcbot.cli --config /path/to/config.yaml run-once
User=gitcord
WorkingDirectory=/path/to/gitcord

[Timer]
OnCalendar=hourly
```

**3. Discord Bot Mode:**

```bash
# Long-running bot with slash commands
.venv/bin/python -m ghdcbot.cli --config config.yaml bot
```

### Monitoring

**Logs:**

- JSON-structured logs (via `logging.setup`)
- Log level configurable (`INFO`, `DEBUG`, `WARNING`, `ERROR`)
- Includes context (org, user, role, etc.)

**Metrics:**

- Contribution counts per run
- Score computation time
- API call counts
- Rate limit status

**Health Checks:**

- Check `data_dir/reports/audit.json` timestamp
- Verify last run completed successfully
- Monitor API rate limits

### Backup & Recovery

**Backup:**

```bash
# Backup database
cp data_dir/state.db backup/state.db.$(date +%Y%m%d)

# Backup reports
tar -czf backup/reports.$(date +%Y%m%d).tar.gz data_dir/reports/
```

**Recovery:**

- Restore `state.db` from backup
- Cursor will be restored (resumes from last sync)
- Reports are regenerated on next run

---

## Conclusion

Gitcord is a **sophisticated, production-ready automation engine** that successfully bridges GitHub contributions and Discord role management. Its **audit-first, deterministic design** ensures transparency and safety, while its **flexible configuration system** allows adaptation to any organization's needs.

**Key Strengths:**

- ✅ Fully organization-agnostic
- ✅ Comprehensive audit trail
- ✅ Multiple safety layers
- ✅ Extensible architecture
- ✅ Deterministic execution
- ✅ Merge-only scoring (prevents gaming)

**Use Cases:**

- Open source communities
- Internal development teams
- Educational organizations
- Contributor recognition systems

**Future Enhancements (Potential):**

- Multi-org support (single instance)
- Real-time webhooks (optional)
- Advanced scoring algorithms
- Custom event types
- GraphQL API support

---

**Report Generated:** 2026-02-05  
**Codebase Version:** Current HEAD  
**Analysis Method:** Static code analysis + runtime inspection
