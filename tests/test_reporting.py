"""Unit tests for engine/reporting.py."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ghdcbot.config.models import (
    BotConfig,
    DiscordConfig,
    GitHubConfig,
    PermissionConfig,
    RepoFilterConfig,
    RuntimeConfig,
)
from ghdcbot.core.models import (
    ContributionSummary,
    DiscordRolePlan,
    GitHubAssignmentPlan,
)
from ghdcbot.core.modes import RunMode
from ghdcbot.engine.reporting import (
    build_audit_payload,
    render_markdown_report,
    write_reports,
)


@pytest.fixture
def sample_config(tmp_path: Path) -> BotConfig:
    return BotConfig(
        runtime=RuntimeConfig(
            mode=RunMode.DRY_RUN,
            log_level="INFO",
            data_dir=str(tmp_path),
            github_adapter="real",
            discord_adapter="real",
            storage_adapter="sqlite",
            activity_period_days=30,
        ),
        github=GitHubConfig(
            org="test-org",
            app_id=123,
            private_key="fake-key",
            installation_id=456,
            permissions=PermissionConfig(read=True, write=False),
        ),
        discord=DiscordConfig(
            bot_token="fake-token",
            guild_id=789,
            permissions=PermissionConfig(read=True, write=False),
        ),
    )


def test_build_audit_payload(sample_config: BotConfig) -> None:
    discord_plans = [
        DiscordRolePlan(
            discord_user_id="200",
            role_name="Contributor",
            action="add",
            reason="Score threshold met",
        ),
        DiscordRolePlan(
            discord_user_id="100",
            role_name="Maintainer",
            action="add",
            reason="Role rule met",
        ),
    ]
    github_plans = [
        GitHubAssignmentPlan(
            repo="repo-b",
            item_type="issue",
            item_number=2,
            assignees=["user2"],
            reviewers=[],
            reason="Auto-assignment",
        ),
        GitHubAssignmentPlan(
            repo="repo-a",
            item_type="pr",
            item_number=1,
            assignees=[],
            reviewers=["user1"],
            reason="Review requested",
        ),
    ]

    payload = build_audit_payload(discord_plans, github_plans, sample_config)

    assert payload["runtime_mode"] == "dry_run"
    assert payload["org"] == "test-org"
    assert payload["summary"]["discord_role_changes"] == 2
    assert payload["summary"]["github_assignments"] == 2

    # Verify deterministic sorting of discord plans by user_id
    assert [p["discord_user_id"] for p in payload["discord_role_plans"]] == ["100", "200"]

    # Verify deterministic sorting of github plans by repo, item_type, item_number
    assert [p["repo"] for p in payload["github_assignment_plans"]] == ["repo-a", "repo-b"]


def test_render_markdown_report_basic(sample_config: BotConfig) -> None:
    discord_plans = [
        DiscordRolePlan(
            discord_user_id="100",
            role_name="Contributor",
            action="add",
            reason="Qualified",
        )
    ]
    github_plans = [
        GitHubAssignmentPlan(
            repo="repo-a",
            item_type="issue",
            item_number=42,
            assignees=["alice"],
            reviewers=[],
            reason="Assigned issue",
        )
    ]
    summaries = [
        ContributionSummary(
            github_user="alice",
            prs_opened=2,
            prs_merged=1,
            issues_opened=3,
            issues_closed=2,
            reviews_submitted=0,
            comments_made=5,
        )
    ]

    md = render_markdown_report(
        discord_plans,
        github_plans,
        sample_config,
        repo_count=5,
        contribution_summaries=summaries,
    )

    assert "## Summary" in md
    assert "Runtime mode: `dry_run`" in md
    assert "Organization: `test-org`" in md
    assert "Discord role changes: `1`" in md
    assert "GitHub assignments: `1`" in md
    assert "alice" in md
    assert "repo-a#42" in md


def test_render_markdown_report_empty_org(sample_config: BotConfig) -> None:
    md = render_markdown_report(
        discord_plans=[],
        github_plans=[],
        config=sample_config,
        repo_count=0,
    )
    assert "- Repositories discovered: 0 (new or empty organization)" in md


def test_write_reports(sample_config: BotConfig, tmp_path: Path) -> None:
    discord_plans = [
        DiscordRolePlan(
            discord_user_id="123",
            role_name="Helper",
            action="add",
            reason="Good standing",
        )
    ]
    github_plans = [
        GitHubAssignmentPlan(
            repo="core",
            item_type="pr",
            item_number=10,
            assignees=["bob"],
            reviewers=[],
            reason="Review assignment",
        )
    ]

    json_path, md_path = write_reports(
        discord_plans, github_plans, sample_config, repo_count=1
    )

    assert json_path.exists()
    assert md_path.exists()

    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["summary"]["discord_role_changes"] == 1
    assert data["summary"]["github_assignments"] == 1

    md_content = md_path.read_text(encoding="utf-8")
    assert "## Summary" in md_content
    assert "core#10" in md_content
