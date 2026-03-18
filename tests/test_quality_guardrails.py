"""Tests for Contribution Quality Guardrails feature."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from ghdcbot.config.models import BotConfig, QualityAdjustmentsConfig, ScoringConfig
from ghdcbot.core.models import ContributionEvent
from ghdcbot.engine.scoring import WeightedScoreStrategy


def test_quality_adjustments_disabled_no_behavior_change() -> None:
    """Feature disabled → no behavior change."""
    strategy = WeightedScoreStrategy(
        weights={"pr_merged": 10, "pr_reviewed": 2},
        period_days=30,
    )
    period_end = datetime.now(timezone.utc)
    events = [
        ContributionEvent(
            github_user="alice",
            event_type="pr_merged",
            repo="test",
            created_at=period_end - timedelta(days=1),
            payload={"pr_number": 1},
        ),
    ]
    scores = strategy.compute_scores(events, period_end)
    assert len(scores) == 1
    assert scores[0].github_user == "alice"
    assert scores[0].points == 10  # Base score only


def test_reverted_pr_penalty_applied_once() -> None:
    """Reverted PR → penalty applied once."""
    strategy = WeightedScoreStrategy(
        weights={"pr_merged": 10},
        period_days=30,
        quality_adjustments={"penalties": {"reverted_pr": -8}, "bonuses": {}},
    )
    period_end = datetime.now(timezone.utc)
    events = [
        ContributionEvent(
            github_user="alice",
            event_type="pr_merged",
            repo="test",
            created_at=period_end - timedelta(days=5),
            payload={"pr_number": 1},
        ),
        ContributionEvent(
            github_user="alice",
            event_type="pr_reverted",
            repo="test",
            created_at=period_end - timedelta(days=5),  # Same time as merge
            payload={"pr_number": 1, "reverted_by_pr": 2},
        ),
        # Duplicate revert event should not apply penalty twice
        ContributionEvent(
            github_user="alice",
            event_type="pr_reverted",
            repo="test",
            created_at=period_end - timedelta(days=5),
            payload={"pr_number": 1, "reverted_by_pr": 2},
        ),
    ]
    scores = strategy.compute_scores(events, period_end)
    assert len(scores) == 1
    assert scores[0].github_user == "alice"
    # Base score (10) + penalty (-8) = 2
    assert scores[0].points == 2


def test_failed_ci_merge_penalty_applied() -> None:
    """Failed CI merge → penalty applied."""
    strategy = WeightedScoreStrategy(
        weights={"pr_merged": 10},
        period_days=30,
        quality_adjustments={"penalties": {"failed_ci_merge": -5}, "bonuses": {}},
    )
    period_end = datetime.now(timezone.utc)
    events = [
        ContributionEvent(
            github_user="alice",
            event_type="pr_merged",
            repo="test",
            created_at=period_end - timedelta(days=1),
            payload={"pr_number": 1},
        ),
        ContributionEvent(
            github_user="alice",
            event_type="pr_merged_with_failed_ci",
            repo="test",
            created_at=period_end - timedelta(days=1),
            payload={"pr_number": 1},
        ),
    ]
    scores = strategy.compute_scores(events, period_end)
    assert len(scores) == 1
    assert scores[0].github_user == "alice"
    # Base score (10) + penalty (-5) = 5
    assert scores[0].points == 5


def test_pr_review_bonus_applied() -> None:
    """PR review bonus applied for APPROVED reviews."""
    strategy = WeightedScoreStrategy(
        weights={"pr_reviewed": 2},
        period_days=30,
        quality_adjustments={"penalties": {}, "bonuses": {"pr_review": 2}},
    )
    period_end = datetime.now(timezone.utc)
    events = [
        ContributionEvent(
            github_user="bob",
            event_type="pr_reviewed",
            repo="test",
            created_at=period_end - timedelta(days=1),
            payload={"pr_number": 1, "state": "APPROVED"},
        ),
    ]
    scores = strategy.compute_scores(events, period_end)
    assert len(scores) == 1
    assert scores[0].github_user == "bob"
    # Merge-only scoring: pr_reviewed has no base score, only bonus (2)
    assert scores[0].points == 2


def test_pr_review_bonus_not_applied_for_non_approved() -> None:
    """PR review bonus only for APPROVED state."""
    strategy = WeightedScoreStrategy(
        weights={"pr_reviewed": 2},
        period_days=30,
        quality_adjustments={"penalties": {}, "bonuses": {"pr_review": 2}},
    )
    period_end = datetime.now(timezone.utc)
    events = [
        ContributionEvent(
            github_user="bob",
            event_type="pr_reviewed",
            repo="test",
            created_at=period_end - timedelta(days=1),
            payload={"pr_number": 1, "state": "CHANGES_REQUESTED"},
        ),
    ]
    scores = strategy.compute_scores(events, period_end)
    # Merge-only scoring: pr_reviewed has no base score, and no bonus (not APPROVED)
    # Users with 0 score don't appear in scores list
    assert len(scores) == 0


def test_multiple_pr_reviews_bonuses_added() -> None:
    """Multiple PR reviews → bonuses added per review."""
    strategy = WeightedScoreStrategy(
        weights={"pr_reviewed": 2},
        period_days=30,
        quality_adjustments={"penalties": {}, "bonuses": {"pr_review": 2}},
    )
    period_end = datetime.now(timezone.utc)
    events = [
        ContributionEvent(
            github_user="bob",
            event_type="pr_reviewed",
            repo="test",
            created_at=period_end - timedelta(days=2),
            payload={"pr_number": 1, "state": "APPROVED"},
        ),
        ContributionEvent(
            github_user="bob",
            event_type="pr_reviewed",
            repo="test",
            created_at=period_end - timedelta(days=1),
            payload={"pr_number": 2, "state": "APPROVED"},
        ),
    ]
    scores = strategy.compute_scores(events, period_end)
    assert len(scores) == 1
    assert scores[0].github_user == "bob"
    # Merge-only scoring: pr_reviewed has no base score, only bonuses (2 + 2) = 4
    assert scores[0].points == 4


def test_helpful_comments_capped() -> None:
    """Helpful comments bonus capped at 5 per PR/issue."""
    strategy = WeightedScoreStrategy(
        weights={"helpful_comment": 0},
        period_days=30,
        quality_adjustments={"penalties": {}, "bonuses": {"helpful_comment": 1}},
    )
    period_end = datetime.now(timezone.utc)
    # Create 7 helpful comments on same PR
    events = [
        ContributionEvent(
            github_user="charlie",
            event_type="helpful_comment",
            repo="test",
            created_at=period_end - timedelta(days=1),
            payload={"pr_number": 1, "target_type": "pull_request"},
        )
        for _ in range(7)
    ]
    scores = strategy.compute_scores(events, period_end)
    assert len(scores) == 1
    assert scores[0].github_user == "charlie"
    # Only 5 bonuses applied (cap)
    assert scores[0].points == 5


def test_helpful_comments_capped_per_target() -> None:
    """Helpful comments cap applies per PR/issue, not globally."""
    strategy = WeightedScoreStrategy(
        weights={"helpful_comment": 0},
        period_days=30,
        quality_adjustments={"penalties": {}, "bonuses": {"helpful_comment": 1}},
    )
    period_end = datetime.now(timezone.utc)
    # 5 comments on PR 1, 5 comments on PR 2
    events = [
        ContributionEvent(
            github_user="charlie",
            event_type="helpful_comment",
            repo="test",
            created_at=period_end - timedelta(days=1),
            payload={"pr_number": i, "target_type": "pull_request"},
        )
        for i in [1, 2]
        for _ in range(5)
    ]
    scores = strategy.compute_scores(events, period_end)
    assert len(scores) == 1
    assert scores[0].github_user == "charlie"
    # 5 bonuses for PR 1 + 5 bonuses for PR 2 = 10
    assert scores[0].points == 10


def test_deterministic_output() -> None:
    """Scoring output is deterministic (order-independent)."""
    strategy = WeightedScoreStrategy(
        weights={"pr_merged": 10, "pr_reviewed": 2},
        period_days=30,
        quality_adjustments={"penalties": {"reverted_pr": -8}, "bonuses": {"pr_review": 2}},
    )
    period_end = datetime.now(timezone.utc)
    events = [
        ContributionEvent(
            github_user="alice",
            event_type="pr_merged",
            repo="test",
            created_at=period_end - timedelta(days=3),
            payload={"pr_number": 1},
        ),
        ContributionEvent(
            github_user="alice",
            event_type="pr_reverted",
            repo="test",
            created_at=period_end - timedelta(days=3),
            payload={"pr_number": 1},
        ),
        ContributionEvent(
            github_user="bob",
            event_type="pr_reviewed",
            repo="test",
            created_at=period_end - timedelta(days=1),
            payload={"pr_number": 2, "state": "APPROVED"},
        ),
    ]
    scores1 = strategy.compute_scores(events, period_end)
    # Reverse order
    scores2 = strategy.compute_scores(list(reversed(events)), period_end)
    assert len(scores1) == len(scores2) == 2
    scores1_dict = {s.github_user: s.points for s in scores1}
    scores2_dict = {s.github_user: s.points for s in scores2}
    assert scores1_dict == scores2_dict


def test_combined_adjustments() -> None:
    """Penalties and bonuses combine correctly."""
    strategy = WeightedScoreStrategy(
        weights={"pr_merged": 10, "pr_reviewed": 2, "helpful_comment": 0},
        period_days=30,
        quality_adjustments={
            "penalties": {"reverted_pr": -8, "failed_ci_merge": -5},
            "bonuses": {"pr_review": 2, "helpful_comment": 1},
        },
    )
    period_end = datetime.now(timezone.utc)
    events = [
        ContributionEvent(
            github_user="alice",
            event_type="pr_merged",
            repo="test",
            created_at=period_end - timedelta(days=2),
            payload={"pr_number": 1},
        ),
        ContributionEvent(
            github_user="alice",
            event_type="pr_merged_with_failed_ci",
            repo="test",
            created_at=period_end - timedelta(days=2),
            payload={"pr_number": 1},
        ),
        ContributionEvent(
            github_user="bob",
            event_type="pr_reviewed",
            repo="test",
            created_at=period_end - timedelta(days=1),
            payload={"pr_number": 1, "state": "APPROVED"},
        ),
        ContributionEvent(
            github_user="charlie",
            event_type="helpful_comment",
            repo="test",
            created_at=period_end - timedelta(days=1),
            payload={"pr_number": 1, "target_type": "pull_request"},
        ),
    ]
    scores = strategy.compute_scores(events, period_end)
    scores_dict = {s.github_user: s.points for s in scores}
    # alice: base (10) + CI penalty (-5) = 5
    assert scores_dict["alice"] == 5
    # bob: merge-only scoring - pr_reviewed has no base score, only bonus (2) = 2
    assert scores_dict["bob"] == 2
    # charlie: merge-only scoring - helpful_comment has no base score, only bonus (1) = 1
    assert scores_dict["charlie"] == 1


def test_quality_adjustments_config_validation() -> None:
    """QualityAdjustmentsConfig validates correctly."""
    # Valid config
    qa = QualityAdjustmentsConfig(
        penalties={"reverted_pr": -8, "failed_ci_merge": -5},
        bonuses={"pr_review": 2, "helpful_comment": 1},
    )
    assert qa.penalties == {"reverted_pr": -8, "failed_ci_merge": -5}
    assert qa.bonuses == {"pr_review": 2, "helpful_comment": 1}
    
    # Empty config (defaults)
    qa_empty = QualityAdjustmentsConfig()
    assert qa_empty.penalties == {}
    assert qa_empty.bonuses == {}


def test_scoring_config_with_quality_adjustments() -> None:
    """ScoringConfig accepts optional quality_adjustments."""
    config = ScoringConfig(
        period_days=30,
        weights={"pr_merged": 10},
        quality_adjustments=QualityAdjustmentsConfig(
            penalties={"reverted_pr": -8},
            bonuses={"pr_review": 2},
        ),
    )
    assert config.quality_adjustments is not None
    assert config.quality_adjustments.penalties == {"reverted_pr": -8}
    assert config.quality_adjustments.bonuses == {"pr_review": 2}
    
    # Config without quality_adjustments (backward compatible)
    config_no_qa = ScoringConfig(
        period_days=30,
        weights={"pr_merged": 10},
    )
    assert config_no_qa.quality_adjustments is None
