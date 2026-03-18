"""Tests for PR context preview feature."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from ghdcbot.engine.pr_context import (
    build_pr_embed,
    determine_mentor_signal,
    fetch_pr_context,
    format_idle_duration,
    format_relative_time,
    parse_pr_url,
)


def test_parse_pr_url_valid() -> None:
    """Test parsing valid GitHub PR URLs."""
    assert parse_pr_url("https://github.com/owner/repo/pull/123") == ("owner", "repo", 123)
    assert parse_pr_url("https://github.com/owner/repo/pull/123/") == ("owner", "repo", 123)
    assert parse_pr_url("github.com/owner/repo/pull/456") == ("owner", "repo", 456)
    assert parse_pr_url("http://github.com/owner/repo/pull/789") == ("owner", "repo", 789)


def test_parse_pr_url_invalid() -> None:
    """Test parsing invalid URLs returns None."""
    assert parse_pr_url("not a url") is None
    assert parse_pr_url("https://github.com/owner/repo") is None
    assert parse_pr_url("https://github.com/owner/repo/issues/123") is None
    assert parse_pr_url("https://gitlab.com/owner/repo/pull/123") is None
    assert parse_pr_url("") is None


def test_format_relative_time() -> None:
    """Test relative time formatting."""
    now = datetime.now(timezone.utc)
    
    # Just now (< 1 minute)
    recent = now - timedelta(seconds=30)
    assert format_relative_time(recent, now) == "Just now"
    
    # Minutes
    minutes_ago = now - timedelta(minutes=5)
    assert format_relative_time(minutes_ago, now) == "5 mins ago"
    assert format_relative_time(now - timedelta(minutes=1), now) == "1 min ago"
    
    # Hours
    hours_ago = now - timedelta(hours=2)
    assert format_relative_time(hours_ago, now) == "2 hours ago"
    assert format_relative_time(now - timedelta(hours=1), now) == "1 hour ago"
    
    # Days
    days_ago = now - timedelta(days=3)
    assert format_relative_time(days_ago, now) == "3 days ago"
    assert format_relative_time(now - timedelta(days=1), now) == "1 day ago"
    
    # Weeks
    weeks_ago = now - timedelta(days=14)
    assert format_relative_time(weeks_ago, now) == "2 weeks ago"
    
    # Months
    months_ago = now - timedelta(days=60)
    assert format_relative_time(months_ago, now) == "2 months ago"
    
    # Years
    years_ago = now - timedelta(days=400)
    assert format_relative_time(years_ago, now) == "1 year ago"
    
    # Unknown
    assert format_relative_time(None, now) == "Unknown"


def test_format_idle_duration() -> None:
    """Test idle duration formatting."""
    now = datetime.now(timezone.utc)
    
    # Active (< 1 hour)
    recent = now - timedelta(minutes=30)
    assert format_idle_duration(recent, now) == "Active"
    
    # Hours
    hours_ago = now - timedelta(hours=2)
    assert format_idle_duration(hours_ago, now) == "Idle for 2 hours"
    
    # Days
    days_ago = now - timedelta(days=3)
    assert format_idle_duration(days_ago, now) == "Idle for 3 days"
    
    # Unknown
    assert format_idle_duration(None, now) == "Unknown"


def test_determine_mentor_signal() -> None:
    """Test mentor signal determination."""
    pr_open = {"state": "open", "draft": False, "mergeable": True}
    pr_draft = {"state": "open", "draft": True, "mergeable": None}
    pr_merged = {"state": "merged", "draft": False, "mergeable": None}
    pr_closed = {"state": "closed", "draft": False, "mergeable": None}
    
    # Ready to merge
    assert (
        determine_mentor_signal(pr_open, [{"state": "APPROVED"}], "success", True)
        == "Ready to merge"
    )
    
    # Blocked by CI
    assert (
        determine_mentor_signal(pr_open, [{"state": "APPROVED"}], "failing", True)
        == "Blocked by CI"
    )
    
    # Waiting on contributor
    assert (
        determine_mentor_signal(pr_open, [{"state": "CHANGES_REQUESTED"}], "success", True)
        == "Waiting on contributor"
    )
    
    # Waiting on reviewer
    assert (
        determine_mentor_signal(pr_open, [], "success", True) == "Waiting on reviewer"
    )
    
    # Merged
    assert determine_mentor_signal(pr_merged, [], "success", None) == "Merged"
    
    # Closed
    assert determine_mentor_signal(pr_closed, [], "success", None) == "Closed"
    
    # Draft
    assert determine_mentor_signal(pr_draft, [], "success", None) == "Draft"


def test_build_pr_embed() -> None:
    """Test embed building from PR data."""
    now = datetime.now(timezone.utc)
    created_at = now - timedelta(days=5)
    last_commit = now - timedelta(days=1)
    
    pr = {
        "title": "Test PR",
        "state": "open",
        "draft": False,
        "mergeable": True,
        "user": {"login": "testuser"},
        "assignees": [{"login": "assignee1"}],
        "requested_reviewers": [{"login": "reviewer1"}],
        "created_at": created_at.isoformat(),
        "html_url": "https://github.com/owner/repo/pull/123",
    }
    
    reviews = [{"state": "APPROVED"}]
    
    embed = build_pr_embed(
        pr=pr,
        owner="owner",
        repo="repo",
        reviews=reviews,
        ci_status="success",
        last_commit_time=last_commit,
        discord_mention=None,
    )
    
    assert embed["title"] == "🧠 Pull Request Overview"
    assert embed["url"] == "https://github.com/owner/repo/pull/123"
    assert len(embed["fields"]) == 8  # All required fields
    
    # Check Repository field
    repo_field = next(f for f in embed["fields"] if f["name"] == "Repository")
    assert repo_field["value"] == "owner/repo"
    
    # Check Title field
    title_field = next(f for f in embed["fields"] if f["name"] == "Title")
    assert title_field["value"] == "Test PR"
    
    # Check Author field
    author_field = next(f for f in embed["fields"] if f["name"] == "Author")
    assert author_field["value"] == "testuser"
    
    # Check Activity field uses relative time
    activity_field = next(f for f in embed["fields"] if f["name"] == "Activity")
    assert "ago" in activity_field["value"]  # Should contain relative time
    assert "Created:" in activity_field["value"]
    assert "Last commit:" in activity_field["value"]
    
    # Check Mentor Signal
    signal_field = next(f for f in embed["fields"] if f["name"] == "Mentor Signal")
    assert signal_field["value"] == "Ready to merge"


def test_build_pr_embed_with_discord_mention() -> None:
    """Test embed includes Discord mention when author is linked."""
    pr = {
        "title": "Test PR",
        "state": "open",
        "draft": False,
        "mergeable": True,
        "user": {"login": "testuser"},
        "assignees": [],
        "requested_reviewers": [],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "html_url": "https://github.com/owner/repo/pull/123",
    }
    
    embed = build_pr_embed(
        pr=pr,
        owner="owner",
        repo="repo",
        reviews=[],
        ci_status="unknown",
        last_commit_time=None,
        discord_mention="<@123456789>",
    )
    
    author_field = next(f for f in embed["fields"] if f["name"] == "Author")
    assert "<@123456789>" in author_field["value"]
    assert "testuser" in author_field["value"]


def test_fetch_pr_context_success() -> None:
    """Test fetching PR context from GitHub adapter."""
    mock_adapter = MagicMock()
    
    pr_data = {
        "title": "Test PR",
        "state": "open",
        "draft": False,
        "mergeable": True,
        "user": {"login": "testuser"},
        "assignees": [],
        "requested_reviewers": [],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "html_url": "https://github.com/owner/repo/pull/123",
        "head": {"sha": "abc123"},
    }
    
    mock_adapter.get_pull_request.return_value = pr_data
    mock_adapter.get_pull_request_reviews.return_value = []
    mock_adapter.get_pull_request_check_runs.return_value = []
    
    pr, reviews, ci_status, last_commit_time = fetch_pr_context(
        mock_adapter, "owner", "repo", 123
    )
    
    assert pr == pr_data
    assert reviews == []
    assert ci_status == "unknown"
    mock_adapter.get_pull_request.assert_called_once_with("owner", "repo", 123)


def test_fetch_pr_context_not_found() -> None:
    """Test fetching PR context when PR doesn't exist."""
    mock_adapter = MagicMock()
    mock_adapter.get_pull_request.return_value = None
    
    pr, reviews, ci_status, last_commit_time = fetch_pr_context(
        mock_adapter, "owner", "repo", 999
    )
    
    assert pr is None
    assert reviews == []
    assert ci_status == "unknown"
    assert last_commit_time is None


