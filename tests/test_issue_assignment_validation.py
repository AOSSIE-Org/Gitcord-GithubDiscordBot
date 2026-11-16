"""Tests for issue assignment validation in GitHubRestAdapter."""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx

from ghdcbot.adapters.github.rest import GitHubRestAdapter


def _build_adapter() -> tuple[GitHubRestAdapter, MagicMock]:
    adapter = GitHubRestAdapter(token="fake-token", org="test-org", api_base="https://api.github.com")
    mock_client = MagicMock(spec=httpx.Client)
    adapter._client = mock_client
    return adapter, mock_client


def test_assign_issue_success() -> None:
    adapter, mock_client = _build_adapter()
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 201
    mock_response.headers = {}
    mock_response.json.return_value = {
        "number": 42,
        "assignees": [{"login": "collaborator1"}],
    }
    mock_client.post.return_value = mock_response

    result = adapter.assign_issue("owner", "repo", 42, "collaborator1")

    assert result is True
    mock_client.post.assert_called_once_with(
        "/repos/owner/repo/issues/42/assignees",
        json={"assignees": ["collaborator1"]},
    )


def test_assign_issue_case_insensitive() -> None:
    adapter, mock_client = _build_adapter()
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.headers = {}
    mock_response.json.return_value = {
        "number": 42,
        "assignees": [{"login": "collaborator1"}],
    }
    mock_client.post.return_value = mock_response

    result = adapter.assign_issue("owner", "repo", 42, "Collaborator1")

    assert result is True


def test_assign_issue_silently_dropped_by_github() -> None:
    adapter, mock_client = _build_adapter()
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 201
    mock_response.headers = {}
    # Non-collaborators are silently omitted by GitHub API even on 201 Created
    mock_response.json.return_value = {
        "number": 42,
        "assignees": [],
    }
    mock_client.post.return_value = mock_response

    result = adapter.assign_issue("owner", "repo", 42, "non_collaborator")

    assert result is False


def test_assign_issue_omitted_when_other_assignees_exist() -> None:
    adapter, mock_client = _build_adapter()
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 201
    mock_response.headers = {}
    mock_response.json.return_value = {
        "number": 42,
        "assignees": [{"login": "existing_user"}],
    }
    mock_client.post.return_value = mock_response

    result = adapter.assign_issue("owner", "repo", 42, "non_collaborator")

    assert result is False


def test_assign_issue_non_2xx_failure() -> None:
    adapter, mock_client = _build_adapter()
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 404
    mock_response.headers = {}
    mock_response.text = "Not Found"
    mock_client.post.return_value = mock_response

    result = adapter.assign_issue("owner", "repo", 42, "user1")

    assert result is False


def test_assign_issue_malformed_json_response() -> None:
    adapter, mock_client = _build_adapter()
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 201
    mock_response.headers = {}
    mock_response.json.side_effect = ValueError("Invalid JSON")
    mock_client.post.return_value = mock_response

    result = adapter.assign_issue("owner", "repo", 42, "user1")

    assert result is False
