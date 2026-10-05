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
    IdentityMapping,
    NotificationConfig,
    PermissionConfig,
    RuntimeConfig,
)
from ghdcbot.core.models import ContributionEvent
from ghdcbot.engine.orchestrator import Orchestrator


class _FakeGitHubReader:
    def __init__(self, events: list[ContributionEvent]) -> None:
        self.events = list(events)
        self.fetched_since: list[datetime] = []

    def peek_repos_for_sync(self) -> int:
        return 1

    @property
    def sync_repos_processed(self) -> int:
        return 1

    @property
    def sync_request_count(self) -> int:
        return 1

    def list_contributions(self, since: datetime) -> list[ContributionEvent]:
        self.fetched_since.append(since)
        return [e for e in self.events if e.created_at >= since]

    def list_open_issues(self) -> list[dict]:
        return []

    def list_open_pull_requests(self) -> list[dict]:
        return []

    def close(self) -> None:
        pass


def _make_config(tmp_path, *, mode: str = "active", notifications_enabled: bool = True) -> BotConfig:
    notif_cfg = NotificationConfig(
        enabled=notifications_enabled,
        issue_assignment=True,
        channel_id="chan-1",
    )
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
            org="testorg",
            token="tok",
            api_base="https://api.github.com",
            permissions=PermissionConfig(write=True),
        ),
        discord=DiscordConfig(
            token="tok",
            guild_id="guild-1",
            permissions=PermissionConfig(write=True),
            notifications=notif_cfg,
        ),
        assignments=AssignmentConfig(issue_assignees=[], review_roles=[]),
        identity_mappings=[
            IdentityMapping(discord_user_id="discord-alice", github_user="alice"),
            IdentityMapping(discord_user_id="discord-bob", github_user="bob"),
        ],
    )


def test_failure_in_notifications_does_not_advance_cursor(tmp_path) -> None:
    storage = SqliteStorage(data_dir=str(tmp_path))
    storage.init_schema()

    prior_cursor = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
    storage.set_cursor("github", prior_cursor)

    new_event_time = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    event = ContributionEvent(
        github_user="alice",
        event_type="issue_assigned",
        repo="repo",
        created_at=new_event_time,
        payload={"issue_number": 10},
    )

    github = _FakeGitHubReader([event])
    discord_reader = SimpleNamespace(list_member_roles=lambda: {"discord-alice": ["Contributor"]})
    discord_writer = SimpleNamespace(
        create_message=MagicMock(return_value="msg-1"),
        send_message=MagicMock(return_value=True),
        add_role=MagicMock(),
        remove_role=MagicMock(),
    )

    orch = Orchestrator(
        github_reader=github,
        github_writer=github,
        discord_reader=discord_reader,
        discord_writer=discord_writer,
        storage=storage,
        config=_make_config(tmp_path),
    )

    with patch(
        "ghdcbot.engine.orchestrator._send_notifications_for_new_events",
        side_effect=RuntimeError("Discord API notification timeout"),
    ), pytest.raises(RuntimeError, match="Discord API notification timeout"):
        orch.run_once()

    # Cursor MUST NOT have moved to new_event_time
    assert storage.get_cursor("github") == prior_cursor


def test_failure_in_role_application_does_not_advance_cursor(tmp_path) -> None:
    storage = SqliteStorage(data_dir=str(tmp_path))
    storage.init_schema()

    prior_cursor = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
    storage.set_cursor("github", prior_cursor)

    new_event_time = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    event = ContributionEvent(
        github_user="alice",
        event_type="issue_assigned",
        repo="repo",
        created_at=new_event_time,
        payload={"issue_number": 10},
    )

    github = _FakeGitHubReader([event])
    discord_reader = SimpleNamespace(list_member_roles=lambda: {"discord-alice": []})
    discord_writer = SimpleNamespace(
        create_message=MagicMock(return_value="msg-1"),
        send_message=MagicMock(return_value=True),
        add_role=MagicMock(),
        remove_role=MagicMock(),
    )

    orch = Orchestrator(
        github_reader=github,
        github_writer=github,
        discord_reader=discord_reader,
        discord_writer=discord_writer,
        storage=storage,
        config=_make_config(tmp_path),
    )

    with patch(
        "ghdcbot.engine.orchestrator.apply_discord_roles",
        side_effect=RuntimeError("Discord role update failed"),
    ), pytest.raises(RuntimeError, match="Discord role update failed"):
        orch.run_once()

    # Cursor MUST NOT have moved
    assert storage.get_cursor("github") == prior_cursor


