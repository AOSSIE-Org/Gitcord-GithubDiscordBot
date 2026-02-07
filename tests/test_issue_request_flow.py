"""Tests for issue request & assignment flow: eligibility, embed, merged PR stats."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from ghdcbot.engine.issue_request_flow import (
    build_mentor_request_embed,
    build_repo_selection_embed,
    compute_eligibility,
    format_activity_signal,
    get_merged_pr_count_and_last_time,
    group_pending_requests_by_repo,
)


def test_get_merged_pr_count_and_last_time_empty() -> None:
    """No contributions in period -> 0 and None."""
    storage = MagicMock()
    storage.list_contributions.return_value = []
    period_start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    period_end = datetime(2025, 1, 31, tzinfo=timezone.utc)
    count, last_at = get_merged_pr_count_and_last_time(storage, "alice", period_start, period_end)
    assert count == 0
    assert last_at is None


def test_get_merged_pr_count_and_last_time_filtered_by_user_and_type() -> None:
    """Only pr_merged for the user in period are counted."""
    from ghdcbot.core.models import ContributionEvent

    base = datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
    events = [
        ContributionEvent("alice", "pr_merged", "org/r", base, {}),
        ContributionEvent("alice", "pr_merged", "org/r", base + timedelta(days=1), {}),
        ContributionEvent("bob", "pr_merged", "org/r", base, {}),
        ContributionEvent("alice", "comment", "org/r", base, {}),
    ]
    storage = MagicMock()
    storage.list_contributions.return_value = events
    period_start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    period_end = datetime(2025, 1, 31, tzinfo=timezone.utc)
    count, last_at = get_merged_pr_count_and_last_time(storage, "alice", period_start, period_end)
    assert count == 2
    assert last_at == base + timedelta(days=1)


def test_get_merged_pr_count_and_last_time_respects_period() -> None:
    """Events outside period are ignored."""
    from ghdcbot.core.models import ContributionEvent

    inside = datetime(2025, 1, 15, tzinfo=timezone.utc)
    before = datetime(2024, 12, 1, tzinfo=timezone.utc)
    storage = MagicMock()
    storage.list_contributions.return_value = [
        ContributionEvent("alice", "pr_merged", "org/r", inside, {}),
        ContributionEvent("alice", "pr_merged", "org/r", before, {}),
    ]
    period_start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    period_end = datetime(2025, 1, 31, tzinfo=timezone.utc)
    count, last_at = get_merged_pr_count_and_last_time(storage, "alice", period_start, period_end)
    assert count == 1
    assert last_at == inside


def test_compute_eligibility_eligible() -> None:
    """Has required role and recent activity -> eligible."""
    now = datetime.now(timezone.utc)
    verdict, reason = compute_eligibility(
        eligible_roles_config=["Contributor", "Maintainer"],
        contributor_roles=["Contributor"],
        merged_count=2,
        last_merged_at=now - timedelta(days=5),
        now=now,
    )
    assert verdict == "eligible"
    assert "Meets role and activity" in reason


def test_compute_eligibility_eligible_empty_roles_config() -> None:
    """Empty eligible_roles_config means any verified user is eligible (role check passes)."""
    now = datetime.now(timezone.utc)
    verdict, reason = compute_eligibility(
        eligible_roles_config=[],
        contributor_roles=[],
        merged_count=1,
        last_merged_at=now - timedelta(days=1),
        now=now,
    )
    assert verdict == "eligible"


def test_compute_eligibility_not_eligible_missing_role() -> None:
    """Contributor without required role -> not_eligible."""
    now = datetime.now(timezone.utc)
    verdict, reason = compute_eligibility(
        eligible_roles_config=["Maintainer"],
        contributor_roles=["Apprentice"],
        merged_count=5,
        last_merged_at=now - timedelta(days=1),
        now=now,
    )
    assert verdict == "not_eligible"
    assert "required role" in reason.lower()


def test_compute_eligibility_eligible_low_activity_no_merged() -> None:
    """No merged PRs in period -> eligible_low_activity (good-first-issue)."""
    now = datetime.now(timezone.utc)
    verdict, reason = compute_eligibility(
        eligible_roles_config=["Contributor"],
        contributor_roles=["Contributor"],
        merged_count=0,
        last_merged_at=None,
        now=now,
    )
    assert verdict == "eligible_low_activity"
    assert "good-first-issue" in reason.lower() or "No merged" in reason


def test_compute_eligibility_eligible_low_activity_old_last_merged() -> None:
    """Last merged PR long ago -> eligible_low_activity."""
    now = datetime.now(timezone.utc)
    verdict, reason = compute_eligibility(
        eligible_roles_config=["Contributor"],
        contributor_roles=["Contributor"],
        merged_count=1,
        last_merged_at=now - timedelta(days=60),
        now=now,
    )
    assert verdict == "eligible_low_activity"
    assert "recent" in reason.lower() or "activity" in reason.lower()


def test_format_activity_signal_active() -> None:
    """Recent merged PR -> Active."""
    now = datetime.now(timezone.utc)
    assert format_activity_signal(1, now - timedelta(days=7), now) == "Active"


def test_format_activity_signal_low_activity() -> None:
    """No merged or old merged -> Low activity."""
    now = datetime.now(timezone.utc)
    assert format_activity_signal(0, None, now) == "Low activity"
    assert format_activity_signal(1, now - timedelta(days=60), now) == "Low activity"


def test_build_mentor_request_embed_required_fields() -> None:
    """Embed contains all mandatory fields for mentor decision."""
    now = datetime.now(timezone.utc)
    request = {
        "request_id": "req-1",
        "discord_user_id": "123",
        "github_user": "alice",
        "owner": "org",
        "repo": "repo",
        "issue_number": 42,
        "issue_url": "https://github.com/org/repo/issues/42",
        "created_at": now.isoformat(),
        "status": "pending",
    }
    issue = {
        "title": "Fix bug",
        "state": "open",
        "number": 42,
        "labels": [{"name": "good-first-issue"}],
        "assignees": [],
        "created_at": (now - timedelta(days=5)).isoformat(),
        "html_url": request["issue_url"],
    }
    embed = build_mentor_request_embed(
        request=request,
        issue=issue,
        contributor_discord_mention="<@123>",
        contributor_roles=["Contributor"],
        merged_count=2,
        last_merged_at=now - timedelta(days=3),
        eligibility_verdict="eligible",
        eligibility_reason="Meets role and activity criteria.",
        eligible_roles_config=["Contributor"],
        period_days=30,
        now=now,
    )
    assert "title" in embed
    assert "Issue assignment request" in embed["title"]
    assert "url" in embed
    assert "fields" in embed
    names = [f["name"] for f in embed["fields"]]
    assert "Repository" in names
    assert "Issue" in names
    assert "Labels" in names
    assert "Issue age" in names
    assert "Current assignees" in names
    assert "Contributor" in names
    assert "Identity" in names
    assert "Discord roles" in names
    assert "Merged PRs" in str(names) or "merged" in str(names).lower()
    assert "Last merged PR" in names
    assert "Activity" in names
    assert "Required roles" in str(names) or "Eligibility" in names
    assert "Eligibility" in names


def test_build_mentor_request_embed_verdict_display_eligible() -> None:
    """Eligible verdict shows checkmark."""
    now = datetime.now(timezone.utc)
    request = {
        "request_id": "r",
        "discord_user_id": "1",
        "github_user": "u",
        "owner": "o",
        "repo": "r",
        "issue_number": 1,
        "issue_url": "https://github.com/o/r/issues/1",
        "created_at": now.isoformat(),
        "status": "pending",
    }
    issue = {"title": "T", "number": 1, "assignees": [], "created_at": now.isoformat(), "labels": []}
    embed = build_mentor_request_embed(
        request=request,
        issue=issue,
        contributor_discord_mention="<@1>",
        contributor_roles=[],
        merged_count=0,
        last_merged_at=None,
        eligibility_verdict="eligible",
        eligibility_reason="OK",
        eligible_roles_config=[],
        period_days=30,
        now=now,
    )
    elig_field = next(f for f in embed["fields"] if f["name"] == "Eligibility")
    assert "✅ Eligible" in elig_field["value"]


def test_build_mentor_request_embed_verdict_not_eligible() -> None:
    """Not eligible verdict shows cross."""
    now = datetime.now(timezone.utc)
    request = {
        "request_id": "r",
        "discord_user_id": "1",
        "github_user": "u",
        "owner": "o",
        "repo": "r",
        "issue_number": 1,
        "issue_url": "https://github.com/o/r/issues/1",
        "created_at": now.isoformat(),
        "status": "pending",
    }
    issue = {"title": "T", "number": 1, "assignees": [], "created_at": now.isoformat(), "labels": []}
    embed = build_mentor_request_embed(
        request=request,
        issue=issue,
        contributor_discord_mention="<@1>",
        contributor_roles=[],
        merged_count=0,
        last_merged_at=None,
        eligibility_verdict="not_eligible",
        eligibility_reason="No required role.",
        eligible_roles_config=["Maintainer"],
        period_days=30,
        now=now,
    )
    elig_field = next(f for f in embed["fields"] if f["name"] == "Eligibility")
    assert "❌ Not eligible" in elig_field["value"]


def test_build_mentor_request_embed_deterministic_same_inputs() -> None:
    """Same inputs produce same embed (deterministic)."""
    now = datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
    request = {
        "request_id": "req-1",
        "discord_user_id": "123",
        "github_user": "alice",
        "owner": "org",
        "repo": "repo",
        "issue_number": 1,
        "issue_url": "https://github.com/org/repo/issues/1",
        "created_at": now.isoformat(),
        "status": "pending",
    }
    issue = {"title": "T", "number": 1, "assignees": [], "created_at": now.isoformat(), "labels": []}
    embed1 = build_mentor_request_embed(
        request=request,
        issue=issue,
        contributor_discord_mention="<@123>",
        contributor_roles=["Contributor"],
        merged_count=1,
        last_merged_at=now - timedelta(days=1),
        eligibility_verdict="eligible",
        eligibility_reason="OK",
        eligible_roles_config=["Contributor"],
        period_days=30,
        now=now,
    )
    embed2 = build_mentor_request_embed(
        request=request,
        issue=issue,
        contributor_discord_mention="<@123>",
        contributor_roles=["Contributor"],
        merged_count=1,
        last_merged_at=now - timedelta(days=1),
        eligibility_verdict="eligible",
        eligibility_reason="OK",
        eligible_roles_config=["Contributor"],
        period_days=30,
        now=now,
    )
    assert embed1["title"] == embed2["title"]
    assert [f["name"] for f in embed1["fields"]] == [f["name"] for f in embed2["fields"]]


# -------- Repo selection (Step 1) --------


def test_group_pending_requests_by_repo_empty() -> None:
    """Empty list -> empty repo list."""
    assert group_pending_requests_by_repo([]) == []


def test_group_pending_requests_by_repo_single_repo() -> None:
    """Single repo with two requests -> one entry, count 2."""
    base = datetime(2025, 1, 10, tzinfo=timezone.utc)
    pending = [
        {"owner": "org", "repo": "r1", "request_id": "a", "created_at": base.isoformat()},
        {"owner": "org", "repo": "r1", "request_id": "b", "created_at": (base + timedelta(days=1)).isoformat()},
    ]
    result = group_pending_requests_by_repo(pending)
    assert len(result) == 1
    assert result[0]["owner"] == "org"
    assert result[0]["repo"] == "r1"
    assert result[0]["count"] == 2
    assert result[0]["oldest_created_at"] == base


def test_group_pending_requests_by_repo_sort_count_desc_then_name() -> None:
    """Repos sorted by count descending, then owner/repo ascending."""
    base = datetime(2025, 1, 1, tzinfo=timezone.utc)
    pending = [
        {"owner": "org", "repo": "aaa", "request_id": "1", "created_at": base.isoformat()},
        {"owner": "org", "repo": "bbb", "request_id": "2", "created_at": base.isoformat()},
        {"owner": "org", "repo": "bbb", "request_id": "3", "created_at": base.isoformat()},
        {"owner": "org", "repo": "bbb", "request_id": "4", "created_at": base.isoformat()},
    ]
    result = group_pending_requests_by_repo(pending)
    assert len(result) == 2
    assert result[0]["repo"] == "bbb" and result[0]["count"] == 3
    assert result[1]["repo"] == "aaa" and result[1]["count"] == 1


def test_group_pending_requests_by_repo_skips_missing_owner_repo() -> None:
    """Entries without owner or repo are skipped."""
    pending = [
        {"owner": "org", "repo": "r1", "request_id": "1", "created_at": "2025-01-01T00:00:00+00:00"},
        {"owner": "", "repo": "r2", "request_id": "2", "created_at": "2025-01-01T00:00:00+00:00"},
        {"owner": "org", "repo": "", "request_id": "3", "created_at": "2025-01-01T00:00:00+00:00"},
    ]
    result = group_pending_requests_by_repo(pending)
    assert len(result) == 1
    assert result[0]["owner"] == "org" and result[0]["repo"] == "r1"


def test_build_repo_selection_embed_required_content() -> None:
    """Repo selection embed has title and repo lines."""
    now = datetime(2025, 1, 15, tzinfo=timezone.utc)
    repo_list = [
        {"owner": "org", "repo": "frontend", "count": 4, "oldest_created_at": now - timedelta(days=3)},
        {"owner": "org", "repo": "backend", "count": 2, "oldest_created_at": now - timedelta(days=1)},
    ]
    embed = build_repo_selection_embed(repo_list, now)
    assert "Repositories with Pending Requests" in embed["title"]
    assert "frontend" in embed["description"] and "4 request" in embed["description"]
    assert "backend" in embed["description"] and "2 request" in embed["description"]


def test_build_repo_selection_embed_empty_list() -> None:
    """Empty repo list -> description says no pending."""
    now = datetime(2025, 1, 15, tzinfo=timezone.utc)
    embed = build_repo_selection_embed([], now)
    assert "No pending" in embed["description"] or "request" in embed["description"].lower()


def test_group_pending_requests_by_repo_deterministic_ordering() -> None:
    """Same input produces same order (deterministic)."""
    base = datetime(2025, 1, 1, tzinfo=timezone.utc)
    pending = [
        {"owner": "o", "repo": "b", "request_id": "1", "created_at": base.isoformat()},
        {"owner": "o", "repo": "a", "request_id": "2", "created_at": base.isoformat()},
        {"owner": "o", "repo": "a", "request_id": "3", "created_at": base.isoformat()},
    ]
    r1 = group_pending_requests_by_repo(pending)
    r2 = group_pending_requests_by_repo(pending)
    assert [x["repo"] for x in r1] == [x["repo"] for x in r2]
    assert r1[0]["repo"] == "a" and r1[0]["count"] == 2
    assert r1[1]["repo"] == "b" and r1[1]["count"] == 1
