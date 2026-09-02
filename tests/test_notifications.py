"""Tests for verified-only GitHub → Discord notifications."""

from datetime import UTC, datetime
from threading import Barrier, Thread
from unittest.mock import MagicMock

import httpx

from ghdcbot.adapters.github.rest import GitHubRestAdapter
from ghdcbot.adapters.storage.sqlite import SqliteStorage
from ghdcbot.config.models import NotificationConfig
from ghdcbot.core.models import ContributionEvent
from ghdcbot.core.modes import MutationPolicy, RunMode
from ghdcbot.engine.notifications import (
    _build_dedupe_key,
    _build_notification_message,
    _build_pr_opened_channel_message,
    _build_pr_opened_github_link_comment,
    _sanitize_discord_pr_title,
    send_notification_for_event,
    send_pr_opened_channel_notification,
    send_pr_opened_github_link_comment,
    update_pr_channel_announcement_for_event,
)


class MockStorage:
    """Mock storage for testing."""
    
    def __init__(self) -> None:
        self.verified_mappings: list[dict] = []
        self.notifications_sent: set[str] = set()
        self.audit_events: list[dict] = []
        self.pr_channel_announcements: dict[tuple[str, int], dict] = {}
    
    def list_verified_identity_mappings(self) -> list[dict]:
        return self.verified_mappings
    
    def was_notification_sent(self, dedupe_key: str) -> bool:
        return dedupe_key in self.notifications_sent
    
    def claim_notification_sent(self, *args: object, **kwargs: object) -> bool:
        dedupe_key = args[0] if args else kwargs.get("dedupe_key", "")
        if dedupe_key in self.notifications_sent:
            return False
        self.notifications_sent.add(str(dedupe_key))
        return True

    def release_notification_claim(self, dedupe_key: str) -> None:
        self.notifications_sent.discard(dedupe_key)

    def mark_notification_sent(self, *args: object, **kwargs: object) -> None:
        dedupe_key = args[0] if args else kwargs.get("dedupe_key", "")
        self.notifications_sent.add(dedupe_key)
    
    def append_audit_event(self, event: dict) -> None:
        self.audit_events.append(event)

    def save_pr_channel_announcement(self, **kwargs: object) -> None:
        repo = str(kwargs["repo"])
        pr_number = int(kwargs["pr_number"])  # type: ignore[arg-type]
        self.pr_channel_announcements[(repo, pr_number)] = {
            "repo": repo,
            "pr_number": pr_number,
            "channel_id": str(kwargs["channel_id"]),
            "message_id": str(kwargs["message_id"]),
            "status": str(kwargs.get("status") or "open"),
            "pr_title": kwargs.get("pr_title"),
            "author_github": kwargs.get("author_github"),
        }

    def get_pr_channel_announcement(self, repo: str, pr_number: int) -> dict | None:
        return self.pr_channel_announcements.get((repo, int(pr_number)))

    def mark_pr_channel_announcement_status(
        self, repo: str, pr_number: int, status: str
    ) -> None:
        row = self.pr_channel_announcements.get((repo, int(pr_number)))
        if row:
            row["status"] = status


class MockDiscordWriter:
    """Mock Discord writer for testing."""
    
    def __init__(self) -> None:
        self.dms_sent: list[tuple[str, str]] = []
        self.messages_sent: list[tuple[str, str]] = []
        self.messages_edited: list[tuple[str, str, str]] = []
        self._next_message_id = 1000
    
    def send_dm(self, discord_user_id: str, content: str) -> bool:
        self.dms_sent.append((discord_user_id, content))
        return True
    
    def send_message(self, channel_id: str, content: str) -> bool:
        self.messages_sent.append((channel_id, content))
        return True

    def create_message(self, channel_id: str, content: str) -> str | None:
        if not content:
            return ""
        self.messages_sent.append((channel_id, content))
        self._next_message_id += 1
        return str(self._next_message_id)

    def edit_message(
        self,
        channel_id: str,
        message_id: str,
        content: str,
        *,
        embeds: list[dict] | None = None,
    ) -> bool:
        self.messages_edited.append((channel_id, message_id, content, embeds))
        return True


def test_build_dedupe_key() -> None:
    """Test deduplication key building."""
    event = ContributionEvent(
        github_user="alice",
        event_type="issue_assigned",
        repo="test-repo",
        created_at=datetime.now(UTC),
        payload={"issue_number": 123},
    )
    key = _build_dedupe_key(event, "alice")
    assert key == "issue_assigned:test-repo:123:alice"
    
    # Different target user (for pr_reviewed)
    key2 = _build_dedupe_key(event, "bob")
    assert key2 == "issue_assigned:test-repo:123:bob"


def test_build_notification_message_issue_assigned() -> None:
    """Test building notification message for issue assignment."""
    event = ContributionEvent(
        github_user="alice",
        event_type="issue_assigned",
        repo="test-repo",
        created_at=datetime.now(UTC),
        payload={
            "issue_number": 123,
            "title": "Fix bug",
            "assigned_by": "mentor",
        },
    )
    msg = _build_notification_message(event, "issue_assigned", "test-org", "alice")
    assert "Issue Assigned" in msg
    assert "#123" in msg
    assert "Fix bug" in msg
    assert "test-org/test-repo" in msg
    assert "mentor" in msg
    assert "Assigned" in msg


def test_build_notification_message_pr_approved() -> None:
    """Test building notification message for PR approval."""
    event = ContributionEvent(
        github_user="reviewer",
        event_type="pr_reviewed",
        repo="test-repo",
        created_at=datetime.now(UTC),
        payload={
            "pr_number": 456,
            "state": "APPROVED",
            "pr_author": "contributor",
        },
    )
    msg = _build_notification_message(event, "pr_approved", "test-org", "contributor")
    assert "PR Approved" in msg
    assert "#456" in msg
    assert "reviewer" in msg
    assert "Ready to merge" in msg


def test_build_notification_message_pr_changes_requested() -> None:
    """Test building notification message for changes requested."""
    event = ContributionEvent(
        github_user="reviewer",
        event_type="pr_reviewed",
        repo="test-repo",
        created_at=datetime.now(UTC),
        payload={
            "pr_number": 789,
            "state": "CHANGES_REQUESTED",
            "pr_author": "contributor",
        },
    )
    msg = _build_notification_message(event, "pr_changes_requested", "test-org", "contributor")
    assert "Changes Requested" in msg
    assert "#789" in msg
    assert "reviewer" in msg
    assert "needs some updates" in msg


def test_build_notification_message_pr_review_comment() -> None:
    """Test building notification message for review comments."""
    event = ContributionEvent(
        github_user="bhavik",
        event_type="pr_reviewed",
        repo="Gitcord",
        created_at=datetime.now(UTC),
        payload={
            "pr_number": 123,
            "state": "COMMENT",
            "pr_author": "contributor",
            "title": "Fix onboarding validation",
        },
    )
    msg = _build_notification_message(event, "pr_review_comment", "AOSSIE", "contributor")
    assert "New Review Comments" in msg
    assert "#123" in msg
    assert "bhavik" in msg
    assert "AOSSIE/Gitcord" in msg
    assert "Fix onboarding validation" in msg
    assert "Review the feedback" in msg


