"""Tests for read-only contribution metrics (engine/metrics.py)."""

from datetime import datetime, timedelta, timezone

from ghdcbot.adapters.storage.sqlite import SqliteStorage
from ghdcbot.core.models import ContributionEvent
from ghdcbot.engine.metrics import (
    build_contribution_summary_message,
    get_contribution_metrics,
    get_rank_for_user,
    rank_by_activity,
)


def test_get_contribution_metrics_pr_opened_vs_merged(tmp_path) -> None:
    storage = SqliteStorage(str(tmp_path))
    storage.init_schema()
    period_end = datetime(2024, 1, 31, tzinfo=timezone.utc)
    period_start = period_end - timedelta(days=30)
    events = [
        ContributionEvent(
            github_user="alice",
            event_type="pr_opened",
            repo="r",
            created_at=period_end - timedelta(days=1),
            payload={},
        ),
        ContributionEvent(
            github_user="alice",
            event_type="pr_opened",
            repo="r",
            created_at=period_end - timedelta(days=2),
            payload={},
        ),
        ContributionEvent(
            github_user="alice",
            event_type="pr_merged",
            repo="r",
            created_at=period_end - timedelta(days=3),
            payload={},
        ),
        ContributionEvent(
            github_user="bob",
            event_type="pr_reviewed",
            repo="r",
            created_at=period_end - timedelta(days=1),
            payload={},
        ),
    ]
    storage.record_contributions(events)
    metrics = get_contribution_metrics(storage, period_start, period_end, {})
    by_user = {m.github_user: m for m in metrics}
    assert by_user["alice"].prs_opened == 2
    assert by_user["alice"].prs_merged == 1
    assert by_user["alice"].reviews_submitted == 0
    assert by_user["bob"].reviews_submitted == 1
    assert by_user["bob"].prs_opened == 0
    assert by_user["bob"].prs_merged == 0


def test_issue_engagement_formula(tmp_path) -> None:
    storage = SqliteStorage(str(tmp_path))
    storage.init_schema()
    period_end = datetime(2024, 1, 31, tzinfo=timezone.utc)
    period_start = period_end - timedelta(days=7)
    events = [
        ContributionEvent(
            github_user="u",
            event_type="issue_opened",
            repo="r",
            created_at=period_end - timedelta(days=1),
            payload={},
        ),
        ContributionEvent(
            github_user="u",
            event_type="comment",
            repo="r",
            created_at=period_end - timedelta(days=2),
            payload={},
        ),
        ContributionEvent(
            github_user="u",
            event_type="comment",
            repo="r",
            created_at=period_end - timedelta(days=3),
            payload={},
        ),
    ]
    storage.record_contributions(events)
    metrics = get_contribution_metrics(storage, period_start, period_end, {})
    assert len(metrics) == 1
    assert metrics[0].issues_opened == 1
    assert metrics[0].comments == 2
    assert metrics[0].issue_engagement == 1.0 + 2 * 0.5


def test_rank_by_activity_and_get_rank_for_user(tmp_path) -> None:
    storage = SqliteStorage(str(tmp_path))
    storage.init_schema()
    period_end = datetime(2024, 1, 31, tzinfo=timezone.utc)
    period_start = period_end - timedelta(days=30)
    weights = {"pr_merged": 10, "comment": 1}
    events = [
        ContributionEvent("a", "pr_merged", "r", period_end - timedelta(days=1), {}),
        ContributionEvent("b", "comment", "r", period_end - timedelta(days=1), {}),
        ContributionEvent("c", "pr_merged", "r", period_end - timedelta(days=1), {}),
        ContributionEvent("c", "pr_merged", "r", period_end - timedelta(days=2), {}),
    ]
    storage.record_contributions(events)
    metrics = get_contribution_metrics(storage, period_start, period_end, weights)
    ranked = rank_by_activity(metrics)
    assert [m.github_user for m in ranked] == ["c", "a", "b"]
    assert get_rank_for_user(ranked, "c") == 1
    assert get_rank_for_user(ranked, "a") == 2
    assert get_rank_for_user(ranked, "b") == 3
    assert get_rank_for_user(ranked, "z") is None


def test_build_contribution_summary_message_for_other_user(tmp_path) -> None:
    storage = SqliteStorage(str(tmp_path))
    storage.init_schema()
    now = datetime(2024, 1, 31, tzinfo=timezone.utc)
    events = [
        ContributionEvent("alice", "pr_merged", "r", now - timedelta(days=1), {}),
        ContributionEvent("bob", "pr_merged", "r", now - timedelta(days=2), {}),
        ContributionEvent("bob", "pr_merged", "r", now - timedelta(days=3), {}),
        ContributionEvent("bob", "pr_opened", "r", now - timedelta(days=1), {}),
        ContributionEvent("carol", "comment", "r", now - timedelta(days=40), {}),
    ]
    storage.record_contributions(events)

    self_msg = build_contribution_summary_message(
        storage, "alice", rank_subject="you're", now=now
    )
    assert "**Last 7 days:**" in self_msg
    assert "**Last 30 days:**" in self_msg
    assert "PRs opened: 0, merged: 1" in self_msg
    assert "you're #2." in self_msg

    other_msg = build_contribution_summary_message(
        storage, "bob", rank_subject="they're", now=now
    )
    assert "PRs opened: 1, merged: 2" in other_msg
    assert "they're #1." in other_msg
    assert "you're" not in other_msg

    empty_msg = build_contribution_summary_message(
        storage, "nobody", rank_subject="they're", now=now
    )
    assert "No activity in this period." in empty_msg
    assert "Top contributors" not in empty_msg
