"""Tests for GitHub PR lifecycle ingestion (Week 5)."""

from __future__ import annotations

from datetime import datetime, timezone

import httpx

from ghdcbot.adapters.github.rest import GitHubRestAdapter


class _MockClient:
    def __init__(self, routes: dict[str, list]) -> None:
        self._routes = routes

    def request(self, method: str, path: str, params: dict | None = None) -> httpx.Response:
        data = self._routes.get(path, [])
        headers = {"X-RateLimit-Remaining": "10"}
        return httpx.Response(200, json=data, headers=headers)


def _adapter_with_repo(monkeypatch, pulls: list[dict]) -> GitHubRestAdapter:
    adapter = GitHubRestAdapter(token="t", org="org", api_base="https://api.github.com")
    monkeypatch.setattr(
        adapter,
        "_list_repos",
        lambda: [
            {
                "name": "repo",
                "owner": {"login": "owner"},
                "full_name": "owner/repo",
            }
        ],
    )
    routes = {
        "/repos/owner/repo/issues": [],
        "/repos/owner/repo/pulls": pulls,
    }
    for pr in pulls:
        number = pr["number"]
        routes[f"/repos/owner/repo/pulls/{number}/reviews"] = []
        routes[f"/repos/owner/repo/pulls/{number}/comments"] = []
        routes[f"/repos/owner/repo/issues/{number}/comments"] = []
        closed_at = pr.get("closed_at")
        merged_at = pr.get("merged_at")
        timeline: list[dict] = []
        if closed_at and not merged_at:
            timeline.append({"event": "closed", "created_at": closed_at})
        if merged_at:
            timeline.append({"event": "merged", "created_at": merged_at})
        routes[f"/repos/owner/repo/issues/{number}/timeline"] = timeline
    adapter._client = _MockClient(routes)
    return adapter


def test_pr_closed_emitted_when_closed_without_merge(monkeypatch) -> None:
    adapter = _adapter_with_repo(
        monkeypatch,
        [
            {
                "number": 20,
                "state": "closed",
                "created_at": "2024-01-02T00:00:00Z",
                "updated_at": "2024-01-08T00:00:00Z",
                "closed_at": "2024-01-08T00:00:00Z",
                "merged_at": None,
                "title": "Improve onboarding validation",
                "html_url": "https://github.com/owner/repo/pull/20",
                "user": {"login": "alice"},
            }
        ],
    )

    since = datetime(2024, 1, 1, tzinfo=timezone.utc)
    events = list(adapter.list_contributions(since))
    closed = [event for event in events if event.event_type == "pr_closed"]

    assert len(closed) == 1
    assert closed[0].github_user == "alice"
    assert closed[0].payload["pr_number"] == 20
    assert closed[0].payload["pr_author"] == "alice"
    assert closed[0].payload["pr_title"] == "Improve onboarding validation"
    assert closed[0].payload["repository"] == "repo"
    assert closed[0].payload["html_url"] == "https://github.com/owner/repo/pull/20"
    assert closed[0].payload["closed_at"] == "2024-01-08T00:00:00Z"


def test_pr_merged_emitted_not_pr_closed_when_merged(monkeypatch) -> None:
    adapter = _adapter_with_repo(
        monkeypatch,
        [
            {
                "number": 21,
                "state": "closed",
                "created_at": "2024-01-02T00:00:00Z",
                "updated_at": "2024-01-09T00:00:00Z",
                "closed_at": "2024-01-09T00:00:00Z",
                "merged_at": "2024-01-09T00:00:00Z",
                "title": "Ship feature",
                "html_url": "https://github.com/owner/repo/pull/21",
                "user": {"login": "bob"},
            }
        ],
    )

    since = datetime(2024, 1, 1, tzinfo=timezone.utc)
    events = list(adapter.list_contributions(since))
    merged = [event for event in events if event.event_type == "pr_merged"]
    closed = [event for event in events if event.event_type == "pr_closed"]

    assert len(merged) == 1
    assert merged[0].payload["pr_number"] == 21
    assert len(closed) == 0


