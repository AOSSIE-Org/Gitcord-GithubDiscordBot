"""Tests for Discord rate limit safety: no false role grants on API failure.

When Discord returns a 429 Too Many Requests (or any HTTP/network error) during
member listing, list_member_roles() returns None.  The orchestrator must skip
role mutations and planning to prevent mass false role additions and DM spam.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

from ghdcbot.adapters.storage.sqlite import SqliteStorage
from ghdcbot.config.models import (
    AssignmentConfig,
    BotConfig,
    DiscordConfig,
    GitHubConfig,
    IdentityMapping,
    PermissionConfig,
    RoleMappingConfig,
    RuntimeConfig,
)
from ghdcbot.core.models import ContributionEvent, Score
from ghdcbot.core.modes import MutationPolicy, RunMode
from ghdcbot.engine.orchestrator import Orchestrator, apply_discord_roles

# -- helpers ----------------------------------------------------------------


class _FakeGitHubReader:
    """Minimal GitHub reader stub for orchestrator tests."""

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


def _make_config(tmp_path, *, mode: str = "active") -> BotConfig:
    """Build a minimal BotConfig for orchestrator tests."""
    return BotConfig(
        runtime=RuntimeConfig(
            mode=mode,
            data_dir=str(tmp_path),
            storage_adapter="ghdcbot.adapters.storage.sqlite:SqliteStorage",
            github_adapter="ghdcbot.adapters.github.rest:GitHubRestAdapter",
            discord_adapter="ghdcbot.adapters.discord.api:DiscordApiAdapter",
            activity_period_days=30,
            enable_discord_role_updates=True,
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
        ),
        assignments=AssignmentConfig(issue_assignees=[], review_roles=[]),
        identity_mappings=[
            IdentityMapping(github_user="alice", discord_user_id="u1"),
            IdentityMapping(github_user="bob", discord_user_id="u2"),
        ],
        role_mappings=[
            RoleMappingConfig(discord_role="Contributor", min_score=5),
        ],
    )


# -- orchestrator-level tests ----------------------------------------------


def test_member_roles_none_skips_apply_discord_roles(tmp_path, caplog) -> None:
    """When list_member_roles() returns None, apply_discord_roles is skipped."""
    storage = SqliteStorage(data_dir=str(tmp_path))
    storage.init_schema()

    discord_reader = SimpleNamespace(
        list_member_roles=lambda: None,
        close=lambda: None,
    )
    discord_writer = MagicMock()
    discord_writer.add_role = MagicMock()
    discord_writer.remove_role = MagicMock()
    discord_writer.send_dm = MagicMock()

    github = _FakeGitHubReader()
    config = _make_config(tmp_path, mode="active")

    orch = Orchestrator(
        github_reader=github,
        github_writer=github,
        discord_reader=discord_reader,
        discord_writer=discord_writer,
        storage=storage,
        config=config,
    )

    caplog.set_level(logging.WARNING, logger="Orchestrator")
    orch.run_once()

    # No role additions or removals should have occurred.
    discord_writer.add_role.assert_not_called()
    discord_writer.remove_role.assert_not_called()
    discord_writer.send_dm.assert_not_called()

    # A warning about unavailable member data should have been logged.
    warning_messages = [
        r.message for r in caplog.records if r.levelno >= logging.WARNING
    ]
    assert any("member role listing failed" in msg.lower() for msg in warning_messages)


def test_member_roles_none_skips_plan_discord_roles(tmp_path, caplog) -> None:
    """When list_member_roles() returns None, plan_discord_roles is skipped."""
    storage = SqliteStorage(data_dir=str(tmp_path))
    storage.init_schema()

    discord_reader = SimpleNamespace(
        list_member_roles=lambda: None,
        close=lambda: None,
    )
    discord_writer = SimpleNamespace(
        add_role=lambda *a, **k: None,
        remove_role=lambda *a, **k: None,
        close=lambda: None,
    )

    github = _FakeGitHubReader()
    config = _make_config(tmp_path, mode="dry-run")

    orch = Orchestrator(
        github_reader=github,
        github_writer=github,
        discord_reader=discord_reader,
        discord_writer=discord_writer,
        storage=storage,
        config=config,
    )

    caplog.set_level(logging.INFO, logger="Orchestrator")
    orch.run_once()

    info_messages = [r.message for r in caplog.records]
    assert any("member data unavailable" in msg.lower() for msg in info_messages)


def test_member_roles_none_does_not_block_github_operations(tmp_path) -> None:
    """GitHub contribution ingestion proceeds even when Discord listing fails."""
    storage = SqliteStorage(data_dir=str(tmp_path))
    storage.init_schema()

    event = ContributionEvent(
        github_user="alice",
        event_type="pr_merged",
        repo="test-repo",
        created_at=datetime.now(UTC) - timedelta(hours=1),
        payload={"pr_number": 42},
    )
    github = _FakeGitHubReader(events=[event])

    discord_reader = SimpleNamespace(
        list_member_roles=lambda: None,
        close=lambda: None,
    )
    discord_writer = MagicMock()
    discord_writer.add_role = MagicMock()
    discord_writer.send_dm = MagicMock()

    config = _make_config(tmp_path, mode="active")

    orch = Orchestrator(
        github_reader=github,
        github_writer=github,
        discord_reader=discord_reader,
        discord_writer=discord_writer,
        storage=storage,
        config=config,
    )

    orch.run_once()

    # Contribution should have been stored despite Discord failure.
    contributions = list(
        storage.list_contributions(datetime.now(UTC) - timedelta(days=1))
    )
    assert len(contributions) >= 1
    assert any(c.github_user == "alice" for c in contributions)

    # No Discord mutations should have occurred.
    discord_writer.add_role.assert_not_called()
    discord_writer.send_dm.assert_not_called()


# -- adapter-level tests ---------------------------------------------------


def test_list_member_roles_returns_none_on_rate_limit() -> None:
    """list_member_roles() returns None when _list_members() fails."""
    from ghdcbot.adapters.discord.api import DiscordApiAdapter

    adapter = DiscordApiAdapter.__new__(DiscordApiAdapter)
    adapter._logger = logging.getLogger("test")
    adapter._guild_id = "test-guild"

    # Simulate _list_roles succeeding but _list_members failing (rate limit).
    adapter._list_roles = lambda: ([{"id": "r1", "name": "Contributor"}], True)
    adapter._list_members = lambda: ([], False)
    adapter._log_capabilities = lambda **kw: None

    result = adapter.list_member_roles()
    assert result is None


def test_list_member_roles_returns_none_on_role_listing_failure() -> None:
    """list_member_roles() returns None when _list_roles() fails."""
    from ghdcbot.adapters.discord.api import DiscordApiAdapter

    adapter = DiscordApiAdapter.__new__(DiscordApiAdapter)
    adapter._logger = logging.getLogger("test")
    adapter._guild_id = "test-guild"

    # Simulate _list_roles failing (rate limit or permission error).
    adapter._list_roles = lambda: ([], False)
    adapter._list_members = lambda: ([], True)
    adapter._log_capabilities = lambda **kw: None

    result = adapter.list_member_roles()
    assert result is None


def test_valid_empty_member_roles_still_works(tmp_path) -> None:
    """A legitimate empty dict {} still allows role operations to proceed.

    This confirms backward compatibility: an empty guild (or guild where no
    mapped members exist) does not trigger the safety guard.
    """
    mock_discord_writer = MagicMock()
    mock_discord_writer.add_role = MagicMock()
    mock_discord_writer.remove_role = MagicMock()
    mock_discord_writer.send_dm = MagicMock(return_value=True)

    period_end = datetime.now(UTC)
    period_start = period_end - timedelta(days=30)

    scores = [
        Score(
            github_user="alice",
            period_start=period_start,
            period_end=period_end,
            points=10,
        ),
    ]

    identity_mappings = [
        IdentityMapping(github_user="alice", discord_user_id="u1"),
    ]

    role_mappings = [
        RoleMappingConfig(discord_role="Contributor", min_score=5),
    ]

    # Empty dict means "successfully fetched, no mapped members have roles".
    member_roles: dict[str, list[str]] = {}

    policy = MutationPolicy(
        mode=RunMode.ACTIVE,
        discord_write_allowed=True,
        github_write_allowed=False,
    )

    apply_discord_roles(
        discord_writer=mock_discord_writer,
        member_roles=member_roles,
        scores=scores,
        identity_mappings=identity_mappings,
        role_mappings=role_mappings,
        policy=policy,
    )

    # The role should be added because {} means "valid empty", not "API failed".
    mock_discord_writer.add_role.assert_called_once_with("u1", "Contributor")