def test_fetch_pr_context_ci_status() -> None:
    """Test CI status detection from check runs."""
    mock_adapter = MagicMock()
    
    pr_data = {
        "title": "Test PR",
        "state": "open",
        "draft": False,
        "mergeable": True,
        "user": {"login": "testuser"},
        "assignees": [],
        "requested_reviewers": [],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "html_url": "https://github.com/owner/repo/pull/123",
        "head": {"sha": "abc123"},
    }
    
    mock_adapter.get_pull_request.return_value = pr_data
    mock_adapter.get_pull_request_reviews.return_value = []
    
    # Test failing CI
    mock_adapter.get_pull_request_check_runs.return_value = [
        {"status": "completed", "conclusion": "failure"}
    ]
    _, _, ci_status, _ = fetch_pr_context(mock_adapter, "owner", "repo", 123)
    assert ci_status == "failing"
    
    # Test success CI
    mock_adapter.get_pull_request_check_runs.return_value = [
        {"status": "completed", "conclusion": "success"}
    ]
    _, _, ci_status, _ = fetch_pr_context(mock_adapter, "owner", "repo", 123)
    assert ci_status == "success"
    
    # Test pending CI
    mock_adapter.get_pull_request_check_runs.return_value = [
        {"status": "in_progress", "conclusion": None}
    ]
    _, _, ci_status, _ = fetch_pr_context(mock_adapter, "owner", "repo", 123)
    assert ci_status == "pending"


