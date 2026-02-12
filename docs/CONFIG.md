# Configuration (config YAML) — How Everything Works

This document explains the Gitcord config YAML: what each section and key does, how the bot uses them, and how they work together.

---

## Overview

- **File**: A single YAML file (e.g. `config/example.yaml`).
- **Secrets**: Never put real tokens in the file. Use placeholders like `${GITHUB_TOKEN}` and `${DISCORD_TOKEN}`; the loader replaces them from environment variables (and optionally from a `.env` file).
- **Validation**: The file is validated with Pydantic when loaded. Invalid values or missing required keys produce a clear error.

---

## 1. `runtime` — How the bot runs

```yaml
runtime:
  mode: "dry-run"
  log_level: "INFO"
  data_dir: "/tmp/ghdcbot-state"
  github_adapter: "ghdcbot.adapters.github.rest:GitHubRestAdapter"
  discord_adapter: "ghdcbot.adapters.discord.api:DiscordApiAdapter"
  storage_adapter: "ghdcbot.adapters.storage.sqlite:SqliteStorage"
```

| Key                 | Required                | What it does                                                                                                                                                                              |
| ------------------- | ----------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **mode**            | No (default: `dry-run`) | Controls whether the bot is allowed to write. `dry-run` or `observer`: no writes, only plans and reports. `active`: writes allowed only if the corresponding `permissions.write` is true. |
| **log_level**       | No (default: `INFO`)    | Logging level: `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL`.                                                                                                                        |
| **data_dir**        | **Yes**                 | Directory for local state: SQLite DB (`state.db`), sync cursor, and report output. Reports go under `<data_dir>/reports/` (e.g. `audit.json`, `audit.md`).                                |
| **github_adapter**  | **Yes**                 | Dotted path to the GitHub adapter class (reader + writer). Example: `ghdcbot.adapters.github.rest:GitHubRestAdapter`.                                                                     |
| **discord_adapter** | **Yes**                 | Dotted path to the Discord adapter class. Example: `ghdcbot.adapters.discord.api:DiscordApiAdapter`.                                                                                      |
| **storage_adapter** | **Yes**                 | Dotted path to the storage adapter class. Example: `ghdcbot.adapters.storage.sqlite:SqliteStorage`.                                                                                       |

**How it’s used:** The CLI loads this file, sets log level from `log_level`, and uses `data_dir` for storage and reports. Adapter paths are used to instantiate the GitHub, Discord, and storage components. `mode` is combined with `github.permissions.write` and `discord.permissions.write` to build the mutation policy that gates all writes.

---

## 2. `github` — GitHub organization and API

```yaml
github:
  org: "example-org"
  token: "${GITHUB_TOKEN}"
  api_base: "https://api.github.com"
  permissions:
    read: true
    write: false
  user_fallback: false
  # repos: optional — see below
```

| Key                   | Required                               | What it does                                                                                                                                                                  |
| --------------------- | -------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **org**               | **Yes**                                | GitHub organization (or user) name. The bot lists repos under `/orgs/<org>/repos` and ingests contributions from those repos.                                                 |
| **token**             | **Yes**                                | API token. Use `${GITHUB_TOKEN}` so the value comes from the environment. Must have at least read access to the org/repos.                                                    |
| **api_base**          | No (default: `https://api.github.com`) | GitHub API base URL. Change only for GitHub Enterprise or custom endpoints.                                                                                                   |
| **permissions.read**  | No (default: true)                     | Reserved for future use; read is always used for ingestion.                                                                                                                   |
| **permissions.write** | No (default: false)                    | When true **and** `runtime.mode` is `active`, the bot is allowed to perform GitHub writes (e.g. assign issues, request reviews). If false, GitHub writes are never performed. |
| **user_fallback**     | No (default: false)                    | If true and the org request fails (e.g. 401/403) or returns no repos, the bot falls back to `/user/repos` so a user account can be used instead of an org.                    |
| **repos**             | No                                     | Optional repo filter. If omitted, all repos under the org are ingested. If set, see “Repo filter” below.                                                                      |

### Repo filter (optional)

```yaml
github:
  repos:
    mode: "allow" # or "deny"
    names:
      - "repo-a"
      - "repo-b"
```

- **mode: allow** — Only repos whose name is in `names` are ingested.
- **mode: deny** — All repos **except** those in `names` are ingested.
- **names** — List of repo names (no slashes). Must be non-empty when `repos` is set.

