"""Tests for author-scoped open PR Search API helper."""

from __future__ import annotations

import httpx

from ghdcbot.adapters.github.rest import (
    GitHubRestAdapter,
    _pr_status_from_search_issue,
    _repo_name_from_search_issue,
)
from ghdcbot.config.models import RepoFilterConfig


class _SearchMockClient:
    def __init__(self, payload: dict | None = None, *, by_query: dict[str, dict] | None = None) -> None:
        self._payload = payload or {"items": []}
        self._by_query = by_query or {}
        self.calls: list[tuple[str, str, dict | None]] = []

    def request(self, method: str, path: str, params: dict | None = None, **kwargs: object) -> httpx.Response:
        self.calls.append((method, path, params))
        payload = self._payload
        if params and isinstance(params.get("q"), str) and params["q"] in self._by_query:
            payload = self._by_query[params["q"]]
        return httpx.Response(200, json=payload, headers={"X-RateLimit-Remaining": "10"})


def test_repo_name_from_search_issue_prefers_repository_url() -> None:
    assert (
        _repo_name_from_search_issue(
            {"repository_url": "https://api.github.com/repos/AOSSIE-Org/PictoPy"},
            "AOSSIE-Org",
        )
        == "PictoPy"
    )


def test_list_open_pull_requests_for_author_uses_search_and_allowlist(monkeypatch) -> None:
    adapter = GitHubRestAdapter(token="t", org="AOSSIE-Org", api_base="https://api.github.com")
    client = _SearchMockClient(
        {
            "items": [
                {
                    "number": 10,
                    "title": "Allowed",
                    "html_url": "https://github.com/AOSSIE-Org/PictoPy/pull/10",
                    "created_at": "2026-07-11T10:00:00Z",
                    "updated_at": "2026-07-11T12:00:00Z",
                    "repository_url": "https://api.github.com/repos/AOSSIE-Org/PictoPy",
                    "user": {"login": "alice"},
                },
                {
                    "number": 11,
                    "title": "Denied",
                    "html_url": "https://github.com/AOSSIE-Org/Other/pull/11",
                    "created_at": "2026-07-11T11:00:00Z",
                    "updated_at": "2026-07-11T13:00:00Z",
                    "repository_url": "https://api.github.com/repos/AOSSIE-Org/Other",
                    "user": {"login": "alice"},
                },
            ]
        }
    )
    adapter._client = client  # type: ignore[assignment]
    monkeypatch.setattr(
        "ghdcbot.adapters.github.rest._load_repo_filter",
        lambda: RepoFilterConfig(mode="allow", names=["PictoPy"]),
    )

    prs = adapter.list_open_pull_requests_for_author("alice")

    assert len(client.calls) == 1
    method, path, params = client.calls[0]
    assert method == "GET"
    assert path == "/search/issues"
    assert params is not None
    assert params["q"] == "is:pr is:open author:alice org:AOSSIE-Org"
    assert "repo:" not in params["q"]
    assert len(prs) == 1
    assert prs[0]["repo"] == "PictoPy"
    assert prs[0]["number"] == 10
    assert prs[0]["author"] == "alice"
    assert prs[0]["title"] == "Allowed"
    assert prs[0]["updated_at"] == "2026-07-11T12:00:00Z"
    assert "status" not in prs[0]


