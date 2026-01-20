from __future__ import annotations

import logging
from typing import Iterable

import httpx

from ghdcbot.core.models import GitHubAssignmentPlan
from ghdcbot.core.modes import MutationPolicy, mutation_skip_reason


class GitHubPlanWriter:
    """Thin executor for GitHubAssignmentPlan objects.

    This adapter only executes precomputed plans when MutationPolicy allows it.
    """

    def __init__(self, token: str, org: str, api_base: str) -> None:
        self._logger = logging.getLogger(self.__class__.__name__)
        self._org = org
        self._client = httpx.Client(
            base_url=api_base,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            },
            timeout=30.0,
        )

    def apply_plans(self, plans: Iterable[GitHubAssignmentPlan], policy: MutationPolicy) -> None:
        seen: set[tuple] = set()
        for plan in plans:
            skip_reason = mutation_skip_reason(policy, policy.allow_github_mutations)
            if skip_reason:
                self._log_plan(plan, result=skip_reason)
                continue
            dedupe_key = (plan.repo, plan.target_type, plan.target_number, plan.action, plan.assignee)
            if dedupe_key in seen:
                self._log_plan(plan, result="skipped (duplicate)")
                continue
            if plan.action == "request_review" and plan.assignee == plan.source.get("author"):
                self._log_plan(plan, result="skipped (author)")
                continue
            seen.add(dedupe_key)
            self._apply_plan(plan)

    def _apply_plan(self, plan: GitHubAssignmentPlan) -> None:
        if plan.action == "assign":
            path = f"/repos/{self._org}/{plan.repo}/issues/{plan.target_number}/assignees"
            payload = {"assignees": [plan.assignee]}
        elif plan.action == "request_review":
            path = f"/repos/{self._org}/{plan.repo}/pulls/{plan.target_number}/requested_reviewers"
            payload = {"reviewers": [plan.assignee]}
        else:
            self._log_plan(plan, result="failed (unknown action)")
            return

        try:
            response = self._client.post(path, json=payload)
        except httpx.HTTPError as exc:
            self._log_plan(plan, result="failed (network error)", error=str(exc))
            return

        if response.status_code in {401, 403, 404}:
            self._log_plan(plan, result="failed (permission denied)", status=response.status_code)
            return
        if response.status_code >= 300:
            self._log_plan(plan, result="failed (http error)", status=response.status_code)
            return

        self._log_plan(plan, result="applied")

    def _log_plan(
        self,
        plan: GitHubAssignmentPlan,
        result: str,
        status: int | None = None,
        error: str | None = None,
    ) -> None:
        self._logger.info(
            "GitHub assignment plan execution",
            extra={
                "repo": plan.repo,
                "target_type": plan.target_type,
                "target_number": plan.target_number,
                "assignee": plan.assignee,
                "action": plan.action,
                "reason": plan.reason,
                "result": result,
                "status": status,
                "error": error,
            },
        )


