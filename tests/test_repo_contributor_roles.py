"""Tests for repo-contributor role assignment (PR merged in project X -> role Contributor-X)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from ghdcbot.adapters.storage.sqlite import SqliteStorage
from ghdcbot.config.models import IdentityMapping, RoleMappingConfig
from ghdcbot.core.models import ContributionEvent, Score
from ghdcbot.engine.planning import plan_discord_roles, repos_with_merged_pr_per_user


def test_repo_contributor_roles_disabled_no_behavior_change(tmp_path) -> None:
    """When repo_contributor_roles is not set, no repo-based roles are planned."""
    storage = SqliteStorage(str(tmp_path))
    storage.init_schema()
    period_end = datetime.now(timezone.utc)
    period_start = period_end - timedelta(days=30)
    identity_mappings = [IdentityMapping(github_user="alice", discord_user_id="123")]
    scores = [
        Score(
            github_user="alice",
            period_start=period_start,
            period_end=period_end,
            points=0,
        ),
    ]
    storage.record_contributions([
        ContributionEvent(
            github_user="alice",
            event_type="pr_merged",
            repo="frontend-app",
            created_at=period_end - timedelta(days=1),
            payload={"pr_number": 1},
        ),
    ])
    plans = plan_discord_roles(
        member_roles={},
        scores=scores,
        identity_mappings=identity_mappings,
        role_mappings=[RoleMappingConfig(discord_role="Contributor", min_score=10)],
        storage=storage,
        period_start=period_start,
        period_end=period_end,
        merge_role_rules=None,
        repo_contributor_roles=None,
    )
    # No score-based role (0 points), no repo-contributor (config None) -> no adds
    assert len(plans) == 0


def test_repo_contributor_roles_grants_role_when_merged_in_repo(tmp_path) -> None:
    """When user has merged PR in a repo and config maps that repo to a role, plan add."""
    storage = SqliteStorage(str(tmp_path))
    storage.init_schema()
    period_end = datetime.now(timezone.utc)
    period_start = period_end - timedelta(days=30)
    identity_mappings = [IdentityMapping(github_user="alice", discord_user_id="123")]
    scores = [
        Score(
            github_user="alice",
            period_start=period_start,
            period_end=period_end,
            points=0,
        ),
    ]
    storage.record_contributions([
        ContributionEvent(
            github_user="alice",
            event_type="pr_merged",
            repo="frontend-app",
            created_at=period_end - timedelta(days=1),
            payload={"pr_number": 1},
        ),
    ])
    repo_contributor_roles = {"frontend-app": "Contributor-Frontend"}
    plans = plan_discord_roles(
        member_roles={},
        scores=scores,
        identity_mappings=identity_mappings,
        role_mappings=[RoleMappingConfig(discord_role="Contributor", min_score=10)],
        storage=storage,
        period_start=period_start,
        period_end=period_end,
        merge_role_rules=None,
        repo_contributor_roles=repo_contributor_roles,
    )
    assert len(plans) == 1
    assert plans[0].action == "add"
    assert plans[0].role == "Contributor-Frontend"
    assert plans[0].discord_user_id == "123"
    assert plans[0].source.get("decision_reason") == "repo_contributor_roles"
    assert plans[0].source.get("repo") == "frontend-app"
    assert "frontend-app" in plans[0].reason and "Contributor-Frontend" in plans[0].reason


def test_repo_contributor_roles_multiple_repos_multiple_roles(tmp_path) -> None:
    """User with merged PRs in two repos gets both roles."""
    storage = SqliteStorage(str(tmp_path))
    storage.init_schema()
    period_end = datetime.now(timezone.utc)
    period_start = period_end - timedelta(days=30)
    identity_mappings = [IdentityMapping(github_user="bob", discord_user_id="456")]
    scores = [
        Score(
            github_user="bob",
            period_start=period_start,
            period_end=period_end,
            points=0,
        ),
    ]
    storage.record_contributions([
        ContributionEvent(
            github_user="bob",
            event_type="pr_merged",
            repo="frontend-app",
            created_at=period_end - timedelta(days=5),
            payload={"pr_number": 1},
        ),
        ContributionEvent(
            github_user="bob",
            event_type="pr_merged",
            repo="backend-api",
            created_at=period_end - timedelta(days=3),
            payload={"pr_number": 2},
        ),
    ])
    repo_contributor_roles = {
        "frontend-app": "Contributor-Frontend",
        "backend-api": "Contributor-Backend",
    }
    plans = plan_discord_roles(
        member_roles={},
        scores=scores,
        identity_mappings=identity_mappings,
        role_mappings=[RoleMappingConfig(discord_role="Contributor", min_score=10)],
        storage=storage,
        period_start=period_start,
        period_end=period_end,
        merge_role_rules=None,
        repo_contributor_roles=repo_contributor_roles,
    )
    roles_added = {p.role for p in plans if p.action == "add"}
    assert roles_added == {"Contributor-Frontend", "Contributor-Backend"}


def test_repo_contributor_roles_not_removed(tmp_path) -> None:
    """Repo-contributor roles are never planned for removal (only score-based can be removed)."""
    storage = SqliteStorage(str(tmp_path))
    storage.init_schema()
    period_end = datetime.now(timezone.utc)
    period_start = period_end - timedelta(days=30)
    identity_mappings = [IdentityMapping(github_user="carol", discord_user_id="789")]
    scores = [
        Score(
            github_user="carol",
            period_start=period_start,
            period_end=period_end,
            points=5,
        ),
    ]
    storage.record_contributions([
        ContributionEvent(
            github_user="carol",
            event_type="pr_merged",
            repo="docs",
            created_at=period_end - timedelta(days=1),
            payload={"pr_number": 1},
        ),
    ])
    # Carol has Contributor (score 5 < 10) and Contributor-Docs (repo). Score-based Contributor
    # would be removed, but she already has Contributor-Docs from repo. We only remove score-based.
    # So we should have one remove (Contributor) and no remove for Contributor-Docs.
    repo_contributor_roles = {"docs": "Contributor-Docs"}
    role_mappings = [
        RoleMappingConfig(discord_role="Contributor", min_score=10),
        RoleMappingConfig(discord_role="Maintainer", min_score=40),
    ]
    plans = plan_discord_roles(
        member_roles={"789": ["Contributor", "Contributor-Docs"]},
        scores=scores,
        identity_mappings=identity_mappings,
        role_mappings=role_mappings,
        storage=storage,
        period_start=period_start,
        period_end=period_end,
        merge_role_rules=None,
        repo_contributor_roles=repo_contributor_roles,
    )
    remove_plans = [p for p in plans if p.action == "remove"]
    add_plans = [p for p in plans if p.action == "add"]
    # Contributor (score-based) should be removed (5 < 10)
    assert any(p.role == "Contributor" for p in remove_plans)
    # Contributor-Docs must never be removed
    assert not any(p.role == "Contributor-Docs" for p in remove_plans)
    # No new adds (she already has both roles in member_roles)
    assert len(add_plans) == 0


def test_repos_with_merged_pr_per_user_all_time(tmp_path) -> None:
    """repos_with_merged_pr_per_user returns repos from all stored contributions."""
    storage = SqliteStorage(str(tmp_path))
    storage.init_schema()
    identity_mappings = [
        IdentityMapping(github_user="alice", discord_user_id="1"),
        IdentityMapping(github_user="bob", discord_user_id="2"),
    ]
    # Old event (would be outside a 30-day window)
    old = datetime(2020, 6, 1, tzinfo=timezone.utc)
    storage.record_contributions([
        ContributionEvent(
            github_user="alice",
            event_type="pr_merged",
            repo="legacy-repo",
            created_at=old,
            payload={"pr_number": 1},
        ),
        ContributionEvent(
            github_user="bob",
            event_type="pr_merged",
            repo="new-repo",
            created_at=datetime.now(timezone.utc) - timedelta(days=1),
            payload={"pr_number": 1},
        ),
    ])
    result = repos_with_merged_pr_per_user(storage, identity_mappings)
    assert result.get("alice") == {"legacy-repo"}
    assert result.get("bob") == {"new-repo"}
