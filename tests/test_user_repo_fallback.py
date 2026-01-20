import logging

from ghdcbot.adapters.github.rest import GitHubRestAdapter
from ghdcbot.config import loader
from ghdcbot.config.models import (
    AssignmentConfig,
    BotConfig,
    DiscordConfig,
    GitHubConfig,
    RepoFilterConfig,
    RoleMappingConfig,
    RuntimeConfig,
    ScoringConfig,
)


def test_user_repo_fallback_on_org_unauthorized(monkeypatch, caplog) -> None:
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
            org="AOSSIE-Org",
            token="token",
            api_base="https://api.github.com",
            repos=RepoFilterConfig(mode="allow", names=["repo-a"]),
            user_fallback=True,
        ),
        discord=DiscordConfig(guild_id="1", token="t"),
        scoring=ScoringConfig(period_days=30, weights={"issue_opened": 1}),
        role_mappings=[RoleMappingConfig(discord_role="Contributor", min_score=1)],
        assignments=AssignmentConfig(),
        identity_mappings=[],
    )
    loader._ACTIVE_CONFIG = config  # test-only setup

    adapter = GitHubRestAdapter(token="t", org="AOSSIE-Org", api_base="https://api.github.com")
    calls: list[str] = []

    def fake_list_repos_from_path(path: str):
        calls.append(path)
        if path == "/orgs/AOSSIE-Org/repos":
            return [], 401
        if path == "/user/repos":
            return [
                {"name": "repo-a", "owner": {"login": "user"}, "full_name": "user/repo-a"}
            ], 200
        return [], 200

    monkeypatch.setattr(adapter, "_list_repos_from_path", fake_list_repos_from_path)

    caplog.set_level(logging.INFO)
    repos = adapter._list_repos()

    assert calls == ["/orgs/AOSSIE-Org/repos", "/user/repos"]
    assert [repo["name"] for repo in repos] == ["repo-a"]
    assert any(
        "Falling back to user repositories (not an org member)" in record.message
        for record in caplog.records
    )