def test_build_notification_message_pr_merged() -> None:
    """Test building notification message for PR merge."""
    event = ContributionEvent(
        github_user="contributor",
        event_type="pr_merged",
        repo="test-repo",
        created_at=datetime.now(UTC),
        payload={"pr_number": 999, "base_branch": "dev"},
    )
    msg = _build_notification_message(event, "pr_merged", "test-org", "contributor")
    assert "PR Merged" in msg
    assert "#999" in msg
    assert "merged into the `dev` branch" in msg
    assert "main branch" not in msg
    assert "Thank you for your contribution" in msg


def test_build_notification_message_pr_merged_without_base_branch() -> None:
    """Older events without base_branch must not claim merge into main."""
    event = ContributionEvent(
        github_user="contributor",
        event_type="pr_merged",
        repo="test-repo",
        created_at=datetime.now(UTC),
        payload={"pr_number": 100},
    )
    msg = _build_notification_message(event, "pr_merged", "test-org", "contributor")
    assert "has been merged" in msg
    assert "main branch" not in msg
    assert "main`" not in msg


def test_build_notification_message_pr_closed_template() -> None:
    """Test building notification message for PR closed without merge."""
    event = ContributionEvent(
        github_user="contributor",
        event_type="pr_closed",
        repo="Gitcord",
        created_at=datetime.now(UTC),
        payload={
            "pr_number": 123,
            "pr_title": "Improve onboarding validation",
            "pr_author": "contributor",
            "html_url": "https://github.com/AOSSIE/Gitcord/pull/123",
        },
    )
    msg = _build_notification_message(event, "pr_closed", "AOSSIE", "contributor")
    assert "PR Closed" in msg
    assert "#123" in msg
    assert "Improve onboarding validation" in msg
    assert "AOSSIE/Gitcord" in msg
    assert "closed without being merged" in msg
    assert "Review the discussion" in msg


def test_send_notification_pr_closed() -> None:
    """Test notification for PR closed without merge."""
    storage = MockStorage()
    storage.verified_mappings = [
        {"discord_user_id": "discord123", "github_user": "contributor"},
    ]
    discord_writer = MockDiscordWriter()
    config = NotificationConfig(enabled=True, pr_closed=True)
    policy = MutationPolicy(mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True)

    event = ContributionEvent(
        github_user="contributor",
        event_type="pr_closed",
        repo="Gitcord",
        created_at=datetime.now(UTC),
        payload={
            "pr_number": 123,
            "pr_title": "Improve onboarding validation",
            "pr_author": "contributor",
            "html_url": "https://github.com/AOSSIE/Gitcord/pull/123",
        },
    )

    result = send_notification_for_event(
        event, storage, discord_writer, policy, config, "AOSSIE"
    )

    assert result is True
    assert len(discord_writer.dms_sent) == 1
    assert "PR Closed" in discord_writer.dms_sent[0][1]


def test_pr_closed_disabled() -> None:
    """Test that pr_closed notifications are skipped when disabled."""
    storage = MockStorage()
    storage.verified_mappings = [
        {"discord_user_id": "discord123", "github_user": "contributor"},
    ]
    discord_writer = MockDiscordWriter()
    config = NotificationConfig(enabled=True, pr_closed=False)
    policy = MutationPolicy(mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True)

    event = ContributionEvent(
        github_user="contributor",
        event_type="pr_closed",
        repo="Gitcord",
        created_at=datetime.now(UTC),
        payload={
            "pr_number": 123,
            "pr_author": "contributor",
        },
    )

    result = send_notification_for_event(
        event, storage, discord_writer, policy, config, "AOSSIE"
    )

    assert result is False
    assert len(discord_writer.dms_sent) == 0


def test_pr_closed_dedupe() -> None:
    """Test that duplicate pr_closed notifications are not sent."""
    storage = MockStorage()
    storage.verified_mappings = [
        {"discord_user_id": "discord123", "github_user": "contributor"},
    ]
    discord_writer = MockDiscordWriter()
    config = NotificationConfig(enabled=True, pr_closed=True)
    policy = MutationPolicy(mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True)

    event = ContributionEvent(
        github_user="contributor",
        event_type="pr_closed",
        repo="Gitcord",
        created_at=datetime.now(UTC),
        payload={
            "pr_number": 123,
            "pr_author": "contributor",
            "closed_at": "2024-06-26T09:00:00Z",
        },
    )
    storage.notifications_sent.add(_build_dedupe_key(event, "contributor"))

    result = send_notification_for_event(
        event, storage, discord_writer, policy, config, "AOSSIE"
    )

    assert result is False
    assert len(discord_writer.dms_sent) == 0


def test_send_notification_unverified_user() -> None:
    """Test that unverified users don't receive notifications."""
    storage = MockStorage()
    discord_writer = MockDiscordWriter()
    config = NotificationConfig(enabled=True, issue_assignment=True)
    policy = MutationPolicy(mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True)
    
    event = ContributionEvent(
        github_user="unverified",
        event_type="issue_assigned",
        repo="test-repo",
        created_at=datetime.now(UTC),
        payload={"issue_number": 123, "title": "Test"},
    )
    
    result = send_notification_for_event(
        event, storage, discord_writer, policy, config, "test-org"
    )
    
    assert result is False
    assert len(discord_writer.dms_sent) == 0


def test_send_notification_verified_user() -> None:
    """Test that verified users receive notifications."""
    storage = MockStorage()
    storage.verified_mappings = [
        {"discord_user_id": "discord123", "github_user": "alice"},
    ]
    discord_writer = MockDiscordWriter()
    config = NotificationConfig(enabled=True, issue_assignment=True)
    policy = MutationPolicy(mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True)
    
    event = ContributionEvent(
        github_user="alice",
        event_type="issue_assigned",
        repo="test-repo",
        created_at=datetime.now(UTC),
        payload={"issue_number": 123, "title": "Test"},
    )
    
    result = send_notification_for_event(
        event, storage, discord_writer, policy, config, "test-org"
    )
    
    assert result is True
    assert len(discord_writer.dms_sent) == 1
    assert discord_writer.dms_sent[0][0] == "discord123"
    assert "Issue Assigned" in discord_writer.dms_sent[0][1]
    assert "123" in discord_writer.dms_sent[0][1]


def test_send_notification_disabled_config() -> None:
    """Test that notifications are skipped when config is disabled."""
    storage = MockStorage()
    storage.verified_mappings = [
        {"discord_user_id": "discord123", "github_user": "alice"},
    ]
    discord_writer = MockDiscordWriter()
    config = NotificationConfig(enabled=False, issue_assignment=True)
    policy = MutationPolicy(mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True)
    
    event = ContributionEvent(
        github_user="alice",
        event_type="issue_assigned",
        repo="test-repo",
        created_at=datetime.now(UTC),
        payload={"issue_number": 123, "title": "Test"},
    )
    
    result = send_notification_for_event(
        event, storage, discord_writer, policy, config, "test-org"
    )
    
    assert result is False
    assert len(discord_writer.dms_sent) == 0


