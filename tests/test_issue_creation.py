"""Tests for issue creation feature."""

import pytest
from unittest.mock import MagicMock

from ghdcbot.engine.issue_creation import (
    validate_issue_params,
    build_issue_created_embed,
    format_issue_creation_audit_context,
)
from ghdcbot.adapters.github.rest import GitHubRestAdapter


def test_validate_issue_params_valid() -> None:
    ok, err = validate_issue_params("Valid Title", "my-repo", "org")
    assert ok is True
    assert err == ""

def test_validate_issue_params_empty_title() -> None:
    ok, err = validate_issue_params("", "my-repo", "org")
    assert ok is False
    assert "empty" in err.lower()

def test_validate_issue_params_title_too_long() -> None:
    long_title = "A" * 257
    ok, err = validate_issue_params(long_title, "my-repo", "org")
    assert ok is False
    assert "too long" in err.lower()

def test_validate_issue_params_empty_repo() -> None:
    ok, err = validate_issue_params("Title", "", "org")
    assert ok is False
    assert "empty" in err.lower()

def test_validate_issue_params_invalid_repo() -> None:
    ok, err = validate_issue_params("Title", "my repo", "org")
    assert ok is False
    assert "invalid" in err.lower()

def test_build_issue_created_embed() -> None:
    issue_data = {
        "number": 42,
        "title": "Bug found",
        "html_url": "https://github.com/org/repo/issues/42",
        "state": "open",
    }
    embed = build_issue_created_embed(issue_data, "org", "repo", "testuser", "123")
    
    assert embed["title"] == "✅ Issue Created Successfully"
    assert embed["url"] == "https://github.com/org/repo/issues/42"
    fields = {f["name"]: f["value"] for f in embed["fields"]}
    assert "testuser" in fields["Creator"]
    assert "123" in fields["Creator"]
    assert "repo" in fields["Repository"]
    assert "42" in fields["Issue"]

def test_build_issue_created_embed_with_labels() -> None:
    issue_data = {
        "number": 42,
        "title": "Bug found",
        "html_url": "https://github.com/org/repo/issues/42",
        "state": "open",
        "labels": [{"name": "bug"}, "urgent"]
    }
    embed = build_issue_created_embed(issue_data, "org", "repo", "testuser", "123")
    
    fields = {f["name"]: f["value"] for f in embed["fields"]}
    assert fields["Labels"] == "`bug`, `urgent`"

def test_format_issue_creation_audit_context() -> None:
    ctx = format_issue_creation_audit_context("org", "repo", 42, "Title", "user", "123")
    assert ctx["action"] == "issue_created_from_discord"
    assert ctx["issue_number"] == 42
    assert ctx["creator_github"] == "user"

def test_list_org_repo_names(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = GitHubRestAdapter("token", "org", "http://api")
    
    # Mock _list_repos to return some dicts
    def mock_list_repos():
        yield {"name": "repo1"}
        yield {"name": "repo2"}
        yield {"id": 123} # No name, should be skipped
        
    monkeypatch.setattr(adapter, "_list_repos", mock_list_repos)
    
    names = adapter.list_org_repo_names()
    assert names == ["repo1", "repo2"]

def test_list_org_repo_names_caching(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = GitHubRestAdapter("token", "org", "http://api")
    
    call_count = 0
    def mock_list_repos():
        nonlocal call_count
        call_count += 1
        yield {"name": "repo1"}
        
    monkeypatch.setattr(adapter, "_list_repos", mock_list_repos)
    
    # First call should hit the mocked _list_repos
    names1 = adapter.list_org_repo_names()
    assert names1 == ["repo1"]
    assert call_count == 1
    
    # Second call should use cache
    names2 = adapter.list_org_repo_names()
    assert names2 == ["repo1"]
    assert call_count == 1  # Still 1
    
    # Explicit invalidation should cause another hit
    adapter.invalidate_repo_cache()
    names3 = adapter.list_org_repo_names()
    assert names3 == ["repo1"]
    assert call_count == 2

def test_create_issue_success(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = GitHubRestAdapter("token", "org", "http://api")
    
    mock_response = MagicMock()
    mock_response.status_code = 201
    mock_response.json.return_value = {"number": 1, "title": "Test"}
    mock_response.headers = {}
    
    mock_client = MagicMock()
    mock_client.post.return_value = mock_response
    monkeypatch.setattr(adapter, "_client", mock_client)
    
    issue = adapter.create_issue("org", "repo", "Test", "Body", ["bug"])
    
    assert issue is not None
    assert issue["number"] == 1
    mock_client.post.assert_called_once()
    _, kwargs = mock_client.post.call_args
    assert kwargs["json"] == {"title": "Test", "body": "Body", "labels": ["bug"]}

def test_create_issue_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = GitHubRestAdapter("token", "org", "http://api")
    
    mock_response = MagicMock()
    mock_response.status_code = 403
    mock_response.headers = {}
    
    mock_client = MagicMock()
    mock_client.post.return_value = mock_response
    monkeypatch.setattr(adapter, "_client", mock_client)
    
    issue = adapter.create_issue("org", "repo", "Test", "Body")
    
    assert issue is None
