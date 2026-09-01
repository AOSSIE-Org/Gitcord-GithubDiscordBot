"""Issue creation workflow: pure business logic for creating GitHub issues from Discord."""

from __future__ import annotations

import re
from typing import Any


def validate_issue_params(title: str, repo: str, org: str) -> tuple[bool, str]:
    """Validate parameters before creating an issue.
    
    Args:
        title: Issue title
        repo: Repository name
        org: Organization name
        
    Returns:
        Tuple of (is_valid, error_message). If valid, error_message is empty.
    """
    title = (title or "").strip()
    repo = (repo or "").strip()
    
    if not title:
        return False, "Issue title cannot be empty."
    
    if len(title) > 256:
        return False, f"Issue title is too long ({len(title)}/256 characters)."
    
    if not repo:
        return False, "Repository name cannot be empty."
        
    # Basic repo name validation (alphanumeric, hyphens, underscores, periods)
    if not re.match(r"^[a-zA-Z0-9_\-\.]+$", repo):
        return False, "Invalid repository name format."
        
    return True, ""


def format_issue_creation_audit_context(
    owner: str,
    repo: str,
    issue_number: int,
    title: str,
    creator_github: str,
    creator_discord_id: str,
) -> dict[str, Any]:
    """Format context dict for audit logging when an issue is created.
    
    Args:
        owner: Repository owner
        repo: Repository name
        issue_number: GitHub issue number
        title: Issue title
        creator_github: GitHub username of the creator
        creator_discord_id: Discord user ID of the creator
        
    Returns:
        Dict suitable for the context field of an audit event.
    """
    return {
        "owner": owner,
        "repo": repo,
        "issue_number": issue_number,
        "title": title[:100],  # Truncate title for audit logs
        "creator_github": creator_github,
        "creator_discord_id": creator_discord_id,
        "action": "issue_created_from_discord",
    }


def build_issue_created_embed(
    issue_data: dict[str, Any],
    owner: str,
    repo: str,
    creator_github: str,
    creator_discord_id: str,
) -> dict[str, Any]:
    """Build a Discord embed dict for a successfully created issue.
    
    Args:
        issue_data: Response dict from GitHub API
        owner: Repository owner
        repo: Repository name
        creator_github: GitHub username of the creator
        creator_discord_id: Discord user ID of the creator
        
    Returns:
        Discord embed dict ready for discord.Embed.from_dict()
    """
    issue_number = issue_data.get("number", "?")
    issue_title = issue_data.get("title", "Untitled")
    issue_url = issue_data.get("html_url", f"https://github.com/{owner}/{repo}/issues/{issue_number}")
    state = issue_data.get("state", "open").title()
    
    # Format labels if present
    labels_str = "None"
    labels_list = issue_data.get("labels", [])
    if isinstance(labels_list, list) and labels_list:
        label_names = []
        for label in labels_list:
            if isinstance(label, dict) and "name" in label:
                label_names.append(f"`{label['name']}`")
            elif isinstance(label, str):
                label_names.append(f"`{label}`")
        if label_names:
            labels_str = ", ".join(label_names)
    
    embed_dict = {
        "title": "✅ Issue Created Successfully",
        "url": issue_url,
        "color": 0x10B981,  # Emerald green for success
        "fields": [
            {
                "name": "Repository",
                "value": f"[{owner}/{repo}](https://github.com/{owner}/{repo})",
                "inline": True,
            },
            {
                "name": "Issue",
                "value": f"[#{issue_number}: {issue_title[:100]}]({issue_url})",
                "inline": False,
            },
            {
                "name": "Creator",
                "value": f"<@{creator_discord_id}> ({creator_github})",
                "inline": True,
            },
            {
                "name": "Status",
                "value": state,
                "inline": True,
            },
        ],
    }
    
    # Add labels field if there are labels
    if labels_str != "None":
        embed_dict["fields"].append({
            "name": "Labels",
            "value": labels_str,
            "inline": False,
        })
        
    return embed_dict