def test_build_pr_embed_empty_assignees() -> None:
    """Test embed handles empty assignees/reviewers."""
    pr = {
        "title": "Test PR",
        "state": "open",
        "draft": False,
        "mergeable": True,
        "user": {"login": "testuser"},
        "assignees": [],
        "requested_reviewers": [],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "html_url": "https://github.com/owner/repo/pull/123",
    }
    
    embed = build_pr_embed(
        pr=pr,
        owner="owner",
        repo="repo",
        reviews=[],
        ci_status="unknown",
        last_commit_time=None,
    )
    
    assignment_field = next(f for f in embed["fields"] if f["name"] == "Assignment")
    assert assignment_field["value"] == "None"


def test_build_pr_embed_long_title() -> None:
    """Test embed truncates long titles."""
    long_title = "A" * 300
    pr = {
        "title": long_title,
        "state": "open",
        "draft": False,
        "mergeable": True,
        "user": {"login": "testuser"},
        "assignees": [],
        "requested_reviewers": [],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "html_url": "https://github.com/owner/repo/pull/123",
    }
    
    embed = build_pr_embed(
        pr=pr,
        owner="owner",
        repo="repo",
        reviews=[],
        ci_status="unknown",
        last_commit_time=None,
    )
    
    title_field = next(f for f in embed["fields"] if f["name"] == "Title")
    assert len(title_field["value"]) <= 256  # Discord limit
