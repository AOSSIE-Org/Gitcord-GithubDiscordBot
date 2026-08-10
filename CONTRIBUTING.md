# Contributing to Gitcord

Thanks for your interest in improving Gitcord.

## Community

- **AOSSIE Discord — Gitcord project thread:**  
  https://discord.com/channels/1022871757289422898/1465995983791063140  
- **AOSSIE Discord server invite:** https://discord.gg/hjUhu33uAn  
- Prefer the **Gitcord** thread for project-specific questions; use GitHub Issues for bugs and feature requests.

## Setup

1. Fork and clone the repository.
2. Create and activate a virtual environment.
3. Install project dependencies (runtime + dev extras: `pytest`, `ruff`):
    - `python -m pip install -e ".[dev]"`

## Development Workflow

1. Create a feature branch from `main`.
2. Make focused changes with clear commit messages.
3. Run tests and lint locally:
   - `pytest`
   - `ruff check src tests`
4. Open a pull request with:
   - What changed
   - Why it changed
   - How it was tested

## Coding Guidelines

- Keep changes small and scoped.
- Preserve offline-first and audit-first behavior.
- Prefer deterministic logic in planning/scoring paths.
- Update docs/config examples when behavior changes.
- Never commit secrets (`.env`, App private keys, live tokens).
- New functionality should include tests in the automated suite when practical.

## Pull Request Checklist

- [ ] Tests pass locally (`pytest`)
- [ ] Docs updated when needed
- [ ] Config examples still match runtime behavior
- [ ] No secrets committed
- [ ] Lint clean (`ruff check`) when you touched Python

## Agent / AI contributors

See [`AGENTS.md`](AGENTS.md) for architecture constraints and safety rules.
