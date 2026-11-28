from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from ghdcbot.config.models import NotificationConfig
from ghdcbot.core.modes import MutationPolicy, RunMode
from ghdcbot.engine.notifications import run_coderabbit_reminders


class DummyStorage:
    def __init__(self, mappings: list[dict] | None = None) -> None:
        self.verified_mappings = (
            [{"discord_user_id": "12345", "github_user": "alice"}]
            if mappings is None
            else mappings
        )
        self.sent_keys: set[str] = set()
        self.recorded_events: list[Any] = []

    def list_verified_identity_mappings(self) -> list[dict]:
        return self.verified_mappings

    def was_notification_sent(self, dedupe_key: str) -> bool:
        return dedupe_key in self.sent_keys

    def mark_notification_sent(
        self,
        dedupe_key: str,
        contribution_event: Any,
        discord_user_id: str,
        channel_id: str | None,
        github_user: str | None = None,
    ) -> None:
        self.sent_keys.add(dedupe_key)
        if contribution_event:
            self.recorded_events.append(contribution_event)


class DummyDiscordWriter:
    def __init__(self) -> None:
        self.dms: list[tuple[str, str]] = []
        self.messages: list[tuple[str, str]] = []

    def send_dm(self, discord_user_id: str, content: str) -> bool:
        self.dms.append((discord_user_id, content))
        return True

    def send_message(self, channel_id: str, content: str) -> bool:
        self.messages.append((channel_id, content))
        return True


_SENTINEL = object()


class DummyGitHubReader:
    def __init__(
        self,
        prs: list[dict] | None = None,
        comments: list[dict] | None = None,
        reviews: Any = _SENTINEL,
        threads: Any = _SENTINEL,
    ) -> None:
        self.prs = prs or []
        self.comments = comments or []
        self.reviews = [] if reviews is _SENTINEL else reviews
        self.threads = [] if threads is _SENTINEL else threads

    def list_open_pull_requests(self) -> list[dict]:
        return self.prs

    def get_pull_request_review_comments(
        self, org: str, repo: str, pr_number: int
    ) -> list[dict]:
        return self.comments

    def get_pull_request_reviews(
        self, org: str, repo: str, pr_number: int
    ) -> list[dict]:
        return self.reviews

    def get_pull_request_review_threads(
        self, org: str, repo: str, pr_number: int
    ) -> list[dict] | None:
        return self.threads


def _make_config(enabled: bool = True, after_hours: int = 48) -> NotificationConfig:
    return NotificationConfig(
        coderabbit_reminders=enabled,
        coderabbit_reminder_after_hours=after_hours,
        channel_id=None,
    )


def test_coderabbit_reminder_sent_for_active_stale_comment() -> None:
    now = datetime.now(timezone.utc)
    stale_time = (now - timedelta(hours=50)).isoformat()

    pr = {
        "repo": "test-repo",
        "number": 10,
        "author": "alice",
        "draft": False,
        "head": {"sha": "head123"},
    }
    comment = {
        "user": {"login": "coderabbitai"},
        "created_at": stale_time,
        "commit_id": "head123",
        "position": 5,
        "original_position": 5,
        "body": "Consider refactoring this loop.",
    }

    reader = DummyGitHubReader(prs=[pr], comments=[comment])
    storage = DummyStorage()
    writer = DummyDiscordWriter()
    policy = MutationPolicy(
        mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True
    )
    config = _make_config()

    run_coderabbit_reminders(
        github_reader=reader,
        storage=storage,
        discord_writer=writer,
        policy=policy,
        config=config,
        github_org="test-org",
    )

    assert len(writer.dms) == 1
    user_id, message = writer.dms[0]
    assert user_id == "12345"
    assert "test-repo#10" in message
    assert "coderabbit_reminder:test-repo:10:12345" in storage.sent_keys


def test_coderabbit_reminder_skips_draft_pr() -> None:
    now = datetime.now(timezone.utc)
    stale_time = (now - timedelta(hours=50)).isoformat()

    pr = {
        "repo": "test-repo",
        "number": 11,
        "author": "alice",
        "draft": True,  # Draft PR!
        "head": {"sha": "head123"},
    }
    comment = {
        "user": {"login": "coderabbitai"},
        "created_at": stale_time,
        "commit_id": "head123",
        "position": 5,
        "body": "Fix this typo.",
    }

    reader = DummyGitHubReader(prs=[pr], comments=[comment])
    storage = DummyStorage()
    writer = DummyDiscordWriter()
    policy = MutationPolicy(
        mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True
    )

    run_coderabbit_reminders(
        github_reader=reader,
        storage=storage,
        discord_writer=writer,
        policy=policy,
        config=_make_config(),
        github_org="test-org",
    )

    assert len(writer.dms) == 0


def test_coderabbit_reminder_skips_when_coderabbit_review_is_approved() -> None:
    now = datetime.now(timezone.utc)
    stale_time = (now - timedelta(hours=50)).isoformat()

    pr = {
        "repo": "test-repo",
        "number": 12,
        "author": "alice",
        "draft": False,
        "head": {"sha": "head123"},
    }
    comment = {
        "user": {"login": "coderabbitai"},
        "created_at": stale_time,
        "commit_id": "head123",
        "position": 5,
        "body": "Older review suggestion.",
    }
    reviews = [
        {"user": {"login": "coderabbitai"}, "state": "CHANGES_REQUESTED"},
        {"user": {"login": "coderabbitai"}, "state": "APPROVED"},
    ]

    reader = DummyGitHubReader(prs=[pr], comments=[comment], reviews=reviews)
    storage = DummyStorage()
    writer = DummyDiscordWriter()
    policy = MutationPolicy(
        mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True
    )

    run_coderabbit_reminders(
        github_reader=reader,
        storage=storage,
        discord_writer=writer,
        policy=policy,
        config=_make_config(),
        github_org="test-org",
    )

    assert len(writer.dms) == 0