def test_send_notification_event_type_disabled() -> None:
    """Test that specific event types can be disabled."""
    storage = MockStorage()
    storage.verified_mappings = [
        {"discord_user_id": "discord123", "github_user": "alice"},
    ]
    discord_writer = MockDiscordWriter()
    config = NotificationConfig(enabled=True, issue_assignment=False, pr_merged=True)
    policy = MutationPolicy(mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True)
    
    event = ContributionEvent(
        github_user="alice",
        event_type="issue_assigned",
        repo="test-repo",
        created_at=datetime.now(UTC),
        payload={"issue_number": 123, "title": "Test"},
    )
    
    result = send_notification_for_event(
        event, storage, discord_writer, policy, config, "test-org"
    )
    
    assert result is False
    assert len(discord_writer.dms_sent) == 0


def test_send_notification_deduplication() -> None:
    """Test that duplicate notifications are not sent."""
    storage = MockStorage()
    storage.verified_mappings = [
        {"discord_user_id": "discord123", "github_user": "alice"},
    ]
    storage.notifications_sent.add("issue_assigned:test-repo:123:alice")
    discord_writer = MockDiscordWriter()
    config = NotificationConfig(enabled=True, issue_assignment=True)
    policy = MutationPolicy(mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True)
    
    event = ContributionEvent(
        github_user="alice",
        event_type="issue_assigned",
        repo="test-repo",
        created_at=datetime.now(UTC),
        payload={"issue_number": 123, "title": "Test"},
    )
    
    result = send_notification_for_event(
        event, storage, discord_writer, policy, config, "test-org"
    )
    
    assert result is False
    assert len(discord_writer.dms_sent) == 0


def test_send_notification_dry_run() -> None:
    """Test that notifications are skipped in dry-run mode."""
    storage = MockStorage()
    storage.verified_mappings = [
        {"discord_user_id": "discord123", "github_user": "alice"},
    ]
    discord_writer = MockDiscordWriter()
    config = NotificationConfig(enabled=True, issue_assignment=True)
    policy = MutationPolicy(mode=RunMode.DRY_RUN, github_write_allowed=True, discord_write_allowed=False)
    
    event = ContributionEvent(
        github_user="alice",
        event_type="issue_assigned",
        repo="test-repo",
        created_at=datetime.now(UTC),
        payload={"issue_number": 123, "title": "Test"},
    )
    
    result = send_notification_for_event(
        event, storage, discord_writer, policy, config, "test-org"
    )
    
    assert result is False
    assert len(discord_writer.dms_sent) == 0


def test_send_notification_pr_reviewed_approved() -> None:
    """Test notification for PR approved review."""
    storage = MockStorage()
    storage.verified_mappings = [
        {"discord_user_id": "discord123", "github_user": "contributor"},
    ]
    discord_writer = MockDiscordWriter()
    config = NotificationConfig(enabled=True, pr_review_result=True)
    policy = MutationPolicy(mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True)
    
    event = ContributionEvent(
        github_user="reviewer",
        event_type="pr_reviewed",
        repo="test-repo",
        created_at=datetime.now(UTC),
        payload={
            "pr_number": 456,
            "state": "APPROVED",
            "pr_author": "contributor",
        },
    )
    
    result = send_notification_for_event(
        event, storage, discord_writer, policy, config, "test-org"
    )
    
    assert result is True
    assert len(discord_writer.dms_sent) == 1
    assert "PR Approved" in discord_writer.dms_sent[0][1]
    assert "reviewer" in discord_writer.dms_sent[0][1]


def test_send_notification_pr_reviewed_comment() -> None:
    """Comment-only reviews never DM, even when pr_review_comment is true."""
    storage = MockStorage()
    storage.verified_mappings = [
        {"discord_user_id": "discord123", "github_user": "contributor"},
    ]
    discord_writer = MockDiscordWriter()
    config = NotificationConfig(enabled=True, pr_review_comment=True)
    policy = MutationPolicy(mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True)

    event = ContributionEvent(
        github_user="reviewer",
        event_type="pr_reviewed",
        repo="test-repo",
        created_at=datetime.now(UTC),
        payload={
            "pr_number": 456,
            "state": "COMMENT",
            "pr_author": "contributor",
            "review_id": 9001,
            "title": "Fix onboarding validation",
        },
    )

    result = send_notification_for_event(
        event, storage, discord_writer, policy, config, "test-org"
    )

    assert result is False
    assert discord_writer.dms_sent == []


def test_send_notification_skips_self_review_comment() -> None:
    """PR author replies show up as COMMENTED reviews — do not DM them as reviewer."""
    storage = MockStorage()
    storage.verified_mappings = [
        {"discord_user_id": "discord123", "github_user": "Sashang-debug"},
    ]
    discord_writer = MockDiscordWriter()
    config = NotificationConfig(enabled=True, pr_review_comment=True)
    policy = MutationPolicy(mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True)

    event = ContributionEvent(
        github_user="Sashang-debug",
        event_type="pr_reviewed",
        repo="Gitcord-GithubDiscordBot",
        created_at=datetime.now(UTC),
        payload={
            "pr_number": 51,
            "state": "COMMENTED",
            "pr_author": "sashang-debug",  # case differs from github_user
            "review_id": 4999427982,
        },
    )

    result = send_notification_for_event(
        event, storage, discord_writer, policy, config, "AOSSIE-Org"
    )

    assert result is False
    assert discord_writer.dms_sent == []


def test_send_notification_pr_reviewed_comment_disabled() -> None:
    """Test that COMMENT reviews are skipped when pr_review_comment is false."""
    storage = MockStorage()
    storage.verified_mappings = [
        {"discord_user_id": "discord123", "github_user": "contributor"},
    ]
    discord_writer = MockDiscordWriter()
    config = NotificationConfig(enabled=True, pr_review_comment=False)
    policy = MutationPolicy(mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True)

    event = ContributionEvent(
        github_user="reviewer",
        event_type="pr_reviewed",
        repo="test-repo",
        created_at=datetime.now(UTC),
        payload={
            "pr_number": 456,
            "state": "COMMENT",
            "pr_author": "contributor",
            "review_id": 9001,
        },
    )

    result = send_notification_for_event(
        event, storage, discord_writer, policy, config, "test-org"
    )

    assert result is False
    assert len(discord_writer.dms_sent) == 0


def test_send_notification_pr_reviewed_comment_dedupe() -> None:
    """Test that duplicate COMMENT review notifications are not sent."""
    storage = MockStorage()
    storage.verified_mappings = [
        {"discord_user_id": "discord123", "github_user": "contributor"},
    ]
    storage.notifications_sent.add(
        "pr_reviewed:test-repo:456:contributor:reviewer:COMMENT"
    )
    discord_writer = MockDiscordWriter()
    config = NotificationConfig(enabled=True, pr_review_comment=True)
    policy = MutationPolicy(mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True)

    event = ContributionEvent(
        github_user="reviewer",
        event_type="pr_reviewed",
        repo="test-repo",
        created_at=datetime.now(UTC),
        payload={
            "pr_number": 456,
            "state": "COMMENT",
            "pr_author": "contributor",
            "review_id": 9001,
        },
    )

    result = send_notification_for_event(
        event, storage, discord_writer, policy, config, "test-org"
    )

    assert result is False
    assert len(discord_writer.dms_sent) == 0


