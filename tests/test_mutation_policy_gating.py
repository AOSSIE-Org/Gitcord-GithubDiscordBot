import logging

import pytest

from ghdcbot.adapters.discord.writer import DiscordPlanWriter
from ghdcbot.adapters.github.writer import GitHubPlanWriter
from ghdcbot.core.models import DiscordRolePlan, GitHubAssignmentPlan
from ghdcbot.core.modes import MutationPolicy, RunMode


class _FailingClient:
    def request(self, *_args, **_kwargs):
        raise AssertionError("HTTP call should not occur in gated modes")

    def post(self, *_args, **_kwargs):
        raise AssertionError("HTTP call should not occur in gated modes")


@pytest.mark.parametrize(
    "mode,allow_discord,allow_github,expected",
    [
        (RunMode.DRY_RUN, True, True, "skipped (dry-run)"),
        (RunMode.OBSERVER, True, True, "skipped (observer mode)"),
        (RunMode.ACTIVE, False, False, "skipped (write disabled)"),
    ],
)
def test_writers_skip_actions_when_gated(mode, allow_discord, allow_github, expected, caplog) -> None:
    policy = MutationPolicy(
        mode=mode,
        github_write_allowed=allow_github,
        discord_write_allowed=allow_discord,
    )
    discord_writer = DiscordPlanWriter(token="t", guild_id="1")
    discord_writer._client = _FailingClient()
    github_writer = GitHubPlanWriter(token="t", org="o", api_base="https://api.github.com")
    github_writer._client = _FailingClient()

    discord_plan = DiscordRolePlan(
        discord_user_id="1",
        role="Contributor",
        action="add",
        reason="test",
        source={"score": 1},
    )
    github_plan = GitHubAssignmentPlan(
        repo="repo",
        target_number=1,
        target_type="issue",
        assignee="octocat",
        action="assign",
        reason="test",
        source={},
    )

    caplog.set_level(logging.INFO)
    discord_writer.apply_plans([discord_plan], policy)
    github_writer.apply_plans([github_plan], policy)

    results = [getattr(record, "result", None) for record in caplog.records]
    assert expected in results, f"Expected '{expected}' in log results, got {results}"