def test_coderabbit_reminder_skips_when_all_threads_resolved() -> None:
    now = datetime.now(timezone.utc)
    stale_time = (now - timedelta(hours=50)).isoformat()

    pr = {
        "repo": "test-repo",
        "number": 13,
        "author": "alice",
        "draft": False,
        "head": {"sha": "head123"},
    }
    comment = {
        "user": {"login": "coderabbitai"},
        "created_at": stale_time,
        "commit_id": "head123",
        "position": 5,
        "body": "Unaddressed thread.",
    }
    threads = [
        {"authors": ["coderabbitai"], "is_resolved": True, "is_outdated": False},
        {"authors": ["coderabbitai"], "is_resolved": False, "is_outdated": True},
    ]

    reader = DummyGitHubReader(prs=[pr], comments=[comment], threads=threads)
    storage = DummyStorage()
    writer = DummyDiscordWriter()
    policy = MutationPolicy(
        mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True
    )

    run_coderabbit_reminders(
        github_reader=reader,
        storage=storage,
        discord_writer=writer,
        policy=policy,
        config=_make_config(),
        github_org="test-org",
    )

    assert len(writer.dms) == 0


def test_coderabbit_reminder_skips_outdated_or_superseded_comments() -> None:
    now = datetime.now(timezone.utc)
    stale_time = (now - timedelta(hours=50)).isoformat()

    pr = {
        "repo": "test-repo",
        "number": 14,
        "author": "alice",
        "draft": False,
        "head": {"sha": "new_sha_456"},
    }
    # Comment 1: position is None, original_position set (outdated diff)
    c1 = {
        "user": {"login": "coderabbitai"},
        "created_at": stale_time,
        "position": None,
        "original_position": 10,
        "commit_id": "new_sha_456",
    }
    # Comment 2: on older superseded commit with outdated diff position
    c2 = {
        "user": {"login": "coderabbitai"},
        "created_at": stale_time,
        "position": None,
        "original_position": 5,
        "commit_id": "old_sha_123",
    }
    # Comment 3: resolved body
    c3 = {
        "user": {"login": "coderabbitai"},
        "created_at": stale_time,
        "position": 5,
        "original_position": 5,
        "commit_id": "new_sha_456",
        "body": "- [x] Fix has been applied.",
    }

    reader = DummyGitHubReader(prs=[pr], comments=[c1, c2, c3])
    storage = DummyStorage()
    writer = DummyDiscordWriter()
    policy = MutationPolicy(
        mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True
    )

    run_coderabbit_reminders(
        github_reader=reader,
        storage=storage,
        discord_writer=writer,
        policy=policy,
        config=_make_config(),
        github_org="test-org",
    )

    assert len(writer.dms) == 0


def test_coderabbit_reminder_deduplication() -> None:
    now = datetime.now(timezone.utc)
    stale_time = (now - timedelta(hours=50)).isoformat()

    pr = {
        "repo": "test-repo",
        "number": 15,
        "author": "alice",
        "draft": False,
        "head": {"sha": "head123"},
    }
    comment = {
        "user": {"login": "coderabbitai"},
        "created_at": stale_time,
        "commit_id": "head123",
        "position": 5,
    }

    reader = DummyGitHubReader(prs=[pr], comments=[comment])
    storage = DummyStorage()
    storage.sent_keys.add("coderabbit_reminder:test-repo:15:12345")
    writer = DummyDiscordWriter()
    policy = MutationPolicy(
        mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True
    )

    run_coderabbit_reminders(
        github_reader=reader,
        storage=storage,
        discord_writer=writer,
        policy=policy,
        config=_make_config(),
        github_org="test-org",
    )

    assert len(writer.dms) == 0


def test_coderabbit_reminder_skips_unlinked_or_disabled() -> None:
    now = datetime.now(timezone.utc)
    stale_time = (now - timedelta(hours=50)).isoformat()

    pr = {
        "repo": "test-repo",
        "number": 16,
        "author": "alice",
        "draft": False,
        "head": {"sha": "head123"},
    }
    comment = {
        "user": {"login": "coderabbitai"},
        "created_at": stale_time,
        "commit_id": "head123",
        "position": 5,
    }

    reader = DummyGitHubReader(prs=[pr], comments=[comment])
    storage = DummyStorage(mappings=[])  # unlinked (explicitly empty list)
    writer = DummyDiscordWriter()
    policy = MutationPolicy(
        mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True
    )

    # 1. Unlinked author (author is alice, but storage has no mappings)
    run_coderabbit_reminders(
        github_reader=reader,
        storage=storage,
        discord_writer=writer,
        policy=policy,
        config=_make_config(enabled=True),
        github_org="test-org",
    )
    assert len(writer.dms) == 0

    # 2. Disabled in config (storage has mapping for alice, but config has enabled=False)
    storage.verified_mappings = [{"discord_user_id": "12345", "github_user": "alice"}]
    run_coderabbit_reminders(
        github_reader=reader,
        storage=storage,
        discord_writer=writer,
        policy=policy,
        config=_make_config(enabled=False),
        github_org="test-org",
    )
    assert len(writer.dms) == 0


def test_coderabbit_reminder_skips_draft_from_rest_adapter_list_repo_open_prs() -> None:
    """_list_repo_open_prs emits 'draft': bool, which run_coderabbit_reminders correctly uses to skip draft PRs."""
    from ghdcbot.adapters.github.rest import GitHubRestAdapter

    adapter = GitHubRestAdapter(token="test-token", org="test-org", api_base="https://api.github.com")
    mock_api_pr = {
        "number": 101,
        "title": "Draft feature",
        "user": {"login": "alice"},
        "html_url": "https://github.com/test-org/repo1/pull/101",
        "created_at": (datetime.now(timezone.utc) - timedelta(hours=60)).isoformat(),
        "head": {"sha": "sha_draft"},
        "draft": True,
    }
    # Mock paginate to yield mock_api_pr
    adapter._paginate = lambda path, params=None: [[mock_api_pr]]  # type: ignore[assignment]

    yielded_prs = list(adapter._list_repo_open_prs({"owner": {"login": "test-org"}, "name": "repo1"}))
    assert len(yielded_prs) == 1
    assert yielded_prs[0]["draft"] is True

    # Pass through run_coderabbit_reminders
    comment = {
        "id": 1,
        "user": {"login": "coderabbitai"},
        "created_at": (datetime.now(timezone.utc) - timedelta(hours=50)).isoformat(),
        "commit_id": "sha_draft",
        "position": 5,
        "body": "Fix this.",
    }
    reader = DummyGitHubReader(prs=yielded_prs, comments=[comment])
    storage = DummyStorage()
    writer = DummyDiscordWriter()
    policy = MutationPolicy(
        mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True
    )
    run_coderabbit_reminders(
        github_reader=reader,
        storage=storage,
        discord_writer=writer,
        policy=policy,
        config=_make_config(),
        github_org="test-org",
    )
    assert len(writer.dms) == 0


