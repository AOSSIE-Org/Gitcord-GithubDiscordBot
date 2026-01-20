from __future__ import annotations

import logging
from typing import Iterable, Sequence

from ghdcbot.config.models import IdentityMapping, RoleMappingConfig
from ghdcbot.core.models import DiscordRolePlan, GitHubAssignmentPlan, Score

logger = logging.getLogger("Planning")


def plan_discord_roles(
    member_roles: dict[str, Sequence[str]],
    scores: Sequence[Score],
    identity_mappings: Iterable[IdentityMapping],
    role_mappings: Iterable[RoleMappingConfig],
) -> list[DiscordRolePlan]:
    """Compute Discord role add/remove plans from scores and role thresholds."""
    score_lookup = {score.github_user: score.points for score in scores}
    role_thresholds = sorted(role_mappings, key=lambda r: r.min_score)
    managed_roles = {mapping.discord_role for mapping in role_thresholds}

    plans: list[DiscordRolePlan] = []
    for mapping in sorted(identity_mappings, key=lambda m: m.discord_user_id):
        current_roles = set(member_roles.get(mapping.discord_user_id, []))
        points = score_lookup.get(mapping.github_user, 0)
        desired_roles = {
            mapping_cfg.discord_role
            for mapping_cfg in role_thresholds
            if points >= mapping_cfg.min_score
        }

        for role in sorted(desired_roles - current_roles):
            plans.append(
                DiscordRolePlan(
                    discord_user_id=mapping.discord_user_id,
                    role=role,
                    action="add",
                    reason=f"Score {points} meets threshold for {role}",
                    source={
                        "github_user": mapping.github_user,
                        "score": points,
                        "threshold": next(
                            mapping_cfg.min_score
                            for mapping_cfg in role_thresholds
                            if mapping_cfg.discord_role == role
                        ),
                    },
                )
            )
        for role in sorted((current_roles & managed_roles) - desired_roles):
            plans.append(
                DiscordRolePlan(
                    discord_user_id=mapping.discord_user_id,
                    role=role,
                    action="remove",
                    reason=f"Score {points} below threshold for {role}",
                    source={
                        "github_user": mapping.github_user,
                        "score": points,
                        "threshold": next(
                            mapping_cfg.min_score
                            for mapping_cfg in role_thresholds
                            if mapping_cfg.discord_role == role
                        ),
                    },
                )
            )

    _log_plan_counts("discord_role", plans)
    return plans


def plan_github_assignments(
    issues: Iterable[dict],
    pull_requests: Iterable[dict],
    role_to_github_users: dict[str, list[str]],
    issue_roles: Sequence[str],
    review_roles: Sequence[str],
) -> list[GitHubAssignmentPlan]:
    """Compute GitHub assignment plans from role-based eligibility."""
    issue_candidates = _eligible_users(role_to_github_users, issue_roles)
    review_candidates = _eligible_users(role_to_github_users, review_roles)

    plans: list[GitHubAssignmentPlan] = []
    plans.extend(_plan_issue_assignments(issues, issue_candidates))
    plans.extend(_plan_review_requests(pull_requests, review_candidates))

    _log_plan_counts("github_assignment", plans)
    return plans


def _plan_issue_assignments(
    issues: Iterable[dict], candidates: list[str]
) -> list[GitHubAssignmentPlan]:
    if not candidates:
        return []

    plans: list[GitHubAssignmentPlan] = []
    for idx, issue in enumerate(sorted(issues, key=_stable_issue_key)):
        assignee = candidates[idx % len(candidates)]
        plans.append(
            GitHubAssignmentPlan(
                repo=issue["repo"],
                target_number=issue["number"],
                target_type="issue",
                assignee=assignee,
                action="assign",
                reason="Role-based issue assignment",
                source={"eligible_role_users": candidates},
            )
        )
    return plans


def _plan_review_requests(
    pull_requests: Iterable[dict], candidates: list[str]
) -> list[GitHubAssignmentPlan]:
    if not candidates:
        return []

    plans: list[GitHubAssignmentPlan] = []
    for idx, pr in enumerate(sorted(pull_requests, key=_stable_pr_key)):
        author = pr.get("author")
        reviewer = _select_reviewer(candidates, idx, author)
        if reviewer is None:
            continue
        plans.append(
            GitHubAssignmentPlan(
                repo=pr["repo"],
                target_number=pr["number"],
                target_type="pull_request",
                assignee=reviewer,
                action="request_review",
                reason="Role-based review assignment",
                source={"eligible_role_users": candidates, "author": author},
            )
        )
    return plans


def _eligible_users(
    role_to_github_users: dict[str, list[str]],
    roles: Sequence[str],
) -> list[str]:
    eligible: list[str] = []
    for role in roles:
        eligible.extend(role_to_github_users.get(role, []))
    return sorted(set(eligible))


def _select_reviewer(candidates: list[str], index: int, author: str | None) -> str | None:
    if not candidates:
        return None
    ordered = [user for user in candidates if user != author]
    if not ordered:
        return None
    return ordered[index % len(ordered)]


def _stable_issue_key(issue: dict) -> tuple:
    return (issue.get("repo", ""), issue.get("number", 0))


def _stable_pr_key(pr: dict) -> tuple:
    return (pr.get("repo", ""), pr.get("number", 0))


def _log_plan_counts(label: str, plans: list) -> None:
    if not plans:
        logger.info("No plan changes required", extra={"plan": label})
        return
    logger.info("Planned actions", extra={"plan": label, "count": len(plans)})
