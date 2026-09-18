"""Claim Issue logic: announcement components, mentor approval prompts, and lifecycle handlers."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from ghdcbot.core.modes import MutationPolicy
from ghdcbot.engine.issue_request_flow import (
    build_mentor_request_embed,
    compute_eligibility,
    get_merged_pr_count_and_last_time,
)

logger = logging.getLogger(__name__)


def build_issue_announcement_components(
    github_org: str,
    repo: str,
    issue_number: int,
) -> list[dict[str, Any]]:
    """Build interactive button components for an issue announcement.

    Includes:
    - [✋ Claim Issue]: Custom ID claim_issue:<repo>:<issue_number>
    - [🔗 View on GitHub]: Direct link to GitHub issue
    """
    issue_url = f"https://github.com/{github_org}/{repo}/issues/{issue_number}"
    return [
        {
            "type": 1,  # Action Row
            "components": [
                {
                    "type": 2,  # Button
                    "style": 1,  # Primary (blurple)
                    "label": "Claim Issue",
                    "custom_id": f"claim_issue:{repo}:{issue_number}",
                    "emoji": {"name": "✋"},
                },
                {
                    "type": 2,  # Button
                    "style": 5,  # Link
                    "label": "View on GitHub",
                    "url": issue_url,
                    "emoji": {"name": "🔗"},
                },
            ],
        }
    ]


def build_mentor_claim_components(request_id: str) -> list[dict[str, Any]]:
    """Build mentor action buttons for an issue claim approval prompt."""
    return [
        {
            "type": 1,  # Action Row
            "components": [
                {
                    "type": 2,  # Button
                    "style": 3,  # Success (green)
                    "label": "Approve & Assign",
                    "custom_id": f"claim_approve:{request_id}",
                    "emoji": {"name": "✅"},
                },
                {
                    "type": 2,  # Button
                    "style": 4,  # Danger (red)
                    "label": "Decline",
                    "custom_id": f"claim_decline:{request_id}",
                    "emoji": {"name": "❌"},
                },
            ],
        }
    ]


def check_existing_user_request(
    storage: Any,
    discord_user_id: str,
    repo: str,
    issue_number: int,
) -> bool:
    """Return True if user already has a pending claim request for this issue."""
    list_pending = getattr(storage, "list_pending_issue_requests", None)
    if not callable(list_pending):
        return False
    try:
        pending_list = list_pending()
        return any(
            str(req.get("discord_user_id")) == str(discord_user_id)
            and req.get("repo") == repo
            and int(req.get("issue_number", -1)) == int(issue_number)
            for req in pending_list
        )
    except (ValueError, TypeError, KeyError) as exc:
        logger.warning("Error checking pending issue requests: %s", exc)
        return False


def create_claim_request(
    storage: Any,
    discord_user_id: str,
    github_user: str,
    owner: str,
    repo: str,
    issue_number: int,
    issue_url: str,
) -> str:
    """Store a new issue claim request and append an audit event."""
    request_id = str(uuid.uuid4())
    insert_req = getattr(storage, "insert_issue_request", None)
    if callable(insert_req):
        insert_req(
            request_id=request_id,
            discord_user_id=discord_user_id,
            github_user=github_user,
            owner=owner,
            repo=repo,
            issue_number=issue_number,
            issue_url=issue_url,
        )

    append_audit = getattr(storage, "append_audit_event", None)
    if callable(append_audit):
        append_audit({
            "event_type": "issue_request_created",
            "context": {
                "request_id": request_id,
                "discord_user_id": discord_user_id,
                "github_user": github_user,
                "issue": f"{owner}/{repo}#{issue_number}",
            },
        })

    return request_id


def build_mentor_claim_card(
    request: dict[str, Any],
    issue: dict[str, Any],
    contributor_roles: list[str],
    storage: Any,
    eligible_roles_config: list[str],
    period_days: int = 30,
    now: datetime | None = None,
) -> tuple[str, dict[str, Any]]:
    """Build mentor prompt header and rich embed with contributor eligibility."""
    if now is None:
        now = datetime.now(UTC)

    github_user = request.get("github_user", "unknown")
    discord_user_id = request.get("discord_user_id", "")
    owner = request.get("owner", "")
    repo = request.get("repo", "")
    issue_number = request.get("issue_number", 0)

    from datetime import timedelta
    period_start = now - timedelta(days=period_days)
    merged_count, last_merged_at = get_merged_pr_count_and_last_time(
        storage, github_user, period_start, now
    )

    verdict, reason = compute_eligibility(
        eligible_roles_config, contributor_roles, merged_count, last_merged_at, now
    )

    embed_dict = build_mentor_request_embed(
        request=request,
        issue=issue,
        contributor_discord_mention=f"<@{discord_user_id}>",
        contributor_roles=contributor_roles,
        merged_count=merged_count,
        last_merged_at=last_merged_at,
        eligibility_verdict=verdict,
        eligibility_reason=reason,
        eligible_roles_config=eligible_roles_config,
        period_days=period_days,
        now=now,
    )

    header_text = (
        f"🔔 **Issue Request: #{issue_number}** in `{owner}/{repo}`\n"
        f"**Requester:** `{github_user}` (<@{discord_user_id}>)"
    )

    return header_text, embed_dict


def process_claim_approval(
    storage: Any,
    github_adapter: Any,
    policy: MutationPolicy,
    request_id: str,
    mentor_discord_id: str,
    mentor_github: str | None = None,
) -> tuple[bool, str, dict[str, Any] | None]:
    """Approve a claim request, assign on GitHub, update SQLite, and audit.

    Returns (success, message, request_dict).
    """
    get_req = getattr(storage, "get_issue_request", None)
    if not callable(get_req):
        return False, "Storage does not support get_issue_request.", None

    req = get_req(request_id)
    if not req or req.get("status") != "pending":
        return False, "Request is no longer pending or does not exist.", req

    if not policy.allow_github_mutations:
        return False, "GitHub mutations are disabled by configuration or run mode.", req

    owner = req["owner"]
    repo = req["repo"]
    issue_number = int(req["issue_number"])
    requester_github = req["github_user"]

    # Pre-check if the issue was already assigned to someone else
    get_issue = getattr(github_adapter, "get_issue", None)
    if callable(get_issue):
        try:
            live_issue = get_issue(owner, repo, issue_number)
            if isinstance(live_issue, dict):
                raw_assignees = live_issue.get("assignees", [])
                if isinstance(raw_assignees, list):
                    assignees = [
                        a.get("login")
                        for a in raw_assignees
                        if isinstance(a, dict) and a.get("login")
                    ]
                    if assignees and requester_github not in assignees:
                        return (
                            False,
                            f"Issue #{issue_number} is already assigned to @{', @'.join(assignees)} on GitHub.",
                            req,
                        )
        except (KeyError, ValueError, TypeError, AttributeError) as exc:
            logger.warning("Could not pre-check issue assignees: %s", exc)

    assign_fn = getattr(github_adapter, "assign_issue", None)
    if not callable(assign_fn):
        return False, "GitHub adapter cannot assign issues.", req

    assigned = assign_fn(owner, repo, issue_number, requester_github)
    if not assigned:
        return False, f"Failed to assign @{requester_github} on GitHub.", req

    update_status = getattr(storage, "update_issue_request_status", None)
    if callable(update_status):
        update_status(request_id, "approved")

    append_audit = getattr(storage, "append_audit_event", None)
    if callable(append_audit):
        append_audit({
            "event_type": "issue_request_approved",
            "context": {
                "request_id": request_id,
                "repo": f"{owner}/{repo}",
                "issue_number": issue_number,
                "mentor_discord_id": mentor_discord_id,
                "mentor_github": mentor_github,
                "contributor_discord_id": req["discord_user_id"],
                "assignee": requester_github,
                "timestamp": datetime.now(UTC).isoformat(),
            },
        })

    return True, f"Approved and assigned @{requester_github} to #{issue_number}.", req


def process_claim_decline(
    storage: Any,
    request_id: str,
    mentor_discord_id: str,
    mentor_github: str | None = None,
) -> tuple[bool, str, dict[str, Any] | None]:
    """Decline an issue claim request, update SQLite status to rejected, and audit.

    Returns (success, message, request_dict).
    """
    get_req = getattr(storage, "get_issue_request", None)
    if not callable(get_req):
        return False, "Storage does not support get_issue_request.", None

    req = get_req(request_id)
    if not req or req.get("status") != "pending":
        return False, "Request is no longer pending or does not exist.", req

    update_status = getattr(storage, "update_issue_request_status", None)
    if callable(update_status):
        update_status(request_id, "rejected")

    append_audit = getattr(storage, "append_audit_event", None)
    if callable(append_audit):
        append_audit({
            "event_type": "issue_request_rejected",
            "context": {
                "request_id": request_id,
                "repo": f"{req['owner']}/{req['repo']}",
                "issue_number": int(req['issue_number']),
                "mentor_discord_id": mentor_discord_id,
                "mentor_github": mentor_github,
                "contributor_discord_id": req["discord_user_id"],
                "requester": req["github_user"],
                "timestamp": datetime.now(UTC).isoformat(),
            },
        })

    return True, f"Declined claim request for #{req['issue_number']}.", req
