"""Tests for merge-only contribution scoring."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from ghdcbot.adapters.storage.sqlite import SqliteStorage
from ghdcbot.core.models import ContributionEvent, Score
from ghdcbot.engine.scoring import WeightedScoreStrategy


def test_spam_activity_does_not_increase_score(tmp_path) -> None:
    """A user with spam activity (PR opens, comments, reviews) but no merged PRs gets score 0."""
    storage = SqliteStorage(str(tmp_path))
    storage.init_schema()
    
    period_end = datetime.now(timezone.utc)
    period_start = period_end - timedelta(days=30)
    
    strategy = WeightedScoreStrategy(
        weights={
            "pr_opened": 5,
            "pr_merged": 10,
            "pr_reviewed": 2,
            "comment": 1,
            "issue_opened": 3,
        },
        period_days=30,
    )
    
    # User with lots of spam activity but 0 merged PRs
    events = [
        ContributionEvent(
            github_user="spammer",
            event_type="pr_opened",
            repo="test",
            created_at=period_end - timedelta(days=1),
            payload={"pr_number": i},
        )
        for i in range(5)  # 5 PR opens
    ] + [
        ContributionEvent(
            github_user="spammer",
            event_type="comment",
            repo="test",
            created_at=period_end - timedelta(days=2),
            payload={"issue_number": i},
        )
        for i in range(20)  # 20 comments
    ] + [
        ContributionEvent(
            github_user="spammer",
            event_type="pr_reviewed",
            repo="test",
            created_at=period_end - timedelta(days=3),
            payload={"pr_number": i, "state": "APPROVED"},
        )
        for i in range(3)  # 3 PR reviews
    ]
    
    scores = strategy.compute_scores(events, period_end)
    
    # Should have 0 score despite all the spam activity
    # Users with 0 points don't appear in scores list (defaultdict behavior)
    spammer_scores = [s for s in scores if s.github_user == "spammer"]
    if spammer_scores:
        assert spammer_scores[0].points == 0
    else:
        # No score entry means 0 points (correct behavior)
        assert True


def test_merged_pr_increases_score(tmp_path) -> None:
    """A user with 1 merged PR gets a score > 0."""
    storage = SqliteStorage(str(tmp_path))
    storage.init_schema()
    
    period_end = datetime.now(timezone.utc)
    period_start = period_end - timedelta(days=30)
    
    strategy = WeightedScoreStrategy(
        weights={
            "pr_merged": 10,
        },
        period_days=30,
    )
    
    events = [
        ContributionEvent(
            github_user="contributor",
            event_type="pr_merged",
            repo="test",
            created_at=period_end - timedelta(days=1),
            payload={"pr_number": 1},
        ),
    ]
    
    scores = strategy.compute_scores(events, period_end)
    
    contributor_scores = [s for s in scores if s.github_user == "contributor"]
    assert len(contributor_scores) == 1
    assert contributor_scores[0].points == 10  # Should get the pr_merged weight


def test_only_merged_prs_count_for_scoring(tmp_path) -> None:
    """Only pr_merged events contribute to scores, even if other weights exist."""
    storage = SqliteStorage(str(tmp_path))
    storage.init_schema()
    
    period_end = datetime.now(timezone.utc)
    period_start = period_end - timedelta(days=30)
    
    strategy = WeightedScoreStrategy(
        weights={
            "pr_opened": 100,  # High weight, but should be ignored
            "pr_merged": 10,
            "pr_reviewed": 50,  # High weight, but should be ignored
            "comment": 20,  # High weight, but should be ignored
            "issue_opened": 30,  # High weight, but should be ignored
        },
        period_days=30,
    )
    
    events = [
        ContributionEvent(
            github_user="user",
            event_type="pr_opened",
            repo="test",
            created_at=period_end - timedelta(days=1),
            payload={"pr_number": 1},
        ),
        ContributionEvent(
            github_user="user",
            event_type="pr_merged",
            repo="test",
            created_at=period_end - timedelta(days=2),
            payload={"pr_number": 2},
        ),
        ContributionEvent(
            github_user="user",
            event_type="pr_reviewed",
            repo="test",
            created_at=period_end - timedelta(days=3),
            payload={"pr_number": 3, "state": "APPROVED"},
        ),
        ContributionEvent(
            github_user="user",
            event_type="comment",
            repo="test",
            created_at=period_end - timedelta(days=4),
            payload={"issue_number": 1},
        ),
        ContributionEvent(
            github_user="user",
            event_type="issue_opened",
            repo="test",
            created_at=period_end - timedelta(days=5),
            payload={"issue_number": 1},
        ),
    ]
    
    scores = strategy.compute_scores(events, period_end)
    
    user_scores = [s for s in scores if s.github_user == "user"]
    assert len(user_scores) == 1
    # Should only get points from the merged PR (10), not from other events
    assert user_scores[0].points == 10


def test_multiple_merged_prs_accumulate(tmp_path) -> None:
    """Multiple merged PRs should accumulate scores."""
    storage = SqliteStorage(str(tmp_path))
    storage.init_schema()
    
    period_end = datetime.now(timezone.utc)
    period_start = period_end - timedelta(days=30)
    
    strategy = WeightedScoreStrategy(
        weights={
            "pr_merged": 10,
        },
        period_days=30,
    )
    
    events = [
        ContributionEvent(
            github_user="contributor",
            event_type="pr_merged",
            repo="test",
            created_at=period_end - timedelta(days=i),
            payload={"pr_number": i},
        )
        for i in range(1, 6)  # 5 merged PRs
    ]
    
    scores = strategy.compute_scores(events, period_end)
    
    contributor_scores = [s for s in scores if s.github_user == "contributor"]
    assert len(contributor_scores) == 1
    assert contributor_scores[0].points == 50  # 5 * 10


def test_difficulty_weights_still_work(tmp_path) -> None:
    """Difficulty-aware scoring should still work for merged PRs."""
    storage = SqliteStorage(str(tmp_path))
    storage.init_schema()
    
    period_end = datetime.now(timezone.utc)
    period_start = period_end - timedelta(days=30)
    
    strategy = WeightedScoreStrategy(
        weights={"pr_merged": 10},
        period_days=30,
        difficulty_weights={"easy": 5, "medium": 10, "hard": 20},
    )
    
    events = [
        ContributionEvent(
            github_user="contributor",
            event_type="pr_merged",
            repo="test",
            created_at=period_end - timedelta(days=1),
            payload={"pr_number": 1, "difficulty_labels": ["easy"]},
        ),
        ContributionEvent(
            github_user="contributor",
            event_type="pr_merged",
            repo="test",
            created_at=period_end - timedelta(days=2),
            payload={"pr_number": 2, "difficulty_labels": ["hard"]},
        ),
    ]
    
    scores = strategy.compute_scores(events, period_end)
    
    contributor_scores = [s for s in scores if s.github_user == "contributor"]
    assert len(contributor_scores) == 1
    # Should use difficulty weights: 5 (easy) + 20 (hard) = 25
    assert contributor_scores[0].points == 25


def test_quality_adjustments_still_work(tmp_path) -> None:
    """Quality adjustments (penalties/bonuses) should still work."""
    storage = SqliteStorage(str(tmp_path))
    storage.init_schema()
    
    period_end = datetime.now(timezone.utc)
    period_start = period_end - timedelta(days=30)
    
    strategy = WeightedScoreStrategy(
        weights={"pr_merged": 10},
        period_days=30,
        quality_adjustments={
            "penalties": {"reverted_pr": -8},
            "bonuses": {"pr_review": 2},
        },
    )
    
    events = [
        ContributionEvent(
            github_user="contributor",
            event_type="pr_merged",
            repo="test",
            created_at=period_end - timedelta(days=1),
            payload={"pr_number": 1},
        ),
        ContributionEvent(
            github_user="contributor",
            event_type="pr_reverted",
            repo="test",
            created_at=period_end - timedelta(days=1),
            payload={"pr_number": 1},
        ),
    ]
    
    scores = strategy.compute_scores(events, period_end)
    
    contributor_scores = [s for s in scores if s.github_user == "contributor"]
    assert len(contributor_scores) == 1
    # Base score (10) + penalty (-8) = 2
    assert contributor_scores[0].points == 2


def test_config_with_other_weights_still_loads() -> None:
    """Configs with weights for other events should still load without errors."""
    # This test just verifies the strategy can be instantiated with various weights
    strategy = WeightedScoreStrategy(
        weights={
            "pr_opened": 5,
            "pr_merged": 10,
            "pr_reviewed": 2,
            "comment": 1,
            "issue_opened": 3,
        },
        period_days=30,
    )
    # Should not raise any errors
    assert strategy._weights["pr_merged"] == 10
    assert strategy._weights["pr_opened"] == 5  # Exists but will be ignored in scoring


def test_deterministic_scoring(tmp_path) -> None:
    """Scoring should be deterministic (same inputs → same outputs)."""
    storage = SqliteStorage(str(tmp_path))
    storage.init_schema()
    
    period_end = datetime.now(timezone.utc)
    period_start = period_end - timedelta(days=30)
    
    strategy = WeightedScoreStrategy(
        weights={"pr_merged": 10},
        period_days=30,
    )
    
    events = [
        ContributionEvent(
            github_user="alice",
            event_type="pr_merged",
            repo="test",
            created_at=period_end - timedelta(days=1),
            payload={"pr_number": 1},
        ),
        ContributionEvent(
            github_user="bob",
            event_type="pr_merged",
            repo="test",
            created_at=period_end - timedelta(days=2),
            payload={"pr_number": 2},
        ),
    ]
    
    scores1 = strategy.compute_scores(events, period_end)
    scores2 = strategy.compute_scores(list(reversed(events)), period_end)
    
    # Should produce same scores regardless of order
    scores1_dict = {s.github_user: s.points for s in scores1}
    scores2_dict = {s.github_user: s.points for s in scores2}
    assert scores1_dict == scores2_dict
