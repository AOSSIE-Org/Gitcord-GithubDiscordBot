from ghdcbot.config.models import IdentityMapping, RoleMappingConfig
from ghdcbot.core.models import Score
from ghdcbot.engine.assignment import RoleBasedAssignmentStrategy
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


def test_role_based_assignment_strategy_sorts_before_round_robin() -> None:
    """Regression for the determinism gap in RoleBasedAssignmentStrategy.

    The orchestrator uses this class directly, not the planning helper. GitHub's
    default issue listing is newest-first, so a freshly opened issue shifts every
    pre-existing issue by one slot in the stream. Without a stable sort inside
    the strategy, the round-robin assignment for an unchanged issue flips on
    every new issue in the repo. Fix: sort inputs by (repo, number) before the
    rotation, mirroring ``engine.planning._stable_issue_key`` / ``_stable_pr_key``.
    """
    strategy = RoleBasedAssignmentStrategy(
        {"Contributor": ["alice", "bob"]},
        issue_roles=["Contributor"],
        review_roles=["Contributor"],
    )

    initial_issues = [
        {"repo": "a", "number": 10, "assignees": []},
        {"repo": "a", "number": 9, "assignees": []},
        {"repo": "a", "number": 8, "assignees": []},
    ]
    later_issues = [
        {"repo": "a", "number": 11, "assignees": []},
        *initial_issues,
    ]

    plan_a = strategy.plan_issue_assignments(initial_issues, [])
    plan_b = strategy.plan_issue_assignments(later_issues, [])

    # Baseline: both plans cover every input issue.
    assert [p.issue_number for p in plan_a] == [8, 9, 10]
    assert [p.issue_number for p in plan_b] == [8, 9, 10, 11]

    # Core invariant: an existing issue's assignee must not drift just because
    # a newer issue showed up in the stream.
    plan_b_by_number = {p.issue_number: p.assignee for p in plan_b}
    for plan in plan_a:
        assert plan_b_by_number[plan.issue_number] == plan.assignee, (
            f"issue #{plan.issue_number} drifted from {plan.assignee} to "
            f"{plan_b_by_number[plan.issue_number]} after a new issue was opened"
        )

    # Same determinism must hold for review requests.
    initial_prs = [
        {"repo": "a", "number": 20},
        {"repo": "a", "number": 19},
    ]
    later_prs = [{"repo": "a", "number": 21}, *initial_prs]

    review_a = strategy.plan_review_requests(initial_prs, [])
    review_b = strategy.plan_review_requests(later_prs, [])

    review_b_by_number = {p.pr_number: p.reviewer for p in review_b}
    for plan in review_a:
        assert review_b_by_number[plan.pr_number] == plan.reviewer, (
            f"PR #{plan.pr_number} reviewer drifted from {plan.reviewer} to "
            f"{review_b_by_number[plan.pr_number]} after a new PR was opened"
        )


def _dt(days: int):
    from datetime import datetime, timedelta, timezone

    return datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(days=days)
