from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Iterable, Sequence

from ghdcbot.config.models import IdentityMapping, MergeRoleRuleConfig, MergeRoleRulesConfig, RoleMappingConfig
from ghdcbot.core.interfaces import Storage
from ghdcbot.core.models import DiscordRolePlan, GitHubAssignmentPlan, Score

logger = logging.getLogger("Planning")


def count_merged_prs_per_user(
    storage: Storage,
    identity_mappings: Iterable[IdentityMapping],
    period_start: datetime,
    period_end: datetime,
) -> dict[str, int]:
    """Count merged PRs per verified GitHub user for the given period.
    
    Returns a dictionary mapping github_user to merged PR count.
    Only counts events for verified users in identity_mappings.
    """
    # Get all contributions since period_start
    all_events = storage.list_contributions(period_start)
    
    # Filter to merged PRs in the period
    verified_github_users = {mapping.github_user for mapping in identity_mappings}
    merged_pr_counts: dict[str, int] = {}
    
    for event in all_events:
        if (
            event.event_type == "pr_merged"
            and event.github_user in verified_github_users
            and period_start <= event.created_at <= period_end
        ):
            merged_pr_counts[event.github_user] = merged_pr_counts.get(event.github_user, 0) + 1
    
    return merged_pr_counts


def plan_merge_based_roles(
    member_roles: dict[str, Sequence[str]],
    merged_pr_counts: dict[str, int],
    identity_mappings: Iterable[IdentityMapping],
    merge_rules: list[MergeRoleRuleConfig],
) -> list[DiscordRolePlan]:
    """Compute promotion-only Discord role plans based on merged PR counts.
    
    Returns only "add" actions (promotion-only). Never removes roles.
    Determines the highest eligible role per user based on merged PR count.
    """
    if not merge_rules:
        return []
    
    # Sort rules by threshold (ascending) for deterministic processing
    sorted_rules = sorted(merge_rules, key=lambda r: r.min_merged_prs)
    
    plans: list[DiscordRolePlan] = []
    for mapping in sorted(identity_mappings, key=lambda m: m.discord_user_id):
        current_roles = set(member_roles.get(mapping.discord_user_id, []))
        merged_count = merged_pr_counts.get(mapping.github_user, 0)
        
        # Find highest eligible role (promotion-only)
        eligible_roles = {
            rule.discord_role
            for rule in sorted_rules
            if merged_count >= rule.min_merged_prs
        }
        
        # Only add roles that user doesn't have yet (promotion-only)
        roles_to_add = eligible_roles - current_roles
        if roles_to_add:
            # Build role -> threshold mapping for selection
            role_threshold_map = {rule.discord_role: rule.min_merged_prs for rule in sorted_rules}
            # Select role with highest threshold value (not alphabetical)
            highest_role = max(roles_to_add, key=lambda r: role_threshold_map.get(r, 0))
            threshold = role_threshold_map[highest_role]
            plans.append(
                DiscordRolePlan(
                    discord_user_id=mapping.discord_user_id,
                    role=highest_role,
                    action="add",
                    reason=f"Merged PR count {merged_count} meets threshold for {highest_role}",
                    source={
                        "github_user": mapping.github_user,
                        "merged_pr_count": merged_count,
                        "threshold": threshold,
                        "decision_reason": "merge_role_rules",
                    },
                )
            )
    
    return plans