def test_coderabbit_reminder_items_indexing_does_not_count_as_resolved() -> None:
    """A comment with indexing syntax like 'items[x]' is not falsely discarded as a resolved checklist."""
    now = datetime.now(timezone.utc)
    stale_time = (now - timedelta(hours=50)).isoformat()

    pr = {
        "repo": "test-repo",
        "number": 102,
        "author": "alice",
        "draft": False,
        "head_sha": "head123",
    }
    comment = {
        "id": 2,
        "user": {"login": "coderabbitai"},
        "created_at": stale_time,
        "commit_id": "head123",
        "position": 5,
        "body": "Avoid `items[x]` here; x can exceed the list length.",
    }

    reader = DummyGitHubReader(prs=[pr], comments=[comment])
    storage = DummyStorage()
    writer = DummyDiscordWriter()
    policy = MutationPolicy(
        mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True
    )
    run_coderabbit_reminders(
        github_reader=reader,
        storage=storage,
        discord_writer=writer,
        policy=policy,
        config=_make_config(),
        github_org="test-org",
    )
    assert len(writer.dms) == 1


def test_coderabbit_reminder_markdown_checklist_resolved_is_skipped() -> None:
    """A comment with markdown checklist where all items are checked is recognized as resolved."""
    now = datetime.now(timezone.utc)
    stale_time = (now - timedelta(hours=50)).isoformat()

    pr = {
        "repo": "test-repo",
        "number": 103,
        "author": "alice",
        "draft": False,
        "head_sha": "head123",
    }
    comment = {
        "id": 3,
        "user": {"login": "coderabbitai"},
        "created_at": stale_time,
        "commit_id": "head123",
        "position": 5,
        "body": "- [x] Fix has been applied to this method.",
    }

    reader = DummyGitHubReader(prs=[pr], comments=[comment])
    storage = DummyStorage()
    writer = DummyDiscordWriter()
    policy = MutationPolicy(
        mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True
    )
    run_coderabbit_reminders(
        github_reader=reader,
        storage=storage,
        discord_writer=writer,
        policy=policy,
        config=_make_config(),
        github_org="test-org",
    )
    assert len(writer.dms) == 0


def test_coderabbit_reminder_different_sha_active_feedback_sends_reminder() -> None:
    """Active feedback on an earlier commit is NOT discarded simply due to SHA inequality."""
    now = datetime.now(timezone.utc)
    stale_time = (now - timedelta(hours=50)).isoformat()

    pr = {
        "repo": "test-repo",
        "number": 104,
        "author": "alice",
        "draft": False,
        "head_sha": "new_commit_456",
    }
    comment = {
        "id": 4,
        "user": {"login": "coderabbitai"},
        "created_at": stale_time,
        "commit_id": "old_commit_123",
        "position": 5,
        "original_position": 5,
        "body": "Active feedback on unchanged line.",
    }

    reader = DummyGitHubReader(prs=[pr], comments=[comment])
    storage = DummyStorage()
    writer = DummyDiscordWriter()
    policy = MutationPolicy(
        mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True
    )
    run_coderabbit_reminders(
        github_reader=reader,
        storage=storage,
        discord_writer=writer,
        policy=policy,
        config=_make_config(),
        github_org="test-org",
    )
    assert len(writer.dms) == 1
    assert "test-repo#104" in writer.dms[0][1]


def test_coderabbit_reminder_different_sha_outdated_diff_position_skipped() -> None:
    """Feedback whose diff position became null (superseded by code modifications) is skipped."""
    now = datetime.now(timezone.utc)
    stale_time = (now - timedelta(hours=50)).isoformat()

    pr = {
        "repo": "test-repo",
        "number": 104,
        "author": "alice",
        "draft": False,
        "head_sha": "new_commit_456",
    }
    comment = {
        "id": 4,
        "user": {"login": "coderabbitai"},
        "created_at": stale_time,
        "commit_id": "old_commit_123",
        "position": None,
        "original_position": 5,
        "body": "Superseded line feedback.",
    }

    reader = DummyGitHubReader(prs=[pr], comments=[comment])
    storage = DummyStorage()
    writer = DummyDiscordWriter()
    policy = MutationPolicy(
        mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True
    )
    run_coderabbit_reminders(
        github_reader=reader,
        storage=storage,
        discord_writer=writer,
        policy=policy,
        config=_make_config(),
        github_org="test-org",
    )
    assert len(writer.dms) == 0


def test_coderabbit_reminder_different_sha_outdated_thread_skipped() -> None:
    """Feedback whose review thread is marked outdated in GraphQL is skipped."""
    now = datetime.now(timezone.utc)
    stale_time = (now - timedelta(hours=50)).isoformat()

    pr = {
        "repo": "test-repo",
        "number": 104,
        "author": "alice",
        "draft": False,
        "head_sha": "new_commit_456",
    }
    comment = {
        "id": 401,
        "user": {"login": "coderabbitai"},
        "created_at": stale_time,
        "commit_id": "old_commit_123",
        "position": 5,
        "body": "Thread outdated feedback.",
    }
    threads = [
        {
            "authors": ["coderabbitai"],
            "is_resolved": False,
            "is_outdated": True,
            "comment_ids": [401],
        }
    ]

    reader = DummyGitHubReader(prs=[pr], comments=[comment], threads=threads)
    storage = DummyStorage()
    writer = DummyDiscordWriter()
    policy = MutationPolicy(
        mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True
    )
    run_coderabbit_reminders(
        github_reader=reader,
        storage=storage,
        discord_writer=writer,
        policy=policy,
        config=_make_config(),
        github_org="test-org",
    )
    assert len(writer.dms) == 0


