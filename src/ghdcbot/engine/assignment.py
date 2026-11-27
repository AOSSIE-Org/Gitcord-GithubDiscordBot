from __future__ import annotations

from collections import deque
from typing import Iterable, Sequence

from ghdcbot.core.interfaces import AssignmentStrategy
from ghdcbot.core.models import AssignmentPlan, ReviewPlan, Score


class RoleBasedAssignmentStrategy(AssignmentStrategy):
    def __init__(
        self,
        role_to_github_users: dict[str, list[str]],
        issue_roles: Sequence[str],
        review_roles: Sequence[str],
    ) -> None:
        self._role_to_github = role_to_github_users
        self._issue_roles = issue_roles
        self._review_roles = review_roles

    def _eligible_users(self, roles: Sequence[str]) -> list[str]:
        eligible: list[str] = []
        for role in roles:
            eligible.extend(self._role_to_github.get(role, []))
        # Ensure deterministic ordering
        return sorted(set(eligible))

    def plan_issue_assignments(
        self, issues: Iterable[dict], _scores: Sequence[Score]
    ) -> Sequence[AssignmentPlan]:
        eligible = self._eligible_users(self._issue_roles)
        if not eligible:
            return []
        queue = deque(eligible)
        plans: list[AssignmentPlan] = []
        # Sort by (repo, number) so round-robin assignments are stable across runs,
        # independent of GitHub's default newest-first listing order.
        for issue in sorted(issues, key=_stable_issue_key):
            # Skip issues that already have assignees (don't overwrite existing assignments)
            assignees = issue.get("assignees", [])
            if assignees:
                continue
            assignee = queue[0]
            queue.rotate(-1)
            plans.append(
                AssignmentPlan(
                    issue_number=issue["number"],
                    repo=issue["repo"],
                    assignee=assignee,
                )
            )
        return plans

    def plan_review_requests(
        self, pull_requests: Iterable[dict], _scores: Sequence[Score]
    ) -> Sequence[ReviewPlan]:
        eligible = self._eligible_users(self._review_roles)
        if not eligible:
            return []
        queue = deque(eligible)
        plans: list[ReviewPlan] = []
        # Sort by (repo, number) for the same determinism reason as plan_issue_assignments.
        for pr in sorted(pull_requests, key=_stable_pr_key):
            reviewer = queue[0]
            queue.rotate(-1)
            plans.append(
                ReviewPlan(
                    pr_number=pr["number"],
                    repo=pr["repo"],
                    reviewer=reviewer,
                )
            )
        return plans


def _stable_issue_key(issue: dict) -> tuple[str, int]:
    return (issue.get("repo", ""), issue.get("number", 0))


def _stable_pr_key(pr: dict) -> tuple[str, int]:
    return (pr.get("repo", ""), pr.get("number", 0))
