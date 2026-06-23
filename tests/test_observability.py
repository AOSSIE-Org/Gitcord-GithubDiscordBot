from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from ghdcbot.adapters.storage.sqlite import SqliteStorage
from ghdcbot.config.models import (
    AssignmentConfig,
    BotConfig,
    DiscordConfig,
    GitHubConfig,
    RoleMappingConfig,
    RuntimeConfig,
)
from ghdcbot.core.models import ContributionEvent
from ghdcbot.engine.orchestrator import Orchestrator
from ghdcbot.logging.setup import JsonFormatter, configure_logging
from ghdcbot.logging.sync_context import SyncContextFilter, SyncSession, generate_sync_id


def test_json_formatter_preserves_extra_fields() -> None:
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="GitHubRestAdapter",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="Ingesting repository",
        args=(),
        exc_info=None,
    )
    record.repo = "AOSSIE/Gitcord"

    payload = json.loads(formatter.format(record))

    assert payload["message"] == "Ingesting repository"
    assert payload["repo"] == "AOSSIE/Gitcord"
    assert payload["level"] == "INFO"


def test_json_formatter_includes_sync_id_from_filter() -> None:
    configure_logging("INFO")
    logger = logging.getLogger("test.sync_id")

    with SyncSession() as sync:
        logger.info("inside sync", extra={"repo": "AOSSIE/Gitcord"})

    # Capture via custom handler for assertion
    records: list[logging.LogRecord] = []

    class _CaptureHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    capture = _CaptureHandler()
    capture.setFormatter(JsonFormatter())
    capture.addFilter(SyncContextFilter())

    with SyncSession() as sync:
        capture.handle(
            logging.LogRecord(
                name="test",
                level=logging.INFO,
                pathname=__file__,
                lineno=1,
                msg="inside sync",
                args=(),
                exc_info=None,
            )
        )
        payload = json.loads(capture.formatter.format(records[-1]))
        assert payload["sync_id"] == sync.sync_id
        assert sync.sync_id.startswith("sync_")


def test_generate_sync_id_format() -> None:
    sync_id = generate_sync_id()
    parts = sync_id.split("_")
    assert parts[0] == "sync"
    assert len(parts[1]) == 8
    assert len(parts[2]) == 6
    assert len(parts[3]) == 4


def test_sync_session_logs_started_and_completed(caplog) -> None:
    caplog.set_level(logging.INFO, logger="Orchestrator")
    started_at = datetime(2026, 6, 18, 14, 23, tzinfo=timezone.utc)
    completed_at = datetime(2026, 6, 18, 14, 24, tzinfo=timezone.utc)

    with SyncSession() as sync:
        sync.log_started(started_at, repos_total=12)
        sync.log_event_summary([])
        sync.log_completed(
            completed_at,
            repos_processed=12,
            events_fetched=240,
            requests_total=418,
        )

    started = [r for r in caplog.records if getattr(r, "event", None) == "github_sync_started"]
    completed = [r for r in caplog.records if getattr(r, "event", None) == "github_sync_completed"]
    request_summary = [r for r in caplog.records if getattr(r, "event", None) == "github_request_summary"]
    event_summary = [r for r in caplog.records if getattr(r, "event", None) == "github_event_summary"]

    assert len(started) == 1
    assert started[0].cursor_before == started_at.isoformat()
    assert started[0].repos_total == 12
    assert started[0].sync_id == sync.sync_id

    assert len(completed) == 1
    assert completed[0].cursor_before == started_at.isoformat()
    assert completed[0].cursor_after == completed_at.isoformat()
    assert completed[0].repos_processed == 12
    assert completed[0].events_fetched == 240
    assert isinstance(completed[0].duration_ms, int)

    assert len(request_summary) == 1
    assert request_summary[0].requests_total == 418

    assert len(event_summary) == 1
    assert event_summary[0].issue_opened == 0
    assert event_summary[0].pr_merged == 0