def test_coderabbit_reminder_same_head_active_feedback_sends_reminder() -> None:
    """Active feedback matching head commit sends reminder."""
    now = datetime.now(timezone.utc)
    stale_time = (now - timedelta(hours=50)).isoformat()

    pr = {
        "repo": "test-repo",
        "number": 104,
        "author": "alice",
        "draft": False,
        "head_sha": "head123",
    }
    comment = {
        "id": 4,
        "user": {"login": "coderabbitai"},
        "created_at": stale_time,
        "commit_id": "head123",
        "position": 5,
        "body": "Fix this.",
    }

    reader = DummyGitHubReader(prs=[pr], comments=[comment])
    storage = DummyStorage()
    writer = DummyDiscordWriter()
    policy = MutationPolicy(
        mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True
    )
    run_coderabbit_reminders(
        github_reader=reader,
        storage=storage,
        discord_writer=writer,
        policy=policy,
        config=_make_config(),
        github_org="test-org",
    )
    assert len(writer.dms) == 1


def test_coderabbit_reminder_skips_when_latest_qualifying_review_is_approved_ignoring_commented() -> None:
    """When CodeRabbit latest review is COMMENTED but the decisive review is APPROVED, skip reminder."""
    now = datetime.now(timezone.utc)
    stale_time = (now - timedelta(hours=50)).isoformat()

    pr = {
        "repo": "test-repo",
        "number": 12,
        "author": "alice",
        "draft": False,
        "head": {"sha": "head123"},
    }
    comment = {
        "user": {"login": "coderabbitai"},
        "created_at": stale_time,
        "commit_id": "head123",
        "position": 5,
        "body": "Older review suggestion.",
    }
    reviews = [
        {"user": {"login": "coderabbitai"}, "state": "CHANGES_REQUESTED"},
        {"user": {"login": "coderabbitai"}, "state": "APPROVED"},
        {"user": {"login": "coderabbitai"}, "state": "COMMENTED"},
    ]

    reader = DummyGitHubReader(prs=[pr], comments=[comment], reviews=reviews)
    storage = DummyStorage()
    writer = DummyDiscordWriter()
    policy = MutationPolicy(
        mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True
    )
    run_coderabbit_reminders(
        github_reader=reader,
        storage=storage,
        discord_writer=writer,
        policy=policy,
        config=_make_config(),
        github_org="test-org",
    )
    assert len(writer.dms) == 0


def test_coderabbit_reminder_does_not_skip_when_latest_qualifying_review_is_changes_requested_ignoring_commented() -> None:
    """When latest qualifying review is CHANGES_REQUESTED even if followed by COMMENTED, send reminder."""
    now = datetime.now(timezone.utc)
    stale_time = (now - timedelta(hours=50)).isoformat()

    pr = {
        "repo": "test-repo",
        "number": 12,
        "author": "alice",
        "draft": False,
        "head": {"sha": "head123"},
    }
    comment = {
        "user": {"login": "coderabbitai"},
        "created_at": stale_time,
        "commit_id": "head123",
        "position": 5,
        "body": "Older review suggestion.",
    }
    reviews = [
        {"user": {"login": "coderabbitai"}, "state": "APPROVED"},
        {"user": {"login": "coderabbitai"}, "state": "CHANGES_REQUESTED"},
        {"user": {"login": "coderabbitai"}, "state": "COMMENTED"},
    ]

    reader = DummyGitHubReader(prs=[pr], comments=[comment], reviews=reviews)
    storage = DummyStorage()
    writer = DummyDiscordWriter()
    policy = MutationPolicy(
        mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True
    )
    run_coderabbit_reminders(
        github_reader=reader,
        storage=storage,
        discord_writer=writer,
        policy=policy,
        config=_make_config(),
        github_org="test-org",
    )
    assert len(writer.dms) == 1


def test_coderabbit_reminder_only_commented_reviews_preserves_safe_behavior() -> None:
    """When CodeRabbit has only COMMENTED reviews, reminder is sent for eligible feedback."""
    now = datetime.now(timezone.utc)
    stale_time = (now - timedelta(hours=50)).isoformat()

    pr = {
        "repo": "test-repo",
        "number": 12,
        "author": "alice",
        "draft": False,
        "head": {"sha": "head123"},
    }
    comment = {
        "user": {"login": "coderabbitai"},
        "created_at": stale_time,
        "commit_id": "head123",
        "position": 5,
        "body": "Older review suggestion.",
    }
    reviews = [
        {"user": {"login": "coderabbitai"}, "state": "COMMENTED"},
    ]

    reader = DummyGitHubReader(prs=[pr], comments=[comment], reviews=reviews)
    storage = DummyStorage()
    writer = DummyDiscordWriter()
    policy = MutationPolicy(
        mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True
    )
    run_coderabbit_reminders(
        github_reader=reader,
        storage=storage,
        discord_writer=writer,
        policy=policy,
        config=_make_config(),
        github_org="test-org",
    )
    assert len(writer.dms) == 1


def test_review_threads_graphql_extracts_full_database_id() -> None:
    """GitHubRestAdapter parses fullDatabaseId into integer comment_ids."""
    from unittest.mock import MagicMock

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
                                "id": "t1",
                                "isResolved": True,
                                "isOutdated": False,
                                "comments": {
                                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                                    "nodes": [
                                        {
                                            "fullDatabaseId": 12345678901,
                                            "author": {"login": "coderabbitai[bot]"},
                                        }
                                    ],
                                },
                            }
                        ],
                    }
                }
            }
        }
    }
    mock_client.post.return_value = mock_resp
    adapter._client = mock_client

    threads = adapter.get_pull_request_review_threads("org", "repo", 42)
    assert threads is not None
    assert len(threads) == 1
    assert threads[0]["comment_ids"] == [12345678901]


