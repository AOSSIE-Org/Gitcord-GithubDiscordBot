from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

from ghdcbot.adapters.storage.sqlite import SqliteStorage
from ghdcbot.config.models import NotificationConfig
from ghdcbot.core.models import ContributionEvent
from ghdcbot.core.modes import MutationPolicy, RunMode
from ghdcbot.engine.notifications import (
    _build_pr_channel_timeline_card,
    send_pr_opened_channel_notification,
    update_pr_channel_notification_for_event,
)


def test_sqlite_pr_channel_messages_crud(tmp_path: Path) -> None:
    storage = SqliteStorage(str(tmp_path))
    storage.init_schema()

    # Initial save
    events = [{"action": "opened", "actor": "alice", "timestamp": "2026-08-27T12:00:00Z"}]
    storage.save_pr_channel_message(
        repo="my-repo",
        pr_number=42,
        channel_id="chan-101",
        message_id="msg-999",
        status="open",
        events=events,
        pr_title="Add live PR updates",
        author_github="alice",
    )

    # Get by (repo, pr_number, channel_id)
    record = storage.get_pr_channel_message("my-repo", 42, "chan-101")
    assert record is not None
    assert record["message_id"] == "msg-999"
    assert record["status"] == "open"
    assert record["pr_title"] == "Add live PR updates"
    assert record["author_github"] == "alice"
    assert len(record["events"]) == 1
    assert record["events"][0]["action"] == "opened"

    # Update message status and append events
    events.append({"action": "approved", "actor": "coderabbitai", "timestamp": "2026-08-27T12:05:00Z"})
    updated = storage.update_pr_channel_message(
        repo="my-repo",
        pr_number=42,
        channel_id="chan-101",
        status="open",
        events=events,
    )
    assert updated is True

    record_updated = storage.get_pr_channel_message("my-repo", 42, "chan-101")
    assert record_updated is not None
    assert len(record_updated["events"]) == 2
    assert record_updated["events"][1]["action"] == "approved"

    # List messages
    all_msgs = storage.list_pr_channel_messages(repo="my-repo")
    assert len(all_msgs) == 1
    assert all_msgs[0]["pr_number"] == 42

    # Delete message
    deleted = storage.delete_pr_channel_message("my-repo", 42, "chan-101")
    assert deleted is True
    assert storage.get_pr_channel_message("my-repo", 42, "chan-101") is None


def test_build_pr_channel_timeline_card_formatting(tmp_path: Path) -> None:
    storage = SqliteStorage(str(tmp_path))
    storage.init_schema()

    # Link verified user
    storage.create_identity_claim("discord-alice", "alice", "CODE123", datetime.now(UTC) + timedelta(days=1))
    storage.mark_identity_verified("discord-alice", "alice")

    events = [
        {"action": "opened", "actor": "alice"},
        {"action": "approved", "actor": "coderabbitai"},
        {"action": "review_requested", "actor": "alice", "detail": "mentor_bob"},
        {"action": "changes_requested", "actor": "mentor_bob"},
        {"action": "approved", "actor": "mentor_bob"},
        {"action": "merged", "actor": "maintainer_dan"},
    ]

    card = _build_pr_channel_timeline_card(
        storage=storage,
        repo="Gitcord",
        pr_number=123,
        pr_title="Implement live PR cards",
        author_github="alice",
        status="merged",
        events=events,
        github_org="AOSSIE-Org",
    )

    assert "🟣 **PR Merged: [Gitcord #123 — Implement live PR cards]" in card
    assert "**Status:** 🟣 Merged | **Author:** alice - <@discord-alice>" in card
    assert "• 🆕 Opened by **@alice** (<@discord-alice>)" in card
    assert "• ✅ Approved by CodeRabbit" in card
    assert "• 👀 Review requested from **@mentor_bob**" in card
    assert "• 🔁 Changes requested by **@mentor_bob**" in card
    assert "• 🟣 Merged by **@maintainer_dan**" in card


