"""Guards against dangerous bulk GitHub mutations during scheduled sync."""

from __future__ import annotations

import os

from ghdcbot.config.models import BotConfig
from ghdcbot.core.errors import ConfigError
from ghdcbot.core.modes import RunMode

_OVERRIDE_ENV = "GITCORD_SYNC_SAFETY_OVERRIDE"


def collect_sync_safety_violations(config: BotConfig) -> list[str]:
    """Return human-readable reasons sync would perform bulk GitHub mutations."""
    if os.environ.get(_OVERRIDE_ENV, "").strip().lower() in {"1", "true", "yes"}:
        return []

    if config.runtime.mode != RunMode.ACTIVE:
        return []

    if not config.github.permissions.write:
        return []

    violations: list[str] = []
    issue_roles = list(config.assignments.issue_assignees or [])
    review_roles = list(config.assignments.review_roles or [])

    if issue_roles:
        roles = ", ".join(issue_roles)
        violations.append(
            "assignments.issue_assignees is non-empty "
            f"({roles}) with github.permissions.write: true — "
            "run-once will auto-assign every open unassigned issue in scanned repos"
        )
    if review_roles:
        roles = ", ".join(review_roles)
        violations.append(
            "assignments.review_roles is non-empty "
            f"({roles}) with github.permissions.write: true — "
            "run-once will request reviews on every open PR in scanned repos"
        )
    return violations


def assert_sync_safe(config: BotConfig) -> None:
    """Abort sync when bulk assignment/review rules are enabled with GitHub write."""
    violations = collect_sync_safety_violations(config)
    if not violations:
        return
    lines = "\n".join(f"  - {item}" for item in violations)
    raise ConfigError(
        "Sync preflight failed: dangerous bulk GitHub settings detected.\n"
        f"{lines}\n"
        "Fix: set assignments.issue_assignees and assignments.review_roles to [] "
        "(mentors can still use /assign-issue).\n"
        f"Override only if intentional: export {_OVERRIDE_ENV}=1"
    )