def test_pr_closed_emitted_when_closed_then_reopened_before_sync(monkeypatch) -> None:
    adapter = GitHubRestAdapter(token="t", org="org", api_base="https://api.github.com")
    monkeypatch.setattr(
        adapter,
        "_list_repos",
        lambda: [
            {
                "name": "repo",
                "owner": {"login": "owner"},
                "full_name": "owner/repo",
            }
        ],
    )
    routes = {
        "/repos/owner/repo/issues": [],
        "/repos/owner/repo/pulls": [
            {
                "number": 22,
                "state": "open",
                "created_at": "2024-01-02T00:00:00Z",
                "updated_at": "2024-01-10T00:00:00Z",
                "closed_at": None,
                "merged_at": None,
                "title": "Reopened PR",
                "html_url": "https://github.com/owner/repo/pull/22",
                "user": {"login": "carol"},
            }
        ],
        "/repos/owner/repo/pulls/22/reviews": [],
        "/repos/owner/repo/pulls/22/comments": [],
        "/repos/owner/repo/issues/22/comments": [],
        "/repos/owner/repo/issues/22/timeline": [
            {"event": "closed", "created_at": "2024-01-08T00:00:00Z"},
            {"event": "reopened", "created_at": "2024-01-10T00:00:00Z"},
        ],
    }
    adapter._client = _MockClient(routes)

    since = datetime(2024, 1, 1, tzinfo=timezone.utc)
    events = list(adapter.list_contributions(since))
    closed = [event for event in events if event.event_type == "pr_closed"]

    assert len(closed) == 1
    assert closed[0].github_user == "carol"
    assert closed[0].payload["pr_number"] == 22
    assert closed[0].payload["closed_at"] == "2024-01-08T00:00:00Z"


def test_issue_reopened_emitted_when_reopened(monkeypatch) -> None:
    """Test that issue_reopened event is emitted when an issue is reopened."""
    adapter = GitHubRestAdapter(token="t", org="org", api_base="https://api.github.com")
    monkeypatch.setattr(
        adapter,
        "_list_repos",
        lambda: [
            {
                "name": "repo",
                "owner": {"login": "owner"},
                "full_name": "owner/repo",
            }
        ],
    )
    # Mock timeline endpoint for issue reopened event
    routes = {
        "/repos/owner/repo/issues": [
            {
                "number": 30,
                "state": "open",
                "created_at": "2024-01-02T00:00:00Z",
                "updated_at": "2024-01-10T00:00:00Z",
                "closed_at": None,
                "title": "Improve onboarding",
                "html_url": "https://github.com/owner/repo/issues/30",
                "user": {"login": "alice"},
                "assignees": [{"login": "eve"}],
            }
        ],
        "/repos/owner/repo/pulls": [],
        "/repos/owner/repo/issues/30/comments": [],
        "/repos/owner/repo/issues/30/timeline": [
            {
                "event": "assigned",
                "created_at": "2024-01-09T09:00:00Z",
                "assignee": {"login": "charlie"},
                "actor": {"login": "mentor"},
            },
            {
                "event": "reopened",
                "created_at": "2024-01-10T10:30:00Z",
                "actor": {"login": "someone"},
            }
        ],
    }
    adapter._client = _MockClient(routes)

    since = datetime(2024, 1, 1, tzinfo=timezone.utc)
    events = list(adapter.list_contributions(since))
    reopened = [event for event in events if event.event_type == "issue_reopened"]

    assert len(reopened) == 1
    assert reopened[0].github_user == "charlie"  # Assigned to charlie
    assert reopened[0].payload["issue_number"] == 30
    assert reopened[0].payload["title"] == "Improve onboarding"
    assert reopened[0].payload["assignee"] == "charlie"
    assert reopened[0].payload["reopened_at"] == "2024-01-10T10:30:00Z"


def test_issue_reopened_emitted_for_each_active_assignee(monkeypatch) -> None:
    """Test that issue_reopened emits one event per active assignee at reopen time."""
    adapter = GitHubRestAdapter(token="t", org="org", api_base="https://api.github.com")
    monkeypatch.setattr(
        adapter,
        "_list_repos",
        lambda: [
            {
                "name": "repo",
                "owner": {"login": "owner"},
                "full_name": "owner/repo",
            }
        ],
    )
    routes = {
        "/repos/owner/repo/issues": [
            {
                "number": 32,
                "state": "open",
                "created_at": "2024-01-02T00:00:00Z",
                "updated_at": "2024-01-10T00:00:00Z",
                "closed_at": None,
                "title": "Multi-assignee issue",
                "html_url": "https://github.com/owner/repo/issues/32",
                "user": {"login": "alice"},
                "assignees": [{"login": "charlie"}, {"login": "dana"}],
            }
        ],
        "/repos/owner/repo/pulls": [],
        "/repos/owner/repo/issues/32/comments": [],
        "/repos/owner/repo/issues/32/timeline": [
            {
                "event": "assigned",
                "created_at": "2024-01-09T09:00:00Z",
                "assignee": {"login": "charlie"},
                "actor": {"login": "mentor"},
            },
            {
                "event": "assigned",
                "created_at": "2024-01-09T10:00:00Z",
                "assignee": {"login": "dana"},
                "actor": {"login": "mentor"},
            },
            {
                "event": "reopened",
                "created_at": "2024-01-10T10:30:00Z",
                "actor": {"login": "someone"},
            },
        ],
    }
    adapter._client = _MockClient(routes)

    since = datetime(2024, 1, 1, tzinfo=timezone.utc)
    events = list(adapter.list_contributions(since))
    reopened = [event for event in events if event.event_type == "issue_reopened"]

    assert len(reopened) == 2
    assert {event.github_user for event in reopened} == {"charlie", "dana"}
    assert all(event.payload["issue_number"] == 32 for event in reopened)


