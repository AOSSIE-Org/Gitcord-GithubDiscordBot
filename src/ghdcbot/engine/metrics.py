"""Read-only contribution metrics for mentor visibility.

All functions use existing storage and contribution data. No schema or config changes.
Metrics are informational only; they do not drive role or assignment logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ghdcbot.core.interfaces import Storage


# Documented, stable, non-competitive formula for issue engagement (informational only).
# 1 point per issue opened, 0.5 per comment. Not used for scoring or roles.
ISSUE_ENGAGEMENT_ISSUE_WEIGHT = 1.0
ISSUE_ENGAGEMENT_COMMENT_WEIGHT = 0.5


@dataclass
class UserMetrics:
    """Per-user contribution metrics for a time window. Read-only, informational."""

    github_user: str
    prs_opened: int  # count of pr_opened events
    prs_merged: int  # count of pr_merged events
    reviews_submitted: int  # count of pr_reviewed events
    issues_opened: int
    comments: int
    issue_engagement: float  # issues_opened * 1.0 + comments * 0.5 (documented formula)
    total_score: int  # from config weights if provided, else 0
    period_start: datetime
    period_end: datetime


def get_contribution_metrics(
    storage: Storage,
    period_start: datetime,
    period_end: datetime,
    weights: dict[str, int] | None = None,
) -> list[UserMetrics]:
    """Compute read-only metrics per user for the given window.

    Uses storage.list_contributions; filters to [period_start, period_end] and
    aggregates in memory. No schema or scoring changes.

    Weights are optional (e.g. config.scoring.weights). If provided, total_score
    is computed using them; otherwise 0.
    """
    weights = weights or {}
    since = period_start
    events = storage.list_contributions(since)
    # Filter to window (list_contributions returns all since `since`)
    in_window = [
        e
        for e in events
        if period_start <= e.created_at <= period_end
    ]
    # Aggregate per user
    buckets: dict[str, dict[str, int | float]] = {}
    for e in in_window:
        b = buckets.setdefault(
            e.github_user,
            {
                "prs_opened": 0,
                "prs_merged": 0,
                "reviews_submitted": 0,
                "issues_opened": 0,
                "comments": 0,
                "total_score": 0,
            },
        )
        if e.event_type == "pr_opened":
            b["prs_opened"] += 1
        elif e.event_type == "pr_merged":
            b["prs_merged"] += 1
        elif e.event_type == "pr_reviewed":
            b["reviews_submitted"] += 1
        elif e.event_type == "issue_opened":
            b["issues_opened"] += 1
        elif e.event_type == "comment":
            b["comments"] += 1
        b["total_score"] += weights.get(e.event_type, 0)

    result = []
    for user, b in sorted(buckets.items(), key=lambda x: x[0]):
        issues = int(b["issues_opened"])
        comments = int(b["comments"])
        issue_engagement = (
            issues * ISSUE_ENGAGEMENT_ISSUE_WEIGHT
            + comments * ISSUE_ENGAGEMENT_COMMENT_WEIGHT
        )
        result.append(
            UserMetrics(
                github_user=user,
                prs_opened=int(b["prs_opened"]),
                prs_merged=int(b["prs_merged"]),
                reviews_submitted=int(b["reviews_submitted"]),
                issues_opened=issues,
                comments=int(b["comments"]),
                issue_engagement=issue_engagement,
                total_score=int(b["total_score"]),
                period_start=period_start,
                period_end=period_end,
            )
        )
    return result


def rank_by_activity(metrics: list[UserMetrics]) -> list[UserMetrics]:
    """Return metrics sorted by total_score descending (top contributors by activity).
    Informational only; no gamification. Same order as audit report.
    """
    return sorted(metrics, key=lambda m: (-m.total_score, m.github_user))


def get_rank_for_user(ranked: list[UserMetrics], github_user: str) -> int | None:
    """Return 1-based rank for the user, or None if not in list."""
    for i, m in enumerate(ranked, start=1):
        if m.github_user == github_user:
            return i
    return None


def format_metrics_summary(metrics: UserMetrics | None) -> str:
    """Format a single user's metrics for display (e.g. /summary)."""
    if metrics is None:
        return "No activity in this period."
    parts = [
        f"PRs opened: {metrics.prs_opened}, merged: {metrics.prs_merged}",
        f"Reviews submitted: {metrics.reviews_submitted}",
        f"Issues opened: {metrics.issues_opened}, comments: {metrics.comments}",
        f"Issue engagement (informational): {metrics.issue_engagement:.1f}",
    ]
    return "\n".join(parts)


def metrics_for_windows(
    storage: Storage,
    period_days: int,
    weights: dict[str, int] | None,
    window_days_list: list[int] | None = None,
) -> tuple[list[UserMetrics], dict[int, list[UserMetrics]]]:
    """Get metrics for the main period and optionally for extra windows (e.g. 7, 30 days).

    Returns (metrics_main_period, {window_days: metrics_list}).
    window_days_list default is [7, 30] for /summary-style output.
    """
    now = datetime.now(timezone.utc)
    main_end = now
    main_start = now - timedelta(days=period_days)
    main = get_contribution_metrics(storage, main_start, main_end, weights)
    by_window: dict[int, list[UserMetrics]] = {}
    for w in window_days_list or [7, 30]:
        end = now
        start = now - timedelta(days=w)
        by_window[w] = get_contribution_metrics(storage, start, end, weights)
    return main, by_window
