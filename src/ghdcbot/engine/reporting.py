from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from ghdcbot.config.models import BotConfig
from ghdcbot.core.models import (
    ContributionEvent,
    ContributionSummary,
    DiscordRolePlan,
    GitHubAssignmentPlan,
)

logger = logging.getLogger("Reporting")

# Event types included in the activity feed (read-only mentor visibility)
_ACTIVITY_FEED_EVENT_TYPES = frozenset({"pr_opened", "pr_merged", "issue_opened", "issue_closed"})


def write_reports(
    discord_plans: Sequence[DiscordRolePlan],
    github_plans: Sequence[GitHubAssignmentPlan],
    config: BotConfig,
    repo_count: int | None = None,
    contribution_summaries: Sequence[ContributionSummary] | None = None,
) -> tuple[Path, Path]:
    """Generate audit JSON and Markdown reports in data_dir/reports."""
    report_dir = Path(config.runtime.data_dir) / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)

    json_path = report_dir / "audit.json"
    md_path = report_dir / "audit.md"

    payload = build_audit_payload(discord_plans, github_plans, config)
    json_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    markdown = render_markdown_report(
        discord_plans,
        github_plans,
        config,
        repo_count,
        contribution_summaries=contribution_summaries,
    )
    md_path.write_text(markdown, encoding="utf-8")

    logger.info(
        "Generated reports",
        extra={
            "json_path": str(json_path),
            "markdown_path": str(md_path),
            "discord_plans": len(discord_plans),
            "github_plans": len(github_plans),
        },
    )
    return json_path, md_path


def build_audit_payload(
    discord_plans: Sequence[DiscordRolePlan],
    github_plans: Sequence[GitHubAssignmentPlan],
    config: BotConfig,
) -> dict:
    """Build a deterministic, machine-readable audit payload."""
    discord_entries = [asdict(plan) for plan in sorted(discord_plans, key=_discord_key)]
    github_entries = [asdict(plan) for plan in sorted(github_plans, key=_github_key)]

    repo_filter = config.github.repos
    repo_filter_payload = (
        {"mode": repo_filter.mode, "names": sorted(repo_filter.names)}
        if repo_filter
        else None
    )

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "runtime_mode": config.runtime.mode.value,
        "org": config.github.org,
        "repo_filter": repo_filter_payload,
        "summary": {
            "discord_role_changes": len(discord_entries),
            "github_assignments": len(github_entries),
        },
        "discord_role_plans": discord_entries,
        "github_assignment_plans": github_entries,
    }


def render_markdown_report(
    discord_plans: Sequence[DiscordRolePlan],
    github_plans: Sequence[GitHubAssignmentPlan],
    config: BotConfig,
    repo_count: int | None = None,
    contribution_summaries: Sequence[ContributionSummary] | None = None,
) -> str:
    """Render a human-readable Markdown report for review workflows."""
    discord_sorted = sorted(discord_plans, key=_discord_key)
    github_sorted = sorted(github_plans, key=_github_key)

    summary_lines = [
        "## Summary",
        f"- Runtime mode: `{config.runtime.mode.value}`",
        f"- Organization: `{config.github.org}`",
        f"- Discord role changes: `{len(discord_sorted)}`",
        f"- GitHub assignments: `{len(github_sorted)}`",
    ]
    repo_filter = config.github.repos
    if repo_filter:
        summary_lines.append(
            f"- Repo filter: `{repo_filter.mode}` ({', '.join(sorted(repo_filter.names))})"
        )
    else:
        summary_lines.append("- Repo filter: `all`")
    if repo_count == 0:
        summary_lines.append("- Repositories discovered: 0 (new or empty organization)")

    sections = [
        "\n".join(summary_lines),
        _render_contribution_summary_section(
            contribution_summaries or [], config.scoring.period_days, config=config
        ),
        _render_discord_section(discord_sorted),
        _render_issue_section(github_sorted),
        _render_pr_section(github_sorted),
    ]
    return "\n\n".join(sections) + "\n"


