from ghdcbot.adapters.github.writer import GitHubPlanWriter
from ghdcbot.core.models import GitHubAssignmentPlan
from ghdcbot.core.modes import MutationPolicy, RunMode
from ghdcbot.engine.planning import plan_github_assignments


class _Response:
    status_code = 201


class _RecordingClient:
    def __init__(self) -> None:
        self.posts: list[tuple[str, dict]] = []

    def post(self, path: str, json: dict) -> _Response:
        self.posts.append((path, json))
        return _Response()

    def close(self) -> None:
        pass


def _active_policy() -> MutationPolicy:
    return MutationPolicy(mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True)


def _review_plan(assignee: str, author: str) -> GitHubAssignmentPlan:
    return GitHubAssignmentPlan(
        repo="repo",
        target_number=7,
        target_type="pull_request",
        assignee=assignee,
        action="request_review",
        reason="Role-based review assignment",
        source={"eligible_role_users": [assignee], "author": author},
    )


def test_review_planning_skips_author_with_different_case() -> None:
    plans = plan_github_assignments(
        issues=[],
        pull_requests=[{"repo": "repo", "number": 7, "author": "Alice"}],
        role_to_github_users={"reviewer": ["alice", "bob"]},
        issue_roles=[],
        review_roles=["reviewer"],
    )

    assert [plan.assignee for plan in plans] == ["bob"]


def test_review_planning_skips_pr_when_only_candidate_is_author() -> None:
    plans = plan_github_assignments(
        issues=[],
        pull_requests=[{"repo": "repo", "number": 7, "author": "Alice"}],
        role_to_github_users={"reviewer": ["alice"]},
        issue_roles=[],
        review_roles=["reviewer"],
    )

    assert plans == []


def test_writer_does_not_request_review_from_author_with_different_case() -> None:
    writer = GitHubPlanWriter(token="t", org="o", api_base="https://api.github.com")
    client = _RecordingClient()
    writer._client = client

    writer.apply_plans([_review_plan("alice", "Alice")], _active_policy())

    assert client.posts == []


def test_writer_dedupes_review_requests_case_insensitively() -> None:
    writer = GitHubPlanWriter(token="t", org="o", api_base="https://api.github.com")
    client = _RecordingClient()
    writer._client = client

    writer.apply_plans([_review_plan("Bob", "alice"), _review_plan("bob", "alice")], _active_policy())

    assert client.posts == [("/repos/o/repo/pulls/7/requested_reviewers", {"reviewers": ["Bob"]})]
