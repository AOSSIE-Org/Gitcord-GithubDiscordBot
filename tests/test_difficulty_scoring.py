"""Tests for issue difficulty-aware scoring."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from ghdcbot.adapters.storage.sqlite import SqliteStorage
from ghdcbot.core.models import ContributionEvent
from ghdcbot.engine.scoring import WeightedScoreStrategy


def test_extract_linked_issue_numbers() -> None:
    """Test extraction of issue numbers from PR body."""
    from ghdcbot.adapters.github.rest import _extract_linked_issue_numbers
    
    # Test closes/fixes/resolves patterns
    assert _extract_linked_issue_numbers("closes #123") == [123]
    assert _extract_linked_issue_numbers("fixes #456") == [456]
    assert _extract_linked_issue_numbers("resolves #789") == [789]
    assert _extract_linked_issue_numbers("Closes #123") == [123]  # Case insensitive
    assert _extract_linked_issue_numbers("Fixes #456") == [456]
    
    # Test multiple issues
    assert _extract_linked_issue_numbers("closes #123 and fixes #456") == [123, 456]
    
    # Test just #number
    assert _extract_linked_issue_numbers("See #123") == [123]
    
    # Test empty
    assert _extract_linked_issue_numbers("") == []
    assert _extract_linked_issue_numbers("No issue here") == []
    
    # Test deduplication
    assert _extract_linked_issue_numbers("closes #123 and #123") == [123]


def test_scoring_uses_difficulty_label_good_first_issue(tmp_path: Path) -> None:
    storage = SqliteStorage(data_dir=str(tmp_path))
    storage.init_schema()
    # Create a merged PR with difficulty label
    event = ContributionEvent(
        github_user="alice",
        event_type="pr_merged",
        repo="test-repo",
        created_at=datetime.now(timezone.utc),
        payload={
            "pr_number": 1,
            "title": "Fix bug",
            "merged_at": datetime.now(timezone.utc).isoformat(),
            "difficulty_labels": ["good first issue"],
        },
    )
    storage.record_contributions([event])
    difficulty_weights = {"good first issue": 3, "easy": 5, "medium": 8, "hard": 13}
    strategy = WeightedScoreStrategy(
        weights={"pr_merged": 5},
        period_days=30,
        difficulty_weights=difficulty_weights,
    )
    period_end = datetime.now(timezone.utc)
    scores = strategy.compute_scores(storage.list_contributions(period_end - strategy._period), period_end)
    assert len(scores) == 1
    assert scores[0].github_user == "alice"
    assert scores[0].points == 3  # Should use "good first issue" weight, not default pr_merged weight


def test_scoring_uses_difficulty_label_easy(tmp_path: Path) -> None:
    storage = SqliteStorage(data_dir=str(tmp_path))
    storage.init_schema()
    event = ContributionEvent(
        github_user="bob",
        event_type="pr_merged",
        repo="test-repo",
        created_at=datetime.now(timezone.utc),
        payload={
            "pr_number": 2,
            "difficulty_labels": ["easy"],
        },
    )
    storage.record_contributions([event])
    difficulty_weights = {"good first issue": 3, "easy": 5, "medium": 8, "hard": 13}
    strategy = WeightedScoreStrategy(
        weights={"pr_merged": 5},
        period_days=30,
        difficulty_weights=difficulty_weights,
    )
    period_end = datetime.now(timezone.utc)
    scores = strategy.compute_scores(storage.list_contributions(period_end - strategy._period), period_end)
    assert scores[0].points == 5


def test_scoring_uses_difficulty_label_medium(tmp_path: Path) -> None:
    storage = SqliteStorage(data_dir=str(tmp_path))
    storage.init_schema()
    event = ContributionEvent(
        github_user="charlie",
        event_type="pr_merged",
        repo="test-repo",
        created_at=datetime.now(timezone.utc),
        payload={
            "pr_number": 3,
            "difficulty_labels": ["medium"],
        },
    )
    storage.record_contributions([event])
    difficulty_weights = {"good first issue": 3, "easy": 5, "medium": 8, "hard": 13}
    strategy = WeightedScoreStrategy(
        weights={"pr_merged": 5},
        period_days=30,
        difficulty_weights=difficulty_weights,
    )
    period_end = datetime.now(timezone.utc)
    scores = strategy.compute_scores(storage.list_contributions(period_end - strategy._period), period_end)
    assert scores[0].points == 8


def test_scoring_uses_difficulty_label_hard(tmp_path: Path) -> None:
    storage = SqliteStorage(data_dir=str(tmp_path))
    storage.init_schema()
    event = ContributionEvent(
        github_user="dave",
        event_type="pr_merged",
        repo="test-repo",
        created_at=datetime.now(timezone.utc),
        payload={
            "pr_number": 4,
            "difficulty_labels": ["hard"],
        },
    )
    storage.record_contributions([event])
    difficulty_weights = {"good first issue": 3, "easy": 5, "medium": 8, "hard": 13}
    strategy = WeightedScoreStrategy(
        weights={"pr_merged": 5},
        period_days=30,
        difficulty_weights=difficulty_weights,
    )
    period_end = datetime.now(timezone.utc)
    scores = strategy.compute_scores(storage.list_contributions(period_end - strategy._period), period_end)
    assert scores[0].points == 13


def test_scoring_uses_max_weight_for_multiple_labels(tmp_path: Path) -> None:
    storage = SqliteStorage(data_dir=str(tmp_path))
    storage.init_schema()
    event = ContributionEvent(
        github_user="eve",
        event_type="pr_merged",
        repo="test-repo",
        created_at=datetime.now(timezone.utc),
        payload={
            "pr_number": 5,
            "difficulty_labels": ["easy", "medium", "hard"],
        },
    )
    storage.record_contributions([event])
    difficulty_weights = {"good first issue": 3, "easy": 5, "medium": 8, "hard": 13}
    strategy = WeightedScoreStrategy(
        weights={"pr_merged": 5},
        period_days=30,
        difficulty_weights=difficulty_weights,
    )
    period_end = datetime.now(timezone.utc)
    scores = strategy.compute_scores(storage.list_contributions(period_end - strategy._period), period_end)
    assert scores[0].points == 13  # Should use max weight (hard)


def test_scoring_fallback_when_no_difficulty_label(tmp_path: Path) -> None:
    storage = SqliteStorage(data_dir=str(tmp_path))
    storage.init_schema()
    event = ContributionEvent(
        github_user="frank",
        event_type="pr_merged",
        repo="test-repo",
        created_at=datetime.now(timezone.utc),
        payload={
            "pr_number": 6,
            # No difficulty_labels
        },
    )
    storage.record_contributions([event])
    difficulty_weights = {"good first issue": 3, "easy": 5, "medium": 8, "hard": 13}
    strategy = WeightedScoreStrategy(
        weights={"pr_merged": 5},
        period_days=30,
        difficulty_weights=difficulty_weights,
    )
    period_end = datetime.now(timezone.utc)
    scores = strategy.compute_scores(storage.list_contributions(period_end - strategy._period), period_end)
    assert scores[0].points == 5  # Should use default pr_merged weight


def test_scoring_fallback_when_unknown_label(tmp_path: Path) -> None:
    storage = SqliteStorage(data_dir=str(tmp_path))
    storage.init_schema()
    event = ContributionEvent(
        github_user="grace",
        event_type="pr_merged",
        repo="test-repo",
        created_at=datetime.now(timezone.utc),
        payload={
            "pr_number": 7,
            "difficulty_labels": ["unknown-label"],
        },
    )
    storage.record_contributions([event])
    difficulty_weights = {"good first issue": 3, "easy": 5, "medium": 8, "hard": 13}
    strategy = WeightedScoreStrategy(
        weights={"pr_merged": 5},
        period_days=30,
        difficulty_weights=difficulty_weights,
    )
    period_end = datetime.now(timezone.utc)
    scores = strategy.compute_scores(storage.list_contributions(period_end - strategy._period), period_end)
    assert scores[0].points == 5  # Should fallback to default


def test_scoring_case_insensitive_label_matching(tmp_path: Path) -> None:
    storage = SqliteStorage(data_dir=str(tmp_path))
    storage.init_schema()
    event = ContributionEvent(
        github_user="henry",
        event_type="pr_merged",
        repo="test-repo",
        created_at=datetime.now(timezone.utc),
        payload={
            "pr_number": 8,
            "difficulty_labels": ["HARD", "Easy"],  # Mixed case
        },
    )
    storage.record_contributions([event])
    difficulty_weights = {"good first issue": 3, "easy": 5, "medium": 8, "hard": 13}
    strategy = WeightedScoreStrategy(
        weights={"pr_merged": 5},
        period_days=30,
        difficulty_weights=difficulty_weights,
    )
    period_end = datetime.now(timezone.utc)
    scores = strategy.compute_scores(storage.list_contributions(period_end - strategy._period), period_end)
    assert scores[0].points == 13  # Should match "HARD" -> "hard" and use max


def test_scoring_no_double_counting(tmp_path: Path) -> None:
    storage = SqliteStorage(data_dir=str(tmp_path))
    storage.init_schema()
    # Two merged PRs from same user
    events = [
        ContributionEvent(
            github_user="alice",
            event_type="pr_merged",
            repo="test-repo",
            created_at=datetime.now(timezone.utc),
            payload={"pr_number": 1, "difficulty_labels": ["easy"]},
        ),
        ContributionEvent(
            github_user="alice",
            event_type="pr_merged",
            repo="test-repo",
            created_at=datetime.now(timezone.utc),
            payload={"pr_number": 2, "difficulty_labels": ["medium"]},
        ),
    ]
    storage.record_contributions(events)
    difficulty_weights = {"easy": 5, "medium": 8}
    strategy = WeightedScoreStrategy(
        weights={"pr_merged": 5},
        period_days=30,
        difficulty_weights=difficulty_weights,
    )
    period_end = datetime.now(timezone.utc)
    scores = strategy.compute_scores(storage.list_contributions(period_end - strategy._period), period_end)
    assert len(scores) == 1
    assert scores[0].github_user == "alice"
    assert scores[0].points == 13  # 5 + 8 (both PRs counted)


def test_scoring_deterministic_ordering(tmp_path: Path) -> None:
    storage = SqliteStorage(data_dir=str(tmp_path))
    storage.init_schema()
    events = [
        ContributionEvent(
            github_user="alice",
            event_type="pr_merged",
            repo="test-repo",
            created_at=datetime.now(timezone.utc),
            payload={"pr_number": 1, "difficulty_labels": ["easy"]},
        ),
        ContributionEvent(
            github_user="bob",
            event_type="pr_merged",
            repo="test-repo",
            created_at=datetime.now(timezone.utc),
            payload={"pr_number": 2, "difficulty_labels": ["hard"]},
        ),
    ]
    storage.record_contributions(events)
    difficulty_weights = {"easy": 5, "hard": 13}
    strategy = WeightedScoreStrategy(
        weights={"pr_merged": 5},
        period_days=30,
        difficulty_weights=difficulty_weights,
    )
    period_end = datetime.now(timezone.utc)
    scores1 = strategy.compute_scores(storage.list_contributions(period_end - strategy._period), period_end)
    scores2 = strategy.compute_scores(storage.list_contributions(period_end - strategy._period), period_end)
    assert len(scores1) == len(scores2) == 2
    assert scores1[0].points == scores2[0].points
    assert scores1[1].points == scores2[1].points


def test_scoring_backward_compatible_no_difficulty_weights(tmp_path: Path) -> None:
    storage = SqliteStorage(data_dir=str(tmp_path))
    storage.init_schema()
    event = ContributionEvent(
        github_user="alice",
        event_type="pr_merged",
        repo="test-repo",
        created_at=datetime.now(timezone.utc),
        payload={"pr_number": 1},
    )
    storage.record_contributions([event])
    strategy = WeightedScoreStrategy(
        weights={"pr_merged": 5},
        period_days=30,
        difficulty_weights=None,  # Not configured
    )
    period_end = datetime.now(timezone.utc)
    scores = strategy.compute_scores(storage.list_contributions(period_end - strategy._period), period_end)
    assert scores[0].points == 5  # Should use default pr_merged weight


def test_list_contribution_summaries_uses_difficulty_labels(tmp_path: Path) -> None:
    storage = SqliteStorage(data_dir=str(tmp_path))
    storage.init_schema()
    import json
    # Insert event with difficulty labels
    now = datetime.now(timezone.utc)
    period_start = now.replace(day=1)  # Start of month
    with storage._connect() as conn:
        conn.execute(
            """
            INSERT INTO contributions (github_user, event_type, repo, created_at, payload_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "alice",
                "pr_merged",
                "test-repo",
                now.isoformat(),
                json.dumps({"pr_number": 1, "difficulty_labels": ["hard"]}),
            ),
        )
    difficulty_weights = {"hard": 13}
    summaries = storage.list_contribution_summaries(
        period_start,
        now,
        weights={"pr_merged": 5},
        difficulty_weights=difficulty_weights,
    )
    assert len(summaries) == 1
    assert summaries[0].github_user == "alice"
    assert summaries[0].total_score == 13  # Should use difficulty weight


def test_list_contribution_summaries_fallback_no_labels(tmp_path: Path) -> None:
    storage = SqliteStorage(data_dir=str(tmp_path))
    storage.init_schema()
    import json
    now = datetime.now(timezone.utc)
    period_start = now.replace(day=1)  # Start of month
    with storage._connect() as conn:
        conn.execute(
            """
            INSERT INTO contributions (github_user, event_type, repo, created_at, payload_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "bob",
                "pr_merged",
                "test-repo",
                now.isoformat(),
                json.dumps({"pr_number": 2}),  # No difficulty_labels
            ),
        )
    difficulty_weights = {"hard": 13}
    summaries = storage.list_contribution_summaries(
        period_start,
        now,
        weights={"pr_merged": 5},
        difficulty_weights=difficulty_weights,
    )
    assert summaries[0].total_score == 5  # Should use default weight
