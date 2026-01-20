# Gitcord (Discord–GitHub Automation Engine)

## What This Is
Gitcord is a local, offline‑first automation engine that reads GitHub activity and
Discord state, then plans role changes and GitHub assignments in a deterministic,
reviewable way. It is designed for safety: dry‑run and observer modes produce audit
reports without mutating anything. **No admin privileges are required to evaluate**
the system in dry‑run mode.

## Core Principles
- Offline-first
- Permission-aware
- Deterministic planning
- Audit before apply
- Thin, boring execution

## Architecture Overview
**Read → Plan → Report → Apply**
- **Read**: Permission-aware GitHub/Discord readers ingest state without mutations.
- **Plan**: Deterministic planning produces role and assignment plans.
- **Report**: Audit reports (JSON + Markdown) make decisions reviewable.
- **Apply**: Thin writers execute plans only when `MutationPolicy` allows it.

`MutationPolicy` enforces dry‑run, observer, and active modes and gates all side effects.

## Repository Structure
```
src/ghdcbot/
  adapters/      # GitHub/Discord/storage adapters (IO)
  core/          # Domain models, policies, interfaces
  engine/        # Orchestrator + planning/scoring/reporting
  config/        # Pydantic config schema + loader
  logging/       # Logging setup
  plugins/       # Adapter registry
tests/           # Safety + determinism test suite
docs/            # Architecture and demo notes
config/          # Example config
```

## Workflow (End-to-End)
1. Load and validate config (`config/example.yaml`).
2. Read GitHub + Discord state into local storage (read-only).
3. Score contributions using configured weights.
4. Plan role changes and assignments deterministically.
5. Write audit reports to `<data_dir>/reports`.
6. Apply mutations only if mode and permissions allow.

## Quickstart (5 minutes)
**Requirements:** Python 3.11+

1. Create a virtual environment and install dependencies:
```
python -m venv .venv
. .venv/bin/activate
pip install -e .
```
2. Export required tokens as environment variables:
```
export GITHUB_TOKEN="your_github_token"
export DISCORD_TOKEN="your_discord_token"
```
3. Copy and edit the example config:
```
cp config/example.yaml /tmp/ghdcbot-config.yaml
```
4. Update `runtime.data_dir`, `github.org`, and `discord.guild_id` in `/tmp/ghdcbot-config.yaml`.
5. Keep `runtime.mode` set to `dry-run` (default).
6. Run a dry‑run cycle:
```
python -m ghdcbot.cli --config /tmp/ghdcbot-config.yaml run-once
```
7. Expected output files:
```
<data_dir>/reports/audit.json
<data_dir>/reports/audit.md
```

## Safe Startup Checklist
- Use `runtime.mode: "dry-run"` for all first runs.
- Ensure `github.permissions.write` and `discord.permissions.write` are `false`.
- Confirm tokens are set via environment variables only.
- Inspect audit reports before enabling any writes.

## Running Modes
- **dry‑run**: Plans and reports only; no mutations.
- **observer**: Read‑only observation; plans and reports only; no mutations.
- **active**: Mutations are allowed only if write permissions are true. Use with care.

## Audit Reports
- **audit.json**: Structured, machine‑readable plan output for tooling and audits.
- **audit.md**: Human‑readable summary for reviewers.
These reports are the primary trust mechanism: reviewers can verify planned actions
without executing them.

## Configuration Overview
- **github.repos**: allow/deny filtering to control which repos are ingested.
- **role thresholds**: map contribution scores to Discord roles.
- **scoring**: configure weights for contribution events.
- **identity mappings**: link GitHub users to Discord user IDs.
All behavior is config‑driven and org‑agnostic.

## Testing
Run the safety‑focused test suite:
```
pytest
```
Tests verify determinism, mutation gating, and safe behavior under missing permissions.

## Project Status
- Production‑ready foundation
- Designed for gradual adoption
- Safe to evaluate without permissions

## Contributing
Start with `docs/architecture.md` and keep the core boundaries intact:
- Readers are read‑only
- Planners are pure
- Writers are thin and mutation‑gated
Changes that blur these boundaries should be avoided without strong justification.