def test_pr_reopened_emitted_when_reopened(monkeypatch) -> None:
    """Test that pr_reopened event is emitted when a PR is reopened."""
    adapter = GitHubRestAdapter(token="t", org="org", api_base="https://api.github.com")
    monkeypatch.setattr(
        adapter,
        "_list_repos",
        lambda: [
            {
                "name": "repo",
                "owner": {"login": "owner"},
                "full_name": "owner/repo",
            }
        ],
    )
    # Mock timeline endpoint for PR reopened event
    routes = {
        "/repos/owner/repo/issues": [],
        "/repos/owner/repo/pulls": [
            {
                "number": 40,
                "state": "open",
                "created_at": "2024-01-02T00:00:00Z",
                "updated_at": "2024-01-11T00:00:00Z",
                "closed_at": None,
                "merged_at": None,
                "title": "Improve onboarding validation",
                "html_url": "https://github.com/owner/repo/pull/40",
                "user": {"login": "bob"},
            }
        ],
        "/repos/owner/repo/pulls/40/reviews": [],
        "/repos/owner/repo/pulls/40/comments": [],
        "/repos/owner/repo/issues/40/timeline": [
            {
                "event": "reopened",
                "created_at": "2024-01-11T11:00:00Z",
                "actor": {"login": "someone"},
            }
        ],
        "/repos/owner/repo/issues/40/comments": [],
    }
    adapter._client = _MockClient(routes)

    since = datetime(2024, 1, 1, tzinfo=timezone.utc)
    events = list(adapter.list_contributions(since))
    reopened = [event for event in events if event.event_type == "pr_reopened"]

    assert len(reopened) == 1
    assert reopened[0].github_user == "bob"  # PR author
    assert reopened[0].payload["pr_number"] == 40
    assert reopened[0].payload["title"] == "Improve onboarding validation"
    assert reopened[0].payload["pr_author"] == "bob"
    assert reopened[0].payload["reopened_at"] == "2024-01-11T11:00:00Z"


def test_issue_reopened_skipped_without_assignee(monkeypatch) -> None:
    """Test that issue_reopened event is skipped if issue has no assignee."""
    adapter = GitHubRestAdapter(token="t", org="org", api_base="https://api.github.com")
    monkeypatch.setattr(
        adapter,
        "_list_repos",
        lambda: [
            {
                "name": "repo",
                "owner": {"login": "owner"},
                "full_name": "owner/repo",
            }
        ],
    )
    routes = {
        "/repos/owner/repo/issues": [
            {
                "number": 31,
                "state": "open",
                "created_at": "2024-01-02T00:00:00Z",
                "updated_at": "2024-01-10T00:00:00Z",
                "closed_at": None,
                "title": "Unassigned issue",
                "html_url": "https://github.com/owner/repo/issues/31",
                "user": {"login": "alice"},
                "assignees": [],  # No assignee
            }
        ],
        "/repos/owner/repo/pulls": [],
        "/repos/owner/repo/issues/31/comments": [],
        "/repos/owner/repo/issues/31/timeline": [
            {
                "event": "reopened",
                "created_at": "2024-01-10T10:30:00Z",
                "actor": {"login": "someone"},
            }
        ],
    }
    adapter._client = _MockClient(routes)

    since = datetime(2024, 1, 1, tzinfo=timezone.utc)
    events = list(adapter.list_contributions(since))
    reopened = [event for event in events if event.event_type == "issue_reopened"]

    assert len(reopened) == 0  # Should not emit if no assignee
