"""Tests for merge-based role assignment feature."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from ghdcbot.adapters.storage.sqlite import SqliteStorage
from ghdcbot.config.models import (
    BotConfig,
    IdentityMapping,
    MergeRoleRuleConfig,
    MergeRoleRulesConfig,
    RoleMappingConfig,
    ScoringConfig,
)
from ghdcbot.core.models import ContributionEvent, Score
from ghdcbot.engine.planning import count_merged_prs_per_user, plan_discord_roles


def test_feature_disabled_no_behavior_change(tmp_path) -> None:
    """Feature disabled → no behavior change."""
    storage = SqliteStorage(str(tmp_path))
    storage.init_schema()
    
    period_end = datetime.now(timezone.utc)
    period_start = period_end - timedelta(days=30)
    
    identity_mappings = [
        IdentityMapping(github_user="alice", discord_user_id="123"),
    ]
    
    scores = [
        Score(
            github_user="alice",
            period_start=period_start,
            period_end=period_end,
            points=10,
        ),
    ]
    
    # Add merged PR events
    events = [
        ContributionEvent(
            github_user="alice",
            event_type="pr_merged",
            repo="test",
            created_at=period_end - timedelta(days=1),
            payload={"pr_number": 1},
        ),
    ]
    storage.record_contributions(events)
    
    plans = plan_discord_roles(
        member_roles={},
        scores=scores,
        identity_mappings=identity_mappings,
        role_mappings=[
            RoleMappingConfig(discord_role="Contributor", min_score=10),
        ],
        storage=storage,
        period_start=period_start,
        period_end=period_end,
        merge_role_rules=None,  # Feature disabled
    )
    
    # Should only have score-based role
    assert len(plans) == 1
    assert plans[0].action == "add"
    assert plans[0].role == "Contributor"
    assert plans[0].source.get("decision_reason") == "score_role_rules"


def test_merge_role_rules_enabled_but_no_rules(tmp_path) -> None:
    """Feature enabled but no rules → no merge-based roles."""
    storage = SqliteStorage(str(tmp_path))
    storage.init_schema()
    
    period_end = datetime.now(timezone.utc)
    period_start = period_end - timedelta(days=30)
    
    identity_mappings = [
        IdentityMapping(github_user="alice", discord_user_id="123"),
    ]
    
    scores = [
        Score(
            github_user="alice",
            period_start=period_start,
            period_end=period_end,
            points=0,
        ),
    ]
    
    events = [
        ContributionEvent(
            github_user="alice",
            event_type="pr_merged",
            repo="test",
            created_at=period_end - timedelta(days=1),
            payload={"pr_number": 1},
        ),
    ]
    storage.record_contributions(events)
    
    merge_rules = MergeRoleRulesConfig(enabled=True, rules=[])
    
    plans = plan_discord_roles(
        member_roles={},
        scores=scores,
        identity_mappings=identity_mappings,
        role_mappings=[],
        storage=storage,
        period_start=period_start,
        period_end=period_end,
        merge_role_rules=merge_rules,
    )
    
    # No plans since no rules configured
    assert len(plans) == 0


def test_merge_based_role_assigned(tmp_path) -> None:
    """Merge-based role assigned when threshold met."""
    storage = SqliteStorage(str(tmp_path))
    storage.init_schema()
    
    period_end = datetime.now(timezone.utc)
    period_start = period_end - timedelta(days=30)
    
    identity_mappings = [
        IdentityMapping(github_user="alice", discord_user_id="123"),
    ]
    
    scores = [
        Score(
            github_user="alice",
            period_start=period_start,
            period_end=period_end,
            points=0,  # No score-based role
        ),
    ]
    
    # Add 5 merged PRs
    events = [
        ContributionEvent(
            github_user="alice",
            event_type="pr_merged",
            repo="test",
            created_at=period_end - timedelta(days=i),
            payload={"pr_number": i},
        )
        for i in range(1, 6)
    ]
    storage.record_contributions(events)
    
    merge_rules = MergeRoleRulesConfig(
        enabled=True,
        rules=[
            MergeRoleRuleConfig(discord_role="Contributor", min_merged_prs=3),
        ],
    )
    
    plans = plan_discord_roles(
        member_roles={},
        scores=scores,
        identity_mappings=identity_mappings,
        role_mappings=[],
        storage=storage,
        period_start=period_start,
        period_end=period_end,
        merge_role_rules=merge_rules,
    )
    
    assert len(plans) == 1
    assert plans[0].action == "add"
    assert plans[0].role == "Contributor"
    assert plans[0].source.get("decision_reason") == "merge_role_rules"
    assert plans[0].source.get("merged_pr_count") == 5
    assert plans[0].source.get("merge_threshold") == 3


def test_highest_eligible_role_selected(tmp_path) -> None:
    """Only highest eligible role is assigned (not all eligible roles)."""
    storage = SqliteStorage(str(tmp_path))
    storage.init_schema()
    
    period_end = datetime.now(timezone.utc)
    period_start = period_end - timedelta(days=30)
    
    identity_mappings = [
        IdentityMapping(github_user="alice", discord_user_id="123"),
    ]
    
    scores = [
        Score(
            github_user="alice",
            period_start=period_start,
            period_end=period_end,
            points=0,
        ),
    ]
    
    # Add 10 merged PRs (meets all thresholds)
    events = [
        ContributionEvent(
            github_user="alice",
            event_type="pr_merged",
            repo="test",
            created_at=period_end - timedelta(days=i),
            payload={"pr_number": i},
        )
        for i in range(1, 11)
    ]
    storage.record_contributions(events)
    
    merge_rules = MergeRoleRulesConfig(
        enabled=True,
        rules=[
            MergeRoleRuleConfig(discord_role="Contributor", min_merged_prs=3),
            MergeRoleRuleConfig(discord_role="Maintainer", min_merged_prs=5),
            MergeRoleRuleConfig(discord_role="Senior", min_merged_prs=10),
        ],
    )
    
    plans = plan_discord_roles(
        member_roles={},
        scores=scores,
        identity_mappings=identity_mappings,
        role_mappings=[],
        storage=storage,
        period_start=period_start,
        period_end=period_end,
        merge_role_rules=merge_rules,
    )
    
    # Should only get highest role (Senior), not all three
    assert len(plans) == 1
    assert plans[0].role == "Senior"
    assert plans[0].source.get("merged_pr_count") == 10


def test_only_verified_users_counted(tmp_path) -> None:
    """Only verified users' merged PRs are counted."""
    storage = SqliteStorage(str(tmp_path))
    storage.init_schema()
    
    period_end = datetime.now(timezone.utc)
    period_start = period_end - timedelta(days=30)
    
    # Only alice is verified
    identity_mappings = [
        IdentityMapping(github_user="alice", discord_user_id="123"),
    ]
    
    scores = [
        Score(
            github_user="alice",
            period_start=period_start,
            period_end=period_end,
            points=0,
        ),
    ]
    
    # Add merged PRs for both verified and unverified users
    events = [
        ContributionEvent(
            github_user="alice",
            event_type="pr_merged",
            repo="test",
            created_at=period_end - timedelta(days=1),
            payload={"pr_number": 1},
        ),
        ContributionEvent(
            github_user="bob",  # Not verified
            event_type="pr_merged",
            repo="test",
            created_at=period_end - timedelta(days=1),
            payload={"pr_number": 2},
        ),
    ]
    storage.record_contributions(events)
    
    merge_rules = MergeRoleRulesConfig(
        enabled=True,
        rules=[
            MergeRoleRuleConfig(discord_role="Contributor", min_merged_prs=1),
        ],
    )
    
    plans = plan_discord_roles(
        member_roles={},
        scores=scores,
        identity_mappings=identity_mappings,
        role_mappings=[],
        storage=storage,
        period_start=period_start,
        period_end=period_end,
        merge_role_rules=merge_rules,
    )
    
    # Only alice should get a role
    assert len(plans) == 1
    assert plans[0].source.get("github_user") == "alice"


