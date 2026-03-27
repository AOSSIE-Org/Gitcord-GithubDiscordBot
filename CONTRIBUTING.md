# Contributing to Gitcord

Thanks for your interest in improving Gitcord.

## Setup

1. Fork and clone the repository.
2. Create and activate a virtual environment.
3. Install project dependencies:
   - `./.venv/bin/python -m pip install -e .`

## Development Workflow

1. Create a feature branch from `main`.
2. Make focused changes with clear commit messages.
3. Run tests locally:
   - `pytest`
4. Open a pull request with:
   - What changed
   - Why it changed
   - How it was tested

## Coding Guidelines

- Keep changes small and scoped.
- Preserve offline-first and audit-first behavior.
- Prefer deterministic logic in planning/scoring paths.
- Update docs/config examples when behavior changes.

## Pull Request Checklist

- [ ] Tests pass locally
- [ ] Docs updated when needed
- [ ] Config examples still match runtime behavior
- [ ] No secrets committed
