"""Tests for PR health status dashboard feature."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock

import pytest

from ghdcbot.engine.pr_status import (
    PR_STATUS_MAX_PRS,
    PRHealthStatus,
    _compute_ci_status,
    _is_coderabbit_bot,
    _split_messages,
    _truncate,
    fetch_all_open_pr_health,
    fetch_pr_health,
    format_all_pr_status,
    format_single_pr_status,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _make_pr_data(
    *,
    title: str = "Test PR",
    state: str = "open",
    draft: bool = False,
    mergeable: bool | None = True,
    author: str = "contributor",
    number: int = 7,
    repo: str = "my-repo",
    head_sha: str = "abc123",
) -> dict:
    return {
        "title": title,
        "state": state,
        "draft": draft,
        "mergeable": mergeable,
        "user": {"login": author},
        "assignees": [],
        "requested_reviewers": [],
        "created_at": "2025-01-01T00:00:00Z",
        "html_url": f"https://github.com/org/{repo}/pull/{number}",
        "head": {"sha": head_sha},
    }


def _make_health(
    *,
    ci_status: str = "passing",
    mergeable: bool | None = True,
    review_state: str = "approved",
    approved_count: int = 1,
    changes_requested_count: int = 0,
    has_coderabbit_comments: bool = False,
    coderabbit_comment_count: int = 0,
    is_draft: bool = False,
    repo: str = "my-repo",
    number: int = 7,
    title: str = "Test PR",
    author: str = "contributor",
) -> PRHealthStatus:
    return PRHealthStatus(
        repo=repo,
        number=number,
        title=title,
        author=author,
        html_url=f"https://github.com/org/{repo}/pull/{number}",
        ci_status=ci_status,
        mergeable=mergeable,
        review_state=review_state,
        approved_count=approved_count,
        changes_requested_count=changes_requested_count,
        has_coderabbit_comments=has_coderabbit_comments,
        coderabbit_comment_count=coderabbit_comment_count,
        is_draft=is_draft,
    )


# ===================================================================
# Health indicator logic
# ===================================================================


class TestHealthIndicator:
    def test_safe_to_merge(self) -> None:
        """Approved + CI passing + mergeable + no CodeRabbit = safe."""
        h = _make_health(
            ci_status="passing",
            mergeable=True,
            review_state="approved",
            has_coderabbit_comments=False,
        )
        assert h.health_indicator == "safe_to_merge"

    def test_safe_to_merge_ci_unknown(self) -> None:
        """Approved + CI unknown + mergeable = still safe."""
        h = _make_health(ci_status="unknown", mergeable=True, review_state="approved")
        assert h.health_indicator == "safe_to_merge"

    def test_blocked_ci_failing(self) -> None:
        """CI failing → blocked."""
        h = _make_health(ci_status="failing")
        assert h.health_indicator == "blocked"

    def test_blocked_merge_conflict(self) -> None:
        """mergeable=False → blocked."""
        h = _make_health(mergeable=False)
        assert h.health_indicator == "blocked"

    def test_blocked_changes_requested(self) -> None:
        """Changes requested → blocked."""
        h = _make_health(
            review_state="changes_requested",
            changes_requested_count=1,
        )
        assert h.health_indicator == "blocked"

    def test_needs_attention_coderabbit(self) -> None:
        """Approved but has CodeRabbit comments → needs attention."""
        h = _make_health(
            review_state="approved",
            has_coderabbit_comments=True,
            coderabbit_comment_count=3,
        )
        assert h.health_indicator == "needs_attention"

    def test_needs_attention_awaiting_review(self) -> None:
        """No reviews → awaiting review → needs attention."""
        h = _make_health(
            review_state="awaiting_review",
            approved_count=0,
        )
        assert h.health_indicator == "needs_attention"

    def test_needs_attention_ci_pending(self) -> None:
        """CI pending → needs attention (not blocked)."""
        h = _make_health(ci_status="pending", review_state="approved")
        assert h.health_indicator == "needs_attention"

    def test_draft(self) -> None:
        """Draft PR → draft indicator."""
        h = _make_health(is_draft=True)
        assert h.health_indicator == "draft"

    def test_draft_overrides_blocking(self) -> None:
        """Draft takes precedence over CI failure."""
        h = _make_health(is_draft=True, ci_status="failing")
        assert h.health_indicator == "draft"


# ===================================================================
# CI status computation
# ===================================================================


class TestComputeCIStatus:
    def test_empty_check_runs(self) -> None:
        assert _compute_ci_status([]) == "unknown"

    def test_failure(self) -> None:
        runs = [{"status": "completed", "conclusion": "failure"}]
        assert _compute_ci_status(runs) == "failing"

    def test_success(self) -> None:
        runs = [{"status": "completed", "conclusion": "success"}]
        assert _compute_ci_status(runs) == "passing"

    def test_pending(self) -> None:
        runs = [{"status": "in_progress", "conclusion": None}]
        assert _compute_ci_status(runs) == "pending"

    def test_queued(self) -> None:
        runs = [{"status": "queued", "conclusion": None}]
        assert _compute_ci_status(runs) == "pending"

    def test_mixed_failure_wins(self) -> None:
        runs = [
            {"status": "completed", "conclusion": "success"},
            {"status": "completed", "conclusion": "failure"},
        ]
        assert _compute_ci_status(runs) == "failing"


# ===================================================================
# CodeRabbit detection
# ===================================================================


class TestCodeRabbitDetection:
    def test_matches_coderabbitai(self) -> None:
        comment = {"user": {"login": "coderabbitai"}}
        assert _is_coderabbit_bot(comment, ["coderabbitai"]) is True

    def test_matches_coderabbitai_bot(self) -> None:
        comment = {"user": {"login": "coderabbitai[bot]"}}
        assert _is_coderabbit_bot(comment, ["coderabbitai[bot]"]) is True

    def test_case_insensitive(self) -> None:
        comment = {"user": {"login": "CodeRabbitAI"}}
        assert _is_coderabbit_bot(comment, ["coderabbitai"]) is True

    def test_non_matching_user(self) -> None:
        comment = {"user": {"login": "contributor123"}}
        assert _is_coderabbit_bot(comment, ["coderabbitai"]) is False

    def test_missing_user(self) -> None:
        comment = {}
        assert _is_coderabbit_bot(comment, ["coderabbitai"]) is False

    def test_empty_login(self) -> None:
        comment = {"user": {"login": ""}}
        assert _is_coderabbit_bot(comment, ["coderabbitai"]) is False

    def test_active_comment_on_head(self) -> None:
        from ghdcbot.engine.pr_status import _is_active_coderabbit_comment
        comment = {
            "user": {"login": "coderabbitai[bot]"},
            "position": 5,
            "original_position": 5,
            "commit_id": "abc123",
            "body": "Consider refactoring this.",
        }
        assert _is_active_coderabbit_comment(comment, ["coderabbitai[bot]"], head_sha="abc123") is True

    def test_outdated_diff_not_active(self) -> None:
        from ghdcbot.engine.pr_status import _is_active_coderabbit_comment
        comment = {
            "user": {"login": "coderabbitai[bot]"},
            "position": None,  # Outdated diff on GitHub
            "original_position": 5,
            "commit_id": "abc123",
            "body": "Consider refactoring this.",
        }
        assert _is_active_coderabbit_comment(comment, ["coderabbitai[bot]"], head_sha="abc123") is False

    def test_superseded_commit_not_active(self) -> None:
        from ghdcbot.engine.pr_status import _is_active_coderabbit_comment
        comment = {
            "user": {"login": "coderabbitai[bot]"},
            "position": 5,
            "commit_id": "old_sha",
            "body": "Consider refactoring this.",
        }
        assert _is_active_coderabbit_comment(comment, ["coderabbitai[bot]"], head_sha="new_head_sha") is False

    def test_resolved_checkbox_not_active(self) -> None:
        from ghdcbot.engine.pr_status import _is_active_coderabbit_comment
        comment = {
            "user": {"login": "coderabbitai[bot]"},
            "position": 5,
            "commit_id": "abc123",
            "body": "- [x] Resolved this change",
        }
        assert _is_active_coderabbit_comment(comment, ["coderabbitai[bot]"], head_sha="abc123") is False


# ===================================================================
# fetch_pr_health
# ===================================================================


class TestFetchPRHealth:
    def test_success(self) -> None:
        """Happy path: fetch health for an open, mergeable, CI-passing PR."""
        adapter = MagicMock()
        adapter.get_pull_request.return_value = _make_pr_data()
        adapter.get_pull_request_reviews.return_value = [{"state": "APPROVED"}]
        adapter.get_pull_request_check_runs.return_value = [
            {"status": "completed", "conclusion": "success"}
        ]
        adapter.get_pull_request_review_comments.return_value = []

        health = fetch_pr_health(adapter, "org", "my-repo", 7)

        assert health is not None
        assert health.repo == "my-repo"
        assert health.number == 7
        assert health.ci_status == "passing"
        assert health.mergeable is True
        assert health.review_state == "approved"
        assert health.approved_count == 1
        assert health.has_coderabbit_comments is False
        assert health.health_indicator == "safe_to_merge"

    def test_not_found(self) -> None:
        """PR not found returns None."""
        adapter = MagicMock()
        adapter.get_pull_request.return_value = None

        health = fetch_pr_health(adapter, "org", "my-repo", 999)
        assert health is None

    def test_ci_failing(self) -> None:
        adapter = MagicMock()
        adapter.get_pull_request.return_value = _make_pr_data()
        adapter.get_pull_request_reviews.return_value = []
        adapter.get_pull_request_check_runs.return_value = [
            {"status": "completed", "conclusion": "failure"}
        ]
        adapter.get_pull_request_review_comments.return_value = []

        health = fetch_pr_health(adapter, "org", "my-repo", 7)
        assert health is not None
        assert health.ci_status == "failing"
        assert health.health_indicator == "blocked"

    def test_coderabbit_comments_detected(self) -> None:
        adapter = MagicMock()
        adapter.get_pull_request.return_value = _make_pr_data()
        adapter.get_pull_request_reviews.return_value = [{"state": "APPROVED"}]
        adapter.get_pull_request_check_runs.return_value = [
            {"status": "completed", "conclusion": "success"}
        ]
        adapter.get_pull_request_review_comments.return_value = [
            {"user": {"login": "coderabbitai"}, "created_at": "2025-01-01T00:00:00Z"},
            {"user": {"login": "coderabbitai[bot]"}, "created_at": "2025-01-01T00:00:00Z"},
            {"user": {"login": "human-reviewer"}, "created_at": "2025-01-01T00:00:00Z"},
        ]

        health = fetch_pr_health(adapter, "org", "my-repo", 7)
        assert health is not None
        assert health.has_coderabbit_comments is True
        assert health.coderabbit_comment_count == 2
        assert health.health_indicator == "needs_attention"

    def test_custom_coderabbit_logins(self) -> None:
        """Custom bot logins are respected."""
        adapter = MagicMock()
        adapter.get_pull_request.return_value = _make_pr_data()
        adapter.get_pull_request_reviews.return_value = [{"state": "APPROVED"}]
        adapter.get_pull_request_check_runs.return_value = []
        adapter.get_pull_request_review_comments.return_value = [
            {"user": {"login": "mybot"}},
        ]

        health = fetch_pr_health(
            adapter, "org", "my-repo", 7, coderabbit_bot_logins=["mybot"]
        )
        assert health is not None
        assert health.coderabbit_comment_count == 1

    def test_merge_conflict(self) -> None:
        adapter = MagicMock()
        adapter.get_pull_request.return_value = _make_pr_data(mergeable=False)
        adapter.get_pull_request_reviews.return_value = [{"state": "APPROVED"}]
        adapter.get_pull_request_check_runs.return_value = [
            {"status": "completed", "conclusion": "success"}
        ]
        adapter.get_pull_request_review_comments.return_value = []

        health = fetch_pr_health(adapter, "org", "my-repo", 7)
        assert health is not None
        assert health.mergeable is False
        assert health.health_indicator == "blocked"

    def test_draft_pr(self) -> None:
        adapter = MagicMock()
        adapter.get_pull_request.return_value = _make_pr_data(draft=True)
        adapter.get_pull_request_reviews.return_value = []
        adapter.get_pull_request_check_runs.return_value = []
        adapter.get_pull_request_review_comments.return_value = []

        health = fetch_pr_health(adapter, "org", "my-repo", 7)
        assert health is not None
        assert health.is_draft is True
        assert health.health_indicator == "draft"

    def test_changes_requested(self) -> None:
        adapter = MagicMock()
        adapter.get_pull_request.return_value = _make_pr_data()
        adapter.get_pull_request_reviews.return_value = [
            {"state": "CHANGES_REQUESTED"},
        ]
        adapter.get_pull_request_check_runs.return_value = []
        adapter.get_pull_request_review_comments.return_value = []

        health = fetch_pr_health(adapter, "org", "my-repo", 7)
        assert health is not None
        assert health.review_state == "changes_requested"
        assert health.changes_requested_count == 1
        assert health.health_indicator == "blocked"

    def test_no_head_sha(self) -> None:
        """PR without head SHA: CI stays unknown."""
        adapter = MagicMock()
        pr_data = _make_pr_data()
        pr_data["head"] = {}
        adapter.get_pull_request.return_value = pr_data
        adapter.get_pull_request_reviews.return_value = [{"state": "APPROVED"}]
        adapter.get_pull_request_review_comments.return_value = []

        health = fetch_pr_health(adapter, "org", "my-repo", 7)
        assert health is not None
        assert health.ci_status == "unknown"
        # Should not call check_runs if no sha
        adapter.get_pull_request_check_runs.assert_not_called()

    def test_review_comments_fetch_failure_non_fatal(self) -> None:
        """If fetching review comments fails, CodeRabbit count is 0."""
        adapter = MagicMock()
        adapter.get_pull_request.return_value = _make_pr_data()
        adapter.get_pull_request_reviews.return_value = [{"state": "APPROVED"}]
        adapter.get_pull_request_check_runs.return_value = [
            {"status": "completed", "conclusion": "success"}
        ]
        adapter.get_pull_request_review_comments.side_effect = RuntimeError("API error")

        health = fetch_pr_health(adapter, "org", "my-repo", 7)
        assert health is not None
        assert health.has_coderabbit_comments is False
        assert health.coderabbit_comment_count == 0


# ===================================================================
# fetch_all_open_pr_health
# ===================================================================


class TestFetchAllOpenPRHealth:
    def test_pagination_skip(self) -> None:
        """skip parameter skips the first N PRs."""
        adapter = MagicMock()
        adapter.list_open_pull_requests.return_value = [
            {"repo": "a", "number": 1},
            {"repo": "b", "number": 2},
            {"repo": "c", "number": 3},
        ]
        adapter.get_pull_request.return_value = _make_pr_data()
        adapter.get_pull_request_reviews.return_value = []
        adapter.get_pull_request_check_runs.return_value = []
        adapter.get_pull_request_review_comments.return_value = []

        results, total = fetch_all_open_pr_health(adapter, "org", max_prs=2, skip=1)
        assert total == 3
        assert len(results) == 2  # items at index 1 and 2

    def test_max_prs_cap(self) -> None:
        """At most max_prs are returned."""
        adapter = MagicMock()
        prs = [{"repo": f"repo-{i}", "number": i} for i in range(10)]
        adapter.list_open_pull_requests.return_value = prs
        adapter.get_pull_request.return_value = _make_pr_data()
        adapter.get_pull_request_reviews.return_value = []
        adapter.get_pull_request_check_runs.return_value = []
        adapter.get_pull_request_review_comments.return_value = []

        results, total = fetch_all_open_pr_health(adapter, "org", max_prs=3, skip=0)
        assert total == 10
        assert len(results) == 3

    def test_empty_org(self) -> None:
        adapter = MagicMock()
        adapter.list_open_pull_requests.return_value = []

        results, total = fetch_all_open_pr_health(adapter, "org")
        assert total == 0
        assert results == []

    def test_individual_pr_failure_skipped(self) -> None:
        """If one PR fails, others still succeed."""
        adapter = MagicMock()
        adapter.list_open_pull_requests.return_value = [
            {"repo": "a", "number": 1},
            {"repo": "b", "number": 2},
        ]

        call_count = 0
        def side_effect(owner, repo, pr_number):
            nonlocal call_count
            call_count += 1
            if repo == "a":
                raise RuntimeError("API error")
            return _make_pr_data(repo=repo, number=pr_number)

        adapter.get_pull_request.side_effect = side_effect
        adapter.get_pull_request_reviews.return_value = []
        adapter.get_pull_request_check_runs.return_value = []
        adapter.get_pull_request_review_comments.return_value = []

        results, total = fetch_all_open_pr_health(adapter, "org")
        assert total == 2
        assert len(results) == 1
        assert results[0].repo == "b"


# ===================================================================
# Format: single PR
# ===================================================================


class TestFormatSinglePRStatus:
    def test_safe_to_merge_format(self) -> None:
        h = _make_health(
            ci_status="passing",
            mergeable=True,
            review_state="approved",
            approved_count=2,
            has_coderabbit_comments=False,
        )
        result = format_single_pr_status(h, "org")

        assert "my-repo#7" in result
        assert "Test PR" in result
        assert "contributor" in result
        assert "Passing" in result
        assert "No pending suggestions" in result
        assert "Mergeable" in result
        assert "Approved" in result
        assert "Safe to Merge" in result

    def test_blocked_format(self) -> None:
        h = _make_health(
            ci_status="failing",
            mergeable=False,
            review_state="changes_requested",
            changes_requested_count=1,
            has_coderabbit_comments=True,
            coderabbit_comment_count=3,
        )
        result = format_single_pr_status(h, "org")

        assert "Failing" in result
        assert "3 suggestions pending" in result
        assert "Merge Conflicts" in result
        assert "Changes Requested" in result
        assert "Blocked" in result

    def test_draft_format(self) -> None:
        h = _make_health(is_draft=True)
        result = format_single_pr_status(h, "org")
        assert "**Draft:** Yes" in result
        assert "⬜ Draft" in result

    def test_coderabbit_singular(self) -> None:
        """Single CodeRabbit comment uses singular form."""
        h = _make_health(
            has_coderabbit_comments=True,
            coderabbit_comment_count=1,
        )
        result = format_single_pr_status(h, "org")
        assert "1 suggestion pending" in result
        assert "suggestions" not in result


# ===================================================================
# Format: all PRs
# ===================================================================


class TestFormatAllPRStatus:
    def test_empty_no_prs(self) -> None:
        msgs = format_all_pr_status([], "org", skip=0, total=0)
        assert len(msgs) == 1
        assert "No open PRs" in msgs[0]

    def test_empty_out_of_range(self) -> None:
        msgs = format_all_pr_status([], "org", skip=50, total=30)
        assert len(msgs) == 1
        assert "No PRs in range" in msgs[0]

    def test_sorting_priority(self) -> None:
        """Blocked → needs_attention → safe_to_merge → draft."""
        statuses = [
            _make_health(repo="draft-repo", number=1, is_draft=True),
            _make_health(repo="safe-repo", number=2, review_state="approved"),
            _make_health(repo="blocked-repo", number=3, ci_status="failing"),
            _make_health(
                repo="attention-repo",
                number=4,
                review_state="awaiting_review",
                approved_count=0,
            ),
        ]
        msgs = format_all_pr_status(statuses, "org", skip=0, total=4)
        combined = "\n".join(msgs)

        # Find positions: blocked < attention < safe < draft
        blocked_pos = combined.index("blocked-repo#3")
        attention_pos = combined.index("attention-repo#4")
        safe_pos = combined.index("safe-repo#2")
        draft_pos = combined.index("draft-repo#1")

        assert blocked_pos < attention_pos < safe_pos < draft_pos

    def test_summary_counts(self) -> None:
        statuses = [
            _make_health(repo="a", number=1, ci_status="failing"),
            _make_health(repo="b", number=2, ci_status="failing"),
            _make_health(repo="c", number=3, review_state="approved"),
        ]
        msgs = format_all_pr_status(statuses, "org", skip=0, total=3)
        combined = "\n".join(msgs)

        assert "2 blocked" in combined
        assert "1 safe to merge" in combined

    def test_pagination_footer(self) -> None:
        statuses = [_make_health(repo="a", number=1)]
        msgs = format_all_pr_status(statuses, "org", skip=0, total=30)
        combined = "\n".join(msgs)

        assert "29 more" in combined
        assert "skip:1" in combined

    def test_no_pagination_footer_when_all_shown(self) -> None:
        statuses = [_make_health(repo="a", number=1)]
        msgs = format_all_pr_status(statuses, "org", skip=0, total=1)
        combined = "\n".join(msgs)

        assert "more" not in combined


# ===================================================================
# Internal helper tests
# ===================================================================


class TestTruncate:
    def test_short_text(self) -> None:
        assert _truncate("hello", 10) == "hello"

    def test_long_text(self) -> None:
        assert _truncate("hello world", 8) == "hello..."

    def test_exact_length(self) -> None:
        assert _truncate("hello", 5) == "hello"


class TestSplitMessages:
    def test_single_message(self) -> None:
        msgs = _split_messages("Header", ["line1", "line2"], [], max_length=2000)
        assert len(msgs) == 1
        assert "Header" in msgs[0]
        assert "line1" in msgs[0]

    def test_splits_long_content(self) -> None:
        header = "H" * 10
        lines = ["L" * 50 for _ in range(50)]
        msgs = _split_messages(header, lines, [], max_length=200)
        assert len(msgs) > 1
        for msg in msgs:
            assert len(msg) <= 200

    def test_footer_included(self) -> None:
        msgs = _split_messages("Header", ["line1"], ["footer"], max_length=2000)
        assert len(msgs) == 1
        assert "footer" in msgs[0]
