"""Inactive issue check-in, reminder, and escalation workflow."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from ghdcbot.config.models import NotificationConfig
from ghdcbot.core.interfaces import DiscordWriter
from ghdcbot.core.models import ContributionEvent
from ghdcbot.core.modes import MutationPolicy
from ghdcbot.engine.notifications import _send_discord_notification

logger = logging.getLogger(__name__)


def _parse_utc_datetime(value: str | datetime | None) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    text = str(value).strip()
    if not text:
        return None
    try:
        normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except (ValueError, TypeError):
        return None


def resolve_github_to_discord(storage: Any, github_user: str) -> str | None:
    """Resolve GitHub username to verified Discord user ID."""
    verified = getattr(storage, "list_verified_identity_mappings", None)
    if not callable(verified):
        return None

    target = (github_user or "").strip().lower()
    for mapping in verified():
        gh = (
            getattr(mapping, "github_user", None)
            or (mapping.get("github_user") if isinstance(mapping, dict) else None)
        )
        if gh and gh.strip().lower() == target:
            return (
                getattr(mapping, "discord_user_id", None)
                or (mapping.get("discord_user_id") if isinstance(mapping, dict) else None)
            )

    return None


def build_checkin_message(
    github_org: str,
    repo: str,
    issue_number: int,
    issue_title: str,
    github_user: str,
    discord_user_id: str | None,
    time_label: str | int = "7 days",
    days: str | int | None = None,
) -> str:
    """Build the polite check-in reminder DM message."""
    display_name = f"<@{discord_user_id}>" if discord_user_id else github_user
    title_str = issue_title.strip() if issue_title else f"Issue #{issue_number}"
    repo_full = f"{github_org}/{repo}" if github_org else repo
    raw_time = days if days is not None else time_label
    time_str = f"{raw_time} days" if isinstance(raw_time, int) else str(raw_time)
    return (
        f"**Gitcord Check-in: Issue #{issue_number}**\n"
        f"**Repository:** {repo_full}\n"
        f"**Issue:** {title_str}\n"
        f"> Hey {display_name}, you were assigned to this issue {time_str} ago. Are you still actively working on it?\n\n"
        f"*If you need help or more time, let the mentors know on Discord! If you are no longer able to work on this, please let us know so someone else can take it.*"
    )


def build_unassign_comment(
    github_user: str,
    time_label: str | int = "14 days",
    total_days: str | int | None = None,
) -> str:
    """Build courtesy comment posted on GitHub issue upon unassignment."""
    raw_time = total_days if total_days is not None else time_label
    time_str = f"{raw_time} days" if isinstance(raw_time, int) else str(raw_time)
    return (
        f"@{github_user} has been unassigned from this issue due to {time_str} "
        f"of inactivity with no updates. This issue is now open for other contributors to claim."
    )


def build_unassign_dm_message(
    github_org: str,
    repo: str,
    issue_number: int,
    issue_title: str,
    github_user: str,
    discord_user_id: str | None,
    time_label: str | int = "14 days",
    total_days: str | int | None = None,
) -> str:
    """Build follow-up DM notifying contributor of unassignment."""
    display_name = f"<@{discord_user_id}>" if discord_user_id else github_user
    title_str = issue_title.strip() if issue_title else f"Issue #{issue_number}"
    repo_full = f"{github_org}/{repo}" if github_org else repo
    raw_time = total_days if total_days is not None else time_label
    time_str = f"{raw_time} days" if isinstance(raw_time, int) else str(raw_time)
    return (
        f"**Gitcord Update: Issue #{issue_number}**\n"
        f"**Repository:** {repo_full}\n"
        f"**Issue:** {title_str}\n"
        f"> Hey {display_name}, you were unassigned from this issue due to {time_str} "
        f"of inactivity with no updates. This issue has been reopened for other contributors.\n\n"
        f"*Feel free to browse open issues and contribute again whenever you're ready!*"
    )


def build_mentor_alert_message(
    github_org: str,
    repo: str,
    issue_number: int,
    issue_title: str,
    github_user: str,
    discord_user_id: str | None,
    time_label: str | int = "14 days",
    total_days: str | int | None = None,
    action: str = "unassigned",
) -> str:
    """Build alert message for mentor channel."""
    user_ref = f"<@{discord_user_id}> (`{github_user}`)" if discord_user_id else f"`{github_user}`"
    title_str = issue_title.strip() if issue_title else f"Issue #{issue_number}"
    repo_full = f"{github_org}/{repo}" if github_org else repo
    raw_time = total_days if total_days is not None else time_label
    time_str = f"{raw_time} days" if isinstance(raw_time, int) else str(raw_time)
    return (
        f"⚠️ **Inactive Issue {action.capitalize()}**: Issue **#{issue_number}** ({title_str}) in **{repo_full}** "
        f"was {action} from {user_ref} after {time_str} of inactivity."
    )


def has_contributor_activity(
    github_reader: Any,
    owner: str,
    repo: str,
    issue_number: int,
    github_user: str,
    since: datetime,
) -> tuple[bool, datetime | None]:
    """Check if contributor has made progress on the issue or repo since the given timestamp.

    Returns (has_activity, latest_activity_time).
    """
    target_user = (github_user or "").strip().lower()
    latest_activity: datetime | None = None

    # 1. Check comments on the issue
    get_comments = getattr(github_reader, "get_issue_comments", None)
    if callable(get_comments):
        try:
            comments = get_comments(owner, repo, issue_number) or []
            for comment in comments:
                user = comment.get("user") or {}
                login = (user.get("login") or "").strip().lower()
                if login == target_user:
                    created_at = _parse_utc_datetime(comment.get("created_at"))
                    if created_at and created_at > since and (latest_activity is None or created_at > latest_activity):
                        latest_activity = created_at
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to fetch issue comments for inactivity check",
                extra={"repo": repo, "issue": issue_number, "error": str(exc)},
            )

    # 2. Check PRs by the author in this repo
    list_author_prs = getattr(github_reader, "list_pull_requests_for_author", None)
    if callable(list_author_prs):
        try:
            prs = list_author_prs(github_user, repo=repo) or []
            pattern = re.compile(rf"#\b{issue_number}\b", re.IGNORECASE)
            for pr in prs:
                pr_created = _parse_utc_datetime(pr.get("created_at"))
                title = str(pr.get("title") or "")
                body = str(pr.get("body") or "")

                # Any PR mentioning this issue or created after assignment
                if pattern.search(title) or pattern.search(body) or (pr_created and pr_created > since):
                    if pr_created and pr_created > since and (latest_activity is None or pr_created > latest_activity):
                        latest_activity = pr_created
                    else:
                        pr_updated = _parse_utc_datetime(pr.get("updated_at"))
                        if pr_updated and pr_updated > since and (latest_activity is None or pr_updated > latest_activity):
                            latest_activity = pr_updated
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to fetch author PRs for inactivity check",
                extra={"repo": repo, "author": github_user, "error": str(exc)},
            )

    if latest_activity is not None and latest_activity > since:
        return True, latest_activity

    return False, None


def run_issue_inactivity_lifecycle(
    github_reader: Any,
    github_writer: Any,
    discord_writer: DiscordWriter,
    storage: Any,
    policy: MutationPolicy,
    config: NotificationConfig,
    github_org: str,
    now: datetime | None = None,
) -> None:
    """Scan assigned open issues and run 2-stage inactivity check-in and escalation."""
    if not getattr(config, "issue_inactivity_reminders", False):
        return

    current_time = now or datetime.now(UTC)
    inactivity_days = getattr(config, "issue_inactivity_days", 7) or 7
    escalate_days = getattr(config, "issue_inactivity_escalate_days", 7) or 7
    inactivity_minutes = getattr(config, "issue_inactivity_minutes", None)
    escalate_minutes = getattr(config, "issue_inactivity_escalate_minutes", None)
    auto_unassign = getattr(config, "issue_inactivity_auto_unassign", True)
    comment_on_unassign = getattr(config, "issue_inactivity_comment_on_unassign", True)
    alert_channel_id = getattr(config, "issue_inactivity_alert_channel_id", None)

    if inactivity_minutes is not None:
        inactivity_delta = timedelta(minutes=inactivity_minutes)
        inactivity_label = f"{inactivity_minutes} minute" if inactivity_minutes == 1 else f"{inactivity_minutes} minutes"
    else:
        inactivity_delta = timedelta(days=inactivity_days)
        inactivity_label = f"{inactivity_days} days"

    if escalate_minutes is not None:
        escalate_delta = timedelta(minutes=escalate_minutes)
        total_mins = (inactivity_minutes or 0) + escalate_minutes
        total_time_label = f"{total_mins} minute" if total_mins == 1 else f"{total_mins} minutes"
    else:
        escalate_delta = timedelta(days=escalate_days)
        total_time_label = f"{inactivity_days + escalate_days} days"

    # Discover open issues
    list_issues = getattr(github_reader, "list_open_issues", None)
    if not callable(list_issues):
        logger.debug("Inactivity lifecycle: GitHub reader has no list_open_issues")
        return

    active_issues: list[dict] = list(list_issues())

    for issue in active_issues:
        repo = issue.get("repo")
        issue_number = issue.get("number")
        if not repo or issue_number is None:
            continue

        raw_assignees = issue.get("assignees") or []
        assignee_logins: list[str] = []
        for a in raw_assignees:
            if isinstance(a, dict):
                login = a.get("login")
                if login:
                    assignee_logins.append(login)
            elif isinstance(a, str) and a.strip():
                assignee_logins.append(a.strip())

        issue_title = issue.get("title") or f"Issue #{issue_number}"
        issue_created_at = _parse_utc_datetime(issue.get("created_at")) or current_time

        # Check existing tracking records for each assignee
        for assignee in assignee_logins:
            record = None
            get_rec = getattr(storage, "get_issue_inactivity_record", None)
            if callable(get_rec):
                record = get_rec(repo, issue_number, assignee)

            if record is None:
                # First time seeing this assignment: initialize tracking
                track_fn = getattr(storage, "track_issue_assignment", None)
                if callable(track_fn):
                    track_fn(
                        repo=repo,
                        issue_number=issue_number,
                        github_user=assignee,
                        assigned_at=issue_created_at,
                        last_activity_at=issue_created_at,
                    )
                record = {
                    "repo": repo,
                    "issue_number": issue_number,
                    "github_user": assignee,
                    "assigned_at": issue_created_at.isoformat(),
                    "last_activity_at": issue_created_at.isoformat(),
                    "status": "assigned",
                    "reminder_sent_at": None,
                    "escalated_at": None,
                }

            status = record.get("status") or "assigned"
            if status in {"unassigned", "resolved"}:
                continue

            last_act = _parse_utc_datetime(record.get("last_activity_at")) or issue_created_at
            reminder_sent_at = _parse_utc_datetime(record.get("reminder_sent_at"))

            # Check for recent contributor activity (resets timer)
            has_activity, new_act_time = has_contributor_activity(
                github_reader=github_reader,
                owner=github_org,
                repo=repo,
                issue_number=issue_number,
                github_user=assignee,
                since=last_act,
            )

            if has_activity and new_act_time:
                update_act_fn = getattr(storage, "update_issue_inactivity_activity", None)
                if callable(update_act_fn):
                    update_act_fn(repo, issue_number, assignee, new_act_time)
                logger.info(
                    "Activity detected for assigned contributor; clock reset",
                    extra={"repo": repo, "issue": issue_number, "assignee": assignee},
                )
                continue

            discord_user_id = resolve_github_to_discord(storage, assignee)

            # Stage 2: 14 days total (escalate_days after reminder_sent_at)
            if reminder_sent_at is not None:
                if (current_time - reminder_sent_at) >= escalate_delta:
                    # Escalation!
                    if auto_unassign and policy.allow_github_mutations:
                        # 1. Unassign on GitHub
                        unassign_fn = getattr(github_writer, "unassign_issue", None)
                        if callable(unassign_fn):
                            try:
                                unassign_fn(github_org, repo, issue_number, assignee)
                            except Exception as exc:  # noqa: BLE001
                                logger.error(
                                    "Failed to unassign inactive contributor",
                                    extra={"repo": repo, "issue": issue_number, "assignee": assignee, "error": str(exc)},
                                )

                        # 2. Courtesy comment on issue
                        if comment_on_unassign:
                            comment_fn = getattr(github_writer, "create_issue_comment", None)
                            if callable(comment_fn):
                                try:
                                    comment_fn(
                                        github_org,
                                        repo,
                                        issue_number,
                                        build_unassign_comment(assignee, total_time_label),
                                    )
                                except Exception as exc:  # noqa: BLE001
                                    logger.warning(
                                        "Failed to post unassignment comment",
                                        extra={"repo": repo, "issue": issue_number, "error": str(exc)},
                                    )

                        # 3. Follow-up DM to verified contributor
                        if discord_user_id and policy.allow_discord_mutations:
                            unassign_dm = build_unassign_dm_message(
                                github_org=github_org,
                                repo=repo,
                                issue_number=issue_number,
                                issue_title=issue_title,
                                github_user=assignee,
                                discord_user_id=discord_user_id,
                                time_label=total_time_label,
                            )
                            _send_discord_notification(
                                discord_writer=discord_writer,
                                discord_user_id=discord_user_id,
                                message=unassign_dm,
                                channel_id=None,
                                policy=policy,
                            )

                        # 4. Mentor channel alert
                        if alert_channel_id and policy.allow_discord_mutations:
                            mentor_alert = build_mentor_alert_message(
                                github_org=github_org,
                                repo=repo,
                                issue_number=issue_number,
                                issue_title=issue_title,
                                github_user=assignee,
                                discord_user_id=discord_user_id,
                                time_label=total_time_label,
                                action="unassigned",
                            )
                            send_msg = getattr(discord_writer, "send_message", None)
                            if callable(send_msg):
                                try:
                                    send_msg(alert_channel_id, mentor_alert)
                                except Exception as exc:  # noqa: BLE001
                                    logger.warning("Failed to send mentor alert", extra={"error": str(exc)})

                        # Record escalation in storage
                        esc_fn = getattr(storage, "record_issue_inactivity_escalation", None)
                        if callable(esc_fn):
                            esc_fn(repo, issue_number, assignee, current_time, status="unassigned")

                        audit_fn = getattr(storage, "append_audit_event", None)
                        if callable(audit_fn):
                            audit_fn({
                                "action": "issue_inactivity_unassigned",
                                "repo": repo,
                                "issue_number": issue_number,
                                "github_user": assignee,
                                "discord_user_id": discord_user_id,
                                "total_time": total_time_label,
                                "timestamp": current_time.isoformat(),
                            })
                    else:
                        # Dry run / observer mode or write disabled
                        logger.info(
                            "[DRY-RUN / AUDIT] Would unassign inactive contributor after %s",
                            total_time_label,
                            extra={"repo": repo, "issue": issue_number, "assignee": assignee},
                        )
                        esc_fn = getattr(storage, "record_issue_inactivity_escalation", None)
                        if callable(esc_fn):
                            esc_fn(repo, issue_number, assignee, current_time, status="escalated")

                        audit_fn = getattr(storage, "append_audit_event", None)
                        if callable(audit_fn):
                            audit_fn({
                                "action": "issue_inactivity_escalated_dry_run",
                                "repo": repo,
                                "issue_number": issue_number,
                                "github_user": assignee,
                                "discord_user_id": discord_user_id,
                                "total_time": total_time_label,
                                "timestamp": current_time.isoformat(),
                            })

                continue

            # Stage 1: Inactive (send check-in DM reminder)
            if (current_time - last_act) >= inactivity_delta:
                dedupe_key = f"issue_inactivity_checkin:{repo}:{issue_number}:{assignee}"
                was_sent = getattr(storage, "was_notification_sent", None)
                if callable(was_sent) and was_sent(dedupe_key):
                    continue

                if discord_user_id:
                    msg = build_checkin_message(
                        github_org=github_org,
                        repo=repo,
                        issue_number=issue_number,
                        issue_title=issue_title,
                        github_user=assignee,
                        discord_user_id=discord_user_id,
                        time_label=inactivity_label,
                    )
                    sent = _send_discord_notification(
                        discord_writer=discord_writer,
                        discord_user_id=discord_user_id,
                        message=msg,
                        channel_id=None,
                        policy=policy,
                    )
                    if sent or not policy.allow_discord_mutations:
                        rem_fn = getattr(storage, "record_issue_inactivity_reminder", None)
                        if callable(rem_fn):
                            rem_fn(repo, issue_number, assignee, current_time)

                        mark_sent = getattr(storage, "mark_notification_sent", None)
                        if callable(mark_sent):
                            event = ContributionEvent(
                                github_user=assignee,
                                event_type="issue_inactivity_checkin",
                                repo=repo,
                                created_at=current_time,
                                payload={"issue_number": issue_number, "days": inactivity_days},
                            )
                            mark_sent(dedupe_key, event, discord_user_id, None, assignee)

                        logger.info(
                            "Sent 7-day inactivity check-in reminder",
                            extra={"repo": repo, "issue": issue_number, "assignee": assignee},
                        )
                else:
                    logger.debug(
                        "Skipping inactivity check-in DM: unverified contributor",
                        extra={"repo": repo, "issue": issue_number, "assignee": assignee},
                    )
