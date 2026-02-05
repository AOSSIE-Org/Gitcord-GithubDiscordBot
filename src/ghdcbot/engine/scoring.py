from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Sequence

from ghdcbot.core.interfaces import ScoreStrategy
from ghdcbot.core.models import ContributionEvent, Score


class WeightedScoreStrategy(ScoreStrategy):
    def __init__(
        self,
        weights: dict[str, int],
        period_days: int,
        difficulty_weights: dict[str, int] | None = None,
        quality_adjustments: dict[str, dict[str, int]] | None = None,
    ) -> None:
        self._weights = weights
        self._period = timedelta(days=period_days)
        self._difficulty_weights = difficulty_weights or {}
        # Normalize difficulty weights keys to lowercase for case-insensitive matching
        if self._difficulty_weights:
            self._difficulty_weights = {
                k.lower(): v for k, v in self._difficulty_weights.items()
            }
        self._quality_adjustments = quality_adjustments or {}
        self._penalties = self._quality_adjustments.get("penalties", {})
        self._bonuses = self._quality_adjustments.get("bonuses", {})

    def compute_scores(
        self, contributions: Sequence[ContributionEvent], period_end: datetime
    ) -> Sequence[Score]:
        period_start = period_end - self._period
        totals: dict[str, int] = defaultdict(int)
        # Track helpful comments per PR/issue (cap at 5)
        helpful_comment_counts: dict[tuple[str, int], int] = defaultdict(int)  # (user, target) -> count
        # Track PR reviews (only APPROVED reviews get bonus)
        pr_reviews: dict[tuple[str, int], bool] = {}  # (user, pr_number) -> has_approved_review
        # Track penalties applied (once per PR)
        reverted_prs: set[tuple[str, int]] = set()  # (user, pr_number) -> already penalized
        failed_ci_prs: set[tuple[str, int]] = set()  # (user, pr_number) -> already penalized
        
        for event in contributions:
            if event.created_at < period_start or event.created_at > period_end:
                continue
            
            # Apply penalties (once per PR)
            if event.event_type == "pr_reverted" and "reverted_pr" in self._penalties:
                pr_number = event.payload.get("pr_number")
                if pr_number:
                    key = (event.github_user, pr_number)
                    if key not in reverted_prs:
                        totals[event.github_user] += self._penalties["reverted_pr"]
                        reverted_prs.add(key)
                continue
            if event.event_type == "pr_merged_with_failed_ci" and "failed_ci_merge" in self._penalties:
                pr_number = event.payload.get("pr_number")
                if pr_number:
                    key = (event.github_user, pr_number)
                    if key not in failed_ci_prs:
                        totals[event.github_user] += self._penalties["failed_ci_merge"]
                        failed_ci_prs.add(key)
                continue
            
            # Apply bonuses (additive to base score)
            bonus_applied = False
            if event.event_type == "pr_reviewed" and "pr_review" in self._bonuses:
                state = event.payload.get("state", "").upper()
                if state == "APPROVED":
                    pr_number = event.payload.get("pr_number")
                    if pr_number:
                        key = (event.github_user, pr_number)
                        if key not in pr_reviews:
                            totals[event.github_user] += self._bonuses["pr_review"]
                            pr_reviews[key] = True
                            bonus_applied = True
            
            if event.event_type == "helpful_comment" and "helpful_comment" in self._bonuses:
                target_number = event.payload.get("issue_number") or event.payload.get("pr_number")
                if target_number:
                    key = (event.github_user, target_number)
                    if helpful_comment_counts[key] < 5:
                        totals[event.github_user] += self._bonuses["helpful_comment"]
                        helpful_comment_counts[key] += 1
                        bonus_applied = True
            
            # If bonus was applied, still continue to base scoring below
            if bonus_applied:
                pass  # Fall through to base scoring
            
            # Base scoring: merge-only to prevent spam and align incentives with mentor-approved contributions.
            # Scores are intentionally computed only from merged PRs to prevent activity spam and gaming.
            # All other events (PR opens, comments, reviews, issues) remain ingested and visible in reports
            # but do not contribute to scores.
            if event.event_type == "pr_merged":
                # Difficulty-aware scoring (if configured)
                if (
                    self._difficulty_weights
                    and event.payload.get("difficulty_labels")
                ):
                    difficulty_labels = event.payload.get("difficulty_labels", [])
                    # Find matching difficulty labels (case-insensitive)
                    matching_weights = []
                    for label in difficulty_labels:
                        label_lower = label.lower() if isinstance(label, str) else str(label).lower()
                        if label_lower in self._difficulty_weights:
                            matching_weights.append(self._difficulty_weights[label_lower])
                    if matching_weights:
                        # Use max weight if multiple labels exist
                        score = max(matching_weights)
                        totals[event.github_user] += score
                        continue
                # Fallback to weight-based scoring for merged PRs
                totals[event.github_user] += self._weights.get("pr_merged", 0)
            # All other event types are ignored for scoring (but remain in audit/reports)

        return [
            Score(
                github_user=user,
                period_start=period_start,
                period_end=period_end,
                points=points,
            )
            for user, points in totals.items()
        ]
