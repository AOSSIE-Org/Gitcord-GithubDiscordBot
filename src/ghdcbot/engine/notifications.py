"""Verified-only GitHub → Discord status notifications (anti-spam, mentor-first)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from ghdcbot.config.models import NotificationConfig
from ghdcbot.core.interfaces import DiscordWriter, Storage
from ghdcbot.core.modes import MutationPolicy, RunMode
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
        elif state in ("COMMENT", "COMMENTED"):
            if not config.pr_review_comment:
                return False
            event_type_key = "pr_review_comment"
            target_github_user = event.payload.get("pr_author")
        else:
            # DISMISSED or other states - no notification
            logger.debug(
                "Skipping notification: PR review state is not supported",
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
            "pr_closed": config.pr_closed,
            "issue_reopened": config.issue_reopened,
            "pr_reopened": config.pr_reopened,
        }
        event_type_key = event.event_type
        if not event_config_map.get(event_type_key, False):
            return False
        if event.event_type == "pr_closed":
            target_github_user = event.payload.get("pr_author") or event.github_user
        elif event.event_type == "issue_reopened":
            target_github_user = event.payload.get("assignee")
        elif event.event_type == "pr_reopened":
            target_github_user = event.payload.get("pr_author") or event.github_user
        else:
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


def send_pr_opened_channel_notification(
    event: ContributionEvent,
    storage: Storage,
    discord_writer: DiscordWriter,
    policy: MutationPolicy,
    config: NotificationConfig,
    pr_open_channels: dict[str, str],
    github_org: str,
) -> bool:
    """Post a PR-opened announcement to a repo-mapped Discord channel/thread.

    Posts for every mapped-repo PR open when notifications.pr_opened is enabled.
    Verified authors are @mentioned; unverified authors show a verification notice.
    """
    if not config.enabled or not config.pr_opened:
        return False
    if event.event_type != "pr_opened":
        return False

    channel_id = pr_open_channels.get(event.repo)
    if not channel_id:
        return False

    author_github = event.github_user
    if not author_github:
        return False

    discord_user_id = _resolve_github_to_discord(storage, author_github)

    dedupe_key = f"pr_opened_channel:{event.repo}:{event.payload.get('pr_number')}:{channel_id}"

    message = _build_pr_opened_channel_message(
        event, github_org, author_github, discord_user_id
    )
    if not message:
        return False

    if not policy.allow_discord_mutations:
        return False

    send_msg = getattr(discord_writer, "send_message", None)
    create_msg = getattr(discord_writer, "create_message", None)
    if not callable(send_msg) and not callable(create_msg):
        return False

    notify_discord_id = discord_user_id or ""
    try:
        claimed = _claim_notification_sent(
            storage, dedupe_key, event, notify_discord_id, channel_id, author_github
        )
    except Exception as exc:
        logger.warning(
            "Failed to claim pr_opened channel notification",
            exc_info=True,
            extra={"error": str(exc), "channel_id": channel_id, "repo": event.repo},
        )
        return False
    if not claimed:
        return False

    message_id: str | None = None
    try:
        if callable(create_msg):
            message_id = create_msg(channel_id, message)
            sent = message_id is not None
            # Empty-string sentinel from create_message means no-op success without an ID.
            if message_id == "":
                message_id = None
        else:
            sent = bool(send_msg(channel_id, message))
    except Exception as exc:
        _release_notification_claim(storage, dedupe_key)
        logger.warning(
            "Failed to send pr_opened channel notification",
            exc_info=True,
            extra={"error": str(exc), "channel_id": channel_id, "repo": event.repo},
        )
        return False

    if sent:
        if message_id:
            save = getattr(storage, "save_pr_channel_announcement", None)
            if callable(save):
                try:
                    save(
                        repo=event.repo,
                        pr_number=int(event.payload.get("pr_number")),
                        channel_id=channel_id,
                        message_id=message_id,
                        pr_title=event.payload.get("title"),
                        author_github=author_github,
                        status="open",
                    )
                except Exception as exc:
                    logger.warning(
                        "Failed to track pr_opened channel message for lifecycle edits",
                        exc_info=True,
                        extra={
                            "error": str(exc),
                            "channel_id": channel_id,
                            "repo": event.repo,
                            "pr_number": event.payload.get("pr_number"),
                        },
                    )
        _audit_notification(storage, event, notify_discord_id, channel_id, author_github)
        return True
    _release_notification_claim(storage, dedupe_key)
    return False


def update_pr_channel_announcement_for_event(
    event: ContributionEvent,
    storage: Storage,
    discord_writer: DiscordWriter,
    policy: MutationPolicy,
    config: NotificationConfig,
    github_org: str,
) -> bool:
    """Edit a tracked PR-opened channel message when the PR is merged or closed.

    Only updates announcements posted after tracking was enabled (row in storage).
    Old channel messages are left unchanged.
    """
    if not config.enabled or not getattr(config, "update_pr_channel_on_lifecycle", True):
        return False
    if event.event_type not in {"pr_merged", "pr_closed"}:
        return False
    if not policy.allow_discord_mutations:
        return False

    pr_number = event.payload.get("pr_number")
    if pr_number is None:
        return False

    get_ann = getattr(storage, "get_pr_channel_announcement", None)
    if not callable(get_ann):
        return False
    try:
        tracked = get_ann(event.repo, int(pr_number))
    except Exception as exc:
        logger.warning(
            "Failed to load tracked PR channel announcement",
            exc_info=True,
            extra={"error": str(exc), "repo": event.repo, "pr_number": pr_number},
        )
        return False
    if not tracked:
        # Pre-feature / untracked opens: do not invent edits for old Discord messages.
        return False

    status = "merged" if event.event_type == "pr_merged" else "closed"
    if tracked.get("status") == status:
        return False

    actor = _pr_lifecycle_actor(event)
    built = _build_pr_lifecycle_channel_message(
        event,
        github_org,
        status=status,
        actor_github=actor,
        tracked=tracked,
    )
    if not built:
        return False
    message, embeds = built

    dedupe_key = f"pr_channel_lifecycle:{event.repo}:{pr_number}:{status}"
    try:
        claimed = _claim_notification_sent(
            storage, dedupe_key, event, "", tracked.get("channel_id"), actor
        )
    except Exception as exc:
        logger.warning(
            "Failed to claim PR channel lifecycle update",
            exc_info=True,
            extra={"error": str(exc), "repo": event.repo, "pr_number": pr_number},
        )
        return False
    if not claimed:
        return False

    edit_msg = getattr(discord_writer, "edit_message", None)
    if not callable(edit_msg):
        _release_notification_claim(storage, dedupe_key)
        return False

    try:
        edited = bool(
            edit_msg(
                str(tracked["channel_id"]),
                str(tracked["message_id"]),
                message,
                embeds=embeds,
            )
        )
    except TypeError:
        # Older DiscordWriter mocks/adapters without embeds kwarg.
        if embeds:
            emb = embeds[0]
            fallback = f"**{emb.get('title', '')}**\n\n{emb.get('description', '')}".strip()
        else:
            fallback = message or ""
        try:
            edited = bool(
                edit_msg(
                    str(tracked["channel_id"]),
                    str(tracked["message_id"]),
                    fallback,
                )
            )
        except Exception as exc:
            _release_notification_claim(storage, dedupe_key)
            logger.warning(
                "Failed to edit PR channel announcement",
                exc_info=True,
                extra={
                    "error": str(exc),
                    "repo": event.repo,
                    "pr_number": pr_number,
                    "channel_id": tracked.get("channel_id"),
                    "message_id": tracked.get("message_id"),
                },
            )
            return False
    except Exception as exc:
        _release_notification_claim(storage, dedupe_key)
        logger.warning(
            "Failed to edit PR channel announcement",
            exc_info=True,
            extra={
                "error": str(exc),
                "repo": event.repo,
                "pr_number": pr_number,
                "channel_id": tracked.get("channel_id"),
                "message_id": tracked.get("message_id"),
            },
        )
        return False

    if not edited:
        _release_notification_claim(storage, dedupe_key)
        return False

    mark = getattr(storage, "mark_pr_channel_announcement_status", None)
    if callable(mark):
        try:
            mark(event.repo, int(pr_number), status)
        except Exception as exc:
            logger.warning(
                "Failed to update PR channel announcement status after edit",
                exc_info=True,
                extra={"error": str(exc), "repo": event.repo, "pr_number": pr_number},
            )
    _audit_notification(storage, event, "", tracked.get("channel_id"), actor)
    return True


def _pr_lifecycle_actor(event: ContributionEvent) -> str:
    """Prefer explicit merged_by/closed_by; fall back to event github_user."""
    payload = event.payload or {}
    if event.event_type == "pr_merged":
        actor = payload.get("merged_by") or event.github_user
    else:
        actor = payload.get("closed_by") or payload.get("pr_author") or event.github_user
    return (actor or "unknown").strip() or "unknown"


# GitHub Primer status colors (match PR badge hues in the GitHub UI).
_GITHUB_MERGED_PURPLE = 0x8250DF  # Primer done/merged
_GITHUB_CLOSED_RED = 0xCF222E  # Primer danger/closed


def _build_pr_lifecycle_channel_message(
    event: ContributionEvent,
    github_org: str,
    *,
    status: str,
    actor_github: str,
    tracked: dict,
) -> tuple[str, list[dict]] | None:
    """Build a Discord embed for a PR merge/close channel edit.

    Text + GitHub Primer purple/red live in one embed (no empty color-only box).
    Plain content is left empty so Discord shows a single card.
    """
    pr_number = event.payload.get("pr_number")
    if pr_number is None:
        return None
    title_raw = (
        event.payload.get("title")
        or event.payload.get("pr_title")
        or tracked.get("pr_title")
        or "Untitled"
    )
    pr_title = _sanitize_discord_pr_title(title_raw)
    repo = event.repo
    raw_url = f"https://github.com/{github_org}/{repo}/pull/{pr_number}"
    actor = (actor_github or "unknown").lstrip("@")
    if status == "merged":
        label = "Merged"
        status_line = f"**Status:** Merged by @{actor}"
        color = _GITHUB_MERGED_PURPLE
    else:
        label = "Closed"
        status_line = f"**Status:** Closed by @{actor}"
        color = _GITHUB_CLOSED_RED
    # Discord embed titles are plain text (no markdown links); put the link in url.
    title = f"{label}: {repo} #{pr_number} — {pr_title}"
    if len(title) > 256:
        title = title[:253] + "..."
    embeds = [
        {
            "title": title,
            "url": raw_url,
            "description": status_line,
            "color": color,
        }
    ]
    return "", embeds

def send_pr_opened_github_link_comment(
    event: ContributionEvent,
    storage: Storage,
    github_writer: Any,
    policy: MutationPolicy,
    config: NotificationConfig,
    github_org: str,
    invite_url: str | None,
) -> bool:
    """Comment on a newly opened PR asking an unverified author to /link in Discord.

    Independent of github.permissions.write (assignments) so orgs that disable
    auto-assign can still nudge contributors. Skipped in dry-run/observer.
    """
    if not config.enabled or not config.pr_opened_github_comment:
        return False
    if event.event_type != "pr_opened":
        return False
    if policy.mode != RunMode.ACTIVE:
        return False

    author_github = (event.github_user or "").strip()
    if not author_github or _is_github_bot_login(author_github):
        return False

    if _resolve_github_to_discord(storage, author_github):
        return False

    invite = (invite_url or "").strip()
    if not invite:
        logger.warning(
            "Skipping PR GitHub link comment: discord.invite_url is not set",
            extra={"repo": event.repo, "pr_number": event.payload.get("pr_number")},
        )
        return False

    pr_number = event.payload.get("pr_number")
    if pr_number is None:
        return False

    dedupe_key = f"pr_opened_github_link:{event.repo}:{pr_number}"
    try:
        claimed = _claim_notification_sent(
            storage, dedupe_key, event, "", None, author_github
        )
    except Exception as exc:
        logger.warning(
            "Failed to claim PR GitHub link comment notification",
            exc_info=True,
            extra={"error": str(exc), "repo": event.repo, "pr_number": pr_number},
        )
        return False
    if not claimed:
        return False

    create_comment = getattr(github_writer, "create_issue_comment", None)
    if not callable(create_comment):
        _release_notification_claim(storage, dedupe_key)
        logger.warning(
            "Skipping PR GitHub link comment: github writer has no create_issue_comment",
            extra={"repo": event.repo, "pr_number": pr_number},
        )
        return False

    body = _build_pr_opened_github_link_comment(author_github, invite)
    try:
        sent = bool(create_comment(github_org, event.repo, int(pr_number), body))
    except Exception as exc:
        _release_notification_claim(storage, dedupe_key)
        logger.warning(
            "Failed to post PR GitHub link comment",
            exc_info=True,
            extra={"error": str(exc), "repo": event.repo, "pr_number": pr_number},
        )
        return False

    if sent:
        _audit_notification(storage, event, "", None, author_github)
        return True
    _release_notification_claim(storage, dedupe_key)
    return False


def _is_github_bot_login(login: str) -> bool:
    return login.strip().lower().endswith("[bot]")


def _build_pr_opened_github_link_comment(author_github: str, invite_url: str) -> str:
    """Polished GitHub markdown asking an unverified PR author to link via Discord."""
    return (
        "### Link your account with Gitcord\n"
        "\n"
        f"Thanks for opening this PR, **@{author_github}**!\n"
        "\n"
        "To receive Discord notifications and contributor tracking for this organization:\n"
        "\n"
        f"1. **Join Discord:** {invite_url}\n"
        f"2. In Discord, run `/link {author_github}`\n"
        "3. Paste the verification code into your GitHub **bio** (or a public gist)\n"
        f"4. Click **Verify** in Discord (or run `/verify-link {author_github}`)\n"
        "\n"
        "Once linked, Gitcord can notify you about reviews, merges, and more.\n"
        "\n"
        "— *Posted by Gitcord*"
    )


def _suppress_discord_embed(url: str) -> str:
    """Wrap a URL so Discord clients do not generate a link preview card."""
    text = (url or "").strip()
    if not text:
        return text
    if text.startswith("<") and text.endswith(">"):
        return text
    return f"<{text}>"


def _sanitize_discord_pr_title(title: str) -> str:
    """Escape markdown link delimiters and neutralize mass-mention tokens in PR titles."""
    text = (title or "Untitled")[:100]
    text = text.replace("\\", "\\\\")
    for ch in ("[", "]", "(", ")"):
        text = text.replace(ch, f"\\{ch}")
    for mention in ("@everyone", "@here"):
        text = text.replace(mention, mention[0] + "\u200b" + mention[1:])
    return text


def _build_pr_opened_channel_message(
    event: ContributionEvent,
    github_org: str,
    author_github: str,
    discord_user_id: str | None,
) -> str | None:
    pr_number = event.payload.get("pr_number")
    if pr_number is None:
        return None
    pr_title = _sanitize_discord_pr_title(event.payload.get("title") or "Untitled")
    repo = event.repo
    url = _suppress_discord_embed(
        f"https://github.com/{github_org}/{repo}/pull/{pr_number}"
    )
    # Compact header: linked title so we don't need a separate Link line (Bruno).
    # Author line: "GITHUB - @Discord" when verified, "GITHUB - unknown" otherwise.
    header = f"🆕 **New PR: [{repo} #{pr_number} — {pr_title}]({url})**"
    if discord_user_id:
        author_line = f"**Author:** {author_github} - <@{discord_user_id}>"
        return f"{header}\n\n{author_line}"

    author_line = f"**Author:** {author_github} - unknown"
    link_nudge = (
        f"If you are `{author_github}`, please use `/link {author_github}` "
        "to link your github account to your Discord account."
    )
    return f"{header}\n\n{author_line}\n\n{link_nudge}"


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

    if event.event_type == "pr_closed":
        closed_at = event.payload.get("closed_at") or event.created_at.isoformat()
        return f"{event.event_type}:{event.repo}:{target}:{user_key}:{closed_at}"

    if event.event_type in {"issue_reopened", "pr_reopened"}:
        reopened_at = event.payload.get("reopened_at") or event.created_at.isoformat()
        return f"{event.event_type}:{event.repo}:{target}:{user_key}:{reopened_at}"
    
    return f"{event.event_type}:{event.repo}:{target}:{user_key}"


def _was_notification_sent(storage: Storage, dedupe_key: str) -> bool:
    """Check if notification was already sent (dedupe)."""
    check = getattr(storage, "was_notification_sent", None)
    if callable(check):
        return check(dedupe_key)
    return False


def _claim_notification_sent(
    storage: Storage,
    dedupe_key: str,
    event: ContributionEvent,
    discord_user_id: str,
    channel_id: str | None,
    target_github_user: str,
) -> bool:
    """Atomically claim a dedupe key. Only the claimant should post the notification."""
    claim = getattr(storage, "claim_notification_sent", None)
    if callable(claim):
        return bool(claim(dedupe_key, event, discord_user_id, channel_id, target_github_user))
    # Fallback for storages without claim support (non-atomic).
    if _was_notification_sent(storage, dedupe_key):
        return False
    _mark_notification_sent(storage, dedupe_key, event, discord_user_id, channel_id, target_github_user)
    return True


def _release_notification_claim(storage: Storage, dedupe_key: str) -> None:
    """Release a failed claim so a later sync can retry."""
    release = getattr(storage, "release_notification_claim", None)
    if callable(release):
        release(dedupe_key)
        return
    sent = getattr(storage, "notifications_sent", None)
    if isinstance(sent, set):
        sent.discard(dedupe_key)


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
            f"**Link:** {_suppress_discord_embed(f'https://github.com/{github_org}/{repo}/issues/{issue_number}')}\n\n"
            f"💡 You're now responsible for this issue. Good luck!"
        )
    
    elif event_type_key == "pr_review_requested":
        pr_number = payload.get("pr_number")
        pr_title = payload.get("title", "Untitled")[:100]
        return (
            f"👀 **PR Review Requested**\n\n"
            f"**PR:** #{pr_number} – {pr_title}\n"
            f"**Repository:** {github_org}/{repo}\n"
            f"**Link:** {_suppress_discord_embed(f'https://github.com/{github_org}/{repo}/pull/{pr_number}')}\n\n"
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
            f"**Link:** {_suppress_discord_embed(f'https://github.com/{github_org}/{repo}/pull/{pr_number}')}\n\n"
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
            f"**Link:** {_suppress_discord_embed(f'https://github.com/{github_org}/{repo}/pull/{pr_number}')}\n\n"
            f"💬 Please check the review comments on GitHub and address the feedback."
        )

    elif event_type_key == "pr_review_comment":
        pr_number = payload.get("pr_number")
        pr_title = payload.get("title", "Untitled")[:100]
        reviewer = event.github_user
        return (
            f"💬 **New Review Comments**\n\n"
            f"New review comments were added to your **PR #{pr_number}**.\n\n"
            f"**Reviewer:** `{reviewer}`\n"
            f"**Repository:** `{github_org}/{repo}`\n"
            f"**PR:** {pr_title}\n"
            f"**Link:** {_suppress_discord_embed(f'https://github.com/{github_org}/{repo}/pull/{pr_number}')}\n\n"
            f"Review the feedback and update your PR if needed."
        )
    
    elif event_type_key == "pr_merged":
        pr_number = payload.get("pr_number")
        base_branch = (payload.get("base_branch") or "").strip()
        if base_branch:
            merge_line = (
                f"Congratulations! Your **PR #{pr_number}** has been merged into "
                f"the `{base_branch}` branch. 🎉\n\n"
            )
        else:
            # Older stored events may lack base_branch; do not assume "main".
            merge_line = f"Congratulations! Your **PR #{pr_number}** has been merged. 🎉\n\n"
        return (
            f"🚀 **PR Merged Successfully!**\n\n"
            f"{merge_line}"
            f"**Repository:** `{github_org}/{repo}`\n"
            f"**Link:** {_suppress_discord_embed(f'https://github.com/{github_org}/{repo}/pull/{pr_number}')}\n\n"
            f"✨ Thank you for your contribution!"
        )

    elif event_type_key == "pr_closed":
        pr_number = payload.get("pr_number")
        pr_title = payload.get("pr_title") or payload.get("title", "Untitled")[:100]
        closed_url = payload.get("html_url") or f"https://github.com/{github_org}/{repo}/pull/{pr_number}"
        return (
            f"🔒 **PR Closed**\n\n"
            f'Your PR **#{pr_number}** — *{pr_title}* was closed without being merged.\n\n'
            f"**Repository:** `{github_org}/{repo}`\n"
            f"**Link:** {_suppress_discord_embed(str(closed_url))}\n\n"
            f"Review the discussion for more details."
        )

    elif event_type_key == "issue_reopened":
        issue_number = payload.get("issue_number")
        issue_title = payload.get("title", "Untitled")[:100]
        issue_url = payload.get("html_url") or f"https://github.com/{github_org}/{repo}/issues/{issue_number}"
        return (
            f"📌 **Issue Reopened**\n\n"
            f"Issue #{issue_number} **{issue_title}**\n\n"
            f"assigned to you has been reopened.\n\n"
            f"**Repository:** `{github_org}/{repo}`\n"
            f"**Link:** {_suppress_discord_embed(str(issue_url))}\n\n"
            f"Please review the latest discussion and continue working if needed."
        )

    elif event_type_key == "pr_reopened":
        pr_number = payload.get("pr_number")
        pr_title = payload.get("title", "Untitled")[:100]
        reopen_url = payload.get("html_url") or f"https://github.com/{github_org}/{repo}/pull/{pr_number}"
        return (
            f"🔄 **PR Reopened**\n\n"
            f"Your PR #{pr_number} **{pr_title}**\n\n"
            f"has been reopened.\n\n"
            f"**Repository:** `{github_org}/{repo}`\n"
            f"**Link:** {_suppress_discord_embed(str(reopen_url))}\n\n"
            f"Please review the discussion and continue updating your PR."
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
    url = _suppress_discord_embed(f"https://github.com/{github_org}/{repo}/pull/{pr_number}")
    return (
        f"📋 **CodeRabbit reminder**\n\n"
        f"You have CodeRabbit review comments on **{repo}#{pr_number}** that are over **{after_hours} hours** old.\n\n"
        f"Please address them when you can:\n{url}"
    )
