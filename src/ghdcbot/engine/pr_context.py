"""PR context preview: fetch and format PR metadata for Discord embeds."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


def parse_pr_url(url: str) -> tuple[str, str, int] | None:
    """Parse GitHub PR URL into (owner, repo, pr_number).
    
    Supports formats:
    - https://github.com/owner/repo/pull/123
    - https://github.com/owner/repo/pull/123/
    - github.com/owner/repo/pull/123
    
    Returns None if URL is invalid.
    """
    # Match GitHub PR URLs
    pattern = r"(?:https?://)?(?:www\.)?github\.com/([^/]+)/([^/]+)/pull/(\d+)"
    match = re.search(pattern, url)
    if not match:
        return None
    owner, repo, pr_num_str = match.groups()
    try:
        pr_number = int(pr_num_str)
        return (owner, repo, pr_number)
    except ValueError:
        return None


def format_relative_time(timestamp: datetime | None, now: datetime) -> str:
    """Format timestamp as relative time (e.g., "2 hours ago", "3 days ago").
    
    Returns human-readable relative time string.
    """
    if not timestamp:
        return "Unknown"
    
    delta = now - timestamp
    total_seconds = int(delta.total_seconds())
    
    # Future timestamps (shouldn't happen, but handle gracefully)
    if total_seconds < 0:
        return "Just now"
    
    # Seconds
    if total_seconds < 60:
        return "Just now"
    
    # Minutes
    minutes = total_seconds // 60
    if minutes < 60:
        return f"{minutes} min{'s' if minutes != 1 else ''} ago"
    
    # Hours
    hours = total_seconds // 3600
    if hours < 24:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    
    # Days
    days = total_seconds // 86400
    if days < 7:
        return f"{days} day{'s' if days != 1 else ''} ago"
    
    # Weeks
    weeks = days // 7
    if weeks < 5:
        return f"{weeks} week{'s' if weeks != 1 else ''} ago"
    
    # Months (approximate, using 30 days)
    months = days // 30
    if months < 12 and months > 0:
        return f"{months} month{'s' if months != 1 else ''} ago"
    
    # Years
    years = days // 365
    if years < 1:
        # Edge case: 28-30 days or 360-364 days
        return f"{days} day{'s' if days != 1 else ''} ago"
    return f"{years} year{'s' if years != 1 else ''} ago"


def format_idle_duration(last_commit_time: datetime | None, now: datetime) -> str:
    """Format idle duration since last commit.
    
    Returns human-readable string like "Idle for 3 days" or "Active" if < 1 hour.
    """
    if not last_commit_time:
        return "Unknown"
    
    delta = now - last_commit_time
    total_seconds = int(delta.total_seconds())
    
    if total_seconds < 3600:  # < 1 hour
        return "Active"
    
    days = total_seconds // 86400
    hours = (total_seconds % 86400) // 3600
    
    if days > 0:
        return f"Idle for {days} day{'s' if days != 1 else ''}"
    else:
        return f"Idle for {hours} hour{'s' if hours != 1 else ''}"


def determine_mentor_signal(
    pr: dict,
    reviews: list[dict],
    ci_status: str,
    mergeable: bool | None,
) -> str:
    """Determine who needs to act next based on PR state.
    
    Returns one of:
    - "Waiting on contributor"
    - "Waiting on reviewer"
    - "Ready to merge"
    - "Blocked by CI"
    """
    state = pr.get("state", "").lower()
    draft = pr.get("draft", False)
    merged = pr.get("merged", False)
    
    if state != "open" or draft or merged:
        if merged:
            return "Merged"
        elif state == "closed":
            return "Closed"
        elif draft:
            return "Draft"
    
    # Check CI status
    if ci_status == "failing":
        return "Blocked by CI"
    
    # Check review state
    approved_count = sum(1 for r in reviews if r.get("state", "").upper() == "APPROVED")
    changes_requested_count = sum(
        1 for r in reviews if r.get("state", "").upper() == "CHANGES_REQUESTED"
    )
    
    if changes_requested_count > 0:
        return "Waiting on contributor"
    
    if approved_count > 0 and mergeable is True:
        return "Ready to merge"
    
    if approved_count == 0:
        return "Waiting on reviewer"
    
    return "Waiting on reviewer"


def build_pr_embed(
    pr: dict,
    owner: str,
    repo: str,
    reviews: list[dict],
    ci_status: str,
    last_commit_time: datetime | None,
    discord_mention: str | None = None,
) -> dict[str, Any]:
    """Build Discord embed from PR data.
    
    Args:
        pr: PR data from GitHub API
        owner: Repository owner
        repo: Repository name
        reviews: List of review dicts from GitHub API
        ci_status: CI status string ("success", "failing", "pending", "unknown")
        last_commit_time: Timestamp of last commit (for idle calculation)
        discord_mention: Discord mention string if author is linked (e.g., "<@123456789>")
    
    Returns:
        Discord embed dict ready for discord.Embed.from_dict()
    """
    now = datetime.now(timezone.utc)
    
    # Parse timestamps
    created_at_str = pr.get("created_at", "")
    created_at = None
    if created_at_str:
        try:
            created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            pass
    
    # Author info
    author = pr.get("user", {}).get("login", "Unknown")
    author_display = f"{discord_mention} ({author})" if discord_mention else author
    
    # Status
    state = pr.get("state", "unknown").title()
    draft = pr.get("draft", False)
    if draft:
        state = "Draft"
    mergeable = pr.get("mergeable")
    mergeable_str = "Yes" if mergeable is True else ("No" if mergeable is False else "Unknown")
    
    # Activity (use relative time)
    created_str = format_relative_time(created_at, now) if created_at else "Unknown"
    last_commit_str = format_relative_time(last_commit_time, now) if last_commit_time else "Unknown"
    idle_str = format_idle_duration(last_commit_time, now)
    
    # Checks
    ci_display = ci_status.title() if ci_status != "unknown" else "Unknown"
    
    # Review state
    approved_count = sum(1 for r in reviews if r.get("state", "").upper() == "APPROVED")
    changes_requested_count = sum(
        1 for r in reviews if r.get("state", "").upper() == "CHANGES_REQUESTED"
    )
    review_state = f"{approved_count} approval{'s' if approved_count != 1 else ''}"
    if changes_requested_count > 0:
        review_state += f", {changes_requested_count} change{'s' if changes_requested_count != 1 else ''} requested"
    
    # Assignment
    assignees = pr.get("assignees", [])
    assignee_names = [a.get("login", "?") for a in assignees]
    requested_reviewers = pr.get("requested_reviewers", [])
    reviewer_names = [r.get("login", "?") for r in requested_reviewers]
    
    assignment_parts = []
    if assignee_names:
        assignment_parts.append(f"Assignees: {', '.join(assignee_names)}")
    if reviewer_names:
        assignment_parts.append(f"Reviewers: {', '.join(reviewer_names)}")
    assignment_str = "\n".join(assignment_parts) if assignment_parts else "None"
    
    # Mentor signal
    mentor_signal = determine_mentor_signal(pr, reviews, ci_status, mergeable)
    
    # Build embed
    embed_dict = {
        "title": "🧠 Pull Request Overview",
        "url": pr.get("html_url", ""),
        "color": 0x5865F2,  # Discord blurple
        "fields": [
            {
                "name": "Repository",
                "value": f"{owner}/{repo}",
                "inline": True,
            },
            {
                "name": "Title",
                "value": pr.get("title", "Untitled")[:256],  # Discord limit
                "inline": False,
            },
            {
                "name": "Author",
                "value": author_display,
                "inline": True,
            },
            {
                "name": "Assignment",
                "value": assignment_str[:1024],  # Discord limit
                "inline": False,
            },
            {
                "name": "Status",
                "value": f"{state}\nMergeable: {mergeable_str}",
                "inline": True,
            },
            {
                "name": "Activity",
                "value": f"Created: {created_str}\nLast commit: {last_commit_str}\n⏱ {idle_str}",
                "inline": False,
            },
            {
                "name": "Checks",
                "value": f"CI: {ci_display}\nReviews: {review_state}",
                "inline": True,
            },
            {
                "name": "Mentor Signal",
                "value": mentor_signal,
                "inline": True,
            },
        ],
        "timestamp": created_at.isoformat() if created_at else None,
    }
    
    return embed_dict


def fetch_pr_context(
    github_adapter: Any,
    owner: str,
    repo: str,
    pr_number: int,
) -> tuple[dict | None, list[dict], str, datetime | None]:
    """Fetch PR context data from GitHub API.
    
    Args:
        github_adapter: GitHubRestAdapter instance
        owner: Repository owner
        repo: Repository name
        pr_number: PR number
    
    Returns:
        Tuple of (pr_dict, reviews_list, ci_status, last_commit_time)
        Returns (None, [], "unknown", None) on error.
    """
    # Fetch PR
    pr = github_adapter.get_pull_request(owner, repo, pr_number)
    if not pr:
        return (None, [], "unknown", None)
    
    # Fetch reviews
    reviews = github_adapter.get_pull_request_reviews(owner, repo, pr_number)
    
    # Fetch CI status from check runs
    head_sha = pr.get("head", {}).get("sha")
    ci_status = "unknown"
    last_commit_time = None
    
    if head_sha:
        check_runs = github_adapter.get_pull_request_check_runs(owner, repo, head_sha)
        if check_runs:
            # Determine CI status from check runs
            statuses = [cr.get("status", "").lower() for cr in check_runs]
            conclusions = [
                (cr.get("conclusion") or "").lower() for cr in check_runs
            ]
            
            if any(c == "failure" for c in conclusions):
                ci_status = "failing"
            elif any(c == "success" for c in conclusions) and not any(c == "failure" for c in conclusions):
                ci_status = "success"
            elif any(s == "in_progress" or s == "queued" for s in statuses):
                ci_status = "pending"
            else:
                ci_status = "unknown"
    
    # Get last commit time from head commit (if available in PR data)
    head_commit = pr.get("head", {}).get("commit", {})
    if head_commit:
        commit_date_str = head_commit.get("commit", {}).get("author", {}).get("date")
        if commit_date_str:
            try:
                last_commit_time = datetime.fromisoformat(commit_date_str.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass
    
    # Fallback: use updated_at if commit time not available
    if not last_commit_time:
        updated_at_str = pr.get("updated_at", "")
        if updated_at_str:
            try:
                last_commit_time = datetime.fromisoformat(updated_at_str.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass
    
    return (pr, reviews, ci_status, last_commit_time)