def test_pr_review_comment_coalesces_multiple_review_ids_from_same_reviewer() -> None:
    """Comment-only reviews are hard-disabled; no DMs even across many review_ids."""
    storage = MockStorage()
    storage.verified_mappings = [
        {"discord_user_id": "discord123", "github_user": "contributor"},
    ]
    discord_writer = MockDiscordWriter()
    config = NotificationConfig(enabled=True, pr_review_comment=True)
    policy = MutationPolicy(mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True)

    def _event(review_id: int) -> ContributionEvent:
        return ContributionEvent(
            github_user="coderabbitai[bot]",
            event_type="pr_reviewed",
            repo="Gitcord-GithubDiscordBot",
            created_at=datetime.now(UTC),
            payload={
                "pr_number": 56,
                "state": "COMMENTED",
                "pr_author": "contributor",
                "review_id": review_id,
            },
        )

    assert send_notification_for_event(
        _event(1001), storage, discord_writer, policy, config, "AOSSIE-Org"
    ) is False
    assert send_notification_for_event(
        _event(1002), storage, discord_writer, policy, config, "AOSSIE-Org"
    ) is False
    assert discord_writer.dms_sent == []


def test_changes_requested_coalesces_multiple_reviews_from_same_reviewer() -> None:
    """Many CHANGES_REQUESTED rounds / messages from one reviewer → one DM."""
    storage = MockStorage()
    storage.verified_mappings = [
        {"discord_user_id": "discord123", "github_user": "contributor"},
    ]
    discord_writer = MockDiscordWriter()
    config = NotificationConfig(enabled=True, pr_review_result=True)
    policy = MutationPolicy(mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True)

    def _event(review_id: int) -> ContributionEvent:
        return ContributionEvent(
            github_user="mentor1",
            event_type="pr_reviewed",
            repo="Gitcord-GithubDiscordBot",
            created_at=datetime.now(UTC),
            payload={
                "pr_number": 56,
                "state": "CHANGES_REQUESTED",
                "pr_author": "contributor",
                "review_id": review_id,
            },
        )

    assert send_notification_for_event(
        _event(2001), storage, discord_writer, policy, config, "AOSSIE-Org"
    )
    assert send_notification_for_event(
        _event(2002), storage, discord_writer, policy, config, "AOSSIE-Org"
    ) is False
    assert len(discord_writer.dms_sent) == 1
    assert "Changes Requested" in discord_writer.dms_sent[0][1]


def test_build_dedupe_key_comment_reviews_ignore_review_id() -> None:
    base = {
        "pr_number": 56,
        "state": "COMMENT",
        "pr_author": "contributor",
    }
    e1 = ContributionEvent(
        github_user="coderabbitai[bot]",
        event_type="pr_reviewed",
        repo="r",
        created_at=datetime.now(UTC),
        payload={**base, "review_id": 1},
    )
    e2 = ContributionEvent(
        github_user="coderabbitai[bot]",
        event_type="pr_reviewed",
        repo="r",
        created_at=datetime.now(UTC),
        payload={**base, "review_id": 2, "state": "COMMENTED"},
    )
    key1 = _build_dedupe_key(e1, "contributor")
    key2 = _build_dedupe_key(e2, "contributor")
    assert key1 == key2
    assert key1 == "pr_reviewed:r:56:contributor:coderabbitai[bot]:COMMENT"


def test_send_notification_channel_mode() -> None:
    """Test that notifications can be sent to a channel instead of DM."""
    storage = MockStorage()
    storage.verified_mappings = [
        {"discord_user_id": "discord123", "github_user": "alice"},
    ]
    discord_writer = MockDiscordWriter()
    config = NotificationConfig(enabled=True, issue_assignment=True, channel_id="channel123")
    policy = MutationPolicy(mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True)
    
    event = ContributionEvent(
        github_user="alice",
        event_type="issue_assigned",
        repo="test-repo",
        created_at=datetime.now(UTC),
        payload={"issue_number": 123, "title": "Test"},
    )
    
    result = send_notification_for_event(
        event, storage, discord_writer, policy, config, "test-org"
    )
    
    assert result is True
    assert len(discord_writer.messages_sent) == 1
    assert discord_writer.messages_sent[0][0] == "channel123"
    assert len(discord_writer.dms_sent) == 0


def test_send_notification_audit_logging() -> None:
    """Test that notifications are audited."""
    storage = MockStorage()
    storage.verified_mappings = [
        {"discord_user_id": "discord123", "github_user": "alice"},
    ]
    discord_writer = MockDiscordWriter()
    config = NotificationConfig(enabled=True, issue_assignment=True)
    policy = MutationPolicy(mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True)
    
    event = ContributionEvent(
        github_user="alice",
        event_type="issue_assigned",
        repo="test-repo",
        created_at=datetime.now(UTC),
        payload={"issue_number": 123, "title": "Test"},
    )
    
    send_notification_for_event(event, storage, discord_writer, policy, config, "test-org")
    
    assert len(storage.audit_events) == 1
    audit = storage.audit_events[0]
    assert audit["event_type"] == "github_notification_sent"
    assert audit["context"]["github_user"] == "alice"
    assert audit["context"]["discord_user_id"] == "discord123"
    assert audit["context"]["event_type"] == "issue_assigned"
    assert audit["context"]["notification_type"] == "dm"


def test_send_notification_issue_reopened() -> None:
    """Test notification for issue reopened."""
    storage = MockStorage()
    storage.verified_mappings = [
        {"discord_user_id": "discord123", "github_user": "alice"},
    ]
    discord_writer = MockDiscordWriter()
    config = NotificationConfig(enabled=True, issue_reopened=True)
    policy = MutationPolicy(mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True)

    event = ContributionEvent(
        github_user="alice",
        event_type="issue_reopened",
        repo="Gitcord",
        created_at=datetime.now(UTC),
        payload={
            "issue_number": 123,
            "title": "Improve onboarding",
            "assignee": "alice",
            "repository": "Gitcord",
            "html_url": "https://github.com/AOSSIE/Gitcord/issues/123",
            "reopened_at": "2024-06-26T10:30:00Z",
        },
    )

    result = send_notification_for_event(
        event, storage, discord_writer, policy, config, "AOSSIE"
    )

    assert result is True
    assert len(discord_writer.dms_sent) == 1
    assert "Issue Reopened" in discord_writer.dms_sent[0][1]
    assert "123" in discord_writer.dms_sent[0][1]
    assert "Improve onboarding" in discord_writer.dms_sent[0][1]


