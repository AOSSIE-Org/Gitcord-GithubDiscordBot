# Gitcord — Project Documentation

This document describes the Gitcord Discord–GitHub automation engine in detail, aligned with the codebase structure and behavior.

---

## 1. What Gitcord Is

Gitcord is a **local, offline-first automation engine** that:

- **Reads** GitHub activity (issues, PRs, reviews, comments, assignments) and Discord server state (members, roles).
- **Scores** contributions over a configurable time window using configurable weights.
- **Plans** Discord role changes (add/remove by score thresholds) and GitHub assignments (issue assignees, PR reviewers) from Discord role eligibility.
- **Reports** planned actions in JSON and Markdown audit reports for review.
- **Applies** mutations (Discord roles, GitHub issue/PR assignments) only when runtime mode and permissions allow.

It is **not** a 24/7 daemon or a chat bot. You run it on demand (e.g. `run-once`) on any machine; no cloud hosting is required. It is designed to be **audit-first** and **permission-aware**: dry-run and observer modes produce reports without writing anything.

---

## 2. High-Level Architecture

### 2.1 Pipeline: Read → Score → Plan → Report → Apply

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  GitHub /       │     │  Storage        │     │  Engine         │     │  Reporting      │     │  Writers        │
│  Discord        │────▶│  (SQLite)       │────▶│  (Scoring,      │────▶│  (audit.json,   │────▶│  (Discord /     │
│  Readers        │     │  contributions  │     │   Planning,     │     │   audit.md)     │     │   GitHub)       │
│                 │     │  scores, cursor │     │   Assignment)   │     │                 │     │   mutation-gated│
└─────────────────┘     └─────────────────┘     └─────────────────┘     └─────────────────┘     └─────────────────┘
```

- **Readers**: GitHub REST adapter and Discord API adapter; ingestion only, no writes.
- **Storage**: SQLite adapter; stores contribution events, scores, and sync cursor (all timestamps normalized to UTC).
- **Engine**: Orchestrator coordinates scoring (weighted by event type), Discord role planning (score vs thresholds), and GitHub assignment planning (role-based eligibility). Planning is pure and deterministic.
- **Reporting**: Writes `audit.json` and `audit.md` under `data_dir/reports` (includes contribution summary when available).
- **Writers**: Apply Discord role changes and GitHub issue/PR actions only when `MutationPolicy` allows (active mode + write permissions).

### 2.2 Module Boundaries

| Layer        | Location        | Responsibility |
|-------------|-----------------|----------------|
| **Core**    | `core/`         | Domain models (`ContributionEvent`, `Score`, `DiscordRolePlan`, `GitHubAssignmentPlan`), interfaces (protocols for readers/writers/storage), run modes and `MutationPolicy`. |
| **Config**  | `config/`       | Pydantic schema (`BotConfig`, `RuntimeConfig`, `GitHubConfig`, etc.), YAML loader with `${VAR}` env expansion, validation. |
| **Engine**   | `engine/`       | Orchestrator (single run-once flow), scoring strategy, Discord role planning, assignment strategy, report generation. |
| **Adapters** | `adapters/`     | GitHub REST (ingestion + optional stubs for write), Discord API (read members/roles + optional stubs), SQLite storage, GitHub/Discord plan writers (mutation-gated). |
| **Plugins**  | `plugins/`      | Registry: load adapter classes by dotted path (e.g. `ghdcbot.adapters.github.rest:GitHubRestAdapter`) and instantiate with kwargs. |
| **CLI**      | `cli.py`        | Parse args, load config, build orchestrator via registry, run `run-once`, handle errors and close resources. |

---

## 3. Repository Layout

```
Gitcord-GithubDiscordBot/
├── config/
│   └── example.yaml          # Example config (tokens via env, dry-run default)
├── docs/
│   ├── PROJECT.md            # This file
│   ├── architecture.md      # High-level design notes
│   └── DEMO.md              # Demo / run instructions
├── src/ghdcbot/
│   ├── __init__.py
│   ├── cli.py                # Entrypoint: --config, run-once
│   ├── adapters/
│   │   ├── github/
│   │   │   ├── rest.py       # GitHub reader (ingestion) + optional write stubs
│   │   │   └── writer.py     # GitHub plan writer (mutation-gated)
│   │   ├── discord/
│   │   │   ├── api.py        # Discord reader (members, roles)
│   │   │   └── writer.py     # Discord role writer (mutation-gated)
│   │   └── storage/
│   │       └── sqlite.py     # SQLite: contributions, scores, cursor (UTC)
│   ├── config/
│   │   ├── loader.py         # load_config, env expansion, get_active_config
│   │   └── models.py         # BotConfig, RuntimeConfig, GitHubConfig, etc.
│   ├── core/
│   │   ├── errors.py         # ConfigError, AdapterError, GitcordPermissionError
│   │   ├── interfaces.py    # Protocols: GitHubReader/Writer, DiscordReader/Writer, Storage, etc.
│   │   ├── models.py         # ContributionEvent, Score, DiscordRolePlan, GitHubAssignmentPlan, etc.
│   │   └── modes.py         # RunMode, MutationPolicy, mutation_skip_reason
│   ├── engine/
│   │   ├── orchestrator.py   # run_once: ingest → score → plan → report → apply
│   │   ├── scoring.py        # WeightedScoreStrategy (config weights, period)
│   │   ├── planning.py       # plan_discord_roles, plan_github_assignments
│   │   ├── assignment.py     # RoleBasedAssignmentStrategy (issue/PR plans)
│   │   └── reporting.py      # write_reports, audit JSON/MD, contribution summary
│   ├── logging/
│   │   └── setup.py         # JSON log formatter, level from config
│   ├── plugins/
│   │   └── registry.py      # load_adapter, build_adapter (dotted path)
│   └── utils/
│       └── __init__.py
├── tests/                    # Pytest: config, ingestion, planning, mutation gating, etc.
├── pyproject.toml           # Package deps, scripts (ghdcbot = cli:main)
├── README.md
├── CONTRIBUTING.md
└── LICENSE
```

---

## 4. Configuration (YAML + Env)

Config is a single YAML file; tokens and secrets are **never** stored in the file. Use placeholders like `${GITHUB_TOKEN}` and `${DISCORD_TOKEN}`; the loader expands them from the environment (and optionally from a `.env` file via `python-dotenv`).

### 4.1 Structure (see `config/models.py`)

| Section           | Key / subkeys | Purpose |
|-------------------|---------------|---------|
| **runtime**      | mode, log_level, data_dir, github_adapter, discord_adapter, storage_adapter | Run mode (dry-run / observer / active), logging, paths, adapter class names. |
| **github**       | org, token, api_base, permissions (read/write), repos (optional filter), user_fallback | Organization, API token (env), base URL, repo allow/deny list. |
| **discord**      | guild_id, token, permissions (read/write) | Discord server ID and bot token (env). |
| **scoring**      | period_days, weights (event_type → points) | Rolling window and contribution weights. |
| **role_mappings**| list of { discord_role, min_score } | Discord roles granted when score ≥ threshold (e.g. Contributor ≥ 10). |
| **assignments**  | review_roles, issue_assignees | Which Discord role names are eligible for PR reviews and issue assignment. |
| **identity_mappings** | list of { github_user, discord_user_id } | Links GitHub usernames to Discord user IDs (required for role/assignment logic). |

### 4.2 Event Types and Weights

Contributions are typed by `event_type`. Typical weights in config (customizable):

- `issue_opened`, `issue_closed`, `issue_assigned` (from issue timeline when applicable)
- `pr_opened`, `pr_merged`, `pr_reviewed`
- `comment` (issue/PR comments)

Scoring sums `weights[event_type]` per user over `period_days`; result is a `Score` (github_user, period_start, period_end, points).

### 4.3 Loader Behavior (`config/loader.py`)

- Validates path exists and is a file; reads as UTF-8; parses YAML. Raises `ConfigError` on missing file, read error, parse error, or empty document.
- Expands `${VAR}` recursively in strings via `os.getenv(VAR)`; missing env raises `ConfigError`.
- Validates with Pydantic (`BotConfig`); invalid schema raises `ConfigError`.
- Sets global “active config” for adapters that need it (e.g. repo filter, user_fallback).

---

## 5. Run Modes and Mutation Policy

Defined in `core/modes.py`:

- **dry-run** (default): No mutations. Plans and reports only.
- **observer**: Same as dry-run for writes; read-only observation.
- **active**: Mutations allowed **only if** the corresponding config permission is true (`github.permissions.write`, `discord.permissions.write`).

`MutationPolicy` exposes:

- `allow_github_mutations`: true only when mode is ACTIVE and `github_write_allowed`.
- `allow_discord_mutations`: true only when mode is ACTIVE and `discord_write_allowed`.

Writers (and the orchestrator’s apply steps) check these before performing any write. Dry-run and observer never write.

---

## 6. Data Flow in `run_once` (Orchestrator)

1. **Init storage**  
   `storage.init_schema()` — ensure SQLite tables exist (contributions, scores, cursors).

2. **Ingest GitHub**  
   Cursor = `storage.get_cursor("github")` or start of scoring period.  
   `github_reader.list_contributions(cursor)` yields `ContributionEvent`s (issues, PRs, reviews, comments, assignments from timeline where used).  
   Events are stored with `storage.record_contributions`; cursor is updated to the latest event time (or period end if none).

3. **Score**  
   `WeightedScoreStrategy` with config weights and `period_days`.  
   Load contributions from storage for the period; compute per-user points; `storage.upsert_scores(scores)`.

4. **Discord state**  
   `discord_reader.list_member_roles()` → map Discord user ID → list of role names.

5. **Build role → GitHub users**  
   From `identity_mappings` and `member_roles`: for each Discord user in the mapping, their Discord roles are used to add their GitHub user to the set of users for that role. Result: which GitHub users are “Contributor”, “Maintainer”, etc., for assignment purposes.

6. **Plan GitHub assignments**  
   `RoleBasedAssignmentStrategy`: eligible users from `issue_assignees` and `review_roles`; round-robin (or similar) over open issues and open PRs to produce `AssignmentPlan` and `ReviewPlan` (later converted to `GitHubAssignmentPlan` for reports and writers).

7. **Mutation policy**  
   Built from `runtime.mode` and `github.permissions.write` / `discord.permissions.write`.

8. **Reports (dry-run / observer)**  
   `plan_discord_roles(...)` → list of `DiscordRolePlan`.  
   Convert issue/review plans to `GitHubAssignmentPlan`.  
   Optionally load `contribution_summaries` from storage for the period.  
   `write_reports(discord_plans, github_plans, config, repo_count=..., contribution_summaries=...)` → `audit.json` and `audit.md` under `data_dir/reports`.

9. **Apply**  
   `apply_github_plans`: if policy allows GitHub writes, call `github_writer.assign_issue` / `request_review` for each plan (or equivalent in plan writer).  
   `apply_discord_roles`: if policy allows Discord writes, compute add/remove from score vs thresholds and call `discord_writer.add_role` / `remove_role` for each change.  
   When adapters are stubs, these only log; when real writers are wired and policy allows, they call the APIs.

---

## 7. Core Domain Models (`core/models.py`)

- **ContributionEvent**: github_user, event_type, repo, created_at (datetime), payload (dict). Immutable.
- **ContributionSummary**: per-user counts (issues, PRs, reviews, comments) and total score over a period; used in reports.
- **Score**: github_user, period_start, period_end, points. Immutable.
- **DiscordRolePlan**: discord_user_id, role, action ("add" | "remove"), reason, source. Immutable.
- **GitHubAssignmentPlan**: repo, target_number, target_type ("issue" | "pull_request"), assignee, action ("assign" | "request_review"), reason, source. Immutable.
- **AssignmentPlan** / **ReviewPlan**: internal issue/PR assignment and review plans (repo, number, assignee/reviewer).

All timestamps in storage and in scoring are normalized to UTC (see storage adapter).

---

## 8. Adapters in Detail

### 8.1 GitHub REST (`adapters/github/rest.py`)

- **Reader**:  
  - Lists org repos (with optional user fallback if configured).  
  - Applies repo filter (allow/deny list).  
  - For each repo: issues (with timeline for assignment events), PRs (opened, merged), PR reviews, issue comments, PR comments.  
  - Emits `ContributionEvent` with correct `event_type` and timestamps (e.g. issue_assigned from timeline `created_at`).  
  - Rate-limit: logs when remaining ≤ 1 but still returns the response so callers get data.  
  - Pagination and 403/404 handling; no writes in reader path.

- **Writer (stub in REST adapter)**:  
  `assign_issue`, `request_review` can be stubs (log only). The real GitHub plan writer in `adapters/github/writer.py` applies plans when policy allows and supports lifecycle (close, context manager).

### 8.2 Discord API (`adapters/discord/api.py`)

- **Reader**:  
  Lists guild roles and members (paginated). Builds map: Discord user ID → list of role names. Handles 401/403 and rate limits; returns empty map on failure so the rest of the pipeline can still run.

- **Writer (stub in API adapter)**:  
  `add_role`, `remove_role` can be stubs. The Discord plan writer in `adapters/discord/writer.py` applies role plans when policy allows.

### 8.3 Storage (`adapters/storage/sqlite.py`)

- **Schema**: contributions (github_user, event_type, repo, created_at, payload_json), scores (user, period, points), cursors (source, cursor).
- All timestamps stored and read as UTC (ISO-8601 with timezone); naive datetimes are treated as UTC.
- `list_contribution_summaries(period_start, period_end, weights)` aggregates stored events into per-user counts and scores for the report.

---

## 9. Engine: Scoring, Planning, Assignment, Reporting

- **Scoring** (`engine/scoring.py`): `WeightedScoreStrategy` filters events by period and sums `weights[event_type]` per user; returns list of `Score`.
- **Discord planning** (`engine/planning.py`): `plan_discord_roles(member_roles, scores, identity_mappings, role_mappings)` computes add/remove per user from score vs thresholds; deterministic ordering (e.g. by discord_user_id, then role).
- **GitHub assignment planning** (`engine/planning.py`): `plan_github_assignments(issues, prs, role_to_github_users, issue_roles, review_roles)` produces issue and PR review plans from role eligibility; deterministic (e.g. sorted issues/PRs, round-robin over candidates).
- **Assignment strategy** (`engine/assignment.py`): `RoleBasedAssignmentStrategy` builds eligible users from roles and produces `AssignmentPlan` and `ReviewPlan` (round-robin over issues/PRs).
- **Reporting** (`engine/reporting.py`): `write_reports` builds audit payload (discord_plans, github_plans, config, repo_filter, summary counts), writes `audit.json`, and renders `audit.md` (summary, contribution table if provided, Discord role changes, GitHub issue/PR assignments).

---

## 10. CLI and Plugins

- **CLI** (`cli.py`):  
  `--config` (required), subcommand `run-once`. Loads config, configures logging, builds GitHub/Discord/storage adapters via `build_adapter(dotted_path, **kwargs)`, constructs `Orchestrator`, runs `run_once()`, and calls `orchestrator.close()` in a finally block.

- **Registry** (`plugins/registry.py`):  
  `load_adapter(dotted_path)` splits "module:Class", imports module, returns class. `build_adapter(dotted_path, **kwargs)` instantiates and returns the adapter. Used for github_adapter, discord_adapter, storage_adapter from config.

---

## 11. How to Run and Test

- **Install**: `pip install -e .` (from repo root). Requires Python 3.11+.
- **Env**: Set `GITHUB_TOKEN` and `DISCORD_TOKEN` (or use `.env`). No secrets in YAML.
- **Run once**:  
  `python -m ghdcbot.cli --config config/example.yaml run-once`  
  or: `ghdcbot --config config/example.yaml run-once`
- **Output**: Logs (JSON) to stdout; reports in `data_dir/reports/audit.json` and `audit.md`. In dry-run, no mutations are performed.
- **Tests**: `pytest` from repo root. Covers config validation, empty org, mutation gating, planning determinism, repo filtering, writer safety, contribution summary, etc.

---

## 12. Safety and Best Practices

- **Default mode**: dry-run. Writes require explicit active mode and write permissions in config.
- **Tokens**: Only via environment (and optional `.env`); never commit real tokens or guild IDs in config files.
- **Audit first**: In dry-run/observer, reports are always generated so reviewers can see what would change before enabling writes.
- **Identity mapping**: No guessing of Discord identity; every linked user must appear in `identity_mappings` (github_user + discord_user_id).
- **Repo hygiene**: `.gitignore` should exclude `.env`, `*.sqlite`, `*.db`, virtualenv, and other local state.

---

## 13. Summary Table (Requirements vs Implementation)

| Requirement | Implementation |
|-------------|-----------------|
| Run on any computer, no cloud | Local CLI; run-once; no daemon. |
| Not required to be online all the time | Offline-first; run when you want. |
| Assign Discord roles from GitHub activity | Scoring → role_mappings → plan_discord_roles → writer (gated). |
| Assign issues/PR reviews by Discord role | role_to_github from identity_mappings + member_roles; assignment strategy → plans → writer (gated). |
| Track contributions and scores | ContributionEvent ingestion, WeightedScoreStrategy, SQLite storage, contribution summaries in report. |
| Configurable and org-agnostic | Single YAML + env; no hardcoded orgs; generic example config. |

This document reflects the codebase as of the last update; for file-level details, refer to the source under `src/ghdcbot/`.