def test_coderabbit_reminder_ordered_task_list_all_checked_suppresses_reminder() -> None:
    """Ordered markdown task checklist where all items are checked is suppressed."""
    now = datetime.now(timezone.utc)
    stale_time = (now - timedelta(hours=50)).isoformat()

    pr = {
        "repo": "test-repo",
        "number": 105,
        "author": "alice",
        "draft": False,
        "head_sha": "head123",
    }
    comment = {
        "user": {"login": "coderabbitai"},
        "created_at": stale_time,
        "commit_id": "head123",
        "position": 5,
        "body": "1. [x] Step 1 resolved\n2. [X] Step 2 resolved",
    }

    reader = DummyGitHubReader(prs=[pr], comments=[comment])
    storage = DummyStorage()
    writer = DummyDiscordWriter()
    policy = MutationPolicy(
        mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True
    )
    run_coderabbit_reminders(
        github_reader=reader,
        storage=storage,
        discord_writer=writer,
        policy=policy,
        config=_make_config(),
        github_org="test-org",
    )
    assert len(writer.dms) == 0


def test_coderabbit_reminder_ordered_task_list_has_unchecked_sends_reminder() -> None:
    """Ordered markdown task checklist with an unchecked item sends reminder."""
    now = datetime.now(timezone.utc)
    stale_time = (now - timedelta(hours=50)).isoformat()

    pr = {
        "repo": "test-repo",
        "number": 106,
        "author": "alice",
        "draft": False,
        "head_sha": "head123",
    }
    comment = {
        "user": {"login": "coderabbitai"},
        "created_at": stale_time,
        "commit_id": "head123",
        "position": 5,
        "body": "- [x] Done\n\n1. [ ] Fix remaining bug",
    }

    reader = DummyGitHubReader(prs=[pr], comments=[comment])
    storage = DummyStorage()
    writer = DummyDiscordWriter()
    policy = MutationPolicy(
        mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True
    )
    run_coderabbit_reminders(
        github_reader=reader,
        storage=storage,
        discord_writer=writer,
        policy=policy,
        config=_make_config(),
        github_org="test-org",
    )
    assert len(writer.dms) == 1
    assert "test-repo#106" in writer.dms[0][1]


def test_review_threads_graphql_errors_with_partial_data_returns_none() -> None:
    """When GraphQL response contains query errors, even with partial data, return None."""
    from unittest.mock import MagicMock

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
                        "nodes": [],
                    }
                }
            }
        },
        "errors": [{"message": "Field 'reviewThreads' execution failed"}],
    }
    mock_client.post.return_value = mock_resp
    adapter._client = mock_client

    threads = adapter.get_pull_request_review_threads("org", "repo", 42)
    assert threads is None


def test_review_threads_graphql_unavailable_connection_returns_none() -> None:
    """When GraphQL reviewThreads connection is null/unavailable, return None."""
    from unittest.mock import MagicMock

    from ghdcbot.adapters.github.rest import GitHubRestAdapter

    adapter = GitHubRestAdapter(token="token", org="org", api_base="https://api.github.com")
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": None,
                }
            }
        }
    }
    mock_client.post.return_value = mock_resp
    adapter._client = mock_client

    threads = adapter.get_pull_request_review_threads("org", "repo", 42)
    assert threads is None


def test_coderabbit_reminder_skips_comment_in_resolved_thread_even_if_another_thread_open() -> None:
    """Comments in a resolved thread are excluded by ID even if another thread on the PR is unresolved."""
    now = datetime.now(timezone.utc)
    stale_time = (now - timedelta(hours=72)).isoformat()
    recent_time = (now - timedelta(hours=1)).isoformat()

    pr = {
        "repo": "test-repo",
        "number": 105,
        "author": "alice",
        "draft": False,
        "head_sha": "head123",
    }
    # Comment 101: 72 hours old, but belongs to resolved thread 1
    c1 = {
        "id": 101,
        "user": {"login": "coderabbitai"},
        "created_at": stale_time,
        "commit_id": "head123",
        "position": 5,
        "body": "Stale suggestion in resolved thread.",
    }
    # Comment 102: only 1 hour old, belongs to unresolved thread 2 (not stale)
    c2 = {
        "id": 102,
        "user": {"login": "coderabbitai"},
        "created_at": recent_time,
        "commit_id": "head123",
        "position": 10,
        "body": "Recent suggestion in open thread.",
    }

    threads = [
        {"authors": ["coderabbitai"], "is_resolved": True, "is_outdated": False, "comment_ids": [101]},
        {"authors": ["coderabbitai"], "is_resolved": False, "is_outdated": False, "comment_ids": [102]},
    ]

    reader = DummyGitHubReader(prs=[pr], comments=[c1, c2], threads=threads)
    storage = DummyStorage()
    writer = DummyDiscordWriter()
    policy = MutationPolicy(
        mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True
    )
    run_coderabbit_reminders(
        github_reader=reader,
        storage=storage,
        discord_writer=writer,
        policy=policy,
        config=_make_config(),
        github_org="test-org",
    )
    # c1 is excluded by thread 1 resolution; c2 is not stale (< 48h) -> 0 DMs sent!
    assert len(writer.dms) == 0