def _render_contribution_summary_section(
    summaries: Sequence[ContributionSummary], period_days: int, config=None
) -> str:
    lines = [f"## Contribution Summary (Last {period_days} days)"]
    # Add note about difficulty-aware scoring if configured
    if config and getattr(config.scoring, "difficulty_weights", None):
        difficulty_weights = config.scoring.difficulty_weights
        weights_str = ", ".join(f"{k}: {v}" for k, v in sorted(difficulty_weights.items()))
        lines.append(f"*Note: Difficulty-aware scoring enabled ({weights_str}). Merged PRs are scored based on linked issue labels.*")
    # Add note about quality adjustments if configured
    if config and getattr(config.scoring, "quality_adjustments", None):
        qa = config.scoring.quality_adjustments
        adjustment_notes = []
        if qa.penalties:
            penalty_strs = [f"{k}: {v}" for k, v in sorted(qa.penalties.items())]
            adjustment_notes.append(f"Penalties: {', '.join(penalty_strs)}")
        if qa.bonuses:
            bonus_strs = [f"{k}: {v}" for k, v in sorted(qa.bonuses.items())]
            adjustment_notes.append(f"Bonuses: {', '.join(bonus_strs)}")
        if adjustment_notes:
            lines.append(f"*Quality adjustments enabled: {'; '.join(adjustment_notes)}.*")
    if not summaries:
        lines.append("No activity in period.")
        return "\n".join(lines)
    lines.append("| User | Issues | PRs | Reviews | Comments | Score |")
    lines.append("|------|--------|-----|---------|----------|-------|")
    for summary in sorted(summaries, key=lambda entry: entry.github_user):
        lines.append(
            "| {user} | {issues} | {prs} | {reviews} | {comments} | {score} |".format(
                user=summary.github_user,
                issues=summary.issues_opened,
                prs=summary.prs_opened,
                reviews=summary.prs_reviewed,
                comments=summary.comments,
                score=summary.total_score,
            )
        )
    return "\n".join(lines)


def _render_discord_section(plans: Sequence[DiscordRolePlan]) -> str:
    lines = ["## Discord Role Changes"]
    if not plans:
        lines.append("No Discord role changes planned.")
        return "\n".join(lines)
    
    # Group plans by user for better readability
    by_user: dict[str, list[DiscordRolePlan]] = {}
    for plan in plans:
        by_user.setdefault(plan.discord_user_id, []).append(plan)
    
    for user_id in sorted(by_user.keys()):
        user_plans = sorted(by_user[user_id], key=lambda p: p.role)
        for plan in user_plans:
            source = plan.source
            decision_reason = source.get("decision_reason", "score_role_rules")
            details_parts = []
            
            # Add merge-based info if present
            if "merged_pr_count" in source:
                details_parts.append(f"merged PRs: {source['merged_pr_count']}")
                if "merge_threshold" in source:
                    details_parts.append(f"threshold: {source['merge_threshold']}")
            
            # Add score-based info if present
            if "score" in source:
                details_parts.append(f"score: {source['score']}")
                if "score_threshold" in source:
                    details_parts.append(f"threshold: {source['score_threshold']}")
            
            details_str = f" ({', '.join(details_parts)})" if details_parts else ""
            lines.append(
                f"- `{plan.action}` `{plan.role}` for `{user_id}` "
                f"(reason: {plan.reason}; decision: {decision_reason}{details_str})"
            )
    
    return "\n".join(lines)


def _render_issue_section(plans: Sequence[GitHubAssignmentPlan]) -> str:
    lines = ["## GitHub Issue Assignments"]
    issues = [plan for plan in plans if plan.target_type == "issue"]
    if not issues:
        lines.append("No GitHub issue assignments planned.")
        return "\n".join(lines)
    for plan in issues:
        lines.append(
            f"- `{plan.action}` `{plan.assignee}` to `{plan.repo}#{plan.target_number}` "
            f"(reason: {plan.reason}; source: {json.dumps(plan.source, sort_keys=True)})"
        )
    return "\n".join(lines)


def _render_pr_section(plans: Sequence[GitHubAssignmentPlan]) -> str:
    lines = ["## GitHub PR Review Assignments"]
    prs = [plan for plan in plans if plan.target_type == "pull_request"]
    if not prs:
        lines.append("No GitHub PR review assignments planned.")
        return "\n".join(lines)
    for plan in prs:
        lines.append(
            f"- `{plan.action}` `{plan.assignee}` on `{plan.repo}#{plan.target_number}` "
            f"(reason: {plan.reason}; source: {json.dumps(plan.source, sort_keys=True)})"
        )
    return "\n".join(lines)