def test_send_notification_pr_reopened() -> None:
    """Test notification for PR reopened."""
    storage = MockStorage()
    storage.verified_mappings = [
        {"discord_user_id": "discord456", "github_user": "bob"},
    ]
    discord_writer = MockDiscordWriter()
    config = NotificationConfig(enabled=True, pr_reopened=True)
    policy = MutationPolicy(mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True)

    event = ContributionEvent(
        github_user="bob",
        event_type="pr_reopened",
        repo="Gitcord",
        created_at=datetime.now(UTC),
        payload={
            "pr_number": 456,
            "title": "Improve onboarding validation",
            "pr_author": "bob",
            "repository": "Gitcord",
            "html_url": "https://github.com/AOSSIE/Gitcord/pull/456",
            "reopened_at": "2024-06-26T11:00:00Z",
        },
    )

    result = send_notification_for_event(
        event, storage, discord_writer, policy, config, "AOSSIE"
    )

    assert result is True
    assert len(discord_writer.dms_sent) == 1
    assert "PR Reopened" in discord_writer.dms_sent[0][1]
    assert "456" in discord_writer.dms_sent[0][1]
    assert "Improve onboarding validation" in discord_writer.dms_sent[0][1]


def test_issue_reopened_disabled() -> None:
    """Test that issue_reopened notifications are skipped when disabled."""
    storage = MockStorage()
    storage.verified_mappings = [
        {"discord_user_id": "discord123", "github_user": "alice"},
    ]
    discord_writer = MockDiscordWriter()
    config = NotificationConfig(enabled=True, issue_reopened=False)
    policy = MutationPolicy(mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True)

    event = ContributionEvent(
        github_user="alice",
        event_type="issue_reopened",
        repo="Gitcord",
        created_at=datetime.now(UTC),
        payload={
            "issue_number": 123,
            "title": "Improve onboarding",
            "assignee": "alice",
        },
    )

    result = send_notification_for_event(
        event, storage, discord_writer, policy, config, "AOSSIE"
    )

    assert result is False
    assert len(discord_writer.dms_sent) == 0


def test_pr_reopened_disabled() -> None:
    """Test that pr_reopened notifications are skipped when disabled."""
    storage = MockStorage()
    storage.verified_mappings = [
        {"discord_user_id": "discord456", "github_user": "bob"},
    ]
    discord_writer = MockDiscordWriter()
    config = NotificationConfig(enabled=True, pr_reopened=False)
    policy = MutationPolicy(mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True)

    event = ContributionEvent(
        github_user="bob",
        event_type="pr_reopened",
        repo="Gitcord",
        created_at=datetime.now(UTC),
        payload={
            "pr_number": 456,
            "title": "Improve onboarding validation",
            "pr_author": "bob",
        },
    )

    result = send_notification_for_event(
        event, storage, discord_writer, policy, config, "AOSSIE"
    )

    assert result is False
    assert len(discord_writer.dms_sent) == 0


def test_issue_reopened_dedupe() -> None:
    """Test that duplicate issue_reopened notifications are not sent."""
    storage = MockStorage()
    storage.verified_mappings = [
        {"discord_user_id": "discord123", "github_user": "alice"},
    ]
    discord_writer = MockDiscordWriter()
    config = NotificationConfig(enabled=True, issue_reopened=True)
    policy = MutationPolicy(mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True)

    event = ContributionEvent(
        github_user="alice",
        event_type="issue_reopened",
        repo="Gitcord",
        created_at=datetime.now(UTC),
        payload={
            "issue_number": 123,
            "title": "Improve onboarding",
            "assignee": "alice",
            "reopened_at": "2024-06-26T10:30:00Z",
        },
    )
    storage.notifications_sent.add(_build_dedupe_key(event, "alice"))

    result = send_notification_for_event(
        event, storage, discord_writer, policy, config, "AOSSIE"
    )

    assert result is False
    assert len(discord_writer.dms_sent) == 0


def test_pr_reopened_dedupe() -> None:
    """Test that duplicate pr_reopened notifications are not sent."""
    storage = MockStorage()
    storage.verified_mappings = [
        {"discord_user_id": "discord456", "github_user": "bob"},
    ]
    discord_writer = MockDiscordWriter()
    config = NotificationConfig(enabled=True, pr_reopened=True)
    policy = MutationPolicy(mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True)

    event = ContributionEvent(
        github_user="bob",
        event_type="pr_reopened",
        repo="Gitcord",
        created_at=datetime.now(UTC),
        payload={
            "pr_number": 456,
            "title": "Improve onboarding validation",
            "pr_author": "bob",
            "reopened_at": "2024-06-26T11:00:00Z",
        },
    )
    storage.notifications_sent.add(_build_dedupe_key(event, "bob"))

    result = send_notification_for_event(
        event, storage, discord_writer, policy, config, "AOSSIE"
    )

    assert result is False
    assert len(discord_writer.dms_sent) == 0


def test_issue_reopened_without_assignee() -> None:
    """Test that issue_reopened notifications are skipped for unassigned issues."""
    storage = MockStorage()
    storage.verified_mappings = [
        {"discord_user_id": "discord123", "github_user": "alice"},
    ]
    discord_writer = MockDiscordWriter()
    config = NotificationConfig(enabled=True, issue_reopened=True)
    policy = MutationPolicy(mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True)

    event = ContributionEvent(
        github_user="",
        event_type="issue_reopened",
        repo="Gitcord",
        created_at=datetime.now(UTC),
        payload={
            "issue_number": 123,
            "title": "Improve onboarding",
            "assignee": None,  # No assignee
        },
    )

    result = send_notification_for_event(
        event, storage, discord_writer, policy, config, "AOSSIE"
    )

    assert result is False
    assert len(discord_writer.dms_sent) == 0


def test_pr_opened_channel_notification_posts_to_mapped_channel() -> None:
    storage = MockStorage()
    storage.verified_mappings = [{"discord_user_id": "999", "github_user": "alice"}]
    discord_writer = MockDiscordWriter()
    config = NotificationConfig(enabled=True, pr_opened=True)
    policy = MutationPolicy(mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True)
    event = ContributionEvent(
        github_user="alice",
        event_type="pr_opened",
        repo="Gitcord-GithubDiscordBot",
        created_at=datetime.now(UTC),
        payload={"pr_number": 42, "title": "Test PR"},
    )

    result = send_pr_opened_channel_notification(
        event,
        storage,
        discord_writer,
        policy,
        config,
        {"Gitcord-GithubDiscordBot": "1465995983791063140"},
        "AOSSIE-Org",
    )

    assert result is True
    assert len(discord_writer.messages_sent) == 1
    channel_id, message = discord_writer.messages_sent[0]
    assert channel_id == "1465995983791063140"
    assert "New PR: [Gitcord-GithubDiscordBot #42" in message
    assert "**Author:** alice - <@999>" in message
    assert "pull/42" in message
    assert "/link" not in message


