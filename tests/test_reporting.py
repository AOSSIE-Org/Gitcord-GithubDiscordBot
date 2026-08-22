from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from ghdcbot.config.models import (
    BotConfig,
    DiscordConfig,
    GitHubConfig,
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
def base_config(tmp_path: Path) -> BotConfig:
    return BotConfig(
        runtime=RuntimeConfig(
            mode=RunMode.DRY_RUN,
            data_dir=str(tmp_path),
            github_adapter="ghdcbot.adapters.github.rest:GitHubRestAdapter",
            discord_adapter="ghdcbot.adapters.discord.api:DiscordApiAdapter",
            storage_adapter="ghdcbot.adapters.storage.sqlite:SqliteStorage",
        ),
        github=GitHubConfig(
            org="test-org",
            token="t",
            repos=RepoFilterConfig(mode="allow", names=["repo-a"]),
            api_base="https://api.github.com",
        ),
        discord=DiscordConfig(guild_id="1", token="t"),
    )


def test_build_audit_payload(base_config: BotConfig) -> None:
    d_plans = [
        DiscordRolePlan(
            discord_user_id="u2", role="Maintainer", action="remove", reason="test2", source={}
        ),
        DiscordRolePlan(
            discord_user_id="u1", role="Contributor", action="add", reason="test", source={}
        ),
    ]
    g_plans = [
        GitHubAssignmentPlan(
            repo="repo-b",
            target_type="pull_request",
            target_number=2,
            action="add",
            assignee="bob",
            reason="test2",
            source={},
        ),
        GitHubAssignmentPlan(
            repo="repo-a",
            target_type="issue",
            target_number=1,
            action="add",
            assignee="alice",
            reason="test",
            source={},
        ),
    ]
    payload = build_audit_payload(d_plans, g_plans, base_config)

    assert payload["runtime_mode"] == "dry-run"
    assert payload["org"] == "test-org"
    assert payload["repo_filter"] == {"mode": "allow", "names": ["repo-a"]}
    assert payload["summary"]["discord_role_changes"] == 2
    assert payload["summary"]["github_assignments"] == 2
    
    # Assert deterministic sorting for plans
    assert len(payload["discord_role_plans"]) == 2
    assert payload["discord_role_plans"][0]["discord_user_id"] == "u1"
    assert payload["discord_role_plans"][1]["discord_user_id"] == "u2"

    assert len(payload["github_assignment_plans"]) == 2
    assert payload["github_assignment_plans"][0]["repo"] == "repo-a"
    assert payload["github_assignment_plans"][1]["repo"] == "repo-b"


def test_render_markdown_report_empty(base_config: BotConfig) -> None:
    md = render_markdown_report(
        [], [], base_config, repo_count=0, contribution_summaries=[]
    )
    assert "## Summary" in md
    assert "Repositories discovered: 0" in md
    assert "No activity in period." in md
    assert "No Discord role changes planned." in md
    assert "No GitHub issue assignments planned." in md
    assert "No GitHub PR review assignments planned." in md


def test_render_markdown_report_with_data(base_config: BotConfig) -> None:
    d_plans = [
        DiscordRolePlan(
            discord_user_id="u1",
            role="Contributor",
            action="add",
            reason="test",
            source={"score": 10},
        )
    ]
    g_plans = [
        GitHubAssignmentPlan(
            repo="repo-a",
            target_type="issue",
            target_number=1,
            action="add",
            assignee="alice",
            reason="test",
            source={},
        ),
        GitHubAssignmentPlan(
            repo="repo-a",
            target_type="pull_request",
            target_number=2,
            action="add",
            assignee="bob",
            reason="test",
            source={},
        ),
    ]
    summaries = [
        ContributionSummary(
            github_user="alice",
            issues_opened=1,
            prs_opened=2,
            prs_reviewed=0,
            comments=5,
            total_score=10,
            period_start=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
            period_end=datetime(2026, 1, 30, 0, 0, 0, tzinfo=UTC),
        )
    ]
    md = render_markdown_report(
        d_plans, g_plans, base_config, repo_count=1, contribution_summaries=summaries
    )

    assert "`add` `Contributor` for `u1`" in md
    assert "`add` `alice` to `repo-a#1`" in md
    assert "`add` `bob` on `repo-a#2`" in md
    assert "| alice | 1 | 2 | 0 | 5 |" in md


def test_write_reports(base_config: BotConfig, tmp_path: Path) -> None:
    json_path, md_path = write_reports([], [], base_config)
    assert json_path == tmp_path / "reports" / "audit.json"
    assert md_path == tmp_path / "reports" / "audit.md"
    assert json_path.exists()
    assert md_path.exists()


def test_build_activity_feed_markdown() -> None:
    t1 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
    t2 = datetime(2026, 1, 1, 11, 0, 0, tzinfo=UTC)

    events = [
        ContributionEvent(
            repo="repo-a",
            event_type="pr_opened",
            github_user="alice",
            created_at=t1,
            payload={"pr_number": 1, "title": "Add feature"},
        ),
        ContributionEvent(
            repo="repo-a",
            event_type="issue_comment",  # Should be ignored
            github_user="bob",
            created_at=t1,
            payload={"issue_number": 2},
        ),
        ContributionEvent(
            repo="repo-b",
            event_type="issue_closed",
            github_user="alice",
            created_at=t2,
            payload={"issue_number": 3, "title": "Fix bug"},
        ),
        ContributionEvent(
            repo="repo-c",
            event_type="pr_merged",
            github_user="charlie",
            created_at=t1,
            payload={"pr_number": 4, "title": "Merge feature", "difficulty_labels": ["hard"]},
        )
    ]

    start = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
    end = datetime(2026, 1, 2, 0, 0, 0, tzinfo=UTC)

    md = build_activity_feed_markdown(events, start, end, "test-org")

    assert "## repo-a" in md
    assert "### PRs opened (1)" in md
    assert "Add feature" in md
    assert "issue_comment" not in md  # Ignored event type
    
    assert "## repo-b" in md
    assert "### Issues closed (1)" in md
    assert "Fix bug" in md

    assert "## repo-c" in md
    assert "### PRs merged (1)" in md
    assert "Merge feature" in md
    assert "Labels: hard" in md


def test_build_activity_feed_markdown_empty() -> None:
    start = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
    end = datetime(2026, 1, 2, 0, 0, 0, tzinfo=UTC)
    md = build_activity_feed_markdown([], start, end, "test-org")
    assert "No PR or issue activity in this period." in md


def test_write_activity_report(base_config: BotConfig, tmp_path: Path) -> None:
    start = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
    end = datetime(2026, 1, 2, 0, 0, 0, tzinfo=UTC)

    path, _ = write_activity_report([], start, end, base_config)

    assert path == tmp_path / "reports" / "activity.md"
    assert path.exists()
    assert "No PR or issue activity" in path.read_text()