def test_promotion_only_no_role_removal(tmp_path) -> None:
    """Merge-based roles are promotion-only (never removed)."""
    storage = SqliteStorage(str(tmp_path))
    storage.init_schema()
    
    period_end = datetime.now(timezone.utc)
    period_start = period_end - timedelta(days=30)
    
    identity_mappings = [
        IdentityMapping(github_user="alice", discord_user_id="123"),
    ]
    
    scores = [
        Score(
            github_user="alice",
            period_start=period_start,
            period_end=period_end,
            points=0,  # No score-based role
        ),
    ]
    
    # User already has the role
    member_roles = {"123": ["Contributor"]}
    
    # No merged PRs in this period (below threshold)
    merge_rules = MergeRoleRulesConfig(
        enabled=True,
        rules=[
            MergeRoleRuleConfig(discord_role="Contributor", min_merged_prs=5),
        ],
    )
    
    plans = plan_discord_roles(
        member_roles=member_roles,
        scores=scores,
        identity_mappings=identity_mappings,
        role_mappings=[],
        storage=storage,
        period_start=period_start,
        period_end=period_end,
        merge_role_rules=merge_rules,
    )
    
    # No removal plans (promotion-only)
    assert len(plans) == 0


def test_max_score_and_merge_roles(tmp_path) -> None:
    """Final role is max(score_based_role, merge_based_role)."""
    storage = SqliteStorage(str(tmp_path))
    storage.init_schema()
    
    period_end = datetime.now(timezone.utc)
    period_start = period_end - timedelta(days=30)
    
    identity_mappings = [
        IdentityMapping(github_user="alice", discord_user_id="123"),
    ]
    
    scores = [
        Score(
            github_user="alice",
            period_start=period_start,
            period_end=period_end,
            points=10,  # Gets Contributor from score
        ),
    ]
    
    # Add 5 merged PRs (gets Maintainer from merge)
    events = [
        ContributionEvent(
            github_user="alice",
            event_type="pr_merged",
            repo="test",
            created_at=period_end - timedelta(days=i),
            payload={"pr_number": i},
        )
        for i in range(1, 6)
    ]
    storage.record_contributions(events)
    
    merge_rules = MergeRoleRulesConfig(
        enabled=True,
        rules=[
            MergeRoleRuleConfig(discord_role="Maintainer", min_merged_prs=5),
        ],
    )
    
    plans = plan_discord_roles(
        member_roles={},
        scores=scores,
        identity_mappings=identity_mappings,
        role_mappings=[
            RoleMappingConfig(discord_role="Contributor", min_score=10),
        ],
        storage=storage,
        period_start=period_start,
        period_end=period_end,
        merge_role_rules=merge_rules,
    )
    
    # Should get both roles (union)
    roles = {plan.role for plan in plans}
    assert "Contributor" in roles
    assert "Maintainer" in roles
    
    # Check decision reasons
    contributor_plan = next(p for p in plans if p.role == "Contributor")
    maintainer_plan = next(p for p in plans if p.role == "Maintainer")
    assert contributor_plan.source.get("decision_reason") == "score_role_rules"
    assert maintainer_plan.source.get("decision_reason") == "merge_role_rules"


