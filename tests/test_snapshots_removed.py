"""GitHub snapshot publishing was removed: configs still load and nothing is written."""

from __future__ import annotations

import logging
from datetime import datetime
from types import SimpleNamespace

from ghdcbot.adapters.github.rest import GitHubRestAdapter
from ghdcbot.adapters.storage.sqlite import SqliteStorage
from ghdcbot.config.models import BotConfig
from ghdcbot.engine.orchestrator import Orchestrator


class _RecordingGitHub:
    def __init__(self) -> None:
        self.writes: list[tuple] = []

    def peek_repos_for_sync(self) -> int:
        return 1

    def list_contributions(self, since: datetime) -> list:
        return []

    def list_open_issues(self) -> list[dict]:
        return []

    def list_open_pull_requests(self) -> list[dict]:
        return []

    def write_file(self, *args, **kwargs) -> bool:
        self.writes.append(args)
        return True

    def close(self) -> None:
        return None


def _config(tmp_path) -> BotConfig:
    return BotConfig.model_validate(
        {
            "runtime": {
                "mode": "active",
                "data_dir": str(tmp_path),
                "storage_adapter": "ghdcbot.adapters.storage.sqlite:SqliteStorage",
                "github_adapter": "ghdcbot.adapters.github.rest:GitHubRestAdapter",
                "discord_adapter": "ghdcbot.adapters.discord.api:DiscordApiAdapter",
                "enable_discord_role_updates": False,
            },
            "github": {"org": "x", "token": "t", "api_base": "https://api.github.com"},
            "discord": {"token": "t", "guild_id": "1"},
            "assignments": {"issue_assignees": [], "review_roles": []},
            "snapshots": {
                "enabled": True,
                "repo_path": "x/.gitcord",
                "branch": "main",
                "min_interval_hours": 24,
            },
        }
    )


def test_config_with_snapshots_block_still_loads(tmp_path) -> None:
    config = _config(tmp_path)
    assert config.snapshots is not None and config.snapshots.enabled


def test_run_once_writes_no_snapshot_and_warns(tmp_path, caplog) -> None:
    caplog.set_level(logging.WARNING, logger="Orchestrator")
    storage = SqliteStorage(data_dir=str(tmp_path))
    storage.init_schema()
    github = _RecordingGitHub()
    discord = SimpleNamespace(list_member_roles=lambda: {"123": ["Contributor"]}, close=lambda: None)

    Orchestrator(
        github_reader=github,
        github_writer=github,
        discord_reader=discord,
        discord_writer=discord,
        storage=storage,
        config=_config(tmp_path),
    ).run_once()

    assert github.writes == []
    assert any("snapshot publishing was removed" in r.getMessage() for r in caplog.records)


def test_github_adapter_cannot_commit_files() -> None:
    assert not hasattr(GitHubRestAdapter, "write_file")
    assert not hasattr(GitHubRestAdapter, "delete_file")