def test_failure_in_role_planning_dry_run_does_not_advance_cursor(tmp_path) -> None:
    storage = SqliteStorage(data_dir=str(tmp_path))
    storage.init_schema()

    prior_cursor = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
    storage.set_cursor("github", prior_cursor)

    new_event_time = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    event = ContributionEvent(
        github_user="alice",
        event_type="issue_assigned",
        repo="repo",
        created_at=new_event_time,
        payload={"issue_number": 10},
    )

    github = _FakeGitHubReader([event])
    discord_reader = SimpleNamespace(list_member_roles=lambda: {"discord-alice": []})
    discord_writer = SimpleNamespace(
        create_message=MagicMock(return_value="msg-1"),
        send_message=MagicMock(return_value=True),
        add_role=MagicMock(),
        remove_role=MagicMock(),
    )

    orch = Orchestrator(
        github_reader=github,
        github_writer=github,
        discord_reader=discord_reader,
        discord_writer=discord_writer,
        storage=storage,
        config=_make_config(tmp_path, mode="dry-run"),
    )

    with patch(
        "ghdcbot.engine.orchestrator.plan_discord_roles",
        side_effect=RuntimeError("Discord role planning crash"),
    ), pytest.raises(RuntimeError, match="Discord role planning crash"):
        orch.run_once()

    # Cursor MUST NOT have moved in dry-run if role planning failed
    assert storage.get_cursor("github") == prior_cursor


def test_recovery_after_failure_reprocesses_events_and_advances_cursor(tmp_path) -> None:
    storage = SqliteStorage(data_dir=str(tmp_path))
    storage.init_schema()

    prior_cursor = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
    storage.set_cursor("github", prior_cursor)

    new_event_time = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    event = ContributionEvent(
        github_user="alice",
        event_type="issue_assigned",
        repo="repo",
        created_at=new_event_time,
        payload={"issue_number": 10},
    )

    github = _FakeGitHubReader([event])
    discord_reader = SimpleNamespace(list_member_roles=lambda: {"discord-alice": []})
    discord_writer = SimpleNamespace(
        create_message=MagicMock(return_value="msg-1"),
        send_message=MagicMock(return_value=True),
        add_role=MagicMock(),
        remove_role=MagicMock(),
    )

    orch = Orchestrator(
        github_reader=github,
        github_writer=github,
        discord_reader=discord_reader,
        discord_writer=discord_writer,
        storage=storage,
        config=_make_config(tmp_path),
    )

    # 1. Failed run: notifications fail
    with patch(
        "ghdcbot.engine.orchestrator._send_notifications_for_new_events",
        side_effect=RuntimeError("Transient Discord 503"),
    ), pytest.raises(RuntimeError, match="Transient Discord 503"):
        orch.run_once()

    assert storage.get_cursor("github") == prior_cursor

    # 2. Recovery run: notifications succeed
    notif_mock = MagicMock()
    with patch(
        "ghdcbot.engine.orchestrator._send_notifications_for_new_events",
        notif_mock,
    ):
        orch.run_once()

    # The event must have been passed to notifications
    assert notif_mock.called
    passed_events = notif_mock.call_args[0][0]
    assert len(passed_events) == 1
    assert passed_events[0].created_at == new_event_time

    # Cursor now advances to the new event timestamp
    assert storage.get_cursor("github") == new_event_time