def test_pr_opened_channel_notification_skips_unmapped_repo() -> None:
    storage = MockStorage()
    storage.verified_mappings = [{"discord_user_id": "999", "github_user": "alice"}]
    discord_writer = MockDiscordWriter()
    config = NotificationConfig(enabled=True, pr_opened=True)
    policy = MutationPolicy(mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True)
    event = ContributionEvent(
        github_user="alice",
        event_type="pr_opened",
        repo="EduAid",
        created_at=datetime.now(UTC),
        payload={"pr_number": 1, "title": "Other"},
    )

    result = send_pr_opened_channel_notification(
        event,
        storage,
        discord_writer,
        policy,
        config,
        {"Gitcord-GithubDiscordBot": "1465995983791063140"},
        "AOSSIE-Org",
    )

    assert result is False
    assert discord_writer.messages_sent == []


def _pr_opened_event(*, github_user: str = "alice", repo: str = "Gitcord-GithubDiscordBot") -> ContributionEvent:
    return ContributionEvent(
        github_user=github_user,
        event_type="pr_opened",
        repo=repo,
        created_at=datetime.now(UTC),
        payload={"pr_number": 42, "title": "Test PR"},
    )


def test_pr_opened_channel_notification_skips_when_pr_opened_disabled() -> None:
    storage = MockStorage()
    storage.verified_mappings = [{"discord_user_id": "999", "github_user": "alice"}]
    discord_writer = MockDiscordWriter()
    config = NotificationConfig(enabled=True, pr_opened=False)
    policy = MutationPolicy(mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True)

    result = send_pr_opened_channel_notification(
        _pr_opened_event(),
        storage,
        discord_writer,
        policy,
        config,
        {"Gitcord-GithubDiscordBot": "1465995983791063140"},
        "AOSSIE-Org",
    )

    assert result is False
    assert discord_writer.messages_sent == []


def test_pr_opened_channel_notification_skips_bots() -> None:
    storage = MockStorage()
    discord_writer = MockDiscordWriter()
    config = NotificationConfig(enabled=True, pr_opened=True)
    policy = MutationPolicy(mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True)

    result = send_pr_opened_channel_notification(
        _pr_opened_event(github_user="dependabot[bot]"),
        storage,
        discord_writer,
        policy,
        config,
        {"Gitcord-GithubDiscordBot": "1465995983791063140"},
        "AOSSIE-Org",
    )

    assert result is False
    assert discord_writer.messages == []


def test_pr_opened_channel_notification_posts_unverified_author() -> None:
    storage = MockStorage()
    storage.verified_mappings = []
    discord_writer = MockDiscordWriter()
    config = NotificationConfig(enabled=True, pr_opened=True)
    policy = MutationPolicy(mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True)

    result = send_pr_opened_channel_notification(
        _pr_opened_event(github_user="stranger"),
        storage,
        discord_writer,
        policy,
        config,
        {"Gitcord-GithubDiscordBot": "1465995983791063140"},
        "AOSSIE-Org",
    )

    assert result is True
    assert len(discord_writer.messages_sent) == 1
    _, message = discord_writer.messages_sent[0]
    assert "New PR: [Gitcord-GithubDiscordBot #42" in message
    assert "**Author:** stranger - unknown" in message
    assert "If you are `stranger`, please use `/link stranger`" in message
    assert "<@" not in message


def test_pr_opened_channel_notification_skips_duplicate() -> None:
    storage = MockStorage()
    storage.verified_mappings = [{"discord_user_id": "999", "github_user": "alice"}]
    storage.notifications_sent.add(
        "pr_opened_channel:Gitcord-GithubDiscordBot:42:1465995983791063140"
    )
    discord_writer = MockDiscordWriter()
    config = NotificationConfig(enabled=True, pr_opened=True)
    policy = MutationPolicy(mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True)

    result = send_pr_opened_channel_notification(
        _pr_opened_event(),
        storage,
        discord_writer,
        policy,
        config,
        {"Gitcord-GithubDiscordBot": "1465995983791063140"},
        "AOSSIE-Org",
    )

    assert result is False
    assert discord_writer.messages_sent == []


def test_pr_opened_channel_notification_skips_when_discord_mutations_disallowed() -> None:
    storage = MockStorage()
    storage.verified_mappings = [{"discord_user_id": "999", "github_user": "alice"}]
    discord_writer = MockDiscordWriter()
    config = NotificationConfig(enabled=True, pr_opened=True)
    policy = MutationPolicy(mode=RunMode.DRY_RUN, github_write_allowed=True, discord_write_allowed=True)

    result = send_pr_opened_channel_notification(
        _pr_opened_event(),
        storage,
        discord_writer,
        policy,
        config,
        {"Gitcord-GithubDiscordBot": "1465995983791063140"},
        "AOSSIE-Org",
    )

    assert result is False
    assert discord_writer.messages_sent == []
    assert policy.allow_discord_mutations is False


def test_pr_opened_channel_notification_tracks_message_id() -> None:
    storage = MockStorage()
    storage.verified_mappings = [{"discord_user_id": "999", "github_user": "alice"}]
    discord_writer = MockDiscordWriter()
    config = NotificationConfig(enabled=True, pr_opened=True)
    policy = MutationPolicy(mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True)

    assert send_pr_opened_channel_notification(
        _pr_opened_event(),
        storage,
        discord_writer,
        policy,
        config,
        {"Gitcord-GithubDiscordBot": "1465995983791063140"},
        "AOSSIE-Org",
    )
    tracked = storage.get_pr_channel_announcement("Gitcord-GithubDiscordBot", 42)
    assert tracked is not None
    assert tracked["channel_id"] == "1465995983791063140"
    assert tracked["message_id"]
    assert tracked["status"] == "open"


def test_update_pr_channel_announcement_edits_on_merge() -> None:
    storage = MockStorage()
    discord_writer = MockDiscordWriter()
    storage.save_pr_channel_announcement(
        repo="Gitcord-GithubDiscordBot",
        pr_number=42,
        channel_id="chan-1",
        message_id="msg-9",
        pr_title="Test PR",
        author_github="alice",
        status="open",
    )
    config = NotificationConfig(enabled=True, update_pr_channel_on_lifecycle=True)
    policy = MutationPolicy(mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True)
    event = ContributionEvent(
        github_user="alice",
        event_type="pr_merged",
        repo="Gitcord-GithubDiscordBot",
        created_at=datetime.now(UTC),
        payload={"pr_number": 42, "title": "Test PR", "merged_by": "mentor1"},
    )

    assert update_pr_channel_announcement_for_event(
        event, storage, discord_writer, policy, config, "AOSSIE-Org"
    )
    assert len(discord_writer.messages_edited) == 1
    channel_id, message_id, content, embeds = discord_writer.messages_edited[0]
    assert channel_id == "chan-1"
    assert message_id == "msg-9"
    assert content == ""
    assert embeds
    assert embeds[0]["color"] == 0x8250DF
    assert "Merged:" in embeds[0]["title"]
    assert "Merged by @mentor1" in embeds[0]["description"]
    assert storage.get_pr_channel_announcement("Gitcord-GithubDiscordBot", 42)["status"] == "merged"


