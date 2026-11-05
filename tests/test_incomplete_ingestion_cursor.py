"""Sync cursor must not skip events from repos the GitHub adapter could not fully read."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest

from ghdcbot.adapters.github import rest as rest_module
from ghdcbot.adapters.github.rest import GitHubRestAdapter
from ghdcbot.adapters.storage.sqlite import SqliteStorage
from ghdcbot.config.models import (
    AssignmentConfig,
    BotConfig,
    DiscordConfig,
    GitHubConfig,
    PermissionConfig,
    RuntimeConfig,
)
from ghdcbot.core.models import ContributionEvent
from ghdcbot.engine.orchestrator import Orchestrator

_REPOS = [
    {"name": name, "full_name": f"org/{name}", "owner": {"login": "org"}}
    for name in ("repo-a", "repo-b")
]

PRIOR = datetime(2026, 11, 1, tzinfo=UTC)


def _issue(number: int, created: str) -> dict:
    return {
        "number": number,
        "created_at": created,
        "updated_at": created,
        "user": {"login": "alice"},
        "title": "t",
        "state": "open",
        "labels": [],
        "html_url": "https://example.com",
    }


def _adapter(handler, monkeypatch) -> GitHubRestAdapter:
    monkeypatch.setattr(rest_module.time, "sleep", lambda _seconds: None)
    adapter = GitHubRestAdapter(token="t", org="org", api_base="https://api.github.com")
    adapter._client = httpx.Client(
        base_url="https://api.github.com", transport=httpx.MockTransport(handler)
    )
    adapter._sync_cached_repos = list(_REPOS)
    return adapter


def _handler(repo_a_issues):
    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/repos/org/repo-a/issues":
            return repo_a_issues(request)
        if path == "/repos/org/repo-b/issues":
            page = request.url.params.get("page", "1")
            body = [_issue(7, "2026-11-04T00:00:00Z")] if page == "1" else []
            return httpx.Response(200, json=body)
        return httpx.Response(200, json=[])

    return handle


@pytest.mark.parametrize("status", [403, 503])
def test_failed_issue_listing_marks_repo_incomplete(monkeypatch, status: int) -> None:
    adapter = _adapter(
        _handler(lambda _request: httpx.Response(status, json={"message": "nope"})),
        monkeypatch,
    )

    events = list(adapter.list_contributions(PRIOR))

    assert [event.repo for event in events] == ["repo-b"]
    assert adapter.sync_incomplete_repos == frozenset({"repo-a"})


def test_failure_on_later_page_marks_repo_incomplete(monkeypatch) -> None:
    def repo_a(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("page", "1") == "1":
            return httpx.Response(
                200,
                json=[_issue(1, "2026-11-03T00:00:00Z")],
                headers={"Link": '<https://api.github.com/x?page=2>; rel="next"'},
            )
        return httpx.Response(403, json={"message": "forbidden"})

    adapter = _adapter(_handler(repo_a), monkeypatch)

    events = list(adapter.list_contributions(PRIOR))

    assert {event.repo for event in events} == {"repo-a", "repo-b"}
    assert adapter.sync_incomplete_repos == frozenset({"repo-a"})


def test_healthy_sync_reports_no_incomplete_repos(monkeypatch) -> None:
    adapter = _adapter(_handler(lambda _request: httpx.Response(200, json=[])), monkeypatch)

    list(adapter.list_contributions(PRIOR))

    assert adapter.sync_incomplete_repos == frozenset()


def test_incomplete_set_resets_between_syncs(monkeypatch) -> None:
    state = {"fail": True}

    def repo_a(_request: httpx.Request) -> httpx.Response:
        if state["fail"]:
            return httpx.Response(403, json={"message": "forbidden"})
        return httpx.Response(200, json=[])

    adapter = _adapter(_handler(repo_a), monkeypatch)
    list(adapter.list_contributions(PRIOR))
    assert adapter.sync_incomplete_repos == frozenset({"repo-a"})

    state["fail"] = False
    adapter._sync_cached_repos = list(_REPOS)
    list(adapter.list_contributions(PRIOR))
    assert adapter.sync_incomplete_repos == frozenset()


def test_repo_since_reads_that_repo_from_an_earlier_point(monkeypatch) -> None:
    seen: dict[str, str] = {}

    def repo_a(request: httpx.Request) -> httpx.Response:
        seen["a"] = request.url.params["since"]
        return httpx.Response(200, json=[])

    adapter = _adapter(_handler(repo_a), monkeypatch)
    earlier = PRIOR - timedelta(days=3)

    list(adapter.list_contributions(PRIOR, repo_since={"repo-a": earlier}))

    assert seen["a"] == earlier.isoformat()


# --- Orchestrator -----------------------------------------------------------------


class _FakeReader:
    def __init__(self, events, incomplete=()) -> None:
        self.events = events
        self.incomplete = frozenset(incomplete)
        self.calls: list[dict] = []

    @property
    def sync_incomplete_repos(self) -> frozenset[str]:
        return self.incomplete

    def peek_repos_for_sync(self) -> int:
        return 2

    def list_contributions(self, since, repo_since=None):
        self.calls.append({"since": since, "repo_since": dict(repo_since or {})})
        return list(self.events)

    def list_open_issues(self) -> list[dict]:
        return []

    def list_open_pull_requests(self) -> list[dict]:
        return []

    def close(self) -> None:
        return None


def _orchestrator(tmp_path, reader) -> tuple[Orchestrator, SqliteStorage]:
    storage = SqliteStorage(data_dir=str(tmp_path))
    storage.init_schema()
    config = BotConfig(
        runtime=RuntimeConfig(
            mode="active",
            data_dir=str(tmp_path),
            storage_adapter="ghdcbot.adapters.storage.sqlite:SqliteStorage",
            github_adapter="ghdcbot.adapters.github.rest:GitHubRestAdapter",
            discord_adapter="ghdcbot.adapters.discord.api:DiscordApiAdapter",
            activity_period_days=30,
            enable_discord_role_updates=False,
        ),
        github=GitHubConfig(org="org", token="t", api_base="https://api.github.com"),
        discord=DiscordConfig(
            token="t", guild_id="1", permissions=PermissionConfig(write=False)
        ),
        assignments=AssignmentConfig(issue_assignees=[], review_roles=[]),
    )
    orchestrator = Orchestrator(
        github_reader=reader,
        github_writer=reader,
        discord_reader=SimpleNamespace(list_member_roles=dict, close=lambda: None),
        discord_writer=MagicMock(),
        storage=storage,
        config=config,
    )
    return orchestrator, storage


def _event(repo: str, created: datetime) -> ContributionEvent:
    return ContributionEvent(
        github_user="alice",
        event_type="issue_opened",
        repo=repo,
        created_at=created,
        payload={"issue_number": 1},
    )


def test_incomplete_repo_keeps_its_restart_point_while_cursor_advances(tmp_path) -> None:
    now = datetime.now(UTC)
    prior = now - timedelta(days=2)
    newest = now - timedelta(hours=1)
    reader = _FakeReader([_event("repo-b", newest)], incomplete={"repo-a"})
    orchestrator, storage = _orchestrator(tmp_path, reader)
    storage.set_cursor("github", prior)

    orchestrator.run_once()

    assert storage.get_cursor("github") == newest
    assert storage.get_cursor("github:retry:repo-a") == prior
    assert storage.get_cursor("github:retry:repo-b") is None


def test_retry_reads_repo_from_restart_point_then_clears_it(tmp_path) -> None:
    now = datetime.now(UTC)
    prior = now - timedelta(days=2)
    newest = now - timedelta(hours=1)
    reader = _FakeReader([_event("repo-b", newest)], incomplete={"repo-a"})
    orchestrator, storage = _orchestrator(tmp_path, reader)
    storage.set_cursor("github", prior)
    orchestrator.run_once()

    reader.incomplete = frozenset()
    orchestrator.run_once()

    assert reader.calls[1]["since"] == newest
    assert reader.calls[1]["repo_since"] == {"repo-a": prior}
    assert storage.get_cursor("github:retry:repo-a") is None

    orchestrator.run_once()
    assert reader.calls[2]["repo_since"] == {}


def test_restart_point_does_not_move_forward_while_still_failing(tmp_path) -> None:
    now = datetime.now(UTC)
    prior = now - timedelta(days=2)
    reader = _FakeReader([_event("repo-b", now - timedelta(days=1))], incomplete={"repo-a"})
    orchestrator, storage = _orchestrator(tmp_path, reader)
    storage.set_cursor("github", prior)

    orchestrator.run_once()
    orchestrator.run_once()

    assert storage.get_cursor("github:retry:repo-a") == prior


def test_restart_point_is_capped_at_activity_window(tmp_path) -> None:
    now = datetime.now(UTC)
    window_start = now - timedelta(days=30)
    reader = _FakeReader([], incomplete={"repo-a"})
    orchestrator, storage = _orchestrator(tmp_path, reader)
    storage.set_cursor("github", now - timedelta(days=90))

    orchestrator.run_once()

    restart = storage.get_cursor("github:retry:repo-a")
    assert restart is not None
    assert restart >= window_start - timedelta(minutes=1)


# --- Storage ----------------------------------------------------------------------


def test_list_and_delete_cursors(tmp_path) -> None:
    storage = SqliteStorage(data_dir=str(tmp_path))
    storage.init_schema()
    t = datetime(2026, 11, 1, tzinfo=UTC)
    storage.set_cursor("github", t)
    storage.set_cursor("github:retry:repo_a", t)
    storage.set_cursor("github:retry:repo-b", t)

    assert storage.list_cursors("github:retry:") == {
        "github:retry:repo_a": t,
        "github:retry:repo-b": t,
    }
    storage.delete_cursor("github:retry:repo_a")
    assert set(storage.list_cursors("github:retry:")) == {"github:retry:repo-b"}
    assert storage.get_cursor("github") == t
