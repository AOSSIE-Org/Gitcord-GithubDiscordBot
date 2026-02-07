"""Issue request & assignment flow: contributor requests, mentor reviews with full context."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

# Activity threshold: no merged PR in this many days = "low activity"
LOW_ACTIVITY_DAYS = 30


def get_merged_pr_count_and_last_time(
    storage: Any,
    github_user: str,
    period_start: datetime,
    period_end: datetime,
) -> tuple[int, datetime | None]:
    """Return (merged_pr_count, last_merged_at) for the user in the period."""
    events = storage.list_contributions(period_start)
    count = 0
    last_at: datetime | None = None
    for event in events:
        if (
            event.event_type == "pr_merged"
            and event.github_user == github_user
            and period_start <= event.created_at <= period_end
        ):
            count += 1
            if last_at is None or event.created_at > last_at:
                last_at = event.created_at
    return (count, last_at)


def compute_eligibility(
    eligible_roles_config: list[str],
    contributor_roles: list[str],
    merged_count: int,
    last_merged_at: datetime | None,
    now: datetime,
) -> tuple[str, str]:
    """Compute eligibility verdict and reason for mentor display.

    Returns (verdict, reason) where verdict is one of:
    - "eligible"
    - "eligible_low_activity"
    - "not_eligible"
    """
    has_required_role = (
        not eligible_roles_config
        or any(r in eligible_roles_config for r in contributor_roles)
    )
    if not has_required_role:
        return (
            "not_eligible",
            "Contributor does not have a required role for assignment.",
        )

    # Low activity: no merged PR in last LOW_ACTIVITY_DAYS
    low_activity = False
    if last_merged_at is None:
        low_activity = True
    else:
        cutoff = now - timedelta(days=LOW_ACTIVITY_DAYS)
        if last_merged_at < cutoff and merged_count == 0:
            low_activity = True
        elif last_merged_at < cutoff:
            low_activity = True

    if low_activity and merged_count == 0:
        return (
            "eligible_low_activity",
            "No merged PRs in the period; consider for good-first-issue.",
        )
    if low_activity:
        return (
            "eligible_low_activity",
            "No recent merged PRs; last activity was a while ago.",
        )
    return ("eligible", "Meets role and activity criteria.")


def format_activity_signal(
    merged_count: int,
    last_merged_at: datetime | None,
    now: datetime,
) -> str:
    """Return 'Active' or 'Low activity' for mentor embed."""
    if merged_count == 0:
        return "Low activity"
    if last_merged_at is None:
        return "Low activity"
    cutoff = now - timedelta(days=LOW_ACTIVITY_DAYS)
    return "Active" if last_merged_at >= cutoff else "Low activity"


def _parse_created_at(value: Any) -> datetime | None:
    """Parse created_at from request dict (ISO string or None)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        s = str(value).replace("Z", "+00:00")
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def group_pending_requests_by_repo(
    pending_requests: list[dict],
) -> list[dict]:
    """Group pending requests by (owner, repo). Return list of repo entries sorted by count desc, then repo name.

    Each entry: {"owner": str, "repo": str, "count": int, "oldest_created_at": datetime | None}.
    """
    groups: dict[tuple[str, str], list[dict]] = {}
    for req in pending_requests:
        owner = req.get("owner", "")
        repo = req.get("repo", "")
        if not owner or not repo:
            continue
        key = (owner, repo)
        if key not in groups:
            groups[key] = []
        groups[key].append(req)

    now = datetime.now(timezone.utc)
    result = []
    for (owner, repo), reqs in groups.items():
        oldest: datetime | None = None
        for req in reqs:
            t = _parse_created_at(req.get("created_at"))
            if t is not None and (oldest is None or t < oldest):
                oldest = t
        result.append({
            "owner": owner,
            "repo": repo,
            "count": len(reqs),
            "oldest_created_at": oldest,
        })
    # Sort: count descending, then owner/repo ascending (stable)
    result.sort(key=lambda r: (-r["count"], f"{r['owner']}/{r['repo']}"))
    return result