def test_update_pr_channel_announcement_edits_on_close() -> None:
    storage = MockStorage()
    discord_writer = MockDiscordWriter()
    storage.save_pr_channel_announcement(
        repo="MiniChain",
        pr_number=7,
        channel_id="chan-2",
        message_id="msg-2",
        pr_title="WIP",
        author_github="bob",
        status="open",
    )
    config = NotificationConfig(enabled=True, update_pr_channel_on_lifecycle=True)
    policy = MutationPolicy(mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True)
    event = ContributionEvent(
        github_user="bob",
        event_type="pr_closed",
        repo="MiniChain",
        created_at=datetime.now(UTC),
        payload={"pr_number": 7, "title": "WIP", "closed_by": "bob"},
    )

    assert update_pr_channel_announcement_for_event(
        event, storage, discord_writer, policy, config, "StabilityNexus"
    )
    assert len(discord_writer.messages_edited) == 1
    embeds = discord_writer.messages_edited[0][3]
    assert embeds
    assert embeds[0]["color"] == 0xCF222E
    assert "Closed:" in embeds[0]["title"]
    assert "Closed by @bob" in embeds[0]["description"]


def test_update_pr_channel_announcement_skips_untracked_old_messages() -> None:
    storage = MockStorage()
    discord_writer = MockDiscordWriter()
    config = NotificationConfig(enabled=True, update_pr_channel_on_lifecycle=True)
    policy = MutationPolicy(mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True)
    event = ContributionEvent(
        github_user="alice",
        event_type="pr_merged",
        repo="Gitcord-GithubDiscordBot",
        created_at=datetime.now(UTC),
        payload={"pr_number": 99, "title": "Old PR", "merged_by": "alice"},
    )

    assert (
        update_pr_channel_announcement_for_event(
            event, storage, discord_writer, policy, config, "AOSSIE-Org"
        )
        is False
    )
    assert discord_writer.messages_edited == []


def test_update_pr_channel_announcement_releases_claim_when_status_mark_fails() -> None:
    """If Discord edit succeeds but status persistence fails, release claim for retry."""
    storage = MockStorage()
    discord_writer = MockDiscordWriter()
    storage.save_pr_channel_announcement(
        repo="Gitcord-GithubDiscordBot",
        pr_number=42,
        channel_id="chan-1",
        message_id="msg-9",
        pr_title="Test PR",
        author_github="alice",
        status="open",
    )

    def _boom(repo: str, pr_number: int, status: str) -> None:
        raise RuntimeError("db locked")

    storage.mark_pr_channel_announcement_status = _boom  # type: ignore[method-assign]
    config = NotificationConfig(enabled=True, update_pr_channel_on_lifecycle=True)
    policy = MutationPolicy(mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True)
    event = ContributionEvent(
        github_user="alice",
        event_type="pr_merged",
        repo="Gitcord-GithubDiscordBot",
        created_at=datetime.now(UTC),
        payload={"pr_number": 42, "title": "Test PR", "merged_by": "mentor1"},
    )

    assert (
        update_pr_channel_announcement_for_event(
            event, storage, discord_writer, policy, config, "AOSSIE-Org"
        )
        is False
    )
    assert len(discord_writer.messages_edited) == 1
    dedupe_key = "pr_channel_lifecycle:Gitcord-GithubDiscordBot:42:merged"
    assert not storage.was_notification_sent(dedupe_key)
    # Status stayed open so a later sync can retry mark after claim release.
    assert storage.get_pr_channel_announcement("Gitcord-GithubDiscordBot", 42)["status"] == "open"


def test_sqlite_pr_channel_announcement_roundtrip(tmp_path) -> None:
    storage = SqliteStorage(tmp_path / "state.db")
    storage.init_schema()
    storage.save_pr_channel_announcement(
        repo="RepoA",
        pr_number=3,
        channel_id="c1",
        message_id="m1",
        pr_title="Hello",
        author_github="alice",
    )
    row = storage.get_pr_channel_announcement("RepoA", 3)
    assert row is not None
    assert row["message_id"] == "m1"
    assert storage.get_pr_channel_announcement("RepoA", 4) is None
    storage.mark_pr_channel_announcement_status("RepoA", 3, "merged")
    assert storage.get_pr_channel_announcement("RepoA", 3)["status"] == "merged"


class MockGithubWriter:
    def __init__(self, *, succeed: bool = True) -> None:
        self.comments: list[tuple[str, str, int, str]] = []
        self.succeed = succeed

    def create_issue_comment(self, owner: str, repo: str, issue_number: int, body: str) -> bool:
        self.comments.append((owner, repo, issue_number, body))
        return self.succeed


def test_build_pr_opened_github_link_comment_format() -> None:
    body = _build_pr_opened_github_link_comment("alice", "https://discord.gg/invite")
    assert "### Link your account with Gitcord" in body
    assert "**@alice**" in body
    assert "https://discord.gg/invite" in body
    assert "`/link alice`" in body
    assert "`/verify-link alice`" in body
    assert "bio" in body.lower()


def test_pr_opened_github_link_comment_posts_for_unverified() -> None:
    storage = MockStorage()
    storage.verified_mappings = []
    github_writer = MockGithubWriter()
    config = NotificationConfig(enabled=True, pr_opened_github_comment=True)
    policy = MutationPolicy(mode=RunMode.ACTIVE, github_write_allowed=False, discord_write_allowed=True)

    result = send_pr_opened_github_link_comment(
        _pr_opened_event(github_user="stranger"),
        storage,
        github_writer,
        policy,
        config,
        "AOSSIE-Org",
        "https://discord.gg/hjUhu33uAn",
    )

    assert result is True
    assert len(github_writer.comments) == 1
    owner, repo, number, body = github_writer.comments[0]
    assert owner == "AOSSIE-Org"
    assert repo == "Gitcord-GithubDiscordBot"
    assert number == 42
    assert "/link stranger" in body
    assert "pr_opened_github_link:Gitcord-GithubDiscordBot:42" in storage.notifications_sent


def test_pr_opened_github_link_comment_skips_verified() -> None:
    storage = MockStorage()
    storage.verified_mappings = [{"discord_user_id": "999", "github_user": "alice"}]
    github_writer = MockGithubWriter()
    config = NotificationConfig(enabled=True, pr_opened_github_comment=True)
    policy = MutationPolicy(mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True)

    result = send_pr_opened_github_link_comment(
        _pr_opened_event(),
        storage,
        github_writer,
        policy,
        config,
        "AOSSIE-Org",
        "https://discord.gg/hjUhu33uAn",
    )

    assert result is False
    assert github_writer.comments == []


def test_pr_opened_github_link_comment_skips_bots() -> None:
    storage = MockStorage()
    github_writer = MockGithubWriter()
    config = NotificationConfig(enabled=True, pr_opened_github_comment=True)
    policy = MutationPolicy(mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True)

    result = send_pr_opened_github_link_comment(
        _pr_opened_event(github_user="dependabot[bot]"),
        storage,
        github_writer,
        policy,
        config,
        "AOSSIE-Org",
        "https://discord.gg/hjUhu33uAn",
    )

    assert result is False
    assert github_writer.comments == []


