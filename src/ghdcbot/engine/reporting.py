from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from ghdcbot.config.models import BotConfig
from ghdcbot.core.models import DiscordRolePlan, GitHubAssignmentPlan

logger = logging.getLogger("Reporting")


def write_reports(
    discord_plans: Sequence[DiscordRolePlan],
    github_plans: Sequence[GitHubAssignmentPlan],
    config: BotConfig,
    repo_count: int | None = None,
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

    markdown = render_markdown_report(discord_plans, github_plans, config, repo_count)
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
        _render_discord_section(discord_sorted),
        _render_issue_section(github_sorted),
        _render_pr_section(github_sorted),
    ]
    return "\n\n".join(sections) + "\n"


def _render_discord_section(plans: Sequence[DiscordRolePlan]) -> str:
    lines = ["## Discord Role Changes"]
    if not plans:
        lines.append("No Discord role changes planned.")
        return "\n".join(lines)
    for plan in plans:
        lines.append(
            f"- `{plan.action}` `{plan.role}` for `{plan.discord_user_id}` "
            f"(reason: {plan.reason}; source: {json.dumps(plan.source, sort_keys=True)})"
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
