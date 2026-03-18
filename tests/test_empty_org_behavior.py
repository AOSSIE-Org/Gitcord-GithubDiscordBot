import logging

from ghdcbot.adapters.github.rest import GitHubRestAdapter
from ghdcbot.config import loader
from ghdcbot.config.models import (
    AssignmentConfig,
    BotConfig,
    DiscordConfig,
    GitHubConfig,
    RoleMappingConfig,
    RuntimeConfig,
    ScoringConfig,
)
from ghdcbot.engine.planning import plan_discord_roles, plan_github_assignments


def test_empty_org_logs_and_plans_empty(monkeypatch, caplog) -> None:
    config = BotConfig(
        runtime=RuntimeConfig(
            mode="dry-run",
            log_level="INFO",
            data_dir="/tmp",
            github_adapter="ghdcbot.adapters.github.rest:GitHubRestAdapter",
            discord_adapter="ghdcbot.adapters.discord.api:DiscordApiAdapter",
            storage_adapter="ghdcbot.adapters.storage.sqlite:SqliteStorage",
        ),
        github=GitHubConfig(
            org="shubham-orld",
            token="token",
            api_base="https://api.github.com",
            user_fallback=False,
        ),
        discord=DiscordConfig(guild_id="1", token="t"),
        scoring=ScoringConfig(period_days=30, weights={"issue_opened": 1}),
        role_mappings=[RoleMappingConfig(discord_role="Contributor", min_score=1)],
        assignments=AssignmentConfig(),
        identity_mappings=[],
    )
    loader._ACTIVE_CONFIG = config  # test-only setup

    adapter = GitHubRestAdapter(token="t", org="shubham-orld", api_base="https://api.github.com")

    def fake_list_repos_from_path(_path: str):
        return [], 200

    monkeypatch.setattr(adapter, "_list_repos_from_path", fake_list_repos_from_path)

    caplog.set_level(logging.INFO)
    repos = adapter._list_repos()

    assert repos == []
    assert any(
        "Organization has no repositories yet" in record.message for record in caplog.records
    )
    assert not any(
        "permission" in record.message.lower() for record in caplog.records
    )

    discord_plans = plan_discord_roles({}, [], [], [])
    github_plans = plan_github_assignments([], [], {}, [], [])
    assert discord_plans == []
    assert github_plans == []
