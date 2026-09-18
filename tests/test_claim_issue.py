"""Tests for the Claim Issue feature (announcements, mentor prompts, approval & decline flows)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

from ghdcbot.config.models import NotificationConfig
from ghdcbot.core.models import ContributionEvent
from ghdcbot.core.modes import MutationPolicy, RunMode
from ghdcbot.engine.claim_issue import (
    build_issue_announcement_components,
    build_mentor_claim_card,
    build_mentor_claim_components,
    check_existing_user_request,
    create_claim_request,
    process_claim_approval,
    process_claim_decline,
)
from ghdcbot.engine.notifications import send_issue_opened_channel_notification


def test_build_issue_announcement_components() -> None:
    """Verify announcement interactive buttons layout and URLs."""
    components = build_issue_announcement_components(
        github_org="AOSSIE-Org",
        repo="Gitcord",
        issue_number=105,
    )
    assert len(components) == 1
    action_row = components[0]
    assert action_row["type"] == 1
    buttons = action_row["components"]
    assert len(buttons) == 2

    claim_btn = buttons[0]
    assert claim_btn["type"] == 2
    assert claim_btn["style"] == 1
    assert claim_btn["label"] == "Claim Issue"
    assert claim_btn["custom_id"] == "claim_issue:Gitcord:105"
    assert claim_btn["emoji"]["name"] == "✋"

    link_btn = buttons[1]
    assert link_btn["type"] == 2
    assert link_btn["style"] == 5
    assert link_btn["label"] == "View on GitHub"
    assert link_btn["url"] == "https://github.com/AOSSIE-Org/Gitcord/issues/105"
    assert link_btn["emoji"]["name"] == "🔗"


def test_build_mentor_claim_components() -> None:
    """Verify mentor prompt action buttons."""
    components = build_mentor_claim_components("req_test_123")
    assert len(components) == 1
    buttons = components[0]["components"]
    assert len(buttons) == 2

    approve_btn = buttons[0]
    assert approve_btn["label"] == "Approve & Assign"
    assert approve_btn["style"] == 3  # Success
    assert approve_btn["custom_id"] == "claim_approve:req_test_123"
    assert approve_btn["emoji"]["name"] == "✅"

    decline_btn = buttons[1]
    assert decline_btn["label"] == "Decline"
    assert decline_btn["style"] == 4  # Danger
    assert decline_btn["custom_id"] == "claim_decline:req_test_123"
    assert decline_btn["emoji"]["name"] == "❌"


def test_check_existing_user_request() -> None:
    """Verify detection of existing pending requests for the same issue."""
    storage = MagicMock()
    storage.list_pending_issue_requests.return_value = [
        {"discord_user_id": "111", "repo": "Gitcord", "issue_number": 105},
        {"discord_user_id": "222", "repo": "Gitcord", "issue_number": 106},
    ]

    assert check_existing_user_request(storage, "111", "Gitcord", 105) is True
    assert check_existing_user_request(storage, "111", "Gitcord", 106) is False
    assert check_existing_user_request(storage, "999", "Gitcord", 105) is False


def test_create_claim_request() -> None:
    """Verify request insertion and audit event recording."""
    storage = MagicMock()
    request_id = create_claim_request(
        storage=storage,
        discord_user_id="111",
        github_user="alex-dev",
        owner="AOSSIE-Org",
        repo="Gitcord",
        issue_number=105,
        issue_url="https://github.com/AOSSIE-Org/Gitcord/issues/105",
    )
    assert request_id is not None
    assert len(request_id) > 0

    storage.insert_issue_request.assert_called_once_with(
        request_id=request_id,
        discord_user_id="111",
        github_user="alex-dev",
        owner="AOSSIE-Org",
        repo="Gitcord",
        issue_number=105,
        issue_url="https://github.com/AOSSIE-Org/Gitcord/issues/105",
    )
    storage.append_audit_event.assert_called_once()
    event = storage.append_audit_event.call_args[0][0]
    assert event["event_type"] == "issue_request_created"
    assert event["context"]["request_id"] == request_id
    assert event["context"]["github_user"] == "alex-dev"


def test_build_mentor_claim_card() -> None:
    """Verify mentor prompt header and embed formatting."""
    storage = MagicMock()
    storage.list_contributions.return_value = []

    req = {
        "request_id": "req_1",
        "discord_user_id": "111",
        "github_user": "alex-dev",
        "owner": "AOSSIE-Org",
        "repo": "Gitcord",
        "issue_number": 105,
        "issue_url": "https://github.com/AOSSIE-Org/Gitcord/issues/105",
    }
    issue = {
        "title": "Add dark mode toggle to contributor profile",
        "state": "open",
        "labels": [{"name": "good first issue"}],
        "assignees": [],
    }

    header, embed = build_mentor_claim_card(
        request=req,
        issue=issue,
        contributor_roles=["Contributor"],
        storage=storage,
        eligible_roles_config=["Contributor"],
        now=datetime.now(UTC),
    )

    assert "Issue Request: #105" in header
    assert "alex-dev" in header
    assert "<@111>" in header
    assert embed["title"] == "🧾 Issue assignment request"
    assert any(f["name"] == "Eligibility" for f in embed["fields"])


def test_process_claim_approval_success() -> None:
    """Verify full approval flow: GitHub assign, DB status update, audit event."""
    storage = MagicMock()
    github_adapter = MagicMock()
    github_adapter.assign_issue.return_value = True

    storage.get_issue_request.return_value = {
        "request_id": "req_1",
        "status": "pending",
        "owner": "AOSSIE-Org",
        "repo": "Gitcord",
        "issue_number": 105,
        "github_user": "alex-dev",
        "discord_user_id": "111",
    }

    policy = MutationPolicy(
        mode=RunMode.ACTIVE,
        github_write_allowed=True,
        discord_write_allowed=True,
    )

    ok, msg, _req = process_claim_approval(
        storage=storage,
        github_adapter=github_adapter,
        policy=policy,
        request_id="req_1",
        mentor_discord_id="999",
        mentor_github="mentor-dev",
    )

    assert ok is True
    assert "Approved and assigned" in msg
    github_adapter.assign_issue.assert_called_once_with("AOSSIE-Org", "Gitcord", 105, "alex-dev")
    storage.update_issue_request_status.assert_called_once_with("req_1", "approved")
    storage.append_audit_event.assert_called_once()
    event = storage.append_audit_event.call_args[0][0]
    assert event["event_type"] == "issue_request_approved"
    assert event["context"]["assignee"] == "alex-dev"


def test_process_claim_approval_dry_run_blocked() -> None:
    """Verify dry-run mode prevents GitHub mutations and approval."""
    storage = MagicMock()
    github_adapter = MagicMock()

    storage.get_issue_request.return_value = {
        "request_id": "req_1",
        "status": "pending",
        "owner": "AOSSIE-Org",
        "repo": "Gitcord",
        "issue_number": 105,
        "github_user": "alex-dev",
        "discord_user_id": "111",
    }

    policy = MutationPolicy(
        mode=RunMode.DRY_RUN,
        github_write_allowed=True,
        discord_write_allowed=True,
    )

    ok, msg, _req = process_claim_approval(
        storage=storage,
        github_adapter=github_adapter,
        policy=policy,
        request_id="req_1",
        mentor_discord_id="999",
    )

    assert ok is False
    assert "disabled" in msg
    github_adapter.assign_issue.assert_not_called()
    storage.update_issue_request_status.assert_not_called()


def test_process_claim_approval_not_pending() -> None:
    """Verify non-pending requests cannot be approved."""
    storage = MagicMock()
    storage.get_issue_request.return_value = {
        "request_id": "req_1",
        "status": "approved",
    }

    policy = MutationPolicy(
        mode=RunMode.ACTIVE,
        github_write_allowed=True,
        discord_write_allowed=True,
    )

    ok, msg, _req = process_claim_approval(
        storage=storage,
        github_adapter=MagicMock(),
        policy=policy,
        request_id="req_1",
        mentor_discord_id="999",
    )

    assert ok is False
    assert "no longer pending" in msg


def test_process_claim_decline() -> None:
    """Verify decline updates status to rejected and logs audit event."""
    storage = MagicMock()
    storage.get_issue_request.return_value = {
        "request_id": "req_1",
        "status": "pending",
        "owner": "AOSSIE-Org",
        "repo": "Gitcord",
        "issue_number": 105,
        "github_user": "alex-dev",
        "discord_user_id": "111",
    }

    ok, msg, _req = process_claim_decline(
        storage=storage,
        request_id="req_1",
        mentor_discord_id="999",
        mentor_github="mentor-dev",
    )

    assert ok is True
    assert "Declined" in msg
    storage.update_issue_request_status.assert_called_once_with("req_1", "rejected")
    storage.append_audit_event.assert_called_once()
    event = storage.append_audit_event.call_args[0][0]
    assert event["event_type"] == "issue_request_rejected"
    assert event["context"]["requester"] == "alex-dev"


def test_announcement_includes_claim_components() -> None:
    """Verify that post_issue_channel_announcement provides interactive components to create_message."""
    storage = MagicMock()
    storage.list_verified_identity_mappings.return_value = []
    # Dedupe claim succeeds
    storage.is_notification_sent.return_value = False

    discord_writer = MagicMock()
    discord_writer.create_message.return_value = "msg_12345"

    policy = MutationPolicy(
        mode=RunMode.ACTIVE,
        github_write_allowed=True,
        discord_write_allowed=True,
    )

    config = NotificationConfig(
        enabled=True,
        issue_opened=True,
    )

    event = ContributionEvent(
        github_user="author-dev",
        event_type="issue_opened",
        repo="Gitcord",
        created_at=datetime.now(UTC),
        payload={
            "issue_number": 105,
            "title": "Add dark mode toggle to contributor profile",
            "html_url": "https://github.com/AOSSIE-Org/Gitcord/issues/105",
        },
    )

    sent = send_issue_opened_channel_notification(
        event=event,
        storage=storage,
        discord_writer=discord_writer,
        policy=policy,
        config=config,
        pr_open_channels={"Gitcord": "chan_999"},
        github_org="AOSSIE-Org",
    )

    assert sent is True
    discord_writer.create_message.assert_called_once()
    call_kwargs = discord_writer.create_message.call_args.kwargs
    assert "components" in call_kwargs
    components = call_kwargs["components"]
    assert len(components) == 1
    buttons = components[0]["components"]
    assert buttons[0]["custom_id"] == "claim_issue:Gitcord:105"
    assert buttons[0]["label"] == "Claim Issue"
    assert buttons[1]["url"] == "https://github.com/AOSSIE-Org/Gitcord/issues/105"
