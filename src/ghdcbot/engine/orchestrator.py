from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from ghdcbot.config.models import BotConfig, IdentityMapping, MergeRoleRulesConfig, RoleMappingConfig
from ghdcbot.core.interfaces import (
    DiscordReader,
    DiscordWriter,
    GitHubReader,
    GitHubWriter,
    Storage,
)
from ghdcbot.core.modes import MutationPolicy, RunMode
from ghdcbot.core.models import ContributionEvent, GitHubAssignmentPlan
from ghdcbot.engine.assignment import RoleBasedAssignmentStrategy
from ghdcbot.engine.notifications import send_notification_for_event
from ghdcbot.engine.planning import plan_discord_roles
from ghdcbot.engine.reporting import write_reports, write_activity_report
from ghdcbot.engine.scoring import WeightedScoreStrategy
from ghdcbot.engine.snapshots import write_snapshots_to_github


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

        identity_mappings = _resolve_identity_mappings(self.storage, self.config.identity_mappings)

        cursor = self.storage.get_cursor("github") or period_start
        contributions = list(self.github_reader.list_contributions(cursor))
        stored = self.storage.record_contributions(contributions)
        last_seen = max((event.created_at for event in contributions), default=period_end)
        self.storage.set_cursor("github", last_seen)
        logger.info("Stored GitHub contributions", extra={"count": stored})

        quality_adjustments = None
        if getattr(self.config.scoring, "quality_adjustments", None) is not None:
            qa = self.config.scoring.quality_adjustments
            quality_adjustments = {
                "penalties": qa.penalties,
                "bonuses": qa.bonuses,
            }
        scoring = WeightedScoreStrategy(
            weights=self.config.scoring.weights,
            period_days=self.config.scoring.period_days,
            difficulty_weights=getattr(self.config.scoring, "difficulty_weights", None),
            quality_adjustments=quality_adjustments,
        )
        recent = self.storage.list_contributions(period_start)
        scores = scoring.compute_scores(recent, period_end)
        self.storage.upsert_scores(scores)
        logger.info("Computed scores", extra={"count": len(scores)})

        member_roles = self.discord_reader.list_member_roles()
        role_to_github = build_role_to_github_map(identity_mappings, member_roles)

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
        
        # Send verified-only notifications for new events (if enabled)
        notification_config = getattr(self.config.discord, "notifications", None)
        if notification_config and notification_config.enabled:
            _send_notifications_for_new_events(
                contributions,
                self.storage,
                self.discord_writer,
                policy,
                notification_config,
                self.config.github.org,
            )

        if not issues and not prs and not contributions:
            logging.getLogger("Planning").info(
                "No issues, PRs, or contributions \u2192 no plans"
            )

        if policy.mode in {RunMode.DRY_RUN, RunMode.OBSERVER}:
            # Generate audit reports before any mutations are attempted.
            try:
                merge_role_rules = getattr(self.config, "merge_role_rules", None)
                discord_plans = plan_discord_roles(
                    member_roles,
                    scores,
                    identity_mappings,
                    self.config.role_mappings,
                    storage=self.storage,
                    period_start=period_start,
                    period_end=period_end,
                    merge_role_rules=merge_role_rules,
                )
                github_plans = _to_github_assignment_plans(issue_plans, review_plans)
                # Pass difficulty_weights if available (optional parameter, backward compatible)
                list_summaries = getattr(self.storage, "list_contribution_summaries", None)
                if callable(list_summaries):
                    difficulty_weights = getattr(self.config.scoring, "difficulty_weights", None)
                    # Check if storage method accepts difficulty_weights (optional param)
                    import inspect
                    sig = inspect.signature(list_summaries)
                    if "difficulty_weights" in sig.parameters:
                        contribution_summaries = list_summaries(
                            period_start,
                            period_end,
                            self.config.scoring.weights,
                            difficulty_weights=difficulty_weights,
                        )
                    else:
                        contribution_summaries = list_summaries(
                            period_start,
                            period_end,
                            self.config.scoring.weights,
                        )
                else:
                    contribution_summaries = []
                repo_count = getattr(self.github_reader, "_last_repo_count", None)
                json_path, _md_path = write_reports(
                    discord_plans,
                    github_plans,
                    self.config,
                    repo_count=repo_count,
                    contribution_summaries=contribution_summaries,
                )
                logger.info(
                    "Audit reports written to %s",
                    str(json_path.parent),
                )
                # Read-only activity feed (mentor visibility): PR/issue events per repo
                events_in_period = [
                    e for e in recent
                    if period_start <= e.created_at <= period_end
                ]
                _activity_path, activity_md = write_activity_report(
                    events_in_period, period_start, period_end, self.config
                )
                append_audit = getattr(self.storage, "append_audit_event", None)
                if callable(append_audit):
                    append_audit({
                        "actor_type": "system",
                        "actor_id": "",
                        "event_type": "report_generated",
                        "context": {
                            "org": self.config.github.org,
                            "mode": policy.mode.value,
                            "report_dir": str(_activity_path.parent),
                        },
                    })
                # Optional: post short summary to Discord if configured
                activity_channel_id = getattr(
                    self.config.discord, "activity_channel_id", None
                )
                if activity_channel_id and activity_md:
                    send_msg = getattr(self.discord_writer, "send_message", None)
                    if callable(send_msg):
                        summary = activity_md[:1900] + ("..." if len(activity_md) > 1900 else "")
                        send_msg(activity_channel_id, summary)
            except Exception as exc:
                logger.exception("Failed to write audit reports", extra={"error": str(exc)})

        apply_github_plans(self.github_writer, issue_plans, review_plans, policy, self.config.github.org)
        merge_role_rules = getattr(self.config, "merge_role_rules", None)
        apply_discord_roles(
            self.discord_writer,
            member_roles,
            scores,
            identity_mappings,
            self.config.role_mappings,
            policy,
            storage=self.storage,
            period_start=period_start,
            period_end=period_end,
            merge_role_rules=merge_role_rules,
        )
        
        # Write GitHub snapshots (additive, non-blocking)
        # This happens AFTER all processing completes successfully
        try:
            # Compute contribution summaries for snapshot if not already computed
            contribution_summaries_for_snapshot = None
            list_summaries = getattr(self.storage, "list_contribution_summaries", None)
            if callable(list_summaries):
                try:
                    contribution_summaries_for_snapshot = list_summaries(
                        period_start,
                        period_end,
                        self.config.scoring.weights,
                    )
                except Exception:
                    # If summaries can't be computed, snapshot will have empty contributors data
                    pass
            
            write_snapshots_to_github(
                storage=self.storage,
                config=self.config,
                github_writer=self.github_writer,
                identity_mappings=identity_mappings,
                scores=scores,
                member_roles=member_roles,
                period_start=period_start,
                period_end=period_end,
                contribution_summaries=contribution_summaries_for_snapshot,
            )
        except Exception as exc:
            # Never block run-once completion
            logger.warning("Snapshot writing failed (non-blocking)", exc_info=True, extra={"error": str(exc)})

    def close(self) -> None:
        for adapter in {self.github_reader, self.github_writer, self.discord_reader, self.discord_writer}:
            close = getattr(adapter, "close", None)
            if callable(close):
                close()