def build_repo_selection_embed(
    repo_list: list[dict],
    now: datetime,
) -> dict[str, Any]:
    """Build Discord embed for repository selection (Step 1 of /issue-requests)."""
    from ghdcbot.engine.pr_context import format_relative_time

    lines = []
    for r in repo_list:
        repo_name = f"{r['owner']}/{r['repo']}"
        count = r["count"]
        oldest = r.get("oldest_created_at")
        age_str = format_relative_time(oldest, now) if oldest else "—"
        if count == 1:
            lines.append(f"• **{repo_name}** — 1 request (oldest {age_str})")
        else:
            lines.append(f"• **{repo_name}** — {count} requests (oldest {age_str})")
    body = "\n".join(lines) if lines else "No pending requests."

    return {
        "title": "📦 Repositories with Pending Requests",
        "description": body,
        "color": 0x5865F2,
        "timestamp": now.isoformat(),
    }


def build_mentor_request_embed(
    request: dict,
    issue: dict,
    contributor_discord_mention: str,
    contributor_roles: list[str],
    merged_count: int,
    last_merged_at: datetime | None,
    eligibility_verdict: str,
    eligibility_reason: str,
    eligible_roles_config: list[str],
    period_days: int,
    now: datetime,
) -> dict[str, Any]:
    """Build Discord embed for one pending issue request (mentor review)."""
    from ghdcbot.engine.pr_context import format_relative_time

    owner = request["owner"]
    repo = request["repo"]
    issue_number = request["issue_number"]
    issue_url = request["issue_url"]
    created_at_str = request.get("created_at", "")
    github_user = request["github_user"]

    # Issue context
    issue_title = (issue.get("title") or "Untitled")[:200]
    labels = issue.get("labels", [])
    label_names = [lb.get("name", "") for lb in labels if isinstance(lb, dict)]
    labels_str = ", ".join(label_names[:10]) if label_names else "None"
    created_at_issue = None
    if issue.get("created_at"):
        try:
            created_at_issue = datetime.fromisoformat(issue["created_at"].replace("Z", "+00:00"))
        except (ValueError, TypeError):
            pass
    issue_age = format_relative_time(created_at_issue, now) if created_at_issue else "Unknown"
    assignees = issue.get("assignees", [])
    assignee_names = [a.get("login", "?") for a in assignees] if assignees else []
    current_assignees_str = ", ".join(assignee_names) if assignee_names else "None"

    # Contributor context
    roles_str = ", ".join(contributor_roles) if contributor_roles else "None"
    last_merged_str = format_relative_time(last_merged_at, now) if last_merged_at else "Never"
    activity_signal = format_activity_signal(merged_count, last_merged_at, now)
    required_roles_str = ", ".join(eligible_roles_config) if eligible_roles_config else "Any verified user"

    # Eligibility display
    if eligibility_verdict == "eligible":
        verdict_display = "✅ Eligible"
    elif eligibility_verdict == "eligible_low_activity":
        verdict_display = "⚠️ Eligible but low activity"
    else:
        verdict_display = "❌ Not eligible"

    embed = {
        "title": "🧾 Issue assignment request",
        "url": issue_url,
        "color": 0x5865F2,
        "fields": [
            {"name": "Repository", "value": f"{owner}/{repo}", "inline": True},
            {"name": "Issue", "value": f"#{issue_number}: {issue_title}", "inline": False},
            {"name": "Labels", "value": labels_str[:1024], "inline": False},
            {"name": "Issue age", "value": f"Opened {issue_age}", "inline": True},
            {"name": "Current assignees", "value": current_assignees_str, "inline": True},
            {"name": "Contributor", "value": f"{contributor_discord_mention} ({github_user})", "inline": False},
            {"name": "Identity", "value": "Verified ✅", "inline": True},
            {"name": "Discord roles", "value": roles_str[:1024], "inline": True},
            {"name": f"Merged PRs (last {period_days} days)", "value": str(merged_count), "inline": True},
            {"name": "Last merged PR", "value": last_merged_str, "inline": True},
            {"name": "Activity", "value": activity_signal, "inline": True},
            {"name": "Required roles for assignment", "value": required_roles_str, "inline": False},
            {"name": "Eligibility", "value": f"{verdict_display}\n{eligibility_reason}", "inline": False},
        ],
        "timestamp": created_at_str or now.isoformat(),
    }
    return embed
