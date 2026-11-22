from ghdcbot.config.models import IdentityMapping, RoleMappingConfig
from ghdcbot.core.models import Score
from ghdcbot.engine.planning import plan_discord_roles


def test_role_planning_add_remove_noop() -> None:
    member_roles = {"1": ["Contributor"], "2": ["Contributor"], "3": ["Mentor"]}
    identity_mappings = [
        IdentityMapping(github_user="alice", discord_user_id="1"),
        IdentityMapping(github_user="bob", discord_user_id="2"),
        IdentityMapping(github_user="carol", discord_user_id="3"),
    ]
    scores = [
        Score(github_user="alice", period_start=_dt(0), period_end=_dt(1), points=5),
        Score(github_user="bob", period_start=_dt(0), period_end=_dt(1), points=15),
        Score(github_user="carol", period_start=_dt(0), period_end=_dt(1), points=60),
    ]
    role_mappings = [
        RoleMappingConfig(discord_role="Contributor", min_score=10),
        RoleMappingConfig(discord_role="Mentor", min_score=50),
    ]

    plans = plan_discord_roles(member_roles, scores, identity_mappings, role_mappings)

    assert any(plan.discord_user_id == "1" and plan.action == "remove" for plan in plans)
    assert not any(plan.discord_user_id == "2" for plan in plans)
    assert any(plan.discord_user_id == "3" and plan.action == "add" for plan in plans)


def test_role_planning_case_insensitive_score_lookup() -> None:
    """GitHub username casing in score records must match identity mappings case-insensitively."""
    member_roles = {"1001": ["Contributor"]}
    identity_mappings = [
        IdentityMapping(github_user="Alex", discord_user_id="1001"),
    ]
    scores = [
        Score(github_user="alex", period_start=_dt(0), period_end=_dt(1), points=500),
    ]
    role_mappings = [
        RoleMappingConfig(discord_role="Contributor", min_score=100),
    ]

    plans = plan_discord_roles(member_roles, scores, identity_mappings, role_mappings)

    # 500 >= 100, so user must retain Contributor; no remove plan should be emitted
    assert not any(plan.discord_user_id == "1001" and plan.action == "remove" for plan in plans)
    assert len(plans) == 0


def test_role_planning_case_insensitive_score_promotion() -> None:
    """Case-insensitive score matching must grant promotion when points meet threshold."""
    member_roles = {"1001": []}
    identity_mappings = [
        IdentityMapping(github_user="alex", discord_user_id="1001"),
    ]
    scores = [
        Score(github_user="ALEX", period_start=_dt(0), period_end=_dt(1), points=250),
    ]
    role_mappings = [
        RoleMappingConfig(discord_role="Contributor", min_score=100),
    ]

    plans = plan_discord_roles(member_roles, scores, identity_mappings, role_mappings)

    assert any(
        plan.discord_user_id == "1001"
        and plan.action == "add"
        and plan.role == "Contributor"
        and plan.source.get("score") == 250
        for plan in plans
    )


def test_apply_discord_roles_case_insensitive_score_lookup() -> None:
    """apply_discord_roles must not revoke roles when username casing differs."""
    from unittest.mock import MagicMock

    from ghdcbot.core.modes import MutationPolicy, RunMode
    from ghdcbot.engine.orchestrator import apply_discord_roles

    member_roles = {"1001": ["Contributor"]}
    identity_mappings = [
        IdentityMapping(github_user="Alex", discord_user_id="1001"),
    ]
    scores = [
        Score(github_user="alex", period_start=_dt(0), period_end=_dt(1), points=500),
    ]
    role_mappings = [
        RoleMappingConfig(discord_role="Contributor", min_score=100),
    ]
    policy = MutationPolicy(mode=RunMode.ACTIVE, discord_write_allowed=True, github_write_allowed=False)
    writer = MagicMock()

    apply_discord_roles(
        discord_writer=writer,
        member_roles=member_roles,
        scores=scores,
        identity_mappings=identity_mappings,
        role_mappings=role_mappings,
        policy=policy,
    )

    writer.remove_role.assert_not_called()
    writer.add_role.assert_not_called()


def _dt(days: int):
    from datetime import datetime, timedelta, timezone

    return datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(days=days)
