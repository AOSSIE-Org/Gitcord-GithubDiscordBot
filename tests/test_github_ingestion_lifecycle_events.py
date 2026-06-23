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