def plan_discord_roles(
    member_roles: dict[str, Sequence[str]],
    scores: Sequence[Score],
    identity_mappings: Iterable[IdentityMapping],
    role_mappings: Iterable[RoleMappingConfig],
    storage: Storage | None = None,
    period_start: datetime | None = None,
    period_end: datetime | None = None,
    merge_role_rules: MergeRoleRulesConfig | None = None,
) -> list[DiscordRolePlan]:
    """Compute Discord role add/remove plans from scores and role thresholds.
    
    If merge_role_rules is enabled, also considers merge-based roles.
    Final role per user is max(score_based_role, merge_based_role).
    """
    score_lookup = {score.github_user: score.points for score in scores}
    role_thresholds = sorted(role_mappings, key=lambda r: r.min_score)
    managed_roles = {mapping.discord_role for mapping in role_thresholds}

    plans: list[DiscordRolePlan] = []
    
    # Materialize identity_mappings into a list to avoid iterator consumption
    identity_list = list(identity_mappings)
    
    # Compute merged PR counts once (if merge-based roles are enabled)
    merged_pr_counts: dict[str, int] = {}
    if (
        merge_role_rules
        and merge_role_rules.enabled
        and merge_role_rules.rules
        and storage is not None
        and period_start is not None
        and period_end is not None
    ):
        merged_pr_counts = count_merged_prs_per_user(
            storage, identity_list, period_start, period_end
        )
    
    # Compute score-based desired roles
    score_based_roles: dict[str, set[str]] = {}
    merge_based_roles: dict[str, set[str]] = {}
    
    for mapping in sorted(identity_list, key=lambda m: m.discord_user_id):
        current_roles = set(member_roles.get(mapping.discord_user_id, []))
        points = score_lookup.get(mapping.github_user, 0)
        
        # Score-based roles
        score_desired = {
            mapping_cfg.discord_role
            for mapping_cfg in role_thresholds
            if points >= mapping_cfg.min_score
        }
        score_based_roles[mapping.discord_user_id] = score_desired
        
        # Merge-based roles (if enabled) - only highest eligible role
        if (
            merge_role_rules
            and merge_role_rules.enabled
            and merge_role_rules.rules
        ):
            merged_count = merged_pr_counts.get(mapping.github_user, 0)
            # Find highest eligible role (deterministic: last in sorted rules)
            eligible_merge_roles = [
                rule.discord_role
                for rule in merge_role_rules.rules
                if merged_count >= rule.min_merged_prs
            ]
            # Highest eligible role is the last one (rules sorted by threshold ascending)
            merge_desired = {eligible_merge_roles[-1]} if eligible_merge_roles else set()
            merge_based_roles[mapping.discord_user_id] = merge_desired
        else:
            merge_based_roles[mapping.discord_user_id] = set()
        
        # Final desired roles = max(score_based, merge_based)
        # For each role, if either system wants it, include it
        final_desired_roles = score_based_roles[mapping.discord_user_id] | merge_based_roles[mapping.discord_user_id]
        
        # Determine decision reason for each role
        for role in sorted(final_desired_roles - current_roles):
            # Determine which system granted this role
            score_granted = role in score_based_roles[mapping.discord_user_id]
            merge_granted = role in merge_based_roles[mapping.discord_user_id]
            
            if score_granted and merge_granted:
                decision_reason = "score_role_rules,merge_role_rules"
            elif merge_granted:
                decision_reason = "merge_role_rules"
            else:
                decision_reason = "score_role_rules"
            
            # Build source with relevant info
            source: dict[str, Any] = {
                "github_user": mapping.github_user,
                "decision_reason": decision_reason,
            }
            
            if score_granted:
                source["score"] = points
                source["score_threshold"] = next(
                    mapping_cfg.min_score
                    for mapping_cfg in role_thresholds
                    if mapping_cfg.discord_role == role
                )
            
            if merge_granted:
                merged_count = merged_pr_counts.get(mapping.github_user, 0)
                source["merged_pr_count"] = merged_count
                source["merge_threshold"] = next(
                    rule.min_merged_prs
                    for rule in merge_role_rules.rules
                    if rule.discord_role == role
                )
            
            plans.append(
                DiscordRolePlan(
                    discord_user_id=mapping.discord_user_id,
                    role=role,
                    action="add",
                    reason=f"Score {points} meets threshold for {role}" if score_granted else f"Merged PR count {merged_count} meets threshold for {role}",
                    source=source,
                )
            )
        
        # Remove roles only if score-based says so, and never remove merge-granted roles
        merge_granted_roles = merge_based_roles.get(mapping.discord_user_id, set())
        removal_candidates = (current_roles & managed_roles) - score_desired - merge_granted_roles
        for role in sorted(removal_candidates):
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
                        "decision_reason": "score_role_rules",
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
        # Skip issues that already have assignees (don't overwrite existing assignments)
        assignees = issue.get("assignees", [])
        if assignees:
            continue
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