def _send_notifications_for_new_events(
    contributions: list[ContributionEvent],
    storage: Storage,
    discord_writer: DiscordWriter,
    policy: MutationPolicy,
    config: Any,
    github_org: str,
) -> None:
    """Send Discord notifications for notification-worthy events (verified users only)."""
    from ghdcbot.engine.notifications import send_notification_for_event
    
    logger = logging.getLogger("Notifications")
    sent_count = 0
    pr_reviewed_count = 0
    for event in contributions:
        if event.event_type in {"issue_assigned", "pr_reviewed", "pr_merged"}:
            if event.event_type == "pr_reviewed":
                pr_reviewed_count += 1
                logger.info(
                    "Processing pr_reviewed event for notification",
                    extra={
                        "reviewer": event.github_user,
                        "repo": event.repo,
                        "pr_number": event.payload.get("pr_number"),
                        "review_id": event.payload.get("review_id"),
                        "state": event.payload.get("state"),
                        "pr_author": event.payload.get("pr_author"),
                    },
                )
            if send_notification_for_event(event, storage, discord_writer, policy, config, github_org):
                sent_count += 1
    if sent_count > 0:
        logger.info("Sent GitHub notifications", extra={"count": sent_count, "pr_reviewed_events": pr_reviewed_count})
    elif pr_reviewed_count > 0:
        logger.warning(
            "Found pr_reviewed events but no notifications were sent",
            extra={"pr_reviewed_count": pr_reviewed_count},
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


def _resolve_identity_mappings(
    storage: Storage,
    config_identity_mappings: Iterable[IdentityMapping],
) -> list[IdentityMapping]:
    """Prefer verified identity mappings from storage when available.

    This keeps legacy config-based mappings working, while enabling Phase-1
    verification-based linking without requiring config edits.
    """
    getter = getattr(storage, "list_verified_identity_mappings", None)
    if callable(getter):
        try:
            verified = list(getter())
        except Exception:  # noqa: BLE001
            verified = []
        if verified:
            return verified
    return list(config_identity_mappings)


def apply_github_plans(
    github_writer: GitHubWriter,
    issue_plans,
    review_plans,
    policy: MutationPolicy,
    github_org: str,
) -> None:
    logger = logging.getLogger("GitHubMutations")
    if not policy.allow_github_mutations:
        logger.info("GitHub mutations disabled", extra={"mode": policy.mode.value})
        return
    for plan in issue_plans:
        # plan.repo is just the repo name, owner comes from config
        github_writer.assign_issue(github_org, plan.repo, plan.issue_number, plan.assignee)
    for plan in review_plans:
        github_writer.request_review(github_org, plan.repo, plan.pr_number, plan.reviewer)


def apply_discord_roles(
    discord_writer: DiscordWriter,
    member_roles: dict[str, list[str]],
    scores,
    identity_mappings: Iterable[IdentityMapping],
    role_mappings: Iterable[RoleMappingConfig],
    policy: MutationPolicy,
    storage: Storage | None = None,
    period_start: datetime | None = None,
    period_end: datetime | None = None,
    merge_role_rules: MergeRoleRulesConfig | None = None,
) -> None:
    logger = logging.getLogger("DiscordMutations")
    if not policy.allow_discord_mutations:
        logger.info("Discord mutations disabled", extra={"mode": policy.mode.value})
        return

    from ghdcbot.engine.planning import count_merged_prs_per_user

    score_lookup = {score.github_user: score.points for score in scores}
    role_thresholds = sorted(role_mappings, key=lambda r: r.min_score)
    managed_roles = {mapping.discord_role for mapping in role_thresholds}

    # Get merge-based role counts if enabled
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
            storage, identity_mappings, period_start, period_end
        )

    for mapping in identity_mappings:
        current_roles = set(member_roles.get(mapping.discord_user_id, []))
        points = score_lookup.get(mapping.github_user, 0)
        
        # Score-based desired roles
        score_desired = {
            mapping_cfg.discord_role
            for mapping_cfg in role_thresholds
            if points >= mapping_cfg.min_score
        }
        
        # Merge-based desired roles (if enabled) - only highest eligible role
        merge_desired: set[str] = set()
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
        
        # Final desired roles = max(score_based, merge_based)
        desired_roles = score_desired | merge_desired
        
        # Track newly added roles for congratulatory messages
        newly_added_roles = sorted(desired_roles - current_roles)
        
        for role in newly_added_roles:
            discord_writer.add_role(mapping.discord_user_id, role)
            # Send congratulatory message for newly assigned roles
            _send_role_congratulation(
                discord_writer=discord_writer,
                discord_user_id=mapping.discord_user_id,
                role_name=role,
                policy=policy,
            )
        # Remove roles that are no longer desired (preserve merge-based roles)
        roles_to_remove = (current_roles & managed_roles) - (score_desired | merge_desired)
        for role in sorted(roles_to_remove):
            discord_writer.remove_role(mapping.discord_user_id, role)


