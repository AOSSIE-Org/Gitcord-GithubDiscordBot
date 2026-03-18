"""Tests for issue assignment from Discord feature."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from ghdcbot.core.modes import MutationPolicy, RunMode
from ghdcbot.engine.issue_assignment import (
    build_assignment_confirmation_embed,
    fetch_issue_context,
    parse_issue_url,
    resolve_discord_to_github,
    resolve_github_to_discord,
)


def test_parse_issue_url_valid() -> None:
    """Test parsing valid GitHub issue URLs."""
    assert parse_issue_url("https://github.com/owner/repo/issues/123") == ("owner", "repo", 123)
    assert parse_issue_url("https://github.com/owner/repo/issues/123/") == ("owner", "repo", 123)
    assert parse_issue_url("github.com/owner/repo/issues/456") == ("owner", "repo", 456)
    assert parse_issue_url("http://github.com/owner/repo/issues/789") == ("owner", "repo", 789)


def test_parse_issue_url_invalid() -> None:
    """Test parsing invalid URLs returns None."""
    assert parse_issue_url("not a url") is None
    assert parse_issue_url("https://github.com/owner/repo") is None
    assert parse_issue_url("https://github.com/owner/repo/pull/123") is None
    assert parse_issue_url("https://gitlab.com/owner/repo/issues/123") is None
    assert parse_issue_url("") is None


def test_fetch_issue_context_success() -> None:
    """Test fetching issue context from GitHub adapter."""
    mock_adapter = MagicMock()
    
    issue_data = {
        "title": "Test Issue",
        "state": "open",
        "number": 123,
        "assignees": [],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "html_url": "https://github.com/owner/repo/issues/123",
    }
    
    mock_adapter.get_issue.return_value = issue_data
    
    issue = fetch_issue_context(mock_adapter, "owner", "repo", 123)
    
    assert issue == issue_data
    mock_adapter.get_issue.assert_called_once_with("owner", "repo", 123)


def test_fetch_issue_context_not_found() -> None:
    """Test fetching issue context when issue doesn't exist."""
    mock_adapter = MagicMock()
    mock_adapter.get_issue.return_value = None
    
    issue = fetch_issue_context(mock_adapter, "owner", "repo", 999)
    
    assert issue is None


def test_resolve_discord_to_github() -> None:
    """Test resolving Discord user ID to GitHub username."""
    mock_storage = MagicMock()
    
    class MockMapping:
        def __init__(self, discord_id: str, github_user: str) -> None:
            self.discord_user_id = discord_id
            self.github_user = github_user
    
    mock_storage.list_verified_identity_mappings.return_value = [
        MockMapping("123456789", "testuser"),
        MockMapping("987654321", "otheruser"),
    ]
    
    github_user = resolve_discord_to_github(mock_storage, "123456789")
    assert github_user == "testuser"
    
    github_user = resolve_discord_to_github(mock_storage, "999999999")
    assert github_user is None


def test_resolve_github_to_discord() -> None:
    """Test resolving GitHub username to Discord user ID."""
    mock_storage = MagicMock()
    
    class MockMapping:
        def __init__(self, discord_id: str, github_user: str) -> None:
            self.discord_user_id = discord_id
            self.github_user = github_user
    
    mock_storage.list_verified_identity_mappings.return_value = [
        MockMapping("123456789", "testuser"),
        MockMapping("987654321", "otheruser"),
    ]
    
    discord_id = resolve_github_to_discord(mock_storage, "testuser")
    assert discord_id == "123456789"
    
    discord_id = resolve_github_to_discord(mock_storage, "unknownuser")
    assert discord_id is None