def test_pr_opened_github_link_comment_skips_without_invite() -> None:
    storage = MockStorage()
    github_writer = MockGithubWriter()
    config = NotificationConfig(enabled=True, pr_opened_github_comment=True)
    policy = MutationPolicy(mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True)

    result = send_pr_opened_github_link_comment(
        _pr_opened_event(github_user="stranger"),
        storage,
        github_writer,
        policy,
        config,
        "AOSSIE-Org",
        None,
    )

    assert result is False
    assert github_writer.comments == []


def test_pr_opened_github_link_comment_skips_duplicate() -> None:
    storage = MockStorage()
    storage.notifications_sent.add("pr_opened_github_link:Gitcord-GithubDiscordBot:42")
    github_writer = MockGithubWriter()
    config = NotificationConfig(enabled=True, pr_opened_github_comment=True)
    policy = MutationPolicy(mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True)

    result = send_pr_opened_github_link_comment(
        _pr_opened_event(github_user="stranger"),
        storage,
        github_writer,
        policy,
        config,
        "AOSSIE-Org",
        "https://discord.gg/hjUhu33uAn",
    )

    assert result is False
    assert github_writer.comments == []


def test_sanitize_discord_pr_title_neutralizes_injection() -> None:
    dirty = "fix](https://evil.example) @everyone"
    clean = _sanitize_discord_pr_title(dirty)
    assert "\\]" in clean
    assert "@everyone" not in clean
    assert "@\u200beveryone" in clean

    message = _build_pr_opened_channel_message(
        ContributionEvent(
            github_user="alice",
            event_type="pr_opened",
            repo="repo",
            created_at=datetime.now(UTC),
            payload={"pr_number": 7, "title": dirty},
        ),
        "AOSSIE-Org",
        "alice",
        None,
    )
    assert message is not None
    assert "](https://evil.example)" not in message
    assert "https://github.com/AOSSIE-Org/repo/pull/7" in message
    assert "@everyone" not in message

    normal = _build_pr_opened_channel_message(
        ContributionEvent(
            github_user="alice",
            event_type="pr_opened",
            repo="repo",
            created_at=datetime.now(UTC),
            payload={"pr_number": 8, "title": "Normal title"},
        ),
        "AOSSIE-Org",
        "alice",
        "123",
    )
    assert normal is not None
    assert "Normal title" in normal
    assert "**Author:** alice - <@123>" in normal


def test_pr_opened_github_link_comment_concurrent_claim(tmp_path) -> None:
    storage = SqliteStorage(str(tmp_path))
    storage.init_schema()
    github_writer = MockGithubWriter()
    config = NotificationConfig(enabled=True, pr_opened_github_comment=True)
    policy = MutationPolicy(mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True)
    event = _pr_opened_event(github_user="stranger")
    barrier = Barrier(2)
    results: list[bool] = []

    def _run() -> None:
        barrier.wait()
        results.append(
            send_pr_opened_github_link_comment(
                event,
                storage,
                github_writer,
                policy,
                config,
                "AOSSIE-Org",
                "https://discord.gg/hjUhu33uAn",
            )
        )

    threads = [Thread(target=_run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results) == [False, True]
    assert len(github_writer.comments) == 1
    assert storage.was_notification_sent("pr_opened_github_link:Gitcord-GithubDiscordBot:42")


def test_pr_opened_github_link_comment_claim_storage_failure() -> None:
    storage = MockStorage()
    storage.claim_notification_sent = MagicMock(  # type: ignore[method-assign]
        side_effect=RuntimeError("database is locked")
    )
    github_writer = MockGithubWriter()
    config = NotificationConfig(enabled=True, pr_opened_github_comment=True)
    policy = MutationPolicy(mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True)

    result = send_pr_opened_github_link_comment(
        _pr_opened_event(github_user="stranger"),
        storage,
        github_writer,
        policy,
        config,
        "AOSSIE-Org",
        "https://discord.gg/hjUhu33uAn",
    )

    assert result is False
    assert github_writer.comments == []


def test_link_comment_timeout_preserves_claim_prevents_duplicate(tmp_path) -> None:
    """Timeout after GitHub may have accepted must not create a duplicate /link comment."""
    storage = SqliteStorage(str(tmp_path))
    storage.init_schema()
    adapter = GitHubRestAdapter(token="t", org="AOSSIE-Org", api_base="https://api.github.com")
    client = MagicMock()
    client.post.side_effect = httpx.TimeoutException(
        "timeout",
        request=httpx.Request("POST", "https://api.github.com/repos/o/r/issues/1/comments"),
    )
    adapter._client = client  # type: ignore[assignment]

    config = NotificationConfig(enabled=True, pr_opened_github_comment=True)
    policy = MutationPolicy(mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True)
    event = _pr_opened_event(github_user="stranger")

    first = send_pr_opened_github_link_comment(
        event,
        storage,
        adapter,
        policy,
        config,
        "AOSSIE-Org",
        "https://discord.gg/hjUhu33uAn",
    )
    second = send_pr_opened_github_link_comment(
        event,
        storage,
        adapter,
        policy,
        config,
        "AOSSIE-Org",
        "https://discord.gg/hjUhu33uAn",
    )

    assert first is True
    assert second is False
    assert client.post.call_count == 1
    assert storage.was_notification_sent("pr_opened_github_link:Gitcord-GithubDiscordBot:42")


def test_batch_pr_opened_notifications_sent_oldest_first() -> None:
    """Batch sync must post channel PR-open notices in created_at order (not API newest-first)."""
    from ghdcbot.engine.orchestrator import _send_notifications_for_new_events

    storage = MockStorage()
    discord_writer = MockDiscordWriter()
    config = NotificationConfig(enabled=True, pr_opened=True)
    policy = MutationPolicy(
        mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True
    )
    channels = {"Gitcord-GithubDiscordBot": "1465995983791063140"}

    newer = ContributionEvent(
        github_user="alice",
        event_type="pr_opened",
        repo="Gitcord-GithubDiscordBot",
        created_at=datetime(2026, 8, 4, 18, 57, tzinfo=UTC),
        payload={"pr_number": 42, "title": "Week 11 remote config"},
    )
    older = ContributionEvent(
        github_user="alice",
        event_type="pr_opened",
        repo="Gitcord-GithubDiscordBot",
        created_at=datetime(2026, 8, 4, 18, 13, tzinfo=UTC),
        payload={"pr_number": 41, "title": "CI mock fix"},
    )

    # Newest-first (GitHub list order) — notifications should still post #41 then #42.
    events = [newer, older]
    _send_notifications_for_new_events(
        events,
        storage,
        discord_writer,
        policy,
        config,
        "AOSSIE-Org",
        channels,
    )

    # Notification pass must not mutate the caller's ingestion list.
    assert events[0] is newer
    assert events[1] is older
    assert events == [newer, older]

    assert len(discord_writer.messages_sent) == 2
    assert "#41" in discord_writer.messages_sent[0][1]
    assert "#42" in discord_writer.messages_sent[1][1]

