"""Tests for verified-only GitHub → Discord notifications."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from ghdcbot.config.models import NotificationConfig
from ghdcbot.core.models import ContributionEvent
from ghdcbot.core.modes import MutationPolicy, RunMode
from ghdcbot.engine.notifications import (
    _build_dedupe_key,
    _build_notification_message,
    send_notification_for_event,
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
    assert "by mentor" in msg


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
    assert "needs updates" in msg


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
    assert "Great work" in msg


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
    """Test that COMMENT reviews don't trigger notifications."""
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
            "state": "COMMENT",
            "pr_author": "contributor",
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
