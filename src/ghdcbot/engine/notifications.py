"""Verified-only GitHub → Discord status notifications (anti-spam, mentor-first)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
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
                "review_id": event.payload.get("review_id"),
                "review_state": event.payload.get("state"),
            },
        )
        return False
    
    # Deduplication: check if we already sent this notification
    dedupe_key = _build_dedupe_key(event, target_github_user)
    if _was_notification_sent(storage, dedupe_key):
        logger.info(
            "Skipping duplicate notification",
            extra={
                "dedupe_key": dedupe_key,
                "event_type": event.event_type,
                "target_github_user": target_github_user,
                "pr_number": event.payload.get("pr_number"),
                "review_id": event.payload.get("review_id"),
                "review_state": event.payload.get("state"),
            },
        )
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
    """Build deduplication key: event_type:repo:target:target_github_user (lowercase for case-insensitivity).
    
    For pr_reviewed events, includes review_id to allow multiple notifications for different reviews.
    """
    target = event.payload.get("issue_number") or event.payload.get("pr_number") or "unknown"
    # Use target_github_user (who receives notification) for dedupe; normalize to lowercase (GitHub is case-insensitive)
    user_key = (target_github_user or "").strip().lower()
    
    # For pr_reviewed events, include review_id and state to allow separate notifications for different reviews
    if event.event_type == "pr_reviewed":
        review_id = event.payload.get("review_id")
        state = event.payload.get("state", "").upper()
        if review_id:
            return f"{event.event_type}:{event.repo}:{target}:{user_key}:{review_id}:{state}"
    
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
        assigned_by_str = f" by **{assigned_by}**" if assigned_by else ""
        return (
            f"📌 **Issue Assigned to You!**\n\n"
            f"You've been assigned to work on:\n"
            f"**#{issue_number} – {issue_title}**\n\n"
            f"**Repository:** `{github_org}/{repo}`\n"
            f"{f'**Assigned{assigned_by_str}**' if assigned_by else ''}\n"
            f"**Link:** https://github.com/{github_org}/{repo}/issues/{issue_number}\n\n"
            f"💡 You're now responsible for this issue. Good luck!"
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
            f"✅ **PR Approved!**\n\n"
            f"Great news! Your **PR #{pr_number}** has been approved by `{reviewer}`.\n\n"
            f"**Repository:** `{github_org}/{repo}`\n"
            f"**Status:** 🟢 Ready to merge\n"
            f"**Link:** https://github.com/{github_org}/{repo}/pull/{pr_number}\n\n"
            f"🎉 Excellent work!"
        )
    
    elif event_type_key == "pr_changes_requested":
        pr_number = payload.get("pr_number")
        # Reviewer is the github_user from the event (the one who requested changes)
        reviewer = event.github_user
        return (
            f"🛠️ **Changes Requested on Your PR**\n\n"
            f"**PR #{pr_number}** needs some updates before it can be merged.\n\n"
            f"**Reviewer:** `{reviewer}`\n"
            f"**Repository:** `{github_org}/{repo}`\n"
            f"**Link:** https://github.com/{github_org}/{repo}/pull/{pr_number}\n\n"
            f"💬 Please check the review comments on GitHub and address the feedback."
        )
    
    elif event_type_key == "pr_merged":
        pr_number = payload.get("pr_number")
        return (
            f"🚀 **PR Merged Successfully!**\n\n"
            f"Congratulations! Your **PR #{pr_number}** has been merged into the main branch. 🎉\n\n"
            f"**Repository:** `{github_org}/{repo}`\n"
            f"**Link:** https://github.com/{github_org}/{repo}/pull/{pr_number}\n\n"
            f"✨ Thank you for your contribution!"
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


def run_coderabbit_reminders(
    github_reader: Any,
    storage: Storage,
    discord_writer: DiscordWriter,
    policy: MutationPolicy,
    config: NotificationConfig,
    github_org: str,
) -> None:
    """For open PRs by verified contributors, remind them if CodeRabbit left review comments older than configured hours.

    Sends at most one reminder per (repo, PR, Discord user); deduplication is stored in notifications_sent.
    No-op if coderabbit_reminders is disabled or GitHub adapter does not support review comments.
    """
    if not getattr(config, "coderabbit_reminders", False):
        return
    after_hours = getattr(config, "coderabbit_reminder_after_hours", 48) or 48
    bot_logins: list[str] = getattr(config, "coderabbit_bot_logins", None) or [
        "coderabbitai",
        "coderabbitai[bot]",
    ]
    bot_logins_lower = [x.strip().lower() for x in bot_logins if x]
    if not bot_logins_lower:
        return
    cutoff = datetime.now(timezone.utc) - timedelta(hours=after_hours)
    get_comments = getattr(github_reader, "get_pull_request_review_comments", None)
    if not callable(get_comments):
        logger.debug("CodeRabbit reminders: GitHub adapter has no get_pull_request_review_comments")
        return
    sent_count = 0
    for pr in github_reader.list_open_pull_requests():
        repo = pr.get("repo")
        pr_number = pr.get("number")
        author = pr.get("author")
        if not repo or pr_number is None or not author:
            continue
        discord_user_id = _resolve_github_to_discord(storage, author)
        if not discord_user_id:
            continue
        try:
            comments = get_comments(github_org, repo, pr_number)
        except Exception as exc:
            logger.warning(
                "Failed to fetch PR review comments for CodeRabbit check",
                extra={"repo": repo, "pr_number": pr_number, "error": str(exc)},
            )
            continue
        old_bot_comments = [
            c for c in comments if _is_coderabbit_comment(c, bot_logins_lower, cutoff)
        ]
        if not old_bot_comments:
            continue
        dedupe_key = f"coderabbit_reminder:{repo}:{pr_number}:{discord_user_id}"
        if _was_notification_sent(storage, dedupe_key):
            continue
        message = _build_coderabbit_reminder_message(github_org, repo, pr_number, after_hours)
        sent = _send_discord_notification(
            discord_writer, discord_user_id, message, config.channel_id, policy
        )
        if sent:
            event = ContributionEvent(
                github_user=author,
                event_type="coderabbit_reminder",
                repo=repo,
                created_at=datetime.now(timezone.utc),
                payload={"pr_number": pr_number},
            )
            _mark_notification_sent(
                storage, dedupe_key, event, discord_user_id, config.channel_id, author
            )
            sent_count += 1
            logger.info(
                "Sent CodeRabbit reminder",
                extra={"repo": repo, "pr_number": pr_number, "github_user": author},
            )
    if sent_count > 0:
        logger.info("CodeRabbit reminders sent", extra={"count": sent_count})


def _is_coderabbit_comment(comment: dict, bot_logins_lower: list[str], cutoff: datetime) -> bool:
    """True if comment is from a configured bot login and was created before cutoff."""
    user = comment.get("user") or {}
    login = (user.get("login") or "").strip().lower()
    if not login or login not in bot_logins_lower:
        return False
    created_at = comment.get("created_at")
    if not created_at:
        return False
    try:
        dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt <= cutoff
    except (ValueError, TypeError):
        return False


def _build_coderabbit_reminder_message(
    github_org: str, repo: str, pr_number: int, after_hours: int
) -> str:
    url = f"https://github.com/{github_org}/{repo}/pull/{pr_number}"
    return (
        f"📋 **CodeRabbit reminder**\n\n"
        f"You have CodeRabbit review comments on **{repo}#{pr_number}** that are over **{after_hours} hours** old.\n\n"
        f"Please address them when you can:\n{url}"
    )
