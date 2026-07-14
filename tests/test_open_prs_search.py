"""Tests for author-scoped open PR Search API helper."""

from __future__ import annotations

import httpx

from ghdcbot.adapters.github.rest import GitHubRestAdapter, _repo_name_from_search_issue
from ghdcbot.config.models import RepoFilterConfig


class _SearchMockClient:
    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.calls: list[tuple[str, str, dict | None]] = []

    def request(self, method: str, path: str, params: dict | None = None) -> httpx.Response:
        self.calls.append((method, path, params))
        return httpx.Response(200, json=self._payload, headers={"X-RateLimit-Remaining": "10"})


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
                    "repository_url": "https://api.github.com/repos/AOSSIE-Org/PictoPy",
                    "user": {"login": "alice"},
                },
                {
                    "number": 11,
                    "title": "Denied",
                    "html_url": "https://github.com/AOSSIE-Org/Other/pull/11",
                    "created_at": "2026-07-11T11:00:00Z",
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
    assert "author:alice" in params["q"]
    assert "org:AOSSIE-Org" in params["q"]
    assert len(prs) == 1
    assert prs[0]["repo"] == "PictoPy"
    assert prs[0]["number"] == 10
    assert prs[0]["author"] == "alice"
    assert prs[0]["title"] == "Allowed"
