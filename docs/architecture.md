# Architecture

This system is an **automation engine** rather than a single bot. The design is
intentionally modular, permission-aware, and offline-first.

## Goals

- Run locally with no requirement for continuous uptime
- Operate safely without admin privileges
- Support dry-run and observer (read-only) modes
- Separate ingestion, scoring, and mutations
- Be extensible to any GitHub organization and Discord server

## High-Level Flow

1. **Ingest** GitHub events and Discord state into local storage.
2. **Score** contributions using configurable rules.
3. **Plan** role and assignment changes in memory.
4. **Apply** changes through mutation interfaces (or emit dry-run logs).

## Module Boundaries

### Core (Domain)
Pure, unit-testable types and rules.

- Domain models (contributors, events, scores)
- Scoring and assignment strategies
- Run modes and permission enforcement rules

### Application (Engine)
Orchestrates the workflow and coordinates adapters.

- Orchestrator (`engine/orchestrator.py`)
- Pipelines (ingest → score → plan → apply)
- Policy handling (dry-run, observer)

### Adapters (IO)
External systems with side effects.

- GitHub REST adapter (read + write with permission checks)
- Discord adapter (read + write with permission checks)
- SQLite storage adapter

### Plugins
Configurable adapter instantiation.

- Registry for dotted-path adapters
- Dependency injection via config

## Configuration Strategy

- YAML configuration validated by Pydantic models
- Strict schema and explicit defaults
- No org-specific logic in code

## Permission Boundaries

The engine does **not** assume admin or owner privileges.

### GitHub
- Read-only: list issues, PRs, reviews, contributors
- Write (if permitted): assign issues, request reviews, add labels
- If write permissions fail: engine downgrades to observer mode for GitHub writes

### Discord
- Read-only: read guild members and roles
- Write (if permitted): assign roles
- If role mutations fail: log and continue in observer mode for Discord writes

## Failure Modes

- **Missing token**: configuration error, fail fast.
- **Insufficient permissions**: log and continue with observer mode.
- **Rate limit / API outage**: exponential backoff, preserve local state.
- **Schema mismatch**: fail fast with config validation error.

## Extensibility

- New adapters by implementing interfaces in `core/interfaces.py`
- New scoring strategies by implementing `ScoreStrategy` protocol
- New assignment strategies by implementing `AssignmentStrategy` protocol
