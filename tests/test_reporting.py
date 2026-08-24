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
    ContributionEvent,
    ContributionSummary,
    DiscordRolePlan,
    GitHubAssignmentPlan,
)
from ghdcbot.core.modes import RunMode
from ghdcbot.engine.reporting import (
    build_activity_feed_markdown,
    build_audit_payload,
    render_markdown_report,
    write_activity_report,
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
            role="Contributor",
            action="add",
            reason="Score threshold met",
            source={"decision_reason": "score_role_rules", "score": 25},
        ),
        DiscordRolePlan(
            discord_user_id="100",
            role="Maintainer",
            action="add",
            reason="Role rule met",
            source={"decision_reason": "score_role_rules", "score": 100},
        ),
    ]
    github_plans = [
        GitHubAssignmentPlan(
            repo="repo-b",
            target_type="issue",
            target_number=2,
            assignee="user2",
            action="assign",
            reason="Auto-assignment",
            source={"rule": "round_robin"},
        ),
        GitHubAssignmentPlan(
            repo="repo-a",
            target_type="pull_request",
            target_number=1,
            assignee="user1",
            action="request_review",
            reason="Review requested",
            source={"rule": "review_rotation"},
        ),
    ]

    payload = build_audit_payload(discord_plans, github_plans, sample_config)

    assert payload["runtime_mode"] == "dry_run"
    assert payload["org"] == "test-org"
    assert payload["summary"]["discord_role_changes"] == 2
    assert payload["summary"]["github_assignments"] == 2

    # Verify deterministic sorting of discord plans by user_id
    assert [p["discord_user_id"] for p in payload["discord_role_plans"]] == ["100", "200"]

    # Verify deterministic sorting of github plans by repo, target_type, target_number
    assert [p["repo"] for p in payload["github_assignment_plans"]] == ["repo-a", "repo-b"]


def test_render_markdown_report_basic(sample_config: BotConfig) -> None:
    discord_plans = [
        DiscordRolePlan(
            discord_user_id="100",
            role="Contributor",
            action="add",
            reason="Qualified",
            source={"decision_reason": "score_role_rules"},
        )
    ]
    github_plans = [
        GitHubAssignmentPlan(
            repo="repo-a",
            target_type="issue",
            target_number=42,
            assignee="alice",
            action="assign",
            reason="Assigned issue",
            source={"rule": "direct"},
        )
    ]
    summaries = [
        ContributionSummary(
            github_user="alice",
            issues_opened=3,
            prs_opened=2,
            prs_reviewed=0,
            comments=5,
            total_score=25,
            period_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
            period_end=datetime(2026, 1, 31, tzinfo=timezone.utc),
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
            role="Helper",
            action="add",
            reason="Good standing",
            source={"decision_reason": "score_role_rules"},
        )
    ]
    github_plans = [
        GitHubAssignmentPlan(
            repo="core",
            target_type="pull_request",
            target_number=10,
            assignee="bob",
            action="request_review",
            reason="Review assignment",
            source={"rule": "review"},
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


def test_build_and_write_activity_report(sample_config: BotConfig) -> None:
    now = datetime(2026, 2, 1, 12, 0, 0, tzinfo=timezone.utc)
    events = [
        ContributionEvent(
            github_user="alice",
            event_type="pr_opened",
            repo="test-repo",
            created_at=now,
            payload={"pr_number": 10, "title": "Feature PR"},
        ),
        ContributionEvent(
            github_user="bob",
            event_type="pr_merged",
            repo="test-repo",
            created_at=now,
            payload={"pr_number": 9, "title": "Bugfix PR", "difficulty_labels": ["easy"]},
        ),
    ]

    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 2, 15, tzinfo=timezone.utc)

    feed_md = build_activity_feed_markdown(events, start, end, "test-org")
    assert "# Activity Feed (read-only)" in feed_md
    assert "test-repo" in feed_md
    assert "Feature PR" in feed_md
    assert "Bugfix PR" in feed_md

    path, md = write_activity_report(events, start, end, sample_config)
    assert path.exists()
    assert "# Activity Feed (read-only)" in md
