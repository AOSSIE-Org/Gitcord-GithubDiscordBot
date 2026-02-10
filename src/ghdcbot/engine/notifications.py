"""Verified-only GitHub → Discord status notifications (anti-spam, mentor-first)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from ghdcbot.config.models import NotificationConfig
from ghdcbot.core.interfaces import DiscordWriter, Storage
from ghdcbot.core.modes import MutationPolicy
from ghdcbot.core.models import ContributionEvent

logger = logging.getLogger(__name__)


def send_notification_for_event(
    event: ContributionEvent,
    storage: Storage,
    discord_writer: DiscordWriter,
    policy: MutationPolicy,
    config: NotificationConfig,
    github_org: str,
) -> bool:
    """Send Discord notification for a GitHub event if user is verified and event type matches config.
    
    Returns True if notification was sent, False otherwise (unverified, disabled, dedupe, etc.).
    For pr_reviewed events, notifies the PR author (not the reviewer).
    """
    logger.debug(
        "Checking notification for event",
        extra={
            "event_type": event.event_type,
            "github_user": event.github_user,
            "repo": event.repo,
            "payload": event.payload,
        },
    )
    if not config.enabled:
        logger.debug("Notifications disabled in config")
        return False
    
    # Determine target GitHub user (who should receive the notification)
    target_github_user: str | None = None
    
    # Handle pr_reviewed events: check state to map to pr_approved/pr_changes_requested
    if event.event_type == "pr_reviewed":
        state = event.payload.get("state", "").upper()
        if state == "APPROVED":
            if not config.pr_review_result:
                return False
            event_type_key = "pr_approved"
            # Notify PR author, not reviewer
            target_github_user = event.payload.get("pr_author")
        elif state == "CHANGES_REQUESTED":
            if not config.pr_review_result:
                return False
            event_type_key = "pr_changes_requested"
            # Notify PR author, not reviewer
            target_github_user = event.payload.get("pr_author")
        else:
            # COMMENT, DISMISSED, or other states - no notification
            logger.debug(
                "Skipping notification: PR review state is not APPROVED or CHANGES_REQUESTED",
                extra={
                    "state": state,
                    "pr_number": event.payload.get("pr_number"),
                    "reviewer": event.github_user,
                    "pr_author": event.payload.get("pr_author"),
                },
            )
            return False
    else:
        # Map event types to config flags
        event_config_map = {
            "issue_assigned": config.issue_assignment,
            "pr_review_requested": config.pr_review_requested,
            "pr_merged": config.pr_merged,
        }
        event_type_key = event.event_type
        if not event_config_map.get(event_type_key, False):
            return False
        # For other events, notify the event.github_user (assignee, PR author, etc.)
        target_github_user = event.github_user
    
    if not target_github_user:
        logger.warning(
            "Skipping notification: target GitHub user not found",
            extra={"event_type": event.event_type, "payload": event.payload, "event_github_user": event.github_user},
        )
        return False
    
    # Resolve GitHub user to Discord user (verified only)
    discord_user_id = _resolve_github_to_discord(storage, target_github_user)
    if not discord_user_id:
        logger.warning(
            "Skipping notification: GitHub user not linked/verified in Gitcord (user must run /link and /verify-link in Discord)",
            extra={
                "github_user": target_github_user,
                "event_type": event.event_type,
                "repo": event.repo,
                "pr_number": event.payload.get("pr_number"),
                "issue_number": event.payload.get("issue_number"),
            },
        )
        return False
    
    # Deduplication: check if we already sent this notification
    dedupe_key = _build_dedupe_key(event, target_github_user)
    if _was_notification_sent(storage, dedupe_key):
        logger.debug("Skipping duplicate notification", extra={"dedupe_key": dedupe_key})
        return False
    
    # Build notification message
    message = _build_notification_message(event, event_type_key, github_org, target_github_user)
    if not message:
        return False
    
    # Send notification (DM or channel)
    sent = _send_discord_notification(
        discord_writer,
        discord_user_id,
        message,
        config.channel_id,
        policy,
    )
    
    if sent:
        # Mark as sent (dedupe)
        _mark_notification_sent(storage, dedupe_key, event, discord_user_id, config.channel_id, target_github_user)
        # Audit
        _audit_notification(storage, event, discord_user_id, config.channel_id, target_github_user)
    
    return sent


def _resolve_github_to_discord(storage: Storage, github_user: str) -> str | None:
    """Resolve verified GitHub user to Discord user ID. Returns None if not verified.
    GitHub usernames are case-insensitive; comparison is done case-insensitively.
    """
    verified = getattr(storage, "list_verified_identity_mappings", None)
    if not callable(verified):
        return None
    github_lower = (github_user or "").strip().lower()
    if not github_lower:
        return None
    for mapping in verified():
        # Handle both dict and object-style mappings
        gh_user = mapping.get("github_user") if isinstance(mapping, dict) else getattr(mapping, "github_user", None)
        if (gh_user or "").strip().lower() == github_lower:
            discord_id = mapping.get("discord_user_id") if isinstance(mapping, dict) else getattr(mapping, "discord_user_id", None)
            return discord_id
    return None


def _build_dedupe_key(event: ContributionEvent, target_github_user: str) -> str:
    """Build deduplication key: event_type:repo:target:target_github_user (lowercase for case-insensitivity)."""
    target = event.payload.get("issue_number") or event.payload.get("pr_number") or "unknown"
    # Use target_github_user (who receives notification) for dedupe; normalize to lowercase (GitHub is case-insensitive)
    user_key = (target_github_user or "").strip().lower()
    return f"{event.event_type}:{event.repo}:{target}:{user_key}"


def _was_notification_sent(storage: Storage, dedupe_key: str) -> bool:
    """Check if notification was already sent (dedupe)."""
    check = getattr(storage, "was_notification_sent", None)
    if callable(check):
        return check(dedupe_key)
    return False


def _mark_notification_sent(
    storage: Storage,
    dedupe_key: str,
    event: ContributionEvent,
    discord_user_id: str,
    channel_id: str | None,
    target_github_user: str,
) -> None:
    """Mark notification as sent (dedupe tracking)."""
    mark = getattr(storage, "mark_notification_sent", None)
    if callable(mark):
        mark(dedupe_key, event, discord_user_id, channel_id, target_github_user)


def _audit_notification(
    storage: Storage,
    event: ContributionEvent,
    discord_user_id: str,
    channel_id: str | None,
    target_github_user: str,
) -> None:
    """Append audit event for notification."""
    append = getattr(storage, "append_audit_event", None)
    if callable(append):
        target = event.payload.get("issue_number") or event.payload.get("pr_number")
        append({
            "event_type": "github_notification_sent",
            "context": {
                "github_user": target_github_user,  # Who received the notification
                "discord_user_id": discord_user_id,
                "event_type": event.event_type,
                "repo": event.repo,
                "target": target,
                "notification_type": "channel" if channel_id else "dm",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        })


def _build_notification_message(
    event: ContributionEvent,
    event_type_key: str,
    github_org: str,
    target_github_user: str,
) -> str | None:
    """Build Discord notification message for the event."""
    repo = event.repo
    payload = event.payload
    
    if event_type_key == "issue_assigned":
        issue_number = payload.get("issue_number")
        issue_title = payload.get("title", "Untitled")[:100]
        assigned_by = payload.get("assigned_by")
        assigned_by_str = f" by {assigned_by}" if assigned_by else ""
        return (
            f"📌 **Issue Assigned**\n\n"
            f"**Repository:** {github_org}/{repo}\n"
            f"**Issue:** #{issue_number} – {issue_title}\n"
            f"**Assigned{assigned_by_str}**\n"
            f"**Link:** https://github.com/{github_org}/{repo}/issues/{issue_number}\n\n"
            f"You are now responsible for this issue."
        )
    
    elif event_type_key == "pr_review_requested":
        pr_number = payload.get("pr_number")
        pr_title = payload.get("title", "Untitled")[:100]
        requested_by = payload.get("requested_by")  # May need to fetch from PR
        return (
            f"👀 **PR Review Requested**\n\n"
            f"**PR:** #{pr_number} – {pr_title}\n"
            f"**Repository:** {github_org}/{repo}\n"
            f"**Link:** https://github.com/{github_org}/{repo}/pull/{pr_number}\n\n"
            f"Please review when you have time."
        )
    
    elif event_type_key == "pr_approved":
        pr_number = payload.get("pr_number")
        # Reviewer is the github_user from the event (the one who reviewed)
        reviewer = event.github_user
        return (
            f"✅ **PR Approved**\n\n"
            f"Your PR #{pr_number} has been approved by {reviewer}\n"
            f"**Repository:** {github_org}/{repo}\n"
            f"**Status:** Ready to merge\n"
            f"**Link:** https://github.com/{github_org}/{repo}/pull/{pr_number}"
        )
    
    elif event_type_key == "pr_changes_requested":
        pr_number = payload.get("pr_number")
        # Reviewer is the github_user from the event (the one who requested changes)
        reviewer = event.github_user
        return (
            f"🛠 **Changes Requested**\n\n"
            f"Your PR #{pr_number} needs updates\n"
            f"**Reviewer:** {reviewer}\n"
            f"**Repository:** {github_org}/{repo}\n"
            f"**Link:** https://github.com/{github_org}/{repo}/pull/{pr_number}\n\n"
            f"Please address the comments on GitHub."
        )
    
    elif event_type_key == "pr_merged":
        pr_number = payload.get("pr_number")
        return (
            f"🎉 **PR Merged**\n\n"
            f"Your PR #{pr_number} has been merged 🎉\n"
            f"**Repository:** {github_org}/{repo}\n"
            f"**Link:** https://github.com/{github_org}/{repo}/pull/{pr_number}\n\n"
            f"Great work!"
        )
    
    return None


def _send_discord_notification(
    discord_writer: DiscordWriter,
    discord_user_id: str,
    message: str,
    channel_id: str | None,
    policy: MutationPolicy,
) -> bool:
    """Send notification via DM or channel. Returns True if sent."""
    if not policy.allow_discord_mutations:
        logger.debug("Skipping notification: Discord writes disabled (dry-run/observer)")
        return False
    
    if channel_id:
        send_msg = getattr(discord_writer, "send_message", None)
        if callable(send_msg):
            try:
                send_msg(channel_id, message)
                return True
            except Exception as exc:
                logger.warning("Failed to send channel notification", exc_info=True, extra={"error": str(exc)})
                return False
    else:
        send_dm = getattr(discord_writer, "send_dm", None)
        if callable(send_dm):
            try:
                return send_dm(discord_user_id, message)
            except Exception as exc:
                logger.warning("Failed to send DM notification", exc_info=True, extra={"error": str(exc)})
                return False
    
    return False
