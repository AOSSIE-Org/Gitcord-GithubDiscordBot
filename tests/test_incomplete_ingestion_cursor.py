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


def _pull(number: int, updated: str) -> dict:
    return {
        "number": number,
        "created_at": updated,
        "updated_at": updated,
        "merged_at": None,
        "user": {"login": "alice"},
        "title": "t",
        "body": "",
        "state": "open",
        "html_url": "https://example.com",
    }


@pytest.mark.parametrize(
    "failing_path",
    [
        "/repos/org/repo-a/pulls",
        "/repos/org/repo-a/pulls/5/reviews",
        "/repos/org/repo-a/pulls/5/comments",
        "/repos/org/repo-a/issues/5/comments",
        "/repos/org/repo-a/issues/5/timeline",
        "/repos/org/repo-a/issues/1/comments",
        "/repos/org/repo-a/issues/1/timeline",
    ],
)
def test_first_page_failure_on_each_listing_marks_repo_incomplete(
    monkeypatch, failing_path: str
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == failing_path:
            return httpx.Response(403, json={"message": "forbidden"})
        if path == "/repos/org/repo-a/issues":
            return httpx.Response(200, json=[_issue(1, "2026-11-03T00:00:00Z")])
        if path == "/repos/org/repo-a/pulls":
            return httpx.Response(200, json=[_pull(5, "2026-11-03T00:00:00Z")])
        return httpx.Response(200, json=[])

    adapter = _adapter(handle, monkeypatch)
    adapter._sync_cached_repos = [_REPOS[0]]

    list(adapter.list_contributions(PRIOR))

    assert adapter.sync_incomplete_repos == frozenset({"repo-a"})


def test_repo_listing_failure_on_later_page_is_reported(monkeypatch) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/orgs/org/repos":
            if request.url.params.get("page", "1") == "1":
                return httpx.Response(
                    200,
                    json=[_REPOS[0]],
                    headers={"Link": '<https://api.github.com/x?page=2>; rel="next"'},
                )
            return httpx.Response(403, json={"message": "forbidden"})
        return httpx.Response(200, json=[])

    adapter = _adapter(handle, monkeypatch)
    adapter._sync_cached_repos = None

    list(adapter.list_contributions(PRIOR))

    assert adapter.sync_repo_listing_incomplete is True


def test_repo_listing_failure_survives_the_sync_reset(monkeypatch) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "forbidden"})

    adapter = _adapter(handle, monkeypatch)
    adapter._sync_cached_repos = None

    adapter.peek_repos_for_sync()
    list(adapter.list_contributions(PRIOR))

    assert adapter.sync_repo_listing_incomplete is True


def test_complete_repo_listing_is_not_reported(monkeypatch) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/orgs/org/repos":
            return httpx.Response(200, json=[_REPOS[0]])
        return httpx.Response(200, json=[])

    adapter = _adapter(handle, monkeypatch)
    adapter._sync_cached_repos = None

    list(adapter.list_contributions(PRIOR))

    assert adapter.sync_repo_listing_incomplete is False


# --- Orchestrator -----------------------------------------------------------------


class _FakeReader:
    def __init__(self, events, incomplete=(), recovered=None, listing_incomplete=False) -> None:
        self.events = events
        self.incomplete = frozenset(incomplete)
        self.recovered = recovered  # returned only when a retry is requested for repo-a
        self.sync_repo_listing_incomplete = listing_incomplete
        self.calls: list[dict] = []

    @property
    def sync_incomplete_repos(self) -> frozenset[str]:
        return self.incomplete

    def peek_repos_for_sync(self) -> int:
        return 2

    def list_contributions(self, since, repo_since=None):
        self.calls.append({"since": since, "repo_since": dict(repo_since or {})})
        events = list(self.events)
        if self.recovered is not None and "repo-a" in (repo_since or {}):
            events.append(self.recovered)
        return events

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
    recovered = _event("repo-a", prior + timedelta(hours=1))
    reader = _FakeReader(
        [_event("repo-b", newest)], incomplete={"repo-a"}, recovered=recovered
    )
    orchestrator, storage = _orchestrator(tmp_path, reader)
    storage.set_cursor("github", prior)
    orchestrator.run_once()
    assert recovered not in storage.list_contributions(prior)

    reader.incomplete = frozenset()
    orchestrator.run_once()

    assert recovered in storage.list_contributions(prior)
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