def test_build_assignment_confirmation_embed_unassigned() -> None:
    """Test building confirmation embed for unassigned issue."""
    now = datetime.now(timezone.utc)
    created_at = now.replace(year=2025, month=1, day=1)
    
    issue = {
        "title": "Test Issue",
        "state": "open",
        "number": 123,
        "assignees": [],
        "created_at": created_at.isoformat(),
        "updated_at": created_at.isoformat(),
        "html_url": "https://github.com/owner/repo/issues/123",
    }
    
    embed = build_assignment_confirmation_embed(
        issue=issue,
        owner="owner",
        repo="repo",
        current_assignee_github=None,
        current_assignee_discord=None,
        new_assignee_github="newuser",
        new_assignee_discord="123456789",
        assignee_activity="Unknown",
        now=now,
    )
    
    assert embed["title"] == "📋 Issue Assignment Confirmation"
    assert embed["url"] == "https://github.com/owner/repo/issues/123"
    
    # Check fields
    fields = {f["name"]: f["value"] for f in embed["fields"]}
    assert fields["Repository"] == "owner/repo"
    assert "Test Issue" in fields["Issue"]
    assert fields["Current Assignment"] == "None"
    assert "newuser" in fields["Proposed Assignment"]


def test_build_assignment_confirmation_embed_assigned() -> None:
    """Test building confirmation embed for already assigned issue."""
    now = datetime.now(timezone.utc)
    created_at = now.replace(year=2025, month=1, day=1)
    
    issue = {
        "title": "Test Issue",
        "state": "open",
        "number": 123,
        "assignees": [{"login": "olduser"}],
        "created_at": created_at.isoformat(),
        "updated_at": created_at.isoformat(),
        "html_url": "https://github.com/owner/repo/issues/123",
    }
    
    embed = build_assignment_confirmation_embed(
        issue=issue,
        owner="owner",
        repo="repo",
        current_assignee_github="olduser",
        current_assignee_discord="987654321",
        new_assignee_github="newuser",
        new_assignee_discord="123456789",
        assignee_activity="2 days ago",
        now=now,
    )
    
    fields = {f["name"]: f["value"] for f in embed["fields"]}
    assert "olduser" in fields["Current Assignment"]
    assert "2 days ago" in fields["Current Assignment"]
    assert "newuser" in fields["Proposed Assignment"]


def test_build_assignment_confirmation_embed_closed_issue() -> None:
    """Test building confirmation embed handles closed issue gracefully."""
    now = datetime.now(timezone.utc)
    
    issue = {
        "title": "Closed Issue",
        "state": "closed",
        "number": 123,
        "assignees": [],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "html_url": "https://github.com/owner/repo/issues/123",
    }
    
    embed = build_assignment_confirmation_embed(
        issue=issue,
        owner="owner",
        repo="repo",
        current_assignee_github=None,
        current_assignee_discord=None,
        new_assignee_github="newuser",
        new_assignee_discord=None,
        assignee_activity="Unknown",
        now=now,
    )
    
    fields = {f["name"]: f["value"] for f in embed["fields"]}
    assert fields["Status"] == "Closed"


def test_mutation_policy_dry_run() -> None:
    """Test that MutationPolicy prevents mutations in dry-run mode."""
    policy = MutationPolicy(
        mode=RunMode.DRY_RUN,
        github_write_allowed=True,
        discord_write_allowed=True,
    )
    
    assert not policy.allow_github_mutations
    assert not policy.allow_discord_mutations


def test_mutation_policy_observer() -> None:
    """Test that MutationPolicy prevents mutations in observer mode."""
    policy = MutationPolicy(
        mode=RunMode.OBSERVER,
        github_write_allowed=True,
        discord_write_allowed=True,
    )
    
    assert not policy.allow_github_mutations
    assert not policy.allow_discord_mutations


def test_mutation_policy_active() -> None:
    """Test that MutationPolicy allows mutations in active mode with permissions."""
    policy = MutationPolicy(
        mode=RunMode.ACTIVE,
        github_write_allowed=True,
        discord_write_allowed=True,
    )
    
    assert policy.allow_github_mutations
    assert policy.allow_discord_mutations


def test_mutation_policy_write_disabled() -> None:
    """Test that MutationPolicy prevents mutations when write is disabled."""
    policy = MutationPolicy(
        mode=RunMode.ACTIVE,
        github_write_allowed=False,
        discord_write_allowed=False,
    )
    
    assert not policy.allow_github_mutations
    assert not policy.allow_discord_mutations
