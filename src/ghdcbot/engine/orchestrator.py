from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

from ghdcbot.config.models import BotConfig, IdentityMapping, RoleMappingConfig
from ghdcbot.core.interfaces import (
    DiscordReader,
    DiscordWriter,
    GitHubReader,
    GitHubWriter,
    Storage,
)
from ghdcbot.core.modes import MutationPolicy, RunMode
from ghdcbot.core.models import GitHubAssignmentPlan
from ghdcbot.engine.assignment import RoleBasedAssignmentStrategy
from ghdcbot.engine.planning import plan_discord_roles
from ghdcbot.engine.reporting import write_reports
from ghdcbot.engine.scoring import WeightedScoreStrategy


@dataclass(frozen=True)
class Orchestrator:
    github_reader: GitHubReader
    github_writer: GitHubWriter
    discord_reader: DiscordReader
    discord_writer: DiscordWriter
    storage: Storage
    config: BotConfig

    def run_once(self) -> None:
        logger = logging.getLogger("Orchestrator")
        self.storage.init_schema()

        period_end = datetime.now(timezone.utc)
        period_start = period_end - timedelta(days=self.config.scoring.period_days)

        cursor = self.storage.get_cursor("github") or period_start
        contributions = list(self.github_reader.list_contributions(cursor))
        stored = self.storage.record_contributions(contributions)
        last_seen = max((event.created_at for event in contributions), default=period_end)
        self.storage.set_cursor("github", last_seen)
        logger.info("Stored GitHub contributions", extra={"count": stored})

        scoring = WeightedScoreStrategy(
            weights=self.config.scoring.weights,
            period_days=self.config.scoring.period_days,
        )
        recent = self.storage.list_contributions(period_start)
        scores = scoring.compute_scores(recent, period_end)
        self.storage.upsert_scores(scores)
        logger.info("Computed scores", extra={"count": len(scores)})

        member_roles = self.discord_reader.list_member_roles()
        role_to_github = build_role_to_github_map(self.config.identity_mappings, member_roles)

        assignment = RoleBasedAssignmentStrategy(
            role_to_github_users=role_to_github,
            issue_roles=self.config.assignments.issue_assignees,
            review_roles=self.config.assignments.review_roles,
        )

        issues = list(self.github_reader.list_open_issues())
        prs = list(self.github_reader.list_open_pull_requests())
        issue_plans = assignment.plan_issue_assignments(issues, scores)
        review_plans = assignment.plan_review_requests(prs, scores)

        policy = MutationPolicy(
            mode=self.config.runtime.mode,
            github_write_allowed=self.config.github.permissions.write,
            discord_write_allowed=self.config.discord.permissions.write,
        )

        if not issues and not prs and not contributions:
            logging.getLogger("Planning").info(
                "No repositories \u2192 no contributions \u2192 no plans"
            )

        if policy.mode in {RunMode.DRY_RUN, RunMode.OBSERVER}:
            # Generate audit reports before any mutations are attempted.
            try:
                discord_plans = plan_discord_roles(
                    member_roles,
                    scores,
                    self.config.identity_mappings,
                    self.config.role_mappings,
                )
                github_plans = _to_github_assignment_plans(issue_plans, review_plans)
                repo_count = getattr(self.github_reader, "_last_repo_count", None)
                json_path, md_path = write_reports(
                    discord_plans, github_plans, self.config, repo_count=repo_count
                )
                logger.info(
                    "Audit reports written to %s",
                    str(json_path.parent),
                )
            except Exception as exc:  # noqa: BLE001
                logger.error("Failed to write audit reports", extra={"error": str(exc)})

        apply_github_plans(self.github_writer, issue_plans, review_plans, policy)
        apply_discord_roles(
            self.discord_writer,
            member_roles,
            scores,
            self.config.identity_mappings,
            self.config.role_mappings,
            policy,
        )


def build_role_to_github_map(
    identity_mappings: Iterable[IdentityMapping],
    member_roles: dict[str, list[str]],
) -> dict[str, list[str]]:
    role_to_github: dict[str, list[str]] = defaultdict(list)
    for mapping in identity_mappings:
        roles = member_roles.get(mapping.discord_user_id, [])
        for role in roles:
            role_to_github[role].append(mapping.github_user)
    return role_to_github


def apply_github_plans(
    github_writer: GitHubWriter,
    issue_plans,
    review_plans,
    policy: MutationPolicy,
) -> None:
    logger = logging.getLogger("GitHubMutations")
    if not policy.allow_github_mutations:
        logger.info("GitHub mutations disabled", extra={"mode": policy.mode.value})
        return
    for plan in issue_plans:
        github_writer.assign_issue(plan.repo, plan.issue_number, plan.assignee)
    for plan in review_plans:
        github_writer.request_review(plan.repo, plan.pr_number, plan.reviewer)


def apply_discord_roles(
    discord_writer: DiscordWriter,
    member_roles: dict[str, list[str]],
    scores,
    identity_mappings: Iterable[IdentityMapping],
    role_mappings: Iterable[RoleMappingConfig],
    policy: MutationPolicy,
) -> None:
    logger = logging.getLogger("DiscordMutations")
    if not policy.allow_discord_mutations:
        logger.info("Discord mutations disabled", extra={"mode": policy.mode.value})
        return

    score_lookup = {score.github_user: score.points for score in scores}
    role_thresholds = sorted(role_mappings, key=lambda r: r.min_score)
    managed_roles = {mapping.discord_role for mapping in role_thresholds}

    for mapping in identity_mappings:
        current_roles = set(member_roles.get(mapping.discord_user_id, []))
        points = score_lookup.get(mapping.github_user, 0)
        desired_roles = {
            mapping_cfg.discord_role
            for mapping_cfg in role_thresholds
            if points >= mapping_cfg.min_score
        }
        for role in sorted(desired_roles - current_roles):
            discord_writer.add_role(mapping.discord_user_id, role)
        for role in sorted((current_roles & managed_roles) - desired_roles):
            discord_writer.remove_role(mapping.discord_user_id, role)


def _to_github_assignment_plans(issue_plans, review_plans) -> list[GitHubAssignmentPlan]:
    plans: list[GitHubAssignmentPlan] = []
    for plan in issue_plans:
        plans.append(
            GitHubAssignmentPlan(
                repo=plan.repo,
                target_number=plan.issue_number,
                target_type="issue",
                assignee=plan.assignee,
                action="assign",
                reason="Role-based issue assignment",
                source={"origin": "assignment_strategy"},
            )
        )
    for plan in review_plans:
        plans.append(
            GitHubAssignmentPlan(
                repo=plan.repo,
                target_number=plan.pr_number,
                target_type="pull_request",
                assignee=plan.reviewer,
                action="request_review",
                reason="Role-based review assignment",
                source={"origin": "assignment_strategy"},
            )
        )
    return plans