def test_deterministic_output(tmp_path) -> None:
    """Same inputs produce same outputs (order-independent)."""
    storage = SqliteStorage(str(tmp_path))
    storage.init_schema()
    
    period_end = datetime.now(timezone.utc)
    period_start = period_end - timedelta(days=30)
    
    identity_mappings = [
        IdentityMapping(github_user="alice", discord_user_id="123"),
        IdentityMapping(github_user="bob", discord_user_id="456"),
    ]
    
    scores = [
        Score(
            github_user="alice",
            period_start=period_start,
            period_end=period_end,
            points=0,
        ),
        Score(
            github_user="bob",
            period_start=period_start,
            period_end=period_end,
            points=0,
        ),
    ]
    
    events = [
        ContributionEvent(
            github_user="alice",
            event_type="pr_merged",
            repo="test",
            created_at=period_end - timedelta(days=1),
            payload={"pr_number": 1},
        ),
        ContributionEvent(
            github_user="bob",
            event_type="pr_merged",
            repo="test",
            created_at=period_end - timedelta(days=2),
            payload={"pr_number": 2},
        ),
    ]
    storage.record_contributions(events)
    
    merge_rules = MergeRoleRulesConfig(
        enabled=True,
        rules=[
            MergeRoleRuleConfig(discord_role="Contributor", min_merged_prs=1),
        ],
    )
    
    plans1 = plan_discord_roles(
        member_roles={},
        scores=scores,
        identity_mappings=identity_mappings,
        role_mappings=[],
        storage=storage,
        period_start=period_start,
        period_end=period_end,
        merge_role_rules=merge_rules,
    )
    
    # Reverse order
    plans2 = plan_discord_roles(
        member_roles={},
        scores=list(reversed(scores)),
        identity_mappings=list(reversed(identity_mappings)),
        role_mappings=[],
        storage=storage,
        period_start=period_start,
        period_end=period_end,
        merge_role_rules=merge_rules,
    )
    
    # Should produce same plans (sorted deterministically)
    assert len(plans1) == len(plans2) == 2
    plans1_dict = {(p.discord_user_id, p.role): p for p in plans1}
    plans2_dict = {(p.discord_user_id, p.role): p for p in plans2}
    assert plans1_dict == plans2_dict


