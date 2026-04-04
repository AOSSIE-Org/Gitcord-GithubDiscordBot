from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Literal, Protocol, TypedDict

from ghdcbot.core.models import (
    AssignmentPlan,
    ContributionEvent,
    ContributionSummary,
    IdentityMapping,
    ReviewPlan,
    Score,
)


class _IdentityLinkRequired(TypedDict):
    discord_user_id: str
    github_user: str
    github_user_normalized: str
    verified: int
    created_at: str


class IdentityLinkDict(_IdentityLinkRequired, total=False):
    verification_code: str | None
    expires_at: str | None
    verified_at: str | None
    unlinked_at: str | None


class _UnlinkResultRequired(TypedDict):
    discord_user_id: str
    github_user: str
    verified_at: str
    unlinked_at: str


class UnlinkResultDict(_UnlinkResultRequired, total=False):
    cooldown_until: str | None
    cooldown_hours: int


class IdentityStatusDict(TypedDict):
    github_user: str | None
    status: Literal["verified", "verified_stale", "pending", "not_linked"]
    verified_at: str | None
    is_stale: bool


class IssueRequestDict(TypedDict):
    request_id: str
    discord_user_id: str
    github_user: str
    owner: str
    repo: str
    issue_number: int
    issue_url: str
    created_at: str
    status: Literal["pending", "approved", "rejected", "cancelled"]


class AuditEventContext(TypedDict, total=False):
    org: str
    repo: str
    snapshot_dir: str
    run_id: str
    files_written: int
    timestamp: str


class _AuditEventRequired(TypedDict):
    event_type: str


class AuditEventDict(_AuditEventRequired, total=False):
    timestamp: str
    context: AuditEventContext


class NotificationRecordDict(TypedDict):
    dedupe_key: str
    event_type: str
    github_user: str
    discord_user_id: str
    repo: str
    target: str | None
    channel_id: str | None
    sent_at: str


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

    def list_contribution_summaries(
        self,
        period_start: datetime,
        period_end: datetime,
        weights: dict[str, int],
        difficulty_weights: dict[str, int] | None = None,
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

    # Identity linking

    def create_identity_claim(
        self,
        discord_user_id: str,
        github_user: str,
        verification_code: str,
        expires_at: datetime,
        *,
        max_age_days: int | None = None,
    ) -> None:
        """Create or refresh a pending identity claim for (discord_user_id, github_user)."""

    def get_identity_link(
        self, discord_user_id: str, github_user: str
    ) -> IdentityLinkDict | None:
        """Return identity link row for (discord_user_id, github_user), or None."""

    def mark_identity_verified(self, discord_user_id: str, github_user: str) -> None:
        """Mark an identity claim as verified."""

    def unlink_identity(
        self, discord_user_id: str, cooldown_hours: int
    ) -> UnlinkResultDict | None:
        """Unlink the verified identity for a Discord user. Returns unlink info or None."""

    def list_verified_identity_mappings(self) -> list[IdentityMapping]:
        """Return all verified identity mappings."""

    def get_identity_links_for_discord_user(
        self, discord_user_id: str
    ) -> list[IdentityLinkDict]:
        """Return all identity link rows for a Discord user (verified and pending)."""

    def get_identity_status(
        self, discord_user_id: str, max_age_days: int | None = None
    ) -> IdentityStatusDict:
        """Return current identity status dict for a Discord user."""

    # Issue requests

    def insert_issue_request(
        self,
        request_id: str,
        discord_user_id: str,
        github_user: str,
        owner: str,
        repo: str,
        issue_number: int,
        issue_url: str,
    ) -> None:
        """Store a new issue assignment request with status pending."""

    def list_pending_issue_requests(self) -> list[IssueRequestDict]:
        """Return all pending issue requests ordered by created_at ascending."""

    def get_issue_request(self, request_id: str) -> IssueRequestDict | None:
        """Return a single issue request by request_id, or None."""

    def update_issue_request_status(
        self,
        request_id: str,
        status: Literal["pending", "approved", "rejected", "cancelled"],
    ) -> None:
        """Update an issue request status (pending, approved, rejected, cancelled)."""

    # Audit log

    def append_audit_event(self, event: AuditEventDict) -> None:
        """Append an audit event (append-only)."""

    def list_audit_events(self) -> list[AuditEventDict]:
        """Return all audit events."""

    # Notifications

    def was_notification_sent(self, dedupe_key: str) -> bool:
        """Check if a notification was already sent (deduplication)."""

    def mark_notification_sent(
        self,
        dedupe_key: str,
        event: ContributionEvent,
        discord_user_id: str,
        channel_id: str | None,
        target_github_user: str | None = None,
    ) -> None:
        """Record that a notification was sent (deduplication tracking)."""

    def list_recent_notifications(self, limit: int = 1000) -> list[NotificationRecordDict]:
        """Return recent sent notifications ordered by sent_at descending."""


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