class _NoRetryCursorStorage:
    """Storage that lacks the optional restart-point methods."""

    def __init__(self, inner: SqliteStorage) -> None:
        self._inner = inner

    def __getattr__(self, name: str):
        if name in {"list_cursors", "delete_cursor"}:
            raise AttributeError(name)
        return getattr(self._inner, name)


def _rebuilt(orchestrator: Orchestrator, reader, storage) -> Orchestrator:
    return Orchestrator(
        github_reader=reader,
        github_writer=reader,
        discord_reader=orchestrator.discord_reader,
        discord_writer=orchestrator.discord_writer,
        storage=storage,
        config=orchestrator.config,
    )


def test_cursor_is_held_when_storage_cannot_keep_restart_points(tmp_path) -> None:
    now = datetime.now(UTC)
    prior = now - timedelta(days=2)
    reader = _FakeReader([_event("repo-b", now - timedelta(hours=1))], incomplete={"repo-a"})
    orchestrator, storage = _orchestrator(tmp_path, reader)
    storage.set_cursor("github", prior)

    _rebuilt(orchestrator, reader, _NoRetryCursorStorage(storage)).run_once()

    assert storage.get_cursor("github") == prior


def test_cursor_is_held_and_restart_points_kept_when_repo_listing_is_incomplete(
    tmp_path,
) -> None:
    now = datetime.now(UTC)
    prior = now - timedelta(days=2)
    reader = _FakeReader(
        [_event("repo-b", now - timedelta(hours=1))],
        incomplete={"repo-a"},
        listing_incomplete=True,
    )
    orchestrator, storage = _orchestrator(tmp_path, reader)
    storage.set_cursor("github", prior)
    storage.set_cursor("github:retry:repo-c", prior)

    orchestrator.run_once()

    assert storage.get_cursor("github") == prior
    assert storage.get_cursor("github:retry:repo-c") == prior
    assert storage.get_cursor("github:retry:repo-a") == prior


def test_held_cursor_never_falls_behind_activity_window(tmp_path) -> None:
    now = datetime.now(UTC)
    reader = _FakeReader([], listing_incomplete=True)
    orchestrator, storage = _orchestrator(tmp_path, reader)
    storage.set_cursor("github", now - timedelta(days=90))

    orchestrator.run_once()

    held = storage.get_cursor("github")
    assert held is not None
    assert held >= now - timedelta(days=30, minutes=1)


def test_restart_points_are_saved_before_the_global_cursor(tmp_path) -> None:
    writes: list[str] = []

    class _Recording(SqliteStorage):
        def set_cursor(self, source, cursor):
            writes.append(source)
            super().set_cursor(source, cursor)

    now = datetime.now(UTC)
    reader = _FakeReader([_event("repo-b", now - timedelta(hours=1))], incomplete={"repo-a"})
    orchestrator, _ = _orchestrator(tmp_path, reader)
    recording = _Recording(data_dir=str(tmp_path))
    recording.set_cursor("github", now - timedelta(days=2))
    writes.clear()

    _rebuilt(orchestrator, reader, recording).run_once()

    assert writes.index("github:retry:repo-a") < writes.index("github")


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


def test_reread_merge_with_changed_payload_is_stored_once(tmp_path) -> None:
    storage = SqliteStorage(data_dir=str(tmp_path))
    storage.init_schema()
    merged_at = datetime(2026, 11, 2, tzinfo=UTC)

    def merged(title: str) -> ContributionEvent:
        return ContributionEvent(
            github_user="alice",
            event_type="pr_merged",
            repo="repo-a",
            created_at=merged_at,
            payload={"pr_number": 5, "title": title, "difficulty_labels": []},
        )

    assert storage.record_contributions([merged("first title")]) == 1
    assert storage.record_contributions([merged("edited title")]) == 0
    assert len(storage.list_contributions(merged_at - timedelta(days=1))) == 1


def test_merges_of_different_prs_or_repos_are_all_stored(tmp_path) -> None:
    storage = SqliteStorage(data_dir=str(tmp_path))
    storage.init_schema()
    merged_at = datetime(2026, 11, 2, tzinfo=UTC)

    def merged(repo: str, number: int) -> ContributionEvent:
        return ContributionEvent(
            github_user="alice",
            event_type="pr_merged",
            repo=repo,
            created_at=merged_at,
            payload={"pr_number": number},
        )

    stored = storage.record_contributions(
        [merged("repo-a", 5), merged("repo-a", 6), merged("repo-b", 5)]
    )
    assert stored == 3
