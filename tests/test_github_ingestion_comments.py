from __future__ import annotations

import logging
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


def test_issue_comment_ingestion_emits_comment_events(monkeypatch, caplog) -> None:
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
            {"number": 1, "created_at": "2024-01-10T00:00:00Z", "user": {"login": "alice"}},
            {"number": 2, "pull_request": {"url": "x"}},
        ],
        "/repos/owner/repo/issues/1/comments": [
            {
                "id": 100,
                "created_at": "2024-01-11T00:00:00Z",
                "html_url": "https://example.com/c/100",
                "user": {"login": "alice", "type": "User"},
            },
            {
                "id": 101,
                "created_at": "2024-01-11T00:00:00Z",
                "html_url": "https://example.com/c/101",
                "user": {"login": "bot[bot]", "type": "Bot"},
            },
            {
                "id": 102,
                "created_at": "2023-12-01T00:00:00Z",
                "html_url": "https://example.com/c/102",
                "user": {"login": "alice", "type": "User"},
            },
        ],
        "/repos/owner/repo/issues/2/comments": [],
        "/repos/owner/repo/pulls": [],
    }
    adapter._client = _MockClient(routes)

    since = datetime(2024, 1, 5, tzinfo=timezone.utc)
    caplog.set_level(logging.INFO)
    events = list(adapter.list_contributions(since))
    comment_events = [event for event in events if event.event_type == "comment"]

    assert len(comment_events) == 1
    assert comment_events[0].github_user == "alice"
    assert comment_events[0].payload["issue_number"] == 1
    assert comment_events[0].payload["comment_id"] == 100

    assert any("Ingesting issue comments" in record.message for record in caplog.records)
    assert any("Emitted comment events" in record.message for record in caplog.records)


def test_pr_opened_and_comment_dedup(monkeypatch) -> None:
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
                "number": 10,
                "created_at": "2024-01-02T00:00:00Z",
                "updated_at": "2024-01-03T00:00:00Z",
                "merged_at": None,
                "title": "pr 10",
                "user": {"login": "alice"},
            },
            {
                "number": 11,
                "created_at": "2024-01-02T00:00:00Z",
                "updated_at": "2024-01-04T00:00:00Z",
                "merged_at": "2024-01-05T00:00:00Z",
                "title": "pr 11",
                "user": {"login": "bob"},
            },
            {
                "number": 12,
                "created_at": "2023-12-01T00:00:00Z",
                "updated_at": "2023-12-01T00:00:00Z",
                "merged_at": None,
                "title": "pr 12",
                "user": {"login": "carol"},
            },
        ],
        "/repos/owner/repo/pulls/11/reviews": [
            {
                "id": 500,
                "submitted_at": "2024-01-06T00:00:00Z",
                "state": "APPROVED",
                "user": {"login": "dana"},
            }
        ],
        "/repos/owner/repo/pulls/10/comments": [
            {
                "id": 600,
                "created_at": "2024-01-03T00:00:00Z",
                "html_url": "https://example.com/c/600",
                "user": {"login": "alice", "type": "User"},
            }
        ],
        "/repos/owner/repo/issues/10/comments": [
            {
                "id": 600,
                "created_at": "2024-01-03T00:00:00Z",
                "html_url": "https://example.com/c/600",
                "user": {"login": "alice", "type": "User"},
            }
        ],
        "/repos/owner/repo/pulls/11/comments": [],
        "/repos/owner/repo/issues/11/comments": [
            {
                "id": 601,
                "created_at": "2023-12-20T00:00:00Z",
                "html_url": "https://example.com/c/601",
                "user": {"login": "bob", "type": "User"},
            }
        ],
    }
    adapter._client = _MockClient(routes)

    since = datetime(2024, 1, 1, tzinfo=timezone.utc)
    events = list(adapter.list_contributions(since))

    pr_opened = [event for event in events if event.event_type == "pr_opened"]
    pr_merged = [event for event in events if event.event_type == "pr_merged"]
    pr_reviewed = [event for event in events if event.event_type == "pr_reviewed"]
    comments = [event for event in events if event.event_type == "comment"]

    assert len(pr_opened) == 2
    assert len(pr_merged) == 1
    assert len(pr_reviewed) == 1
    assert len(comments) == 1
