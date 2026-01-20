from ghdcbot.adapters.discord.writer import DiscordPlanWriter
from ghdcbot.adapters.github.writer import GitHubPlanWriter
from ghdcbot.core.modes import MutationPolicy, RunMode


class _FailingClient:
    def request(self, *args, **kwargs):
        raise AssertionError("HTTP call should not occur with empty plans")

    def post(self, *args, **kwargs):
        raise AssertionError("HTTP call should not occur with empty plans")


def test_writers_noop_on_empty_plans() -> None:
    policy = MutationPolicy(
        mode=RunMode.ACTIVE,
        github_write_allowed=True,
        discord_write_allowed=True,
    )

    discord_writer = DiscordPlanWriter(token="t", guild_id="1")
    discord_writer._client = _FailingClient()
    github_writer = GitHubPlanWriter(token="t", org="o", api_base="https://api.github.com")
    github_writer._client = _FailingClient()

    discord_writer.apply_plans([], policy)
    github_writer.apply_plans([], policy)
