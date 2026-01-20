from ghdcbot.config.models import IdentityMapping, RoleMappingConfig
from ghdcbot.core.models import Score
from ghdcbot.engine.planning import plan_discord_roles, plan_github_assignments


def test_discord_role_plans_deterministic_ordering() -> None:
    member_roles = {"2": ["Contributor"], "1": []}
    identity_mappings = [
        IdentityMapping(github_user="bob", discord_user_id="2"),
        IdentityMapping(github_user="alice", discord_user_id="1"),
    ]
    scores = [
        Score(github_user="alice", period_start=_dt(0), period_end=_dt(1), points=15),
        Score(github_user="bob", period_start=_dt(0), period_end=_dt(1), points=5),
    ]
    role_mappings = [RoleMappingConfig(discord_role="Contributor", min_score=10)]

    first = plan_discord_roles(member_roles, scores, identity_mappings, role_mappings)
    second = plan_discord_roles(member_roles, scores, identity_mappings, role_mappings)

    assert first == second
    assert [plan.discord_user_id for plan in first] == ["1", "2"]


def test_github_assignment_plans_deterministic_ordering() -> None:
    issues = [{"repo": "b", "number": 2}, {"repo": "a", "number": 1}]
    prs = [
        {"repo": "b", "number": 5, "author": "alice"},
        {"repo": "a", "number": 3, "author": "bob"},
    ]
    role_to_github = {"Contributor": ["bob", "alice"]}

    first = plan_github_assignments(
        issues,
        prs,
        role_to_github_users=role_to_github,
        issue_roles=["Contributor"],
        review_roles=["Contributor"],
    )
    second = plan_github_assignments(
        issues,
        prs,
        role_to_github_users=role_to_github,
        issue_roles=["Contributor"],
        review_roles=["Contributor"],
    )

    assert first == second
    assert [plan.repo for plan in first[:2]] == ["a", "b"]


def _dt(days: int):
    from datetime import datetime, timedelta, timezone

    return datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(days=days)
