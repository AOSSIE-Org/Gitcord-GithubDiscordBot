from __future__ import annotations

import tempfile
from datetime import UTC, datetime

import pytest

from ghdcbot.adapters.storage.sqlite import SqliteStorage


@pytest.fixture
def temp_storage():
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = SqliteStorage(tmpdir)
        storage.init_schema()
        yield storage


def test_issue_request_lifecycle(temp_storage: SqliteStorage):
    # 1. No pending requests initially
    assert temp_storage.list_pending_issue_requests() == []
    assert temp_storage.get_pending_issue_request("repo-a", 101) is None

    # 2. Insert issue request
    temp_storage.insert_issue_request(
        request_id="req-123",
        discord_user_id="user-456",
        github_user="octocat",
        owner="org",
        repo="repo-a",
        issue_number=101,
        issue_url="https://github.com/org/repo-a/issues/101",
    )

    # 3. Retrieve request
    req = temp_storage.get_issue_request("req-123")
    assert req is not None
    assert req["request_id"] == "req-123"
    assert req["discord_user_id"] == "user-456"
    assert req["github_user"] == "octocat"
    assert req["repo"] == "repo-a"
    assert req["issue_number"] == 101
    assert req["status"] == "pending"

    # 4. Check get_pending_issue_request
    pending = temp_storage.get_pending_issue_request("repo-a", 101)
    assert pending is not None
    assert pending["request_id"] == "req-123"

    # Filtered by github_user (case-insensitive)
    assert temp_storage.get_pending_issue_request("repo-a", 101, github_user="OCTOCAT") is not None
    assert temp_storage.get_pending_issue_request("repo-a", 101, github_user="other") is None

    # Filtered by discord_user_id
    assert temp_storage.get_pending_issue_request("repo-a", 101, discord_user_id="user-456") is not None
    assert temp_storage.get_pending_issue_request("repo-a", 101, discord_user_id="other-id") is None

    # 5. List pending requests (all vs repo-filtered)
    temp_storage.insert_issue_request(
        request_id="req-124",
        discord_user_id="user-789",
        github_user="hubber",
        owner="org",
        repo="repo-b",
        issue_number=202,
        issue_url="https://github.com/org/repo-b/issues/202",
    )

    all_pending = temp_storage.list_pending_issue_requests()
    assert len(all_pending) == 2

    repo_a_pending = temp_storage.list_pending_issue_requests(repo="repo-a")
    assert len(repo_a_pending) == 1
    assert repo_a_pending[0]["request_id"] == "req-123"

    repo_b_pending = temp_storage.list_pending_issue_requests(repo="repo-b")
    assert len(repo_b_pending) == 1
    assert repo_b_pending[0]["request_id"] == "req-124"

    # 6. Resolve request to approved
    now_str = datetime.now(UTC).isoformat()
    temp_storage.update_issue_request_status(
        "req-123",
        "approved",
        resolved_by_discord_id="mentor-999",
        resolved_at=now_str,
    )

    req_approved = temp_storage.get_issue_request("req-123")
    assert req_approved["status"] == "approved"
    assert req_approved["resolved_by_discord_id"] == "mentor-999"
    assert req_approved["resolved_at"] == now_str

    # Should no longer be pending
    assert temp_storage.get_pending_issue_request("repo-a", 101) is None
    assert len(temp_storage.list_pending_issue_requests(repo="repo-a")) == 0

    # 7. Resolve req-124 to declined with reason
    temp_storage.update_issue_request_status(
        "req-124",
        "declined",
        resolved_by_discord_id="mentor-888",
        rejection_reason="Already assigned to someone else",
    )
    req_declined = temp_storage.get_issue_request("req-124")
    assert req_declined["status"] == "declined"
    assert req_declined["resolved_by_discord_id"] == "mentor-888"
    assert req_declined["rejection_reason"] == "Already assigned to someone else"
    assert len(temp_storage.list_pending_issue_requests()) == 0
