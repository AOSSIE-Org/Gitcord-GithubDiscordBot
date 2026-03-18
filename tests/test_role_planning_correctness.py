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


def _dt(days: int):
    from datetime import datetime, timedelta, timezone

    return datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(days=days)
