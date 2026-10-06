"""Tests for cursor advancement timing in Orchestrator.

Verifies that the GitHub ingestion cursor is NOT advanced if a crash
occurs during notification dispatch, ensuring unsent notifications are
retried on the subsequent sync cycle. Also verifies that cursor advances
normally upon successful sync completion.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from ghdcbot.adapters.storage.sqlite import SqliteStorage
from ghdcbot.config.models import (
    AssignmentConfig,
    BotConfig,
    DiscordConfig,
    GitHubConfig,
    NotificationConfig,
    PermissionConfig,
    RuntimeConfig,
)
from ghdcbot.core.models import ContributionEvent
from ghdcbot.engine.orchestrator import Orchestrator


class _FakeGitHubReader:
    """Minimal GitHub reader stub for cursor timing tests."""

    def __init__(self, events: list[ContributionEvent] | None = None) -> None:
        self._events = events or []
        self._repos_processed = 0

    def peek_repos_for_sync(self) -> int:
        return 1

    @property
    def sync_repos_processed(self) -> int:
        return self._repos_processed

    @property
    def sync_request_count(self) -> int:
        return 0

    def list_contributions(self, since: datetime) -> list[ContributionEvent]:
        self._repos_processed = 1
        return list(self._events)

    def list_open_issues(self) -> list[dict]:
        return []

    def list_open_pull_requests(self) -> list[dict]:
        return []

    def close(self) -> None:
        return None


def _make_config(tmp_path, *, notifications_enabled: bool = True) -> BotConfig:
    """Build a minimal BotConfig with notification configuration."""
    return BotConfig(
        runtime=RuntimeConfig(
            mode="active",
            data_dir=str(tmp_path),
            storage_adapter="ghdcbot.adapters.storage.sqlite:SqliteStorage",
            github_adapter="ghdcbot.adapters.github.rest:GitHubRestAdapter",
            discord_adapter="ghdcbot.adapters.discord.api:DiscordApiAdapter",
            activity_period_days=30,
            enable_discord_role_updates=False,
        ),
        github=GitHubConfig(
            org="test-org",
            token="fake-token",
            api_base="https://api.github.com",
        ),
        discord=DiscordConfig(
            token="fake-token",
            guild_id="111",
            permissions=PermissionConfig(write=True),
            notifications=NotificationConfig(
                enabled=notifications_enabled,
                verified_only=True,
            ),
        ),
        assignments=AssignmentConfig(issue_assignees=[], review_roles=[]),
    )


def _make_orchestrator(
    tmp_path,
    events: list[ContributionEvent],
    *,
    notifications_enabled: bool = True,
) -> tuple[Orchestrator, SqliteStorage]:
    """Helper to assemble Orchestrator with fake readers/writers."""
    storage = SqliteStorage(data_dir=str(tmp_path))
    storage.init_schema()

    github = _FakeGitHubReader(events)
    discord_reader = SimpleNamespace(
        list_member_roles=dict,
        close=lambda: None,
    )
    discord_writer = MagicMock()

    config = _make_config(tmp_path, notifications_enabled=notifications_enabled)
    orch = Orchestrator(
        github_reader=github,
        github_writer=github,
        discord_reader=discord_reader,
        discord_writer=discord_writer,
        storage=storage,
        config=config,
    )
    return orch, storage


def test_cursor_not_advanced_when_notifications_crash(tmp_path) -> None:
    """When notification dispatch raises an exception, the ingestion cursor
    must NOT advance, so events are re-evaluated on the next sync cycle."""
    event_time = datetime(2026, 10, 6, 12, 0, 0, tzinfo=UTC)
    event = ContributionEvent(
        github_user="alice",
        event_type="pr_opened",
        repo="test-repo",
        created_at=event_time,
        payload={"number": 42},
    )

    orch, storage = _make_orchestrator(tmp_path, [event], notifications_enabled=True)

    # Prior cursor should be None initially
    assert storage.get_cursor("github") is None

    # Simulate a crash during notification dispatch
    with patch(
        "ghdcbot.engine.orchestrator._send_notifications_for_new_events",
        side_effect=RuntimeError("Discord API unavailable"),
    ), pytest.raises(RuntimeError, match="Discord API unavailable"):
        orch.run_once()

    # The cursor must NOT have advanced because the sync crashed during notifications
    assert storage.get_cursor("github") is None


def test_cursor_advanced_on_successful_sync(tmp_path) -> None:
    """When sync and notification dispatch succeed, the cursor must advance
    to the timestamp of the latest contribution event."""
    event_time = datetime(2026, 10, 6, 12, 0, 0, tzinfo=UTC)
    event = ContributionEvent(
        github_user="alice",
        event_type="pr_opened",
        repo="test-repo",
        created_at=event_time,
        payload={"number": 42},
    )

    orch, storage = _make_orchestrator(tmp_path, [event], notifications_enabled=True)

    orch.run_once()

    # Cursor should have advanced to event_time
    saved_cursor = storage.get_cursor("github")
    assert saved_cursor == event_time


def test_cursor_unchanged_when_no_new_contributions(tmp_path) -> None:
    """When there are no new contributions, cursor remains at its prior state."""
    prior_time = datetime(2026, 10, 5, 0, 0, 0, tzinfo=UTC)
    orch, storage = _make_orchestrator(tmp_path, [], notifications_enabled=True)
    storage.set_cursor("github", prior_time)

    orch.run_once()

    assert storage.get_cursor("github") == prior_time
