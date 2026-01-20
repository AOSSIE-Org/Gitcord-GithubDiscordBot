from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Sequence

from ghdcbot.core.interfaces import ScoreStrategy
from ghdcbot.core.models import ContributionEvent, Score


class WeightedScoreStrategy(ScoreStrategy):
    def __init__(self, weights: dict[str, int], period_days: int) -> None:
        self._weights = weights
        self._period = timedelta(days=period_days)

    def compute_scores(
        self, contributions: Sequence[ContributionEvent], period_end: datetime
    ) -> Sequence[Score]:
        period_start = period_end - self._period
        totals: dict[str, int] = defaultdict(int)
        for event in contributions:
            if event.created_at < period_start:
                continue
            totals[event.github_user] += self._weights.get(event.event_type, 0)

        return [
            Score(
                github_user=user,
                period_start=period_start,
                period_end=period_end,
                points=points,
            )
            for user, points in totals.items()
        ]