def test_success_path_advances_cursor_to_max_timestamp(tmp_path) -> None:
    storage = SqliteStorage(data_dir=str(tmp_path))
    storage.init_schema()

    prior_cursor = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
    storage.set_cursor("github", prior_cursor)

    t1 = datetime(2026, 6, 1, 11, 0, tzinfo=UTC)
    t2 = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    events = [
        ContributionEvent(
            github_user="alice",
            event_type="issue_assigned",
            repo="repo",
            created_at=t1,
            payload={"issue_number": 1},
        ),
        ContributionEvent(
            github_user="bob",
            event_type="issue_assigned",
            repo="repo",
            created_at=t2,
            payload={"issue_number": 2},
        ),
    ]

    github = _FakeGitHubReader(events)
    discord_reader = SimpleNamespace(list_member_roles=dict)
    discord_writer = SimpleNamespace(
        create_message=MagicMock(return_value="msg-1"),
        send_message=MagicMock(return_value=True),
        add_role=MagicMock(),
        remove_role=MagicMock(),
    )

    orch = Orchestrator(
        github_reader=github,
        github_writer=github,
        discord_reader=discord_reader,
        discord_writer=discord_writer,
        storage=storage,
        config=_make_config(tmp_path),
    )

    orch.run_once()

    assert storage.get_cursor("github") == t2


def test_partial_failure_does_not_duplicate_notifications_on_retry(tmp_path) -> None:
    storage = SqliteStorage(data_dir=str(tmp_path))
    storage.init_schema()

    # Pre-verify alice and bob in storage
    storage.create_identity_claim("discord-alice", "alice", "code1", datetime(2030, 1, 1, tzinfo=UTC))
    storage.mark_identity_verified("discord-alice", "alice")
    storage.create_identity_claim("discord-bob", "bob", "code2", datetime(2030, 1, 1, tzinfo=UTC))
    storage.mark_identity_verified("discord-bob", "bob")

    prior_cursor = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
    storage.set_cursor("github", prior_cursor)

    t1 = datetime(2026, 6, 1, 11, 0, tzinfo=UTC)
    t2 = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    e1 = ContributionEvent(
        github_user="alice",
        event_type="issue_assigned",
        repo="repo",
        created_at=t1,
        payload={"issue_number": 1},
    )
    e2 = ContributionEvent(
        github_user="bob",
        event_type="issue_assigned",
        repo="repo",
        created_at=t2,
        payload={"issue_number": 2},
    )

    github = _FakeGitHubReader([e1, e2])
    discord_reader = SimpleNamespace(list_member_roles=dict)

    sent_messages: list[tuple[str, str]] = []

    def fake_send_message(channel_or_user_id, content):
        sent_messages.append((channel_or_user_id, content))
        return True

    discord_writer = SimpleNamespace(
        create_message=MagicMock(side_effect=fake_send_message),
        send_message=MagicMock(side_effect=fake_send_message),
        add_role=MagicMock(),
        remove_role=MagicMock(),
    )

    orch = Orchestrator(
        github_reader=github,
        github_writer=github,
        discord_reader=discord_reader,
        discord_writer=discord_writer,
        storage=storage,
        config=_make_config(tmp_path),
    )

    from ghdcbot.engine.orchestrator import send_notification_for_event as real_send_notification

    calls = 0

    def fail_on_second_event(event, storage_arg, writer, pol, cfg, org):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("Crash on second event notification")
        return real_send_notification(event, storage_arg, writer, pol, cfg, org)

    # 1. First run: sends alice's notification (#1), crashes on bob's (#2)
    with patch(
        "ghdcbot.engine.orchestrator.send_notification_for_event",
        side_effect=fail_on_second_event,
    ), pytest.raises(RuntimeError, match="Crash on second event notification"):
        orch.run_once()

    # Cursor should not have advanced
    assert storage.get_cursor("github") == prior_cursor
    assert len(sent_messages) == 1  # Only event 1 was sent
    assert "#1" in sent_messages[0][1]

    # 2. Second run: failure is resolved (normal send_notification_for_event)
    orch.run_once()

    # Event 1 was deduplicated and NOT re-sent! Exactly 2 messages in total.
    assert len(sent_messages) == 2
    assert "#1" in sent_messages[0][1]
    assert "#2" in sent_messages[1][1]
    # Cursor now advanced to t2
    assert storage.get_cursor("github") == t2