**How it’s used:** The GitHub adapter lists org (or user) repos, then applies this filter before fetching issues, PRs, reviews, and comments. Only contributions from the resulting repos are stored and scored.

---

## 3. `discord` — Discord server (guild)

```yaml
discord:
  guild_id: "000000000000000000"
  token: "${DISCORD_TOKEN}"
  permissions:
    read: true
    write: false
```

| Key                   | Required            | What it does                                                                                                                                                                     |
| --------------------- | ------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **guild_id**          | **Yes**             | Discord server (guild) ID. The bot reads roles and members from this server. Get it by enabling Developer Mode in Discord and right‑clicking the server name → “Copy Server ID”. |
| **token**             | **Yes**             | Discord **bot** token. Use `${DISCORD_TOKEN}` so the value comes from the environment. Create a bot in the Discord Developer Portal and invite it to the guild.                  |
| **permissions.read**  | No (default: true)  | Reserved; read is always used to list roles and members.                                                                                                                         |
| **permissions.write** | No (default: false) | When true **and** `runtime.mode` is `active`, the bot is allowed to add/remove Discord roles. If false, Discord role changes are never performed.                                |

**How it’s used:** The Discord adapter calls the Discord API to list guild roles and members. It builds a map: Discord user ID → list of role names. That map is used with `identity_mappings` and `role_mappings` to plan role changes and to decide who is eligible for issue/PR assignments.

---

## 4. `scoring` — Contribution window and weights

```yaml
scoring:
  period_days: 30
  weights:
    issue_opened: 3
    pr_opened: 5
    pr_reviewed: 2
    comment: 1
```

| Key             | Required         | What it does                                                                                                                                                        |
| --------------- | ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **period_days** | No (default: 30) | Length of the scoring window in days. Only contributions with `created_at` inside `[period_end - period_days, period_end]` are counted. Must be &gt; 0.             |
| **weights**     | **Yes**          | Map from contribution **event type** to points. Each contribution adds `weights[event_type]` to that user’s score for the period. Unknown event types get 0 points. |

**Event types** the GitHub adapter can emit (you can add weights for any of these):

- `issue_opened`, `issue_closed`, `issue_assigned`
- `pr_opened`, `pr_merged`, `pr_reviewed`
- `comment`

**How it’s used:** The engine loads contributions from storage for the period, then sums `weights[event_type]` per user. The result is the user’s **score** for that period. Scores are stored and used for Discord role thresholds and (optionally) for assignment logic. The same weights are used when building the contribution summary table in the audit report.

---

## 5. `role_mappings` — Discord roles by score

```yaml
role_mappings:
  - discord_role: "Contributor"
    min_score: 10
  - discord_role: "Maintainer"
    min_score: 40
```

| Key              | Required        | What it does                                                                                                                                                |
| ---------------- | --------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **discord_role** | **Yes**         | Exact name of a role in your Discord server. The bot will add or remove this role based on score.                                                           |
| **min_score**    | No (default: 0) | Minimum score (for the current period) required to **have** this role. If a user’s score is below this, the bot plans to remove the role (if they have it). |

**Rules:**

- At least one entry is required.
- Roles are evaluated in order of `min_score` (low to high). A user gets every role whose `min_score` they meet.
- Only users who appear in `identity_mappings` are considered; their score is their GitHub contribution score for the period.

**How it’s used:** For each user in `identity_mappings`, the engine compares their score to each `min_score`. It plans to **add** roles they don’t have but qualify for, and **remove** roles they have but no longer qualify for. In dry-run/observer these plans only appear in the report; in active mode with Discord write allowed, the Discord writer would apply them.

---

## 6. `assignments` — Who gets issues and PR reviews

```yaml
assignments:
  review_roles:
    - "Maintainer"
  issue_assignees:
    - "Contributor"
```

| Key                 | Required         | What it does                                                                                                                                               |
| ------------------- | ---------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **review_roles**    | No (default: []) | List of **Discord role names**. Only users who have one of these roles (and are linked in `identity_mappings`) are eligible to be chosen as PR reviewers.  |
| **issue_assignees** | No (default: []) | List of **Discord role names**. Only users who have one of these roles (and are linked in `identity_mappings`) are eligible to be assigned to open issues. |

