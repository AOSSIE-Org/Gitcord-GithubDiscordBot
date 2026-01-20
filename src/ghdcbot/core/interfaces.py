from __future__ import annotations

from datetime import datetime
from typing import Iterable, Protocol, Sequence

from ghdcbot.core.models import AssignmentPlan, ContributionEvent, ReviewPlan, Score


class GitHubReader(Protocol):
    def list_contributions(self, since: datetime) -> Iterable[ContributionEvent]:
        """Yield contributions since the given timestamp."""

    def list_open_issues(self) -> Iterable[dict]:
        """Yield open issues with metadata needed for assignment."""

    def list_open_pull_requests(self) -> Iterable[dict]:
        """Yield open PRs with metadata needed for review assignment."""


class GitHubWriter(Protocol):
    def assign_issue(self, repo: str, issue_number: int, assignee: str) -> None:
        """Assign a user to a GitHub issue."""

    def request_review(self, repo: str, pr_number: int, reviewer: str) -> None:
        """Request a review from a GitHub user."""


class DiscordReader(Protocol):
    def list_member_roles(self) -> dict[str, Sequence[str]]:
        """Return mapping of discord user ID to role names."""


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