def test_build_pr_channel_timeline_card_closed_status(tmp_path: Path) -> None:
    storage = SqliteStorage(str(tmp_path))
    storage.init_schema()

    events = [
        {"action": "opened", "actor": "unlinked_dev"},
        {"action": "closed", "actor": "unlinked_dev"},
    ]

    card = _build_pr_channel_timeline_card(
        storage=storage,
        repo="Gitcord",
        pr_number=55,
        pr_title="Experimental branch",
        author_github="unlinked_dev",
        status="closed",
        events=events,
        github_org="AOSSIE-Org",
    )

    assert "🚫 **PR Closed: [Gitcord #55 — Experimental branch]" in card
    assert "**Status:** 🔴 Closed | **Author:** unlinked_dev - unknown" in card
    assert "• 🚫 Closed by **@unlinked_dev**" in card


def test_send_pr_opened_and_update_lifecycle_flow(tmp_path: Path) -> None:
    storage = SqliteStorage(str(tmp_path))
    storage.init_schema()

    discord_writer = MagicMock()
    discord_writer.post_channel_message.return_value = "msg-12345"
    discord_writer.edit_message.return_value = True

    policy = MutationPolicy(mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True)
    config = NotificationConfig(enabled=True, pr_opened=True, update_pr_channel_messages=True)
    pr_open_channels = {"Gitcord": "channel-dev"}
    org = "AOSSIE-Org"

    # 1. PR Opened event
    open_event = ContributionEvent(
        github_user="alice",
        event_type="pr_opened",
        repo="Gitcord",
        created_at=datetime(2026, 8, 27, 10, 0, 0, tzinfo=UTC),
        payload={"pr_number": 10, "title": "New feature"},
    )

    sent = send_pr_opened_channel_notification(
        open_event, storage, discord_writer, policy, config, pr_open_channels, org
    )
    assert sent is True
    assert discord_writer.post_channel_message.called

    # Check record saved in storage
    record = storage.get_pr_channel_message("Gitcord", 10, "channel-dev")
    assert record is not None
    assert record["message_id"] == "msg-12345"
    assert record["status"] == "open"
    assert len(record["events"]) == 1

    # 2. PR Review Requested
    review_req_event = ContributionEvent(
        github_user="alice",
        event_type="pr_review_requested",
        repo="Gitcord",
        created_at=datetime(2026, 8, 27, 10, 5, 0, tzinfo=UTC),
        payload={"pr_number": 10, "requested_reviewer": "bob", "title": "New feature"},
    )
    updated = update_pr_channel_notification_for_event(
        review_req_event, storage, discord_writer, policy, config, pr_open_channels, org
    )
    assert updated is True
    assert discord_writer.edit_message.called

    # 3. PR Reviewed (Approved by CodeRabbit)
    reviewed_event = ContributionEvent(
        github_user="coderabbitai",
        event_type="pr_reviewed",
        repo="Gitcord",
        created_at=datetime(2026, 8, 27, 10, 10, 0, tzinfo=UTC),
        payload={"pr_number": 10, "state": "APPROVED", "review_id": 9991, "title": "New feature"},
    )
    updated = update_pr_channel_notification_for_event(
        reviewed_event, storage, discord_writer, policy, config, pr_open_channels, org
    )
    assert updated is True

    # 4. Duplicate event check (re-syncing the same review)
    dup_updated = update_pr_channel_notification_for_event(
        reviewed_event, storage, discord_writer, policy, config, pr_open_channels, org
    )
    assert dup_updated is False

    # 5. PR Closed event
    closed_event = ContributionEvent(
        github_user="alice",
        event_type="pr_closed",
        repo="Gitcord",
        created_at=datetime(2026, 8, 27, 10, 20, 0, tzinfo=UTC),
        payload={"pr_number": 10, "title": "New feature"},
    )
    updated = update_pr_channel_notification_for_event(
        closed_event, storage, discord_writer, policy, config, pr_open_channels, org
    )
    assert updated is True

    record_final = storage.get_pr_channel_message("Gitcord", 10, "channel-dev")
    assert record_final is not None
    assert record_final["status"] == "closed"
    assert len(record_final["events"]) == 4  # opened, review_requested, approved, closed