def test_coderabbit_reminder_code_block_checklist_does_not_suppress_active_comment() -> None:
    """Checklist markers inside fenced code blocks or inline code do not falsely suppress active comments."""
    now = datetime.now(timezone.utc)
    stale_time = (now - timedelta(hours=50)).isoformat()

    pr = {
        "repo": "test-repo",
        "number": 107,
        "author": "alice",
        "draft": False,
        "head_sha": "head123",
    }
    comment = {
        "user": {"login": "coderabbitai"},
        "created_at": stale_time,
        "commit_id": "head123",
        "position": 5,
        "body": (
            "Here is an active finding on your pull request.\n\n"
            "```markdown\n"
            "- [x] Example of completed item\n"
            "```\n\n"
            "Also refer to `- [x]` inline. Please fix this issue."
        ),
    }

    reader = DummyGitHubReader(prs=[pr], comments=[comment])
    storage = DummyStorage()
    writer = DummyDiscordWriter()
    policy = MutationPolicy(
        mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True
    )
    run_coderabbit_reminders(
        github_reader=reader,
        storage=storage,
        discord_writer=writer,
        policy=policy,
        config=_make_config(),
        github_org="test-org",
    )
    assert len(writer.dms) == 1
    assert "test-repo#107" in writer.dms[0][1]


def test_coderabbit_reminder_early_dedup_skips_api_calls() -> None:
    """When a reminder was already sent, reviews, threads, and comments are never fetched."""
    from unittest.mock import MagicMock

    pr = {
        "repo": "test-repo",
        "number": 108,
        "author": "alice",
        "draft": False,
    }
    reader = DummyGitHubReader(prs=[pr])
    reader.get_pull_request_reviews = MagicMock()
    reader.get_pull_request_review_threads = MagicMock()
    reader.get_pull_request_review_comments = MagicMock()

    storage = DummyStorage()
    storage.sent_keys.add("coderabbit_reminder:test-repo:108:12345")
    writer = DummyDiscordWriter()
    policy = MutationPolicy(
        mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True
    )

    run_coderabbit_reminders(
        github_reader=reader,
        storage=storage,
        discord_writer=writer,
        policy=policy,
        config=_make_config(),
        github_org="test-org",
    )

    assert len(writer.dms) == 0
    reader.get_pull_request_reviews.assert_not_called()
    reader.get_pull_request_review_threads.assert_not_called()
    reader.get_pull_request_review_comments.assert_not_called()


def test_coderabbit_reminder_truncated_threads_does_not_shortcut_skip_pr() -> None:
    """When thread retrieval is truncated/incomplete, postpone reminder without sending DMs or writing dedupe keys."""
    from ghdcbot.adapters.github.rest import ReviewThreadList

    now = datetime.now(timezone.utc)
    stale_time = (now - timedelta(hours=50)).isoformat()

    pr = {
        "repo": "test-repo",
        "number": 109,
        "author": "alice",
        "draft": False,
        "head_sha": "head123",
    }
    comment = {
        "id": 999,
        "user": {"login": "coderabbitai"},
        "created_at": stale_time,
        "commit_id": "head123",
        "position": 5,
        "body": "Active finding on PR.",
    }
    # Threads has 0 unresolved threads in the fetched slice, but is marked truncated/incomplete
    truncated_threads = ReviewThreadList(
        [
            {"authors": ["coderabbitai"], "is_resolved": True, "is_outdated": False, "comment_ids": [101]},
        ],
        truncated=True,
    )

    reader = DummyGitHubReader(prs=[pr], comments=[comment], threads=truncated_threads)
    storage = DummyStorage()
    writer = DummyDiscordWriter()
    policy = MutationPolicy(
        mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True
    )
    run_coderabbit_reminders(
        github_reader=reader,
        storage=storage,
        discord_writer=writer,
        policy=policy,
        config=_make_config(),
        github_org="test-org",
    )
    # When thread results are incomplete, reminders are postponed: no DM sent, no dedupe key written
    assert len(writer.dms) == 0
    assert not any("coderabbit_reminder:test-repo:109" in key for key in storage.sent_keys)


def test_review_threads_graphql_pagination_truncation_sets_flag() -> None:
    """When thread pagination hits max limit and hasNextPage is True, truncated flag is True."""
    from unittest.mock import MagicMock

    from ghdcbot.adapters.github.rest import GitHubRestAdapter

    adapter = GitHubRestAdapter(token="token", org="org", api_base="https://api.github.com")
    mock_client = MagicMock()

    call_count = 0

    def mock_post(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "pageInfo": {"hasNextPage": True, "endCursor": f"cursor_{call_count}"},
                            "nodes": [
                                {
                                    "id": f"t{call_count}",
                                    "isResolved": True,
                                    "isOutdated": False,
                                    "comments": {
                                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                                        "nodes": [],
                                    },
                                }
                            ],
                        }
                    }
                }
            }
        }
        return resp

    mock_client.post.side_effect = mock_post
    adapter._client = mock_client

    threads = adapter.get_pull_request_review_threads("org", "repo", 42)
    assert threads is not None
    assert threads.truncated is True
    assert threads.incomplete is True
    assert len(threads) == 10  # 10 pages * 1 node per page


def test_review_threads_graphql_missing_or_repeated_cursor_sets_truncated() -> None:
    """Missing or repeated cursor when hasNextPage is True marks the result truncated/incomplete."""
    from unittest.mock import MagicMock

    from ghdcbot.adapters.github.rest import GitHubRestAdapter

    adapter = GitHubRestAdapter(token="token", org="org", api_base="https://api.github.com")

    # Case A: hasNextPage is True but endCursor is None
    mock_client = MagicMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": True, "endCursor": None},
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
    mock_client.post.return_value = resp
    adapter._client = mock_client

    threads = adapter.get_pull_request_review_threads("org", "repo", 42)
    assert threads is not None
    assert threads.truncated is True
    assert threads.incomplete is True

    # Case B: hasNextPage is True but endCursor repeats previous cursor
    call_count = 0

    def mock_stuck_post(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        r = MagicMock()
        r.status_code = 200
        r.json.return_value = {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "pageInfo": {"hasNextPage": True, "endCursor": "same_cursor"},
                            "nodes": [
                                {
                                    "id": f"t{call_count}",
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
        return r

    mock_client.post.side_effect = mock_stuck_post
    threads = adapter.get_pull_request_review_threads("org", "repo", 42)
    assert threads is not None
    assert threads.truncated is True
    assert threads.incomplete is True
    assert call_count == 2


def test_review_threads_graphql_comment_pagination_interrupted_sets_truncated() -> None:
    """Interrupted comment pagination (missing cursor, stuck cursor, missing threadId) marks truncated."""
    from unittest.mock import MagicMock

    from ghdcbot.adapters.github.rest import GitHubRestAdapter

    adapter = GitHubRestAdapter(token="token", org="org", api_base="https://api.github.com")

    # Case A: comment hasNextPage is True but endCursor is None
    mock_client = MagicMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                        "nodes": [
                            {
                                "id": "t1",
                                "isResolved": True,
                                "isOutdated": False,
                                "comments": {
                                    "pageInfo": {"hasNextPage": True, "endCursor": None},
                                    "nodes": [],
                                },
                            }
                        ],
                    }
                }
            }
        }
    }
    mock_client.post.return_value = resp
    adapter._client = mock_client

    threads = adapter.get_pull_request_review_threads("org", "repo", 42)
    assert threads is not None
    assert threads.truncated is True
    assert threads.incomplete is True

    # Case B: comment hasNextPage is True but thread id is missing
    resp.json.return_value["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"][0]["id"] = None
    resp.json.return_value["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"][0]["comments"]["pageInfo"]["endCursor"] = "cur1"
    threads = adapter.get_pull_request_review_threads("org", "repo", 42)
    assert threads is not None
    assert threads.truncated is True
    assert threads.incomplete is True


