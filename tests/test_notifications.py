"""Tests for verified-only GitHub → Discord notifications."""

from datetime import datetime, timezone

from ghdcbot.config.models import NotificationConfig
from ghdcbot.core.models import ContributionEvent
from ghdcbot.core.modes import MutationPolicy, RunMode
from ghdcbot.engine.notifications import (
    _build_dedupe_key,
    _build_notification_message,
    send_notification_for_event,
    send_pr_opened_channel_notification,
)


class MockStorage:
    """Mock storage for testing."""
    
    def __init__(self) -> None:
        self.verified_mappings: list[dict] = []
        self.notifications_sent: set[str] = set()
        self.audit_events: list[dict] = []
    
    def list_verified_identity_mappings(self) -> list[dict]:
        return self.verified_mappings
    
    def was_notification_sent(self, dedupe_key: str) -> bool:
        return dedupe_key in self.notifications_sent
    
    def mark_notification_sent(self, *args: object, **kwargs: object) -> None:
        dedupe_key = args[0] if args else kwargs.get("dedupe_key", "")
        self.notifications_sent.add(dedupe_key)
    
    def append_audit_event(self, event: dict) -> None:
        self.audit_events.append(event)


class MockDiscordWriter:
    """Mock Discord writer for testing."""
    
    def __init__(self) -> None:
        self.dms_sent: list[tuple[str, str]] = []
        self.messages_sent: list[tuple[str, str]] = []
    
    def send_dm(self, discord_user_id: str, content: str) -> bool:
        self.dms_sent.append((discord_user_id, content))
        return True
    
    def send_message(self, channel_id: str, content: str) -> bool:
        self.messages_sent.append((channel_id, content))
        return True


def test_build_dedupe_key() -> None:
    """Test deduplication key building."""
    event = ContributionEvent(
        github_user="alice",
        event_type="issue_assigned",
        repo="test-repo",
        created_at=datetime.now(timezone.utc),
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
        created_at=datetime.now(timezone.utc),
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
        created_at=datetime.now(timezone.utc),
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
        created_at=datetime.now(timezone.utc),
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
        created_at=datetime.now(timezone.utc),
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
        created_at=datetime.now(timezone.utc),
        payload={"pr_number": 999},
    )
    msg = _build_notification_message(event, "pr_merged", "test-org", "contributor")
    assert "PR Merged" in msg
    assert "#999" in msg
    assert "Thank you for your contribution" in msg


def test_build_notification_message_pr_closed_template() -> None:
    """Test building notification message for PR closed without merge."""
    event = ContributionEvent(
        github_user="contributor",
        event_type="pr_closed",
        repo="Gitcord",
        created_at=datetime.now(timezone.utc),
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
        created_at=datetime.now(timezone.utc),
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
        created_at=datetime.now(timezone.utc),
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
        created_at=datetime.now(timezone.utc),
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
        created_at=datetime.now(timezone.utc),
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
        created_at=datetime.now(timezone.utc),
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
        created_at=datetime.now(timezone.utc),
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
        created_at=datetime.now(timezone.utc),
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
        created_at=datetime.now(timezone.utc),
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
        created_at=datetime.now(timezone.utc),
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
        created_at=datetime.now(timezone.utc),
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
    """Test notification for PR review comments when enabled."""
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
        created_at=datetime.now(timezone.utc),
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

    assert result is True
    assert len(discord_writer.dms_sent) == 1
    assert "New Review Comments" in discord_writer.dms_sent[0][1]
    assert "reviewer" in discord_writer.dms_sent[0][1]


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
        created_at=datetime.now(timezone.utc),
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
        "pr_reviewed:test-repo:456:contributor:9001:COMMENT"
    )
    discord_writer = MockDiscordWriter()
    config = NotificationConfig(enabled=True, pr_review_comment=True)
    policy = MutationPolicy(mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True)

    event = ContributionEvent(
        github_user="reviewer",
        event_type="pr_reviewed",
        repo="test-repo",
        created_at=datetime.now(timezone.utc),
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
        created_at=datetime.now(timezone.utc),
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
        created_at=datetime.now(timezone.utc),
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
        created_at=datetime.now(timezone.utc),
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
        created_at=datetime.now(timezone.utc),
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
        created_at=datetime.now(timezone.utc),
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
        created_at=datetime.now(timezone.utc),
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
        created_at=datetime.now(timezone.utc),
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
        created_at=datetime.now(timezone.utc),
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
        created_at=datetime.now(timezone.utc),
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
        created_at=datetime.now(timezone.utc),
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
    assert "New PR opened" in message
    assert "<@999>" in message
    assert "pull/42" in message


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
        created_at=datetime.now(timezone.utc),
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
        created_at=datetime.now(timezone.utc),
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
    assert "Contributor is not verified on gitcord" in message
    assert "`stranger`" in message
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
