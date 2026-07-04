from __future__ import annotations

import pytest

from ghdcbot.config.models import (
    AssignmentConfig,
    BotConfig,
    DiscordConfig,
    GitHubConfig,
    PermissionConfig,
    RuntimeConfig,
)
from ghdcbot.config.sync_safety import assert_sync_safe, collect_sync_safety_violations
from ghdcbot.core.errors import ConfigError
from ghdcbot.core.modes import RunMode


def _config(
    *,
    mode: RunMode = RunMode.ACTIVE,
    github_write: bool = True,
    issue_assignees: list[str] | None = None,
    review_roles: list[str] | None = None,
) -> BotConfig:
    return BotConfig(
        runtime=RuntimeConfig(
            mode=mode,
            data_dir="/tmp/x",
            github_adapter="ghdcbot.adapters.github.rest:GitHubRestAdapter",
            discord_adapter="ghdcbot.adapters.discord.api:DiscordApiAdapter",
            storage_adapter="ghdcbot.adapters.storage.sqlite:SqliteStorage",
        ),
        github=GitHubConfig(
            org="AOSSIE-Org",
            token="t",
            api_base="https://api.github.com",
            permissions=PermissionConfig(read=True, write=github_write),
        ),
        discord=DiscordConfig(guild_id="1", token="t"),
        assignments=AssignmentConfig(
            issue_assignees=issue_assignees or [],
            review_roles=review_roles or [],
        ),
    )


def test_preflight_ok_when_bulk_rules_empty() -> None:
    assert collect_sync_safety_violations(_config()) == []


def test_preflight_ok_when_github_write_disabled() -> None:
    violations = collect_sync_safety_violations(
        _config(github_write=False, issue_assignees=["Mentor"], review_roles=["Maintainer"])
    )
    assert violations == []


def test_preflight_ok_in_dry_run_even_with_bulk_rules() -> None:
    violations = collect_sync_safety_violations(
        _config(mode=RunMode.DRY_RUN, issue_assignees=["Mentor"])
    )
    assert violations == []


def test_preflight_fails_on_issue_assignees() -> None:
    violations = collect_sync_safety_violations(_config(issue_assignees=["Mentor"]))
    assert len(violations) == 1
    assert "issue_assignees" in violations[0]


def test_preflight_fails_on_review_roles() -> None:
    violations = collect_sync_safety_violations(_config(review_roles=["Maintainer"]))
    assert len(violations) == 1
    assert "review_roles" in violations[0]


def test_preflight_fails_on_both_rules() -> None:
    violations = collect_sync_safety_violations(
        _config(issue_assignees=["Mentor"], review_roles=["Maintainer"])
    )
    assert len(violations) == 2


def test_assert_sync_safe_raises_config_error() -> None:
    with pytest.raises(ConfigError, match="issue_assignees"):
        assert_sync_safe(_config(issue_assignees=["Mentor"]))


def test_override_env_allows_bulk_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITCORD_SYNC_SAFETY_OVERRIDE", "1")
    assert collect_sync_safety_violations(_config(issue_assignees=["Mentor"])) == []