def test_coderabbit_reminder_failed_review_read_postpones_reminder() -> None:
    """When reviews read returns None (failed), reminder is postponed without marking dedupe key."""
    now = datetime.now(timezone.utc)
    stale_time = (now - timedelta(hours=50)).isoformat()

    pr = {
        "repo": "test-repo",
        "number": 110,
        "author": "alice",
        "draft": False,
        "head_sha": "head123",
    }
    comment = {
        "id": 1101,
        "user": {"login": "coderabbitai"},
        "created_at": stale_time,
        "commit_id": "head123",
        "position": 5,
        "body": "Active finding on PR.",
    }

    # reviews=None simulates failed review read
    reader = DummyGitHubReader(prs=[pr], comments=[comment], reviews=None)
    # Reader returns None explicitly
    reader.get_pull_request_reviews = lambda org, repo, num: None  # type: ignore

    storage = DummyStorage()
    writer = DummyDiscordWriter()
    policy = MutationPolicy(
        mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True
    )
    run_coderabbit_reminders(
        github_reader=reader,
        storage=storage,
        discord_writer=writer,
        policy=policy,
        config=_make_config(),
        github_org="test-org",
    )
    # Postponed: no DM sent, no dedupe key recorded
    assert len(writer.dms) == 0
    assert "coderabbit_reminder:test-repo:110:12345" not in storage.sent_keys


def test_coderabbit_reminder_incomplete_review_read_postpones_reminder() -> None:
    """When reviews read returns incomplete list, reminder is postponed without marking dedupe key."""
    from ghdcbot.adapters.github.rest import ReviewList

    now = datetime.now(timezone.utc)
    stale_time = (now - timedelta(hours=50)).isoformat()

    pr = {
        "repo": "test-repo",
        "number": 111,
        "author": "alice",
        "draft": False,
        "head_sha": "head123",
    }
    comment = {
        "id": 1111,
        "user": {"login": "coderabbitai"},
        "created_at": stale_time,
        "commit_id": "head123",
        "position": 5,
        "body": "Active finding on PR.",
    }

    partial_reviews = ReviewList(
        [{"user": {"login": "coderabbitai"}, "state": "CHANGES_REQUESTED"}],
        incomplete=True,
    )
    reader = DummyGitHubReader(prs=[pr], comments=[comment])
    reader.get_pull_request_reviews = lambda org, repo, num: partial_reviews  # type: ignore

    storage = DummyStorage()
    writer = DummyDiscordWriter()
    policy = MutationPolicy(
        mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True
    )
    run_coderabbit_reminders(
        github_reader=reader,
        storage=storage,
        discord_writer=writer,
        policy=policy,
        config=_make_config(),
        github_org="test-org",
    )
    # Postponed
    assert len(writer.dms) == 0
    assert "coderabbit_reminder:test-repo:111:12345" not in storage.sent_keys


def test_coderabbit_reminder_exception_in_reviews_postpones_reminder() -> None:
    """When get_pull_request_reviews raises an exception, reminder is postponed."""
    now = datetime.now(timezone.utc)
    stale_time = (now - timedelta(hours=50)).isoformat()

    pr = {
        "repo": "test-repo",
        "number": 112,
        "author": "alice",
        "draft": False,
        "head_sha": "head123",
    }
    comment = {
        "id": 1121,
        "user": {"login": "coderabbitai"},
        "created_at": stale_time,
        "commit_id": "head123",
        "position": 5,
        "body": "Active finding on PR.",
    }

    reader = DummyGitHubReader(prs=[pr], comments=[comment])

    def _raise(*args, **kwargs):
        raise RuntimeError("API timeout")

    reader.get_pull_request_reviews = _raise  # type: ignore

    storage = DummyStorage()
    writer = DummyDiscordWriter()
    policy = MutationPolicy(
        mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True
    )
    run_coderabbit_reminders(
        github_reader=reader,
        storage=storage,
        discord_writer=writer,
        policy=policy,
        config=_make_config(),
        github_org="test-org",
    )
    assert len(writer.dms) == 0
    assert "coderabbit_reminder:test-repo:112:12345" not in storage.sent_keys


def test_coderabbit_reminder_successful_empty_reviews_sends_reminder() -> None:
    """A genuinely empty reviews result (200 OK with no reviews) remains eligible for comment checks."""
    now = datetime.now(timezone.utc)
    stale_time = (now - timedelta(hours=50)).isoformat()

    pr = {
        "repo": "test-repo",
        "number": 113,
        "author": "alice",
        "draft": False,
        "head_sha": "head123",
    }
    comment = {
        "id": 1131,
        "user": {"login": "coderabbitai"},
        "created_at": stale_time,
        "commit_id": "head123",
        "position": 5,
        "body": "Active finding on PR.",
    }

    reader = DummyGitHubReader(prs=[pr], comments=[comment], reviews=[])
    storage = DummyStorage()
    writer = DummyDiscordWriter()
    policy = MutationPolicy(
        mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True
    )
    run_coderabbit_reminders(
        github_reader=reader,
        storage=storage,
        discord_writer=writer,
        policy=policy,
        config=_make_config(),
        github_org="test-org",
    )
    # Delivered!
    assert len(writer.dms) == 1
    assert "test-repo#113" in writer.dms[0][1]
    assert "coderabbit_reminder:test-repo:113:12345" in storage.sent_keys