def _send_role_congratulation(
    discord_writer: DiscordWriter,
    discord_user_id: str,
    role_name: str,
    policy: MutationPolicy,
) -> None:
    """Send a congratulatory DM to a user when they receive a new role.
    
    Only sends if mutations are allowed (active mode) and DM sending is available.
    Fails gracefully if DM cannot be sent (privacy settings, etc.).
    """
    logger = logging.getLogger("DiscordMutations")
    
    # Only send in active mode (mutations allowed)
    if not policy.allow_discord_mutations:
        return
    
    # Check if discord_writer supports DM sending
    send_dm = getattr(discord_writer, "send_dm", None)
    if not callable(send_dm):
        # DM sending not available (e.g., using DiscordPlanWriter)
        return
    
    # Build congratulatory message (generic, works for any role source)
    message = (
        f"🎉 Congratulations!\n\n"
        f"Hi <@{discord_user_id}>,\n\n"
        f"You have earned the **{role_name}** role in the server.\n\n"
        f"Thank you for your contribution — keep building 🚀"
    )
    
    # Send DM (fails gracefully if user has DMs disabled)
    success = send_dm(discord_user_id, message)
    if success:
        logger.info(
            "Sent congratulation message for role",
            extra={"discord_user_id": discord_user_id, "role": role_name},
        )
    else:
        logger.warning(
            "Failed to send congratulation message (user may have DMs disabled)",
            extra={"discord_user_id": discord_user_id, "role": role_name},
        )


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
