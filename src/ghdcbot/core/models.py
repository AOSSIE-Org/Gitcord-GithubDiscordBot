from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class ContributionEvent:
    github_user: str
    event_type: str
    repo: str
    created_at: datetime
    payload: dict[str, Any]


@dataclass(frozen=True)
class Score:
    github_user: str
    period_start: datetime
    period_end: datetime
    points: int


@dataclass(frozen=True)
class RoleMapping:
    discord_role: str
    min_score: int


@dataclass(frozen=True)
class AssignmentPlan:
    issue_number: int
    repo: str
    assignee: str


@dataclass(frozen=True)
class ReviewPlan:
    pr_number: int
    repo: str
    reviewer: str


@dataclass(frozen=True)
class DiscordRolePlan:
    discord_user_id: str
    role: str
    action: str  # "add" | "remove"
    reason: str
    source: dict[str, Any]


@dataclass(frozen=True)
class GitHubAssignmentPlan:
    repo: str
    target_number: int
    target_type: str  # "issue" | "pull_request"
    assignee: str
    action: str  # "assign" | "request_review"
    reason: str
    source: dict[str, Any]
