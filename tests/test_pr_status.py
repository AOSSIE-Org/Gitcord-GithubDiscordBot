"""Tests for PR health status dashboard feature."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from ghdcbot.engine.pr_status import (
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
    html_url: str | None = None,
) -> PRHealthStatus:
    return PRHealthStatus(
        repo=repo,
        number=number,
        title=title,
        author=author,
        html_url=html_url or f"https://github.com/org/{repo}/pull/{number}",
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

    def test_review_threads_graphql_unresolved_coderabbit_counted(self) -> None:
        """Unresolved CodeRabbit threads are counted, while resolved/outdated/human threads are excluded, and REST fallback is not called."""
        adapter = MagicMock()
        adapter.get_pull_request.return_value = _make_pr_data()
        adapter.get_pull_request_reviews.return_value = [{"state": "APPROVED"}]
        adapter.get_pull_request_check_runs.return_value = [
            {"status": "completed", "conclusion": "success"}
        ]
        adapter.get_pull_request_review_threads.return_value = [
            {
                "is_resolved": False,
                "is_outdated": False,
                "authors": ["human-dev", "coderabbitai[bot]"],
            },
            {
                "is_resolved": True,
                "is_outdated": False,
                "authors": ["coderabbitai[bot]"],
            },
            {
                "is_resolved": False,
                "is_outdated": True,
                "authors": ["coderabbitai[bot]"],
            },
            {
                "is_resolved": False,
                "is_outdated": False,
                "authors": ["human-reviewer"],
            },
        ]

        health = fetch_pr_health(adapter, "org", "my-repo", 7)
        assert health is not None
        assert health.has_coderabbit_comments is True
        assert health.coderabbit_comment_count == 1
        assert health.health_indicator == "needs_attention"
        adapter.get_pull_request_review_threads.assert_called_once_with("org", "my-repo", 7)
        adapter.get_pull_request_review_comments.assert_not_called()

    def test_review_threads_graphql_empty_list_no_fallback_to_rest(self) -> None:
        """When get_pull_request_review_threads returns an empty list, GraphQL succeeded with 0 threads and does NOT call REST fallback."""
        adapter = MagicMock()
        adapter.get_pull_request.return_value = _make_pr_data()
        adapter.get_pull_request_reviews.return_value = [{"state": "APPROVED"}]
        adapter.get_pull_request_check_runs.return_value = [
            {"status": "completed", "conclusion": "success"}
        ]
        adapter.get_pull_request_review_threads.return_value = []
        adapter.get_pull_request_review_comments.return_value = [
            {"user": {"login": "coderabbitai"}, "created_at": "2025-01-01T00:00:00Z"},
        ]

        health = fetch_pr_health(adapter, "org", "my-repo", 7)
        assert health is not None
        assert health.has_coderabbit_comments is False
        assert health.coderabbit_comment_count == 0
        adapter.get_pull_request_review_threads.assert_called_once_with("org", "my-repo", 7)
        adapter.get_pull_request_review_comments.assert_not_called()

    def test_review_threads_graphql_returns_none_falls_back_to_rest(self) -> None:
        """When get_pull_request_review_threads returns None (error/unsupported), falls back to REST comments."""
        adapter = MagicMock()
        adapter.get_pull_request.return_value = _make_pr_data()
        adapter.get_pull_request_reviews.return_value = [{"state": "APPROVED"}]
        adapter.get_pull_request_check_runs.return_value = [
            {"status": "completed", "conclusion": "success"}
        ]
        adapter.get_pull_request_review_threads.return_value = None
        adapter.get_pull_request_review_comments.return_value = [
            {"user": {"login": "coderabbitai"}, "created_at": "2025-01-01T00:00:00Z"},
        ]

        health = fetch_pr_health(adapter, "org", "my-repo", 7)
        assert health is not None
        assert health.has_coderabbit_comments is True
        assert health.coderabbit_comment_count == 1
        adapter.get_pull_request_review_threads.assert_called_once_with("org", "my-repo", 7)
        adapter.get_pull_request_review_comments.assert_called_once_with("org", "my-repo", 7)

    def test_review_threads_graphql_fallback_when_threads_raise(self) -> None:
        """When get_pull_request_review_threads raises an exception, falls back to REST comments."""
        adapter = MagicMock()
        adapter.get_pull_request.return_value = _make_pr_data()
        adapter.get_pull_request_reviews.return_value = [{"state": "APPROVED"}]
        adapter.get_pull_request_check_runs.return_value = [
            {"status": "completed", "conclusion": "success"}
        ]
        adapter.get_pull_request_review_threads.side_effect = RuntimeError("GraphQL error")
        adapter.get_pull_request_review_comments.return_value = [
            {"user": {"login": "coderabbitai"}, "created_at": "2025-01-01T00:00:00Z"},
        ]

        health = fetch_pr_health(adapter, "org", "my-repo", 7)
        assert health is not None
        assert health.has_coderabbit_comments is True
        assert health.coderabbit_comment_count == 1
        adapter.get_pull_request_review_threads.assert_called_once_with("org", "my-repo", 7)
        adapter.get_pull_request_review_comments.assert_called_once_with("org", "my-repo", 7)


# ===================================================================
# GitHubRestAdapter review threads GraphQL pagination tests
# ===================================================================


class TestReviewThreadsGraphQLAdapter:
    def test_review_threads_and_comments_pagination(self) -> None:
        from ghdcbot.adapters.github.rest import GitHubRestAdapter

        adapter = GitHubRestAdapter(token="token", org="org", api_base="https://api.github.com")
        mock_client = MagicMock()

        # Call 1: Page 1 of threads (Thread 1 has next page of comments, Thread 2 has <= 100 comments)
        page1_response = MagicMock()
        page1_response.status_code = 200
        page1_response.json.return_value = {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "pageInfo": {"hasNextPage": True, "endCursor": "thread_cursor_1"},
                            "nodes": [
                                {
                                    "id": "thread_1",
                                    "isResolved": False,
                                    "isOutdated": False,
                                    "comments": {
                                        "pageInfo": {"hasNextPage": True, "endCursor": "comm_cursor_1"},
                                        "nodes": [{"author": {"login": "dev1"}}],
                                    },
                                },
                            ],
                        }
                    }
                }
            }
        }

        # Call 2: Comments page 2 for thread_1
        thread1_comm_response = MagicMock()
        thread1_comm_response.status_code = 200
        thread1_comm_response.json.return_value = {
            "data": {
                "node": {
                    "comments": {
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                        "nodes": [{"author": {"login": "coderabbitai[bot]"}}],
                    }
                }
            }
        }

        # Call 3: Page 2 of threads
        page2_response = MagicMock()
        page2_response.status_code = 200
        page2_response.json.return_value = {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "pageInfo": {"hasNextPage": False, "endCursor": "thread_cursor_2"},
                            "nodes": [
                                {
                                    "id": "thread_2",
                                    "isResolved": True,
                                    "isOutdated": False,
                                    "comments": {
                                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                                        "nodes": [{"author": {"login": "coderabbitai"}}],
                                    },
                                },
                            ],
                        }
                    }
                }
            }
        }

        mock_client.post.side_effect = [page1_response, thread1_comm_response, page2_response]
        adapter._client = mock_client

        threads = adapter.get_pull_request_review_threads("org", "repo", 42)
        assert len(threads) == 2
        assert threads[0] == {
            "is_resolved": False,
            "is_outdated": False,
            "authors": ["dev1", "coderabbitai[bot]"],
        }
        assert threads[1] == {
            "is_resolved": True,
            "is_outdated": False,
            "authors": ["coderabbitai"],
        }
        assert mock_client.post.call_count == 3

    def test_review_threads_error_handling(self) -> None:
        from ghdcbot.adapters.github.rest import GitHubRestAdapter

        adapter = GitHubRestAdapter(token="token", org="org", api_base="https://api.github.com")
        mock_client = MagicMock()
        mock_client.post.side_effect = RuntimeError("Network failure")
        adapter._client = mock_client

        threads = adapter.get_pull_request_review_threads("org", "repo", 42)
        assert threads is None

    def test_review_threads_non_200_response(self) -> None:
        from ghdcbot.adapters.github.rest import GitHubRestAdapter

        adapter = GitHubRestAdapter(token="token", org="org", api_base="https://api.github.com")
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_client.post.return_value = mock_resp
        adapter._client = mock_client

        threads = adapter.get_pull_request_review_threads("org", "repo", 42)
        assert threads is None

    def test_review_threads_empty_or_malformed_data(self) -> None:
        from ghdcbot.adapters.github.rest import GitHubRestAdapter

        adapter = GitHubRestAdapter(token="token", org="org", api_base="https://api.github.com")
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": None}
        mock_client.post.return_value = mock_resp
        adapter._client = mock_client

        threads = adapter.get_pull_request_review_threads("org", "repo", 42)
        assert threads is None

    def test_review_threads_graphql_errors_payload_returns_none(self) -> None:
        from ghdcbot.adapters.github.rest import GitHubRestAdapter

        adapter = GitHubRestAdapter(token="token", org="org", api_base="https://api.github.com")
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"errors": [{"message": "Field 'reviewThreads' doesn't exist"}]}
        mock_client.post.return_value = mock_resp
        adapter._client = mock_client

        threads = adapter.get_pull_request_review_threads("org", "repo", 42)
        assert threads is None

    def test_review_threads_null_author_safely_ignored(self) -> None:
        from ghdcbot.adapters.github.rest import GitHubRestAdapter

        adapter = GitHubRestAdapter(token="token", org="org", api_base="https://api.github.com")
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                            "nodes": [
                                {
                                    "id": "thread_1",
                                    "isResolved": False,
                                    "isOutdated": False,
                                    "comments": {
                                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                                        "nodes": [
                                            {"author": None},
                                            {"author": {"login": "coderabbitai[bot]"}},
                                        ],
                                    },
                                },
                            ],
                        }
                    }
                }
            }
        }
        mock_client.post.return_value = mock_resp
        adapter._client = mock_client

        threads = adapter.get_pull_request_review_threads("org", "repo", 42)
        assert threads == [
            {
                "is_resolved": False,
                "is_outdated": False,
                "authors": ["coderabbitai[bot]"],
            }
        ]

    def test_graphql_endpoint_enterprise_and_public(self) -> None:
        from ghdcbot.adapters.github.rest import GitHubRestAdapter

        # Public GitHub
        adapter_public = GitHubRestAdapter(token="t", org="o", api_base="https://api.github.com")
        assert adapter_public._api_base == "https://api.github.com"
        mock_client_pub = MagicMock()
        mock_client_pub.post.return_value.status_code = 500
        adapter_public._client = mock_client_pub
        adapter_public.get_pull_request_review_threads("o", "r", 1)
        mock_client_pub.post.assert_called_once()
        assert mock_client_pub.post.call_args[0][0] == "https://api.github.com/graphql"

        # Enterprise GitHub with /api/v3
        adapter_ghe = GitHubRestAdapter(token="t", org="o", api_base="https://ghe.example.com/api/v3")
        assert adapter_ghe._api_base == "https://ghe.example.com/api/v3"
        mock_client_ghe = MagicMock()
        mock_client_ghe.post.return_value.status_code = 500
        adapter_ghe._client = mock_client_ghe
        adapter_ghe.get_pull_request_review_threads("o", "r", 1)
        mock_client_ghe.post.assert_called_once()
        assert mock_client_ghe.post.call_args[0][0] == "https://ghe.example.com/api/graphql"

        # Enterprise GitHub with trailing slash
        adapter_ghe_slash = GitHubRestAdapter(token="t", org="o", api_base="https://ghe.example.com/api/v3/")
        mock_client_slash = MagicMock()
        mock_client_slash.post.return_value.status_code = 500
        adapter_ghe_slash._client = mock_client_slash
        adapter_ghe_slash.get_pull_request_review_threads("o", "r", 1)
        mock_client_slash.post.assert_called_once()
        assert mock_client_slash.post.call_args[0][0] == "https://ghe.example.com/api/graphql"

    def test_review_threads_repeated_cursor_stops_pagination(self) -> None:
        from ghdcbot.adapters.github.rest import GitHubRestAdapter

        adapter = GitHubRestAdapter(token="t", org="o", api_base="https://api.github.com")
        mock_client = MagicMock()

        # Returns hasNextPage=True, but endCursor stays identical
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "pageInfo": {"hasNextPage": True, "endCursor": "same_cursor"},
                            "nodes": [
                                {
                                    "id": "t1",
                                    "isResolved": True,
                                    "isOutdated": False,
                                    "comments": {"pageInfo": {"hasNextPage": False}, "nodes": []},
                                }
                            ],
                        }
                    }
                }
            }
        }
        mock_client.post.return_value = mock_resp
        adapter._client = mock_client

        threads = adapter.get_pull_request_review_threads("o", "r", 1)
        assert threads is not None
        assert len(threads) == 2
        # Called once for initial request (cursor=None -> "same_cursor"), and once for second request ("same_cursor" -> "same_cursor" repeats and halts)
        assert mock_client.post.call_count == 2

    def test_thread_comments_repeated_cursor_stops_pagination(self) -> None:
        from ghdcbot.adapters.github.rest import GitHubRestAdapter

        adapter = GitHubRestAdapter(token="t", org="o", api_base="https://api.github.com")
        mock_client = MagicMock()

        threads_resp = MagicMock()
        threads_resp.status_code = 200
        threads_resp.json.return_value = {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                            "nodes": [
                                {
                                    "id": "t1",
                                    "isResolved": False,
                                    "isOutdated": False,
                                    "comments": {
                                        "pageInfo": {"hasNextPage": True, "endCursor": "comm_cur_1"},
                                        "nodes": [{"author": {"login": "dev1"}}],
                                    },
                                }
                            ],
                        }
                    }
                }
            }
        }

        # Comments query returns same cursor repeatedly
        comm_resp = MagicMock()
        comm_resp.status_code = 200
        comm_resp.json.return_value = {
            "data": {
                "node": {
                    "comments": {
                        "pageInfo": {"hasNextPage": True, "endCursor": "comm_cur_1"},
                        "nodes": [{"author": {"login": "dev2"}}],
                    }
                }
            }
        }

        mock_client.post.side_effect = [threads_resp, comm_resp]
        adapter._client = mock_client

        threads = adapter.get_pull_request_review_threads("o", "r", 1)
        assert threads is not None
        assert len(threads) == 1
        assert sorted(threads[0]["authors"]) == ["dev1", "dev2"]
        assert mock_client.post.call_count == 2



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

    def test_no_github_link_embedded_box(self) -> None:
        """Single PR status report does not contain naked html_url link or link embed box."""
        h = _make_health(html_url="https://github.com/org/my-repo/pull/7")
        result = format_single_pr_status(h, "org")
        assert "https://github.com" not in result
        assert "🔗" not in result


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


# ===================================================================
# pr_status_cmd repo filter validation tests
# ===================================================================


class TestPRStatusCommandRepoFilter:
    def test_repo_filter_allow_mode_blocks_unlisted_repo(self) -> None:
        from ghdcbot.config.models import RepoFilterConfig
        from ghdcbot.engine.pr_status import is_repo_allowed

        repo_filter = RepoFilterConfig(mode="allow", names=["allowed-repo"])
        assert is_repo_allowed(repo_filter, "secret-repo") is False

    def test_repo_filter_deny_mode_blocks_denied_repo(self) -> None:
        from ghdcbot.config.models import RepoFilterConfig
        from ghdcbot.engine.pr_status import is_repo_allowed

        repo_filter = RepoFilterConfig(mode="deny", names=["denied-repo"])
        assert is_repo_allowed(repo_filter, "denied-repo") is False

    def test_repo_filter_allows_valid_repo(self) -> None:
        from ghdcbot.config.models import RepoFilterConfig
        from ghdcbot.engine.pr_status import is_repo_allowed

        repo_filter = RepoFilterConfig(mode="allow", names=["allowed-repo"])
        assert is_repo_allowed(repo_filter, "allowed-repo") is True

    def test_repo_filter_none_allows_all_repos(self) -> None:
        from ghdcbot.engine.pr_status import is_repo_allowed

        assert is_repo_allowed(None, "any-repo") is True


# ===================================================================
# pr_status_cmd permissions gating tests
# ===================================================================


class TestPRStatusCommandPermissions:
    def test_pr_status_allowed_by_default_without_explicit_rule(self) -> None:
        """When discord.command_permissions has no pr-status rule, any guild member with roles is allowed."""
        from ghdcbot.config.models import BotConfig, DiscordConfig, GitHubConfig, RuntimeConfig
        from ghdcbot.discord_command_permissions import slash_command_allowed

        config = BotConfig(
            runtime=RuntimeConfig(
                data_dir="/tmp/test",
                github_adapter="rest",
                discord_adapter="rest",
                storage_adapter="sqlite",
            ),
            github=GitHubConfig(org="test-org"),
            discord=DiscordConfig(guild_id="123", token="xyz"),
        )

        member = MagicMock()
        member.roles = [MagicMock(name="Contributor", id="999")]
        member.guild_permissions = MagicMock(administrator=False)
        interaction = MagicMock()
        interaction.user = member

        assert slash_command_allowed(interaction, config, "pr-status", allow_all_by_default=True) is True

    def test_pr_status_gated_when_configured_in_command_permissions(self) -> None:
        """When discord.command_permissions specifies pr-status, unauthorized members are blocked and authorized members pass."""
        from ghdcbot.config.models import (
            BotConfig,
            DiscordConfig,
            GitHubConfig,
            RuntimeConfig,
            SlashCommandPermissionRule,
        )
        from ghdcbot.discord_command_permissions import slash_command_allowed

        config = BotConfig(
            runtime=RuntimeConfig(
                data_dir="/tmp/test",
                github_adapter="rest",
                discord_adapter="rest",
                storage_adapter="sqlite",
            ),
            github=GitHubConfig(org="test-org"),
            discord=DiscordConfig(
                guild_id="123",
                token="xyz",
                command_permissions={
                    "pr-status": SlashCommandPermissionRule(role_names=["Mentor", "Maintainer"])
                },
            ),
        )

        # 1. Contributor without Mentor role
        member_contributor = MagicMock()
        role_contrib = MagicMock()
        role_contrib.name = "Contributor"
        role_contrib.id = 111
        member_contributor.roles = [role_contrib]
        member_contributor.guild_permissions = MagicMock(administrator=False)
        interaction_contrib = MagicMock()
        interaction_contrib.user = member_contributor

        assert (
            slash_command_allowed(
                interaction_contrib, config, "pr-status", allow_all_by_default=True
            )
            is False
        )

        # 2. Mentor with Mentor role
        member_mentor = MagicMock()
        role_mentor = MagicMock()
        role_mentor.name = "Mentor"
        role_mentor.id = 222
        member_mentor.roles = [role_mentor]
        member_mentor.guild_permissions = MagicMock(administrator=False)
        interaction_mentor = MagicMock()
        interaction_mentor.user = member_mentor

        assert (
            slash_command_allowed(
                interaction_mentor, config, "pr-status", allow_all_by_default=True
            )
            is True
        )


# ===================================================================
# repo box recommendation and autocomplete tests
# ===================================================================


class TestRepoRecommendationAndAutocomplete:
    def test_get_configured_repo_names_allow_mode(self) -> None:
        """Configured repos with mode='allow' are returned in order."""
        from ghdcbot.config.models import (
            BotConfig,
            DiscordConfig,
            GitHubConfig,
            RepoFilterConfig,
            RuntimeConfig,
        )
        from ghdcbot.engine.pr_status import get_configured_repo_names

        config = BotConfig(
            runtime=RuntimeConfig(
                data_dir="/tmp/test",
                github_adapter="ghdcbot.adapters.github.rest:GitHubRestAdapter",
                discord_adapter="ghdcbot.adapters.discord.api:DiscordApiAdapter",
                storage_adapter="ghdcbot.adapters.storage.sqlite:SqliteStorage",
            ),
            github=GitHubConfig(
                org="test-org",
                repos=RepoFilterConfig(mode="allow", names=["Knowledge-Agent", "Devr.AI"]),
            ),
            discord=DiscordConfig(guild_id="123", token="xyz"),
        )
        assert get_configured_repo_names(config) == ["Knowledge-Agent", "Devr.AI"]

    def test_get_configured_repo_names_deny_mode_excludes_denied(self) -> None:
        """When repos.mode='deny', denied repos are not suggested."""
        from ghdcbot.config.models import (
            BotConfig,
            DiscordConfig,
            GitHubConfig,
            RepoFilterConfig,
            RuntimeConfig,
        )
        from ghdcbot.engine.pr_status import get_configured_repo_names

        config = BotConfig(
            runtime=RuntimeConfig(
                data_dir="/tmp/test",
                github_adapter="ghdcbot.adapters.github.rest:GitHubRestAdapter",
                discord_adapter="ghdcbot.adapters.discord.api:DiscordApiAdapter",
                storage_adapter="ghdcbot.adapters.storage.sqlite:SqliteStorage",
            ),
            github=GitHubConfig(
                org="test-org",
                repos=RepoFilterConfig(mode="deny", names=["denied-repo"]),
            ),
            discord=DiscordConfig(guild_id="123", token="xyz"),
        )
        assert get_configured_repo_names(config) == []

    def test_get_configured_repo_names_from_channels_and_roles(self) -> None:
        """Repos configured in discord.pr_open_channels and repo_contributor_roles are included."""
        from ghdcbot.config.models import (
            BotConfig,
            DiscordConfig,
            GitHubConfig,
            RepoFilterConfig,
            RuntimeConfig,
        )
        from ghdcbot.engine.pr_status import get_configured_repo_names

        config = BotConfig(
            runtime=RuntimeConfig(
                data_dir="/tmp/test",
                github_adapter="ghdcbot.adapters.github.rest:GitHubRestAdapter",
                discord_adapter="ghdcbot.adapters.discord.api:DiscordApiAdapter",
                storage_adapter="ghdcbot.adapters.storage.sqlite:SqliteStorage",
            ),
            github=GitHubConfig(
                org="test-org",
                repos=RepoFilterConfig(mode="allow", names=["Repo-A"]),
            ),
            discord=DiscordConfig(
                guild_id="123",
                token="xyz",
                pr_open_channels={"Repo-B": "111", "repo-a": "222"},
            ),
            repo_contributor_roles={"Repo-C": "Contributor-Role"},
        )
        repos = get_configured_repo_names(config)
        assert repos == ["Repo-A", "Repo-B", "Repo-C"]

    def test_get_configured_repo_names_case_insensitive_dedup(self) -> None:
        """Duplicate repo names across sources are deduplicated case-insensitively."""
        from ghdcbot.config.models import (
            BotConfig,
            DiscordConfig,
            GitHubConfig,
            RepoFilterConfig,
            RuntimeConfig,
        )
        from ghdcbot.engine.pr_status import get_configured_repo_names

        config = BotConfig(
            runtime=RuntimeConfig(
                data_dir="/tmp/test",
                github_adapter="ghdcbot.adapters.github.rest:GitHubRestAdapter",
                discord_adapter="ghdcbot.adapters.discord.api:DiscordApiAdapter",
                storage_adapter="ghdcbot.adapters.storage.sqlite:SqliteStorage",
            ),
            github=GitHubConfig(
                org="test-org",
                repos=RepoFilterConfig(mode="allow", names=["Knowledge-Agent", "knowledge-agent"]),
            ),
            discord=DiscordConfig(
                guild_id="123",
                token="xyz",
                pr_open_channels={"KNOWLEDGE-AGENT": "999"},
            ),
        )
        assert get_configured_repo_names(config) == ["Knowledge-Agent"]

    def test_get_configured_repo_names_none_or_empty(self) -> None:
        """Returns empty list when config is None or empty dict."""
        from ghdcbot.engine.pr_status import get_configured_repo_names

        assert get_configured_repo_names(None) == []
        assert get_configured_repo_names({}) == []

    def test_filter_repo_suggestions_prefix_then_substring(self) -> None:
        """Filter prioritizes prefix matches over substring matches."""
        from ghdcbot.engine.pr_status import filter_repo_suggestions

        repos = ["Knowledge-Agent", "Agent-Smith", "Devr.AI", "PictoPy", "Gitcord"]
        # Empty string returns all
        assert filter_repo_suggestions(repos, "") == repos

        # "Agent" matches Agent-Smith (prefix) first, then Knowledge-Agent (substring)
        matches = filter_repo_suggestions(repos, "agent")
        assert matches == ["Agent-Smith", "Knowledge-Agent"]

    def test_filter_repo_suggestions_capped_at_25(self) -> None:
        """Filter returns at most 25 choices (Discord interaction limit)."""
        from ghdcbot.engine.pr_status import filter_repo_suggestions

        long_list = [f"repo-{i:02d}" for i in range(35)]
        capped = filter_repo_suggestions(long_list, "")
        assert len(capped) == 25
        assert capped[0] == "repo-00"
        assert capped[24] == "repo-24"

    @pytest.mark.asyncio
    async def test_repo_autocomplete_choice_creation(self) -> None:
        """Filtered suggestions map cleanly to discord app_commands Choice objects."""
        from discord import app_commands

        from ghdcbot.engine.pr_status import filter_repo_suggestions

        repos = ["Knowledge-Agent", "Gitcord-Bot"]
        suggestions = filter_repo_suggestions(repos, "know")
        choices = [app_commands.Choice(name=r, value=r) for r in suggestions]
        assert len(choices) == 1
        assert choices[0].name == "Knowledge-Agent"
        assert choices[0].value == "Knowledge-Agent"

    @pytest.mark.asyncio
    async def test_pr_status_repo_autocomplete_integration(self) -> None:
        """In run_bot, pr-status repo param has autocomplete connected to config."""
        from unittest.mock import MagicMock, patch

        import discord

        from ghdcbot.bot import run_bot
        from ghdcbot.config.models import (
            BotConfig,
            DiscordConfig,
            GitHubConfig,
            RepoFilterConfig,
            RuntimeConfig,
        )

        cfg = BotConfig(
            runtime=RuntimeConfig(
                data_dir="./data",
                github_adapter="ghdcbot.adapters.github.rest:GitHubRestAdapter",
                discord_adapter="ghdcbot.adapters.discord.api:DiscordApiAdapter",
                storage_adapter="ghdcbot.adapters.storage.sqlite:SqliteStorage",
            ),
            github=GitHubConfig(
                org="test-org",
                repos=RepoFilterConfig(mode="allow", names=["Knowledge-Agent", "Devr.AI"]),
            ),
            discord=DiscordConfig(guild_id="123", token="fake"),
        )

        captured = []
        orig_tree_init = discord.app_commands.CommandTree.__init__

        def mock_tree_init(tree_self: Any, client: Any) -> None:
            captured.append(tree_self)
            orig_tree_init(tree_self, client)

        with (
            patch("ghdcbot.bot.load_config", return_value=cfg),
            patch("ghdcbot.bot.resolve_github_token", return_value="fake"),
            patch("ghdcbot.bot.build_adapter"),
            patch("ghdcbot.bot.GitHubIdentityReader"),
            patch("ghdcbot.bot.IdentityLinkService"),
            patch("ghdcbot.bot.SocialProfileService"),
            patch("discord.app_commands.CommandTree.__init__", mock_tree_init),
            patch("discord.Client.run", side_effect=SystemExit(0)),
        ):
            try:
                run_bot("dummy.yaml")
            except SystemExit:
                pass

        assert len(captured) == 1
        tree = captured[0]
        pr_status_cmds = [
            cmd
            for cmd in tree.get_commands(guild=discord.Object(id=123))
            if cmd.name == "pr-status"
        ]
        assert len(pr_status_cmds) == 1
        pr_status = pr_status_cmds[0]

        # Verify repo parameter has autocomplete registered
        repo_params = [p for p in pr_status.parameters if p.name == "repo"]
        assert len(repo_params) == 1
        assert repo_params[0].autocomplete is True

        # Call the autocomplete callback and verify it returns configured repos from config
        callback = pr_status._params["repo"].autocomplete
        mock_interaction = MagicMock()
        choices = await callback(mock_interaction, "know")
        assert len(choices) == 1
        assert choices[0].name == "Knowledge-Agent"
        assert choices[0].value == "Knowledge-Agent"

    @pytest.mark.asyncio
    async def test_pr_status_single_pr_suppress_embeds(self) -> None:
        """pr_status_cmd sends single PR status message with suppress_embeds=True."""
        from unittest.mock import AsyncMock, MagicMock, patch

        import discord

        from ghdcbot.bot import run_bot
        from ghdcbot.config.models import (
            BotConfig,
            DiscordConfig,
            GitHubConfig,
            RepoFilterConfig,
            RuntimeConfig,
        )
        from ghdcbot.engine.pr_status import PRHealthStatus

        cfg = BotConfig(
            runtime=RuntimeConfig(
                data_dir="./data",
                github_adapter="ghdcbot.adapters.github.rest:GitHubRestAdapter",
                discord_adapter="ghdcbot.adapters.discord.api:DiscordApiAdapter",
                storage_adapter="ghdcbot.adapters.storage.sqlite:SqliteStorage",
            ),
            github=GitHubConfig(
                org="test-org",
                repos=RepoFilterConfig(mode="allow", names=["Knowledge-Agent"]),
            ),
            discord=DiscordConfig(guild_id="123", token="fake"),
        )

        captured = []
        orig_tree_init = discord.app_commands.CommandTree.__init__

        def mock_tree_init(tree_self: Any, client: Any) -> None:
            captured.append(tree_self)
            orig_tree_init(tree_self, client)

        with (
            patch("ghdcbot.bot.load_config", return_value=cfg),
            patch("ghdcbot.bot.resolve_github_token", return_value="fake"),
            patch("ghdcbot.bot.build_adapter"),
            patch("ghdcbot.bot.GitHubIdentityReader"),
            patch("ghdcbot.bot.IdentityLinkService"),
            patch("ghdcbot.bot.SocialProfileService"),
            patch("discord.app_commands.CommandTree.__init__", mock_tree_init),
            patch("discord.Client.run", side_effect=SystemExit(0)),
        ):
            try:
                run_bot("dummy.yaml")
            except SystemExit:
                pass

        pr_status = next(
            cmd
            for cmd in captured[0].get_commands(guild=discord.Object(id=123))
            if cmd.name == "pr-status"
        )

        mock_interaction = MagicMock()
        mock_interaction.response.defer = AsyncMock()
        mock_interaction.followup.send = AsyncMock()

        sample_health = PRHealthStatus(
            repo="Knowledge-Agent",
            number=1,
            title="Test PR",
            author="contributor",
            html_url="https://github.com/org/Knowledge-Agent/pull/1",
            ci_status="passing",
            mergeable=True,
            review_state="approved",
            approved_count=1,
            changes_requested_count=0,
            has_coderabbit_comments=False,
            coderabbit_comment_count=0,
            is_draft=False,
        )

        with patch("ghdcbot.bot.fetch_pr_health", return_value=sample_health):
            await pr_status.callback(mock_interaction, repo="Knowledge-Agent", pr_number=1)

        mock_interaction.followup.send.assert_awaited_once()
        kwargs = mock_interaction.followup.send.call_args.kwargs
        assert kwargs.get("ephemeral") is True
        assert kwargs.get("suppress_embeds") is True

    @pytest.mark.asyncio
    async def test_resolve_repo_for_pr_single_configured_repo(self) -> None:
        """When 1 repo is configured, resolve_repo_for_pr returns it without extra queries."""
        from unittest.mock import MagicMock

        from ghdcbot.config.models import (
            BotConfig,
            DiscordConfig,
            GitHubConfig,
            RepoFilterConfig,
            RuntimeConfig,
        )
        from ghdcbot.engine.pr_status import resolve_repo_for_pr

        cfg = BotConfig(
            runtime=RuntimeConfig(
                data_dir="./data",
                github_adapter="fake",
                discord_adapter="fake",
                storage_adapter="fake",
            ),
            github=GitHubConfig(
                org="test-org",
                repos=RepoFilterConfig(mode="allow", names=["Knowledge-Agent"]),
            ),
            discord=DiscordConfig(guild_id="123", token="fake"),
        )
        mock_adapter = MagicMock()
        repo, err = await resolve_repo_for_pr(cfg, mock_adapter, 42, repo=None)
        assert err is None
        assert repo == "Knowledge-Agent"
        mock_adapter.get_pull_request.assert_not_called()

    @pytest.mark.asyncio
    async def test_resolve_repo_for_pr_multiple_repos_single_match(self) -> None:
        """When multiple repos are configured, scans candidates and resolves the matching repo."""
        from unittest.mock import MagicMock

        from ghdcbot.config.models import (
            BotConfig,
            DiscordConfig,
            GitHubConfig,
            RepoFilterConfig,
            RuntimeConfig,
        )
        from ghdcbot.engine.pr_status import resolve_repo_for_pr

        cfg = BotConfig(
            runtime=RuntimeConfig(
                data_dir="./data",
                github_adapter="fake",
                discord_adapter="fake",
                storage_adapter="fake",
            ),
            github=GitHubConfig(
                org="test-org",
                repos=RepoFilterConfig(mode="allow", names=["Repo-A", "Repo-B"]),
            ),
            discord=DiscordConfig(guild_id="123", token="fake"),
        )
        mock_adapter = MagicMock()
        # Repo-A returns None, Repo-B returns PR dict
        def fake_get_pr(org: str, repo: str, num: int):
            if repo == "Repo-B":
                return {"number": num}
            return None

        mock_adapter.get_pull_request.side_effect = fake_get_pr
        repo, err = await resolve_repo_for_pr(cfg, mock_adapter, 7, repo=None)
        assert err is None
        assert repo == "Repo-B"

    @pytest.mark.asyncio
    async def test_resolve_repo_for_pr_multiple_repos_ambiguous(self) -> None:
        """When PR exists in multiple repos, returns informative disambiguation error."""
        from unittest.mock import MagicMock

        from ghdcbot.config.models import (
            BotConfig,
            DiscordConfig,
            GitHubConfig,
            RepoFilterConfig,
            RuntimeConfig,
        )
        from ghdcbot.engine.pr_status import resolve_repo_for_pr

        cfg = BotConfig(
            runtime=RuntimeConfig(
                data_dir="./data",
                github_adapter="fake",
                discord_adapter="fake",
                storage_adapter="fake",
            ),
            github=GitHubConfig(
                org="test-org",
                repos=RepoFilterConfig(mode="allow", names=["Repo-A", "Repo-B"]),
            ),
            discord=DiscordConfig(guild_id="123", token="fake"),
        )
        mock_adapter = MagicMock()
        mock_adapter.get_pull_request.return_value = {"number": 10}

        repo, err = await resolve_repo_for_pr(cfg, mock_adapter, 10, repo=None)
        assert repo is None
        assert err is not None
        assert "Found PR **#10** in multiple configured repositories" in err
        assert "`Repo-A`" in err
        assert "`Repo-B`" in err

    @pytest.mark.asyncio
    async def test_resolve_repo_for_pr_multiple_repos_not_found(self) -> None:
        """When PR is not found in any configured repo, returns not found message."""
        from unittest.mock import MagicMock

        from ghdcbot.config.models import (
            BotConfig,
            DiscordConfig,
            GitHubConfig,
            RepoFilterConfig,
            RuntimeConfig,
        )
        from ghdcbot.engine.pr_status import resolve_repo_for_pr

        cfg = BotConfig(
            runtime=RuntimeConfig(
                data_dir="./data",
                github_adapter="fake",
                discord_adapter="fake",
                storage_adapter="fake",
            ),
            github=GitHubConfig(
                org="test-org",
                repos=RepoFilterConfig(mode="allow", names=["Repo-A", "Repo-B"]),
            ),
            discord=DiscordConfig(guild_id="123", token="fake"),
        )
        mock_adapter = MagicMock()
        mock_adapter.get_pull_request.return_value = None

        repo, err = await resolve_repo_for_pr(cfg, mock_adapter, 999, repo=None)
        assert repo is None
        assert err is not None
        assert "PR **#999** not found in configured repositories" in err

    @pytest.mark.asyncio
    async def test_resolve_repo_for_pr_empty_configured_repos(self) -> None:
        """When no repos are configured, asks user to specify repo."""
        from unittest.mock import MagicMock

        from ghdcbot.config.models import BotConfig, DiscordConfig, GitHubConfig, RuntimeConfig
        from ghdcbot.engine.pr_status import resolve_repo_for_pr

        cfg = BotConfig(
            runtime=RuntimeConfig(
                data_dir="./data",
                github_adapter="fake",
                discord_adapter="fake",
                storage_adapter="fake",
            ),
            github=GitHubConfig(org="test-org"),
            discord=DiscordConfig(guild_id="123", token="fake"),
        )
        mock_adapter = MagicMock()
        repo, err = await resolve_repo_for_pr(cfg, mock_adapter, 1, repo=None)
        assert repo is None
        assert "Please specify `repo`" in (err or "")

    @pytest.mark.asyncio
    async def test_resolve_repo_for_pr_explicit_disallowed_repo(self) -> None:
        """Explicit repo not matching allowlist is rejected."""
        from unittest.mock import MagicMock

        from ghdcbot.config.models import (
            BotConfig,
            DiscordConfig,
            GitHubConfig,
            RepoFilterConfig,
            RuntimeConfig,
        )
        from ghdcbot.engine.pr_status import resolve_repo_for_pr

        cfg = BotConfig(
            runtime=RuntimeConfig(
                data_dir="./data",
                github_adapter="fake",
                discord_adapter="fake",
                storage_adapter="fake",
            ),
            github=GitHubConfig(
                org="test-org",
                repos=RepoFilterConfig(mode="allow", names=["Knowledge-Agent"]),
            ),
            discord=DiscordConfig(guild_id="123", token="fake"),
        )
        mock_adapter = MagicMock()
        repo, err = await resolve_repo_for_pr(cfg, mock_adapter, 1, repo="Secret-Repo")
        assert repo is None
        assert "not allowed by Gitcord configuration" in (err or "")

    @pytest.mark.asyncio
    async def test_pr_status_cmd_auto_detects_repo_when_omitted(self) -> None:
        """pr_status_cmd works end-to-end without repo argument (auto-resolves from config)."""
        from unittest.mock import AsyncMock, MagicMock, patch

        import discord

        from ghdcbot.bot import run_bot
        from ghdcbot.config.models import (
            BotConfig,
            DiscordConfig,
            GitHubConfig,
            RepoFilterConfig,
            RuntimeConfig,
        )
        from ghdcbot.engine.pr_status import PRHealthStatus

        cfg = BotConfig(
            runtime=RuntimeConfig(
                data_dir="./data",
                github_adapter="ghdcbot.adapters.github.rest:GitHubRestAdapter",
                discord_adapter="ghdcbot.adapters.discord.api:DiscordApiAdapter",
                storage_adapter="ghdcbot.adapters.storage.sqlite:SqliteStorage",
            ),
            github=GitHubConfig(
                org="test-org",
                repos=RepoFilterConfig(mode="allow", names=["Knowledge-Agent"]),
            ),
            discord=DiscordConfig(guild_id="123", token="fake"),
        )

        captured = []
        orig_tree_init = discord.app_commands.CommandTree.__init__

        def mock_tree_init(tree_self: Any, client: Any) -> None:
            captured.append(tree_self)
            orig_tree_init(tree_self, client)

        with (
            patch("ghdcbot.bot.load_config", return_value=cfg),
            patch("ghdcbot.bot.resolve_github_token", return_value="fake"),
            patch("ghdcbot.bot.build_adapter"),
            patch("ghdcbot.bot.GitHubIdentityReader"),
            patch("ghdcbot.bot.IdentityLinkService"),
            patch("ghdcbot.bot.SocialProfileService"),
            patch("discord.app_commands.CommandTree.__init__", mock_tree_init),
            patch("discord.Client.run", side_effect=SystemExit(0)),
        ):
            try:
                run_bot("dummy.yaml")
            except SystemExit:
                pass

        pr_status = next(
            cmd
            for cmd in captured[0].get_commands(guild=discord.Object(id=123))
            if cmd.name == "pr-status"
        )

        mock_interaction = MagicMock()
        mock_interaction.response.defer = AsyncMock()
        mock_interaction.followup.send = AsyncMock()

        sample_health = PRHealthStatus(
            repo="Knowledge-Agent",
            number=2,
            title="Auto detected PR",
            author="contributor",
            html_url="https://github.com/org/Knowledge-Agent/pull/2",
            ci_status="passing",
            mergeable=True,
            review_state="approved",
            approved_count=1,
            changes_requested_count=0,
            has_coderabbit_comments=False,
            coderabbit_comment_count=0,
            is_draft=False,
        )

        # Call WITHOUT repo parameter
        with patch("ghdcbot.bot.fetch_pr_health", return_value=sample_health) as mock_fetch:
            await pr_status.callback(mock_interaction, repo=None, pr_number=2)

        # Verify fetch_pr_health was called with auto-detected "Knowledge-Agent"
        mock_fetch.assert_called_once()
        assert mock_fetch.call_args[0][2] == "Knowledge-Agent"

        mock_interaction.followup.send.assert_awaited_once()
        kwargs = mock_interaction.followup.send.call_args.kwargs
        assert kwargs.get("ephemeral") is True
        assert kwargs.get("suppress_embeds") is True
        msg = mock_interaction.followup.send.call_args[0][0]
        assert "Knowledge-Agent#2" in msg
        assert "Safe to Merge" in msg

    def test_format_single_pr_status_merged_shows_only_merged(self) -> None:
        """When PR is merged, format_single_pr_status only shows Merged and no CI/CodeRabbit."""
        from ghdcbot.engine.pr_status import PRHealthStatus, format_single_pr_status

        status = PRHealthStatus(
            repo="Knowledge-Agent",
            number=42,
            title="Feature X",
            author="prith",
            html_url="https://github.com/org/Knowledge-Agent/pull/42",
            ci_status="passing",
            mergeable=True,
            review_state="approved",
            approved_count=1,
            changes_requested_count=0,
            has_coderabbit_comments=False,
            coderabbit_comment_count=0,
            is_draft=False,
            state="merged",
        )
        msg = format_single_pr_status(status, "org")
        assert "**Knowledge-Agent#42** — Feature X" in msg
        assert "👤 **Author:** prith" in msg
        assert "🟣 **Status:** Merged" in msg
        # Ensure CI, CodeRabbit, Mergeable, Review, and Health are omitted
        assert "CI:" not in msg
        assert "CodeRabbit:" not in msg
        assert "Mergeable:" not in msg
        assert "Review:" not in msg
        assert "Health:" not in msg

    def test_format_single_pr_status_closed_shows_only_closed(self) -> None:
        """When PR is closed, format_single_pr_status only shows Closed and no CI/CodeRabbit."""
        from ghdcbot.engine.pr_status import PRHealthStatus, format_single_pr_status

        status = PRHealthStatus(
            repo="Knowledge-Agent",
            number=43,
            title="Abandoned Fix",
            author="contributor",
            html_url="https://github.com/org/Knowledge-Agent/pull/43",
            ci_status="failing",
            mergeable=False,
            review_state="changes_requested",
            approved_count=0,
            changes_requested_count=1,
            has_coderabbit_comments=True,
            coderabbit_comment_count=3,
            is_draft=False,
            state="closed",
        )
        msg = format_single_pr_status(status, "org")
        assert "**Knowledge-Agent#43** — Abandoned Fix" in msg
        assert "👤 **Author:** contributor" in msg
        assert "🔴 **Status:** Closed" in msg
        # Ensure CI, CodeRabbit, Mergeable, Review, and Health are omitted
        assert "CI:" not in msg
        assert "CodeRabbit:" not in msg
        assert "Mergeable:" not in msg
        assert "Review:" not in msg
        assert "Health:" not in msg

    def test_fetch_pr_health_merged_skips_reviews_and_ci(self) -> None:
        """fetch_pr_health detects merged PR and skips check-runs / reviews / threads."""
        from unittest.mock import MagicMock

        from ghdcbot.engine.pr_status import fetch_pr_health

        mock_adapter = MagicMock()
        mock_adapter.get_pull_request.return_value = {
            "number": 15,
            "title": "Merged PR",
            "state": "closed",
            "merged": True,
            "user": {"login": "dev1"},
            "html_url": "https://github.com/org/repo/pull/15",
        }

        health = fetch_pr_health(mock_adapter, "org", "repo", 15)
        assert health is not None
        assert health.state == "merged"
        assert health.health_indicator == "merged"
        mock_adapter.get_pull_request_check_runs.assert_not_called()
        mock_adapter.get_pull_request_reviews.assert_not_called()

    def test_fetch_pr_health_closed_skips_reviews_and_ci(self) -> None:
        """fetch_pr_health detects closed PR and skips check-runs / reviews / threads."""
        from unittest.mock import MagicMock

        from ghdcbot.engine.pr_status import fetch_pr_health

        mock_adapter = MagicMock()
        mock_adapter.get_pull_request.return_value = {
            "number": 16,
            "title": "Closed PR",
            "state": "closed",
            "merged": False,
            "user": {"login": "dev2"},
            "html_url": "https://github.com/org/repo/pull/16",
        }

        health = fetch_pr_health(mock_adapter, "org", "repo", 16)
        assert health is not None
        assert health.state == "closed"
        assert health.health_indicator == "closed"
        mock_adapter.get_pull_request_check_runs.assert_not_called()
        mock_adapter.get_pull_request_reviews.assert_not_called()

    @pytest.mark.asyncio
    async def test_pr_status_cmd_informs_when_target_is_issue(self) -> None:
        """When number points to an Issue rather than a PR, pr_status_cmd explains it clearly."""
        from unittest.mock import AsyncMock, MagicMock, patch

        import discord

        from ghdcbot.bot import run_bot
        from ghdcbot.config.models import (
            BotConfig,
            DiscordConfig,
            GitHubConfig,
            RepoFilterConfig,
            RuntimeConfig,
        )

        cfg = BotConfig(
            runtime=RuntimeConfig(
                data_dir="./data",
                github_adapter="ghdcbot.adapters.github.rest:GitHubRestAdapter",
                discord_adapter="ghdcbot.adapters.discord.api:DiscordApiAdapter",
                storage_adapter="ghdcbot.adapters.storage.sqlite:SqliteStorage",
            ),
            github=GitHubConfig(
                org="test-org",
                repos=RepoFilterConfig(mode="allow", names=["Knowledge-Agent"]),
            ),
            discord=DiscordConfig(guild_id="123", token="fake"),
        )

        captured = []
        orig_tree_init = discord.app_commands.CommandTree.__init__

        def mock_tree_init(tree_self: Any, client: Any) -> None:
            captured.append(tree_self)
            orig_tree_init(tree_self, client)

        mock_github = MagicMock()
        # get_pull_request returns None, get_issue returns an issue without 'pull_request'
        mock_github.get_pull_request.return_value = None
        mock_github.get_issue.return_value = {
            "title": "Bug in context engine",
            "state": "closed",
        }

        with (
            patch("ghdcbot.bot.load_config", return_value=cfg),
            patch("ghdcbot.bot.resolve_github_token", return_value="fake"),
            patch("ghdcbot.bot.build_adapter", return_value=mock_github),
            patch("ghdcbot.bot.GitHubIdentityReader"),
            patch("ghdcbot.bot.IdentityLinkService"),
            patch("ghdcbot.bot.SocialProfileService"),
            patch("discord.app_commands.CommandTree.__init__", mock_tree_init),
            patch("discord.Client.run", side_effect=SystemExit(0)),
        ):
            try:
                run_bot("dummy.yaml")
            except SystemExit:
                pass

        pr_status = next(
            cmd
            for cmd in captured[0].get_commands(guild=discord.Object(id=123))
            if cmd.name == "pr-status"
        )

        mock_interaction = MagicMock()
        mock_interaction.response.defer = AsyncMock()
        mock_interaction.followup.send = AsyncMock()

        await pr_status.callback(mock_interaction, repo=None, pr_number=1)

        mock_interaction.followup.send.assert_awaited_once()
        msg = mock_interaction.followup.send.call_args[0][0]
        assert "is a GitHub **Issue**, not a Pull Request" in msg
        assert "Bug in context engine" in msg
        assert "Closed 🔴" in msg