def test_get_pull_request_reviews_adapter_outcomes() -> None:
    """GitHubRestAdapter distinguishes successful reads (including empty) from failed or partial reads."""
    from unittest.mock import MagicMock

    from ghdcbot.adapters.github.rest import GitHubRestAdapter

    adapter = GitHubRestAdapter(token="token", org="org", api_base="https://api.github.com")

    # 1. Successful empty read -> returns []
    mock_resp_empty = MagicMock()
    mock_resp_empty.status_code = 200
    mock_resp_empty.json.return_value = []
    mock_resp_empty.headers = {}
    adapter._request = MagicMock(return_value=mock_resp_empty)

    reviews = adapter.get_pull_request_reviews("org", "repo", 1)
    assert reviews is not None
    assert len(reviews) == 0
    assert getattr(reviews, "incomplete", False) is False

    # 2. Failed read on first page -> returns None
    mock_resp_err = MagicMock()
    mock_resp_err.status_code = 500
    mock_resp_err.headers = {}
    adapter._request = MagicMock(return_value=mock_resp_err)

    reviews = adapter.get_pull_request_reviews("org", "repo", 1)
    assert reviews is None

    # 3. Partial read failure (page 1 succeeds, page 2 fails) -> returns None
    page_count = 0

    def mock_paginate_req(method, path, params=None):
        nonlocal page_count
        page_count += 1
        resp = MagicMock()
        if page_count == 1:
            resp.status_code = 200
            resp.json.return_value = [{"id": 1, "state": "COMMENTED"}]
            resp.headers = {"Link": '<https://api.github.com/next>; rel="next"'}
            return resp
        resp.status_code = 500
        resp.headers = {}
        return resp

    adapter._request = mock_paginate_req
    reviews = adapter.get_pull_request_reviews("org", "repo", 1)
    assert reviews is None

    # 4. Non-list payload (e.g. error dict {"message": "Not Found"}) -> returns None
    mock_resp_nonlist = MagicMock()
    mock_resp_nonlist.status_code = 200
    mock_resp_nonlist.json.return_value = {"message": "Not Found"}
    mock_resp_nonlist.headers = {}
    adapter._request = MagicMock(return_value=mock_resp_nonlist)
    reviews = adapter.get_pull_request_reviews("org", "repo", 1)
    assert reviews is None

    # 5. Non-list payload on page 2 -> returns None
    page_count = 0

    def mock_paginate_nonlist_page2(method, path, params=None):
        nonlocal page_count
        page_count += 1
        resp = MagicMock()
        if page_count == 1:
            resp.status_code = 200
            resp.json.return_value = [{"id": 1, "state": "COMMENTED"}]
            resp.headers = {"Link": '<https://api.github.com/next>; rel="next"'}
            return resp
        resp.status_code = 200
        resp.json.return_value = {"message": "Unexpected dict on page 2"}
        resp.headers = {}
        return resp

    adapter._request = mock_paginate_nonlist_page2
    reviews = adapter.get_pull_request_reviews("org", "repo", 1)
    assert reviews is None


def test_coderabbit_reminder_failed_threads_read_postpones_reminder() -> None:
    """When get_pull_request_review_threads returns None (failed), reminder is postponed."""
    now = datetime.now(timezone.utc)
    stale_time = (now - timedelta(hours=50)).isoformat()

    pr = {
        "repo": "test-repo",
        "number": 114,
        "author": "alice",
        "draft": False,
        "head_sha": "head123",
    }
    comment = {
        "id": 1141,
        "user": {"login": "coderabbitai"},
        "created_at": stale_time,
        "commit_id": "head123",
        "position": 5,
        "body": "Active finding on PR.",
    }

    reader = DummyGitHubReader(prs=[pr], comments=[comment], threads=None)
    storage = DummyStorage()
    writer = DummyDiscordWriter()
    policy = MutationPolicy(
        mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True
    )
    run_coderabbit_reminders(
        github_reader=reader,
        storage=storage,
        discord_writer=writer,
        policy=policy,
        config=_make_config(),
        github_org="test-org",
    )
    assert len(writer.dms) == 0
    assert "coderabbit_reminder:test-repo:114:12345" not in storage.sent_keys


def test_coderabbit_reminder_exception_in_threads_read_postpones_reminder() -> None:
    """When get_pull_request_review_threads raises an exception, reminder is postponed."""
    now = datetime.now(timezone.utc)
    stale_time = (now - timedelta(hours=50)).isoformat()

    pr = {
        "repo": "test-repo",
        "number": 115,
        "author": "alice",
        "draft": False,
        "head_sha": "head123",
    }
    comment = {
        "id": 1151,
        "user": {"login": "coderabbitai"},
        "created_at": stale_time,
        "commit_id": "head123",
        "position": 5,
        "body": "Active finding on PR.",
    }

    reader = DummyGitHubReader(prs=[pr], comments=[comment])

    def _raise(*args, **kwargs):
        raise RuntimeError("GraphQL query error")

    reader.get_pull_request_review_threads = _raise  # type: ignore

    storage = DummyStorage()
    writer = DummyDiscordWriter()
    policy = MutationPolicy(
        mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True
    )
    run_coderabbit_reminders(
        github_reader=reader,
        storage=storage,
        discord_writer=writer,
        policy=policy,
        config=_make_config(),
        github_org="test-org",
    )
    assert len(writer.dms) == 0
    assert "coderabbit_reminder:test-repo:115:12345" not in storage.sent_keys