**How it’s used:** The engine builds a “role → GitHub users” map from `identity_mappings` and the current Discord member roles. For each role in `review_roles` or `issue_assignees`, it collects the linked GitHub usernames. It then plans issue assignments and PR review requests by distributing open issues and open PRs among those users (e.g. round‑robin). If a list is empty, no assignments of that type are planned.

---

## 7. `identity_mappings` — Link GitHub users to Discord

```yaml
identity_mappings:
  - github_user: "alice"
    discord_user_id: "123456789012345678"
  - github_user: "bob"
    discord_user_id: "987654321098765432"
```

| Key                 | Required | What it does                                                                                                         |
| ------------------- | -------- | -------------------------------------------------------------------------------------------------------------------- |
| **github_user**     | **Yes**  | GitHub username (login) as it appears in the API (e.g. in issues, PRs, reviews).                                     |
| **discord_user_id** | **Yes**  | That same person’s Discord user ID (numeric string). Get it with Developer Mode → right‑click user → “Copy User ID”. |

**How it’s used:**

- **Discord roles:** The bot only plans role changes for Discord user IDs that appear here. Their score is the one computed for the matching `github_user`.
- **Assignments:** The “role → GitHub users” map is built by looking at each mapping’s Discord user ID, seeing which Discord roles they have, and adding their `github_user` to the set of users for those roles. So both role updates and issue/PR assignment eligibility depend on this list.

**Important:** There is no automatic link between GitHub and Discord. Every person who should get roles or be eligible for assignments must be listed here.

---

## 8. How the sections work together

1. **runtime** — Chooses mode (dry-run/observer/active), data directory, and which adapter classes to load.
2. **github** — Defines which org and repos to ingest, and whether GitHub writes are allowed when mode is active.
3. **discord** — Defines which guild to read and whether Discord role writes are allowed when mode is active.
4. **scoring** — Defines the time window and how many points each event type is worth; scores drive role thresholds.
5. **role_mappings** — Defines which Discord roles exist and at what score they are granted or removed.
6. **assignments** — Defines which Discord roles make someone eligible for issue assignment and PR review.
7. **identity_mappings** — Links GitHub usernames to Discord user IDs so the bot knows who gets which role and who can be assigned.

**Flow in one sentence:** The bot ingests GitHub activity and Discord state, scores GitHub users over the configured period, uses `identity_mappings` and Discord roles to know who is in which role, uses `role_mappings` to plan Discord role changes from scores, and uses `assignments` plus the same role→user map to plan issue and PR review assignments; all writes are gated by `runtime.mode` and the `permissions.write` flags.

---

## 9. Environment variables

- **Required for example config:** `GITHUB_TOKEN`, `DISCORD_TOKEN`.
- In the YAML use: `token: "${GITHUB_TOKEN}"` and `token: "${DISCORD_TOKEN}"`.
- Set them in the shell or in a `.env` file in the project root (the loader supports both). Never commit real tokens.

---

## 10. Example: minimal valid config

```yaml
runtime:
  mode: "dry-run"
  log_level: "INFO"
  data_dir: "/tmp/ghdcbot-state"
  github_adapter: "ghdcbot.adapters.github.rest:GitHubRestAdapter"
  discord_adapter: "ghdcbot.adapters.discord.api:DiscordApiAdapter"
  storage_adapter: "ghdcbot.adapters.storage.sqlite:SqliteStorage"

github:
  org: "my-org"
  token: "${GITHUB_TOKEN}"
  permissions:
    write: false

discord:
  guild_id: "YOUR_GUILD_ID"
  token: "${DISCORD_TOKEN}"
  permissions:
    write: false

scoring:
  period_days: 30
  weights:
    issue_opened: 3
    pr_opened: 5
    pr_reviewed: 2
    comment: 1

role_mappings:
  - discord_role: "Contributor"
    min_score: 10

assignments:
  review_roles: []
  issue_assignees:
    - "Contributor"

identity_mappings:
  - github_user: "your-github-username"
    discord_user_id: "your-discord-user-id"
```

This runs in dry-run, ingests all repos in `my-org`, scores contributions, plans Discord role “Contributor” for users with score ≥ 10, and plans issue assignments to users with the “Contributor” role; no writes are performed. Adjust org, guild_id, and identity_mappings for your setup.