def test_large_allowlist_filters_org_search_client_side(monkeypatch) -> None:
    """Large allowlists must not use repo: OR queries; filter org search in Python."""
    huge_allow = [f"Repo{i:02d}-With-A-Long-Name" for i in range(40)] + [
        "Gitcord-GithubDiscordBot"
    ]
    adapter = GitHubRestAdapter(token="t", org="AOSSIE-Org", api_base="https://api.github.com")
    client = _SearchMockClient(
        {
            "items": [
                {
                    "number": 40,
                    "title": "who-is",
                    "state": "open",
                    "html_url": "https://github.com/AOSSIE-Org/Gitcord-GithubDiscordBot/pull/40",
                    "created_at": "2026-08-03T07:00:00Z",
                    "updated_at": "2026-08-05T05:00:00Z",
                    "repository_url": "https://api.github.com/repos/AOSSIE-Org/Gitcord-GithubDiscordBot",
                    "user": {"login": "PrithvijitBose"},
                    "pull_request": {},
                },
                {
                    "number": 99,
                    "title": "outside allowlist",
                    "state": "open",
                    "html_url": "https://github.com/AOSSIE-Org/NotAllowed/pull/99",
                    "created_at": "2026-08-03T07:00:00Z",
                    "updated_at": "2026-08-05T06:00:00Z",
                    "repository_url": "https://api.github.com/repos/AOSSIE-Org/NotAllowed",
                    "user": {"login": "PrithvijitBose"},
                    "pull_request": {},
                },
            ]
        }
    )
    adapter._client = client  # type: ignore[assignment]
    monkeypatch.setattr(
        "ghdcbot.adapters.github.rest._load_repo_filter",
        lambda: RepoFilterConfig(mode="allow", names=huge_allow),
    )

    prs = adapter.list_pull_requests_for_author("PrithvijitBose")
    assert len(client.calls) == 1
    _method, _path, params = client.calls[0]
    assert params is not None
    assert params["q"] == "is:pr author:PrithvijitBose org:AOSSIE-Org"
    assert "repo:" not in params["q"]
    assert len(prs) == 1
    assert prs[0]["repo"] == "Gitcord-GithubDiscordBot"
    assert prs[0]["number"] == 40


def test_pr_status_from_search_issue() -> None:
    assert _pr_status_from_search_issue({"state": "open", "pull_request": {}}) == "open"
    assert (
        _pr_status_from_search_issue(
            {"state": "closed", "pull_request": {"merged_at": "2026-07-01T00:00:00Z"}}
        )
        == "merged"
    )
    assert (
        _pr_status_from_search_issue({"state": "closed", "pull_request": {"merged_at": None}})
        == "closed"
    )


def test_list_pull_requests_for_author_classifies_status(monkeypatch) -> None:
    adapter = GitHubRestAdapter(token="t", org="AOSSIE-Org", api_base="https://api.github.com")
    client = _SearchMockClient(
        {
            "items": [
                {
                    "number": 1,
                    "title": "Open one",
                    "state": "open",
                    "html_url": "https://github.com/AOSSIE-Org/PictoPy/pull/1",
                    "created_at": "2026-07-11T10:00:00Z",
                    "updated_at": "2026-07-12T10:00:00Z",
                    "repository_url": "https://api.github.com/repos/AOSSIE-Org/PictoPy",
                    "user": {"login": "alice"},
                    "pull_request": {},
                },
                {
                    "number": 2,
                    "title": "Merged one",
                    "state": "closed",
                    "html_url": "https://github.com/AOSSIE-Org/PictoPy/pull/2",
                    "created_at": "2026-07-10T10:00:00Z",
                    "updated_at": "2026-07-11T10:00:00Z",
                    "repository_url": "https://api.github.com/repos/AOSSIE-Org/PictoPy",
                    "user": {"login": "alice"},
                    "pull_request": {"merged_at": "2026-07-11T09:00:00Z"},
                },
                {
                    "number": 3,
                    "title": "Closed one",
                    "state": "closed",
                    "html_url": "https://github.com/AOSSIE-Org/PictoPy/pull/3",
                    "created_at": "2026-07-09T10:00:00Z",
                    "updated_at": "2026-07-10T10:00:00Z",
                    "repository_url": "https://api.github.com/repos/AOSSIE-Org/PictoPy",
                    "user": {"login": "alice"},
                    "pull_request": {"merged_at": None},
                },
            ]
        }
    )
    adapter._client = client  # type: ignore[assignment]
    monkeypatch.setattr("ghdcbot.adapters.github.rest._load_repo_filter", lambda: None)

    prs = adapter.list_pull_requests_for_author("alice")

    assert len(client.calls) == 1
    _method, path, params = client.calls[0]
    assert path == "/search/issues"
    assert params is not None
    assert params["q"] == "is:pr author:alice org:AOSSIE-Org"
    assert params["sort"] == "updated"
    assert params["order"] == "desc"
    by_number = {pr["number"]: pr for pr in prs}
    assert by_number[1]["status"] == "open"
    assert by_number[2]["status"] == "merged"
    assert by_number[3]["status"] == "closed"
    assert by_number[1]["updated_at"] == "2026-07-12T10:00:00Z"
    assert by_number[2]["updated_at"] == "2026-07-11T10:00:00Z"
    assert by_number[3]["updated_at"] == "2026-07-10T10:00:00Z"