def _discord_key(plan: DiscordRolePlan) -> tuple:
    return (plan.discord_user_id, plan.role, plan.action)


def _github_key(plan: GitHubAssignmentPlan) -> tuple:
    return (plan.repo, plan.target_type, plan.target_number, plan.action, plan.assignee)


def build_activity_feed_markdown(
    events: Sequence[ContributionEvent],
    period_start: datetime,
    period_end: datetime,
    org: str,
) -> str:
    """Build a read-only, repo-wise activity feed for mentor visibility.

    Includes only pr_opened, pr_merged, issue_opened, issue_closed.
    Events must already be filtered to [period_start, period_end].
    """
    period_days = max(1, (period_end - period_start).days)
    lines = [
        "# Activity Feed (read-only)",
        f"Period: last {period_days} days (through {period_end.date().isoformat()} UTC).",
        "",
    ]
    # Filter to feed event types and to period
    feed_events = [
        e
        for e in events
        if e.event_type in _ACTIVITY_FEED_EVENT_TYPES
        and period_start <= e.created_at <= period_end
    ]
    if not feed_events:
        lines.append("No PR or issue activity in this period.")
        return "\n".join(lines)

    # Group by repo
    by_repo: dict[str, list[ContributionEvent]] = {}
    for e in feed_events:
        by_repo.setdefault(e.repo, []).append(e)
    base = f"https://github.com/{org}"
    for repo in sorted(by_repo.keys()):
        repo_events = sorted(by_repo[repo], key=lambda x: (x.created_at, x.event_type))
        pr_opened = [e for e in repo_events if e.event_type == "pr_opened"]
        pr_merged = [e for e in repo_events if e.event_type == "pr_merged"]
        issue_opened = [e for e in repo_events if e.event_type == "issue_opened"]
        issue_closed = [e for e in repo_events if e.event_type == "issue_closed"]
        lines.append(f"## {repo}")
        if pr_opened:
            lines.append(f"### PRs opened ({len(pr_opened)})")
            for e in pr_opened:
                num = e.payload.get("pr_number", "?")
                title = (e.payload.get("title") or "No title")[:60]
                lines.append(f"- #{num} **{title}** — {e.github_user} — {base}/{repo}/pull/{num}")
        if pr_merged:
            lines.append(f"### PRs merged ({len(pr_merged)})")
            for e in pr_merged:
                num = e.payload.get("pr_number", "?")
                title = (e.payload.get("title") or "No title")[:60]
                difficulty_labels = e.payload.get("difficulty_labels", [])
                difficulty_note = ""
                if difficulty_labels:
                    # Show difficulty labels if present
                    label_str = ", ".join(difficulty_labels)
                    difficulty_note = f" [Labels: {label_str}]"
                lines.append(f"- #{num} **{title}** — {e.github_user}{difficulty_note} — {base}/{repo}/pull/{num}")
        if issue_opened:
            lines.append(f"### Issues opened ({len(issue_opened)})")
            for e in issue_opened:
                num = e.payload.get("issue_number", "?")
                title = (e.payload.get("title") or "No title")[:60]
                lines.append(f"- #{num} **{title}** — {e.github_user} — {base}/{repo}/issues/{num}")
        if issue_closed:
            lines.append(f"### Issues closed ({len(issue_closed)})")
            for e in issue_closed:
                num = e.payload.get("issue_number", "?")
                title = (e.payload.get("title") or "No title")[:60]
                lines.append(f"- #{num} **{title}** — {e.github_user} — {base}/{repo}/issues/{num}")
        lines.append("")

    return "\n".join(lines).strip()


def write_activity_report(
    events: Sequence[ContributionEvent],
    period_start: datetime,
    period_end: datetime,
    config: BotConfig,
) -> tuple[Path, str]:
    """Write activity feed to data_dir/reports/activity.md. Returns (path, markdown)."""
    report_dir = Path(config.runtime.data_dir) / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    activity_path = report_dir / "activity.md"
    org = config.github.org
    markdown = build_activity_feed_markdown(events, period_start, period_end, org)
    activity_path.write_text(markdown, encoding="utf-8")
    logger.info("Activity report written", extra={"path": str(activity_path)})
    return activity_path, markdown
