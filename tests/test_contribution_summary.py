from datetime import datetime, timedelta, timezone

import pytest

from ghdcbot.adapters.storage.sqlite import SqliteStorage
from ghdcbot.core.models import ContributionEvent


def test_list_contribution_summaries_counts_activity(tmp_path) -> None:
    storage = SqliteStorage(str(tmp_path))
    storage.init_schema()

    period_end = datetime(2024, 1, 31, tzinfo=timezone.utc)
    period_start = period_end - timedelta(days=30)

    events = [
        ContributionEvent(
            github_user="alice",
            event_type="issue_opened",
            repo="repo",
            created_at=period_end - timedelta(days=1),
            payload={},
        ),
        ContributionEvent(
            github_user="alice",
            event_type="issue_opened",
            repo="repo",
            created_at=period_end - timedelta(days=1),
            payload={},
        ),
        ContributionEvent(
            github_user="alice",
            event_type="pr_reviewed",
            repo="repo",
            created_at=period_end - timedelta(days=2),
            payload={},
        ),
        ContributionEvent(
            github_user="bob",
            event_type="pr_merged",
            repo="repo",
            created_at=period_end - timedelta(days=3),
            payload={},
        ),
        ContributionEvent(
            github_user="bob",
            event_type="comment",
            repo="repo",
            created_at=period_end - timedelta(days=4),
            payload={},
        ),
        ContributionEvent(
            github_user="carol",
            event_type="issue_opened",
            repo="repo",
            created_at=period_end - timedelta(days=40),
            payload={},
        ),
    ]
    storage.record_contributions(events)

    summaries = storage.list_contribution_summaries(period_start, period_end)

    assert [summary.github_user for summary in summaries] == ["alice", "bob"]

    alice, bob = summaries
    assert alice.issues_opened == 1
    assert alice.prs_opened == 0
    assert alice.prs_reviewed == 1
    assert alice.comments == 0
    assert alice.total_score == 0

    assert bob.issues_opened == 0
    assert bob.prs_opened == 1
    assert bob.prs_reviewed == 0
    assert bob.comments == 1
    assert bob.total_score == 0


def test_list_contribution_summaries_rejects_deprecated_scoring_args(tmp_path) -> None:
    storage = SqliteStorage(str(tmp_path))
    storage.init_schema()
    period_end = datetime(2024, 1, 31, tzinfo=timezone.utc)
    period_start = period_end - timedelta(days=30)

    with pytest.raises(ValueError):
        storage.list_contribution_summaries(period_start, period_end, weights={"pr_merged": 10})

    with pytest.raises(ValueError):
        storage.list_contribution_summaries(
            period_start, period_end, difficulty_weights={"easy": 5}
        )
