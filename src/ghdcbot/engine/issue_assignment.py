"""Issue assignment workflow: mentor-safe assignment from Discord with confirmation UI."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


def parse_issue_url(url: str) -> tuple[str, str, int] | None:
    """Parse GitHub issue URL into (owner, repo, issue_number).
    
    Supports formats:
    - https://github.com/owner/repo/issues/123
    - https://github.com/owner/repo/issues/123/
    - github.com/owner/repo/issues/123
    
    Returns None if URL is invalid.
    """
    # Match GitHub issue URLs
    pattern = r"(?:https?://)?(?:www\.)?github\.com/([^/]+)/([^/]+)/issues/(\d+)"
    match = re.search(pattern, url)
    if not match:
        return None
    owner, repo, issue_num_str = match.groups()
    try:
        issue_number = int(issue_num_str)
        return (owner, repo, issue_number)
    except ValueError:
        return None


def fetch_issue_context(
    github_adapter: Any,
    owner: str,
    repo: str,
    issue_number: int,
) -> dict | None:
    """Fetch issue metadata from GitHub API.
    
    Returns issue dict or None if not found/accessible.
    """
    issue = github_adapter.get_issue(owner, repo, issue_number)
    return issue


def resolve_discord_to_github(
    storage: Any,
    discord_user_id: str,
) -> str | None:
    """Resolve Discord user ID to verified GitHub username.
    
    Returns GitHub username if verified, None otherwise.
    """
    verified = getattr(storage, "list_verified_identity_mappings", None)
    if not callable(verified):
        return None
    
    for mapping in verified():
        if mapping.discord_user_id == discord_user_id:
            return mapping.github_user
    
    return None


def resolve_github_to_discord(
    storage: Any,
    github_user: str,
) -> str | None:
    """Resolve GitHub username to Discord user ID.
    
    Returns Discord user ID if verified, None otherwise.
    """
    verified = getattr(storage, "list_verified_identity_mappings", None)
    if not callable(verified):
        return None
    
    for mapping in verified():
        if mapping.github_user == github_user:
            return mapping.discord_user_id
    
    return None


def get_assignee_activity(
    github_adapter: Any,
    owner: str,
    repo: str,
    github_user: str,
) -> tuple[datetime | None, str]:
    """Get last commit time for a GitHub user in a repository.
    
    Returns (last_commit_time, relative_time_string).
    """
    # Try to get last commit from recent PRs/issues
    # For now, return None - this can be enhanced later
    return (None, "Unknown")


def build_assignment_confirmation_embed(
    issue: dict,
    owner: str,
    repo: str,
    current_assignee_github: str | None,
    current_assignee_discord: str | None,
    new_assignee_github: str,
    new_assignee_discord: str | None,
    assignee_activity: str,
    now: datetime,
) -> dict[str, Any]:
    """Build Discord embed for assignment confirmation.
    
    Args:
        issue: Issue data from GitHub API
        owner: Repository owner
        repo: Repository name
        current_assignee_github: Current assignee GitHub username (if any)
        current_assignee_discord: Current assignee Discord user ID (if any)
        new_assignee_github: New assignee GitHub username
        new_assignee_discord: New assignee Discord user ID (if any)
        assignee_activity: Activity string for current assignee
        now: Current timestamp
    
    Returns:
        Discord embed dict ready for discord.Embed.from_dict()
    """
    # Parse timestamps
    created_at_str = issue.get("created_at", "")
    updated_at_str = issue.get("updated_at", "")
    
    created_at = None
    updated_at = None
    
    if created_at_str:
        try:
            created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            pass
    
    if updated_at_str:
        try:
            updated_at = datetime.fromisoformat(updated_at_str.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            pass
    
    # Format relative times
    from ghdcbot.engine.pr_context import format_relative_time
    
    created_str = format_relative_time(created_at, now) if created_at else "Unknown"
    updated_str = format_relative_time(updated_at, now) if updated_at else "Unknown"
    
    # Current assignment display
    current_assignment_str = "None"
    if current_assignee_github:
        if current_assignee_discord:
            current_assignment_str = f"<@{current_assignee_discord}> ({current_assignee_github})"
        else:
            current_assignment_str = current_assignee_github
        if assignee_activity != "Unknown":
            current_assignment_str += f"\nLast activity: {assignee_activity}"
    
    # New assignment display
    if new_assignee_discord:
        new_assignment_str = f"<@{new_assignee_discord}> ({new_assignee_github})"
    else:
        new_assignment_str = new_assignee_github
    
    # Status
    state = issue.get("state", "unknown").title()
    
    # Build embed
    embed_dict = {
        "title": "📋 Issue Assignment Confirmation",
        "url": issue.get("html_url", ""),
        "color": 0xF59E0B,  # Amber/orange for confirmation
        "fields": [
            {
                "name": "Repository",
                "value": f"{owner}/{repo}",
                "inline": True,
            },
            {
                "name": "Issue",
                "value": f"#{issue.get('number', '?')}: {issue.get('title', 'Untitled')[:100]}",
                "inline": False,
            },
            {
                "name": "Status",
                "value": state,
                "inline": True,
            },
            {
                "name": "Created",
                "value": created_str,
                "inline": True,
            },
            {
                "name": "Last Updated",
                "value": updated_str,
                "inline": True,
            },
            {
                "name": "Current Assignment",
                "value": current_assignment_str,
                "inline": False,
            },
            {
                "name": "Proposed Assignment",
                "value": new_assignment_str,
                "inline": False,
            },
        ],
        "timestamp": created_at.isoformat() if created_at else None,
    }
    
    return embed_dict