def test_log_event_summary_handles_missing_event_type(caplog) -> None:
    caplog.set_level(logging.INFO, logger="Orchestrator")

    with SyncSession() as sync:
        sync.log_event_summary([SimpleNamespace(), ContributionEvent(
            github_user="alice",
            event_type="issue_opened",
            repo="repo",
            created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
            payload={},
        )])

    summary = [r for r in caplog.records if getattr(r, "event", None) == "github_event_summary"]
    assert len(summary) == 1
    assert summary[0].issue_opened == 1
    assert summary[0].unknown == 1


def test_sync_session_logs_failed(caplog) -> None:
    caplog.set_level(logging.ERROR, logger="Orchestrator")

    with SyncSession() as sync:
        sync.log_failed("boom")

    failed = [r for r in caplog.records if getattr(r, "event", None) == "github_sync_failed"]
    assert len(failed) == 1
    assert failed[0].error == "boom"
    assert isinstance(failed[0].duration_ms, int)


class _FakeGitHubReader:
    def __init__(self, events: list[ContributionEvent]) -> None:
        self._events = events
        self._repos_processed = 0

    def peek_repos_for_sync(self) -> int:
        return 1

    @property
    def sync_repos_processed(self) -> int:
        return self._repos_processed

    @property
    def sync_request_count(self) -> int:
        return 3

    def list_contributions(self, since: datetime) -> list[ContributionEvent]:
        self._repos_processed = 1
        return list(self._events)

    def list_open_issues(self) -> list[dict]:
        return []

    def list_open_pull_requests(self) -> list[dict]:
        return []

    def close(self) -> None:
        return None


def _minimal_config(tmp_path) -> BotConfig:
    return BotConfig(
        runtime=RuntimeConfig(
            mode="dry-run",
            data_dir=str(tmp_path),
            storage_adapter="ghdcbot.adapters.storage.sqlite:SqliteStorage",
            github_adapter="ghdcbot.adapters.github.rest:GitHubRestAdapter",
            discord_adapter="ghdcbot.adapters.discord.api:DiscordApiAdapter",
            activity_period_days=30,
            enable_discord_role_updates=False,
        ),
        github=GitHubConfig(org="x", token="t", api_base="https://api.github.com"),
        discord=DiscordConfig(token="t", guild_id="1"),
        assignments=AssignmentConfig(issue_assignees=[], review_roles=[]),
        identity_mappings=[],
    )


def test_orchestrator_run_once_emits_sync_lifecycle_logs(tmp_path, caplog) -> None:
    caplog.set_level(logging.INFO, logger="Orchestrator")

    storage = SqliteStorage(data_dir=str(tmp_path))
    storage.init_schema()
    since = datetime(2026, 1, 1, tzinfo=timezone.utc)
    events = [
        ContributionEvent(
            github_user="alice",
            event_type="issue_opened",
            repo="repo",
            created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
            payload={"issue_number": 1},
        ),
        ContributionEvent(
            github_user="bob",
            event_type="pr_merged",
            repo="repo",
            created_at=datetime(2026, 6, 2, tzinfo=timezone.utc),
            payload={"pr_number": 2},
        ),
    ]
    github = _FakeGitHubReader(events)
    discord = SimpleNamespace(
        list_member_roles=lambda: {},
        close=lambda: None,
    )
    orch = Orchestrator(
        github_reader=github,
        github_writer=github,
        discord_reader=discord,
        discord_writer=discord,
        storage=storage,
        config=_minimal_config(tmp_path),
    )

    orch.run_once()

    started = [r for r in caplog.records if getattr(r, "event", None) == "github_sync_started"]
    completed = [r for r in caplog.records if getattr(r, "event", None) == "github_sync_completed"]
    event_summary = [r for r in caplog.records if getattr(r, "event", None) == "github_event_summary"]

    assert len(started) == 1
    assert started[0].repos_total == 1
    assert len(completed) == 1
    assert completed[0].events_fetched == 2
    assert completed[0].repos_processed == 1
    assert completed[0].cursor_before is not None
    assert completed[0].cursor_after is not None
    assert len(event_summary) == 1
    assert event_summary[0].issue_opened == 1
    assert event_summary[0].pr_merged == 1