def test_count_merged_prs_per_user(tmp_path) -> None:
    """Test merged PR counting function."""
    storage = SqliteStorage(str(tmp_path))
    storage.init_schema()
    
    period_end = datetime.now(timezone.utc)
    period_start = period_end - timedelta(days=30)
    
    identity_mappings = [
        IdentityMapping(github_user="alice", discord_user_id="123"),
        IdentityMapping(github_user="bob", discord_user_id="456"),
    ]
    
    events = [
        ContributionEvent(
            github_user="alice",
            event_type="pr_merged",
            repo="test",
            created_at=period_end - timedelta(days=1),
            payload={"pr_number": 1},
        ),
        ContributionEvent(
            github_user="alice",
            event_type="pr_merged",
            repo="test",
            created_at=period_end - timedelta(days=2),
            payload={"pr_number": 2},
        ),
        ContributionEvent(
            github_user="bob",
            event_type="pr_merged",
            repo="test",
            created_at=period_end - timedelta(days=3),
            payload={"pr_number": 3},
        ),
        # Outside period
        ContributionEvent(
            github_user="alice",
            event_type="pr_merged",
            repo="test",
            created_at=period_start - timedelta(days=1),
            payload={"pr_number": 4},
        ),
    ]
    storage.record_contributions(events)
    
    counts = count_merged_prs_per_user(
        storage, identity_mappings, period_start, period_end
    )
    
    assert counts["alice"] == 2
    assert counts["bob"] == 1


def test_merge_role_rules_config_validation() -> None:
    """Test config validation."""
    # Valid config
    rules = MergeRoleRulesConfig(
        enabled=True,
        rules=[
            MergeRoleRuleConfig(discord_role="Contributor", min_merged_prs=3),
            MergeRoleRuleConfig(discord_role="Maintainer", min_merged_prs=5),
        ],
    )
    assert rules.enabled is True
    assert len(rules.rules) == 2
    # Rules should be sorted by threshold
    assert rules.rules[0].min_merged_prs == 3
    assert rules.rules[1].min_merged_prs == 5
    
    # Empty rules
    empty_rules = MergeRoleRulesConfig(enabled=True, rules=[])
    assert empty_rules.rules == []
    
    # Disabled
    disabled_rules = MergeRoleRulesConfig(enabled=False, rules=[])
    assert disabled_rules.enabled is False
