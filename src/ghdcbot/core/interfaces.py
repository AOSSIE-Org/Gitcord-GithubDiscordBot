from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Protocol

from ghdcbot.core.models import (
    AssignmentPlan,
    ContributionEvent,
    ContributionSummary,
    ReviewPlan,
    Score,
)


class GitHubReader(Protocol):
    def list_contributions(self, since: datetime) -> Iterable[ContributionEvent]:
        """Yield contributions since the given timestamp."""

    def list_open_issues(self) -> Iterable[dict]:
        """Yield open issues with metadata needed for assignment."""

    def list_open_pull_requests(self) -> Iterable[dict]:
        """Yield open PRs with metadata needed for review assignment."""

    def list_org_repo_names(self) -> list[str]:
        """Return repository names available for slash-command autocomplete."""

    def list_repo_open_issues(
        self,
        owner: str,
        repo: str,
        limit: int = 100,
        per_page: int = 100,
    ) -> list[dict] | None:
        """Return open issues for one repository (excluding PRs), or None on error."""

    def get_issue_comments(
        self, owner: str, repo: str, issue_number: int
    ) -> list[dict] | None:
        """Fetch comments for an issue, or None on error."""

    def list_pull_requests_for_author(
        self, github_user: str, *, repo: str | None = None
    ) -> list[dict] | None:
        """List pull requests for an author, or None on error."""


class GitHubWriter(Protocol):
    def assign_issue(self, repo: str, issue_number: int, assignee: str) -> None:
        """Assign a user to a GitHub issue."""

    def unassign_issue(
        self, owner: str, repo: str, issue_number: int, assignee: str
    ) -> bool:
        """Unassign a user from a GitHub issue."""

    def create_issue_comment(
        self, owner: str, repo: str, issue_number: int, body: str
    ) -> bool:
        """Post a comment on a GitHub issue."""

    def request_review(self, repo: str, pr_number: int, reviewer: str) -> None:
        """Request a review from a GitHub user."""


class DiscordReader(Protocol):
    def list_member_roles(self) -> dict[str, Sequence[str]] | None:
        """Return mapping of discord user ID to role names.

        Returns None when the Discord API is unavailable (rate limit, network
        error, or permission denied).  Callers must check for None before
        using the result for mutation decisions.
        """

    def list_roles_for_member(self, discord_user_id: str) -> Sequence[str]:
        """Return role names for a single guild member (cheap, one API call)."""


class DiscordWriter(Protocol):
    def add_role(self, discord_user_id: str, role_name: str) -> None:
        """Assign a role to a Discord user."""

    def remove_role(self, discord_user_id: str, role_name: str) -> None:
        """Remove a role from a Discord user."""


class Storage(Protocol):
    def init_schema(self) -> None:
        """Initialize database schema if needed."""

    def record_contributions(self, events: Iterable[ContributionEvent]) -> int:
        """Persist contribution events and return count stored."""

    def list_contributions(self, since: datetime) -> Sequence[ContributionEvent]:
        """List contributions from storage since time."""

    def list_contribution_summaries(
        self,
        period_start: datetime,
        period_end: datetime,
        weights: dict[str, int],
    ) -> Sequence[ContributionSummary]:
        """Aggregate contribution counts and scores for the period."""

    def upsert_scores(self, scores: Sequence[Score]) -> None:
        """Persist scores for users."""

    def get_scores(self) -> Sequence[Score]:
        """Load most recent scores."""

    def get_cursor(self, source: str) -> datetime | None:
        """Return last sync cursor for a source."""

    def set_cursor(self, source: str, cursor: datetime) -> None:
        """Persist last sync cursor for a source."""


class ScoreStrategy(Protocol):
    def compute_scores(
        self, contributions: Sequence[ContributionEvent], period_end: datetime
    ) -> Sequence[Score]:
        """Compute scores from contributions."""


class AssignmentStrategy(Protocol):
    def plan_issue_assignments(
        self, issues: Iterable[dict], scores: Sequence[Score]
    ) -> Sequence[AssignmentPlan]:
        """Plan issue assignments based on scores and roles."""

    def plan_review_requests(
        self, pull_requests: Iterable[dict], scores: Sequence[Score]
    ) -> Sequence[ReviewPlan]:
        """Plan review requests based on scores and roles."""
