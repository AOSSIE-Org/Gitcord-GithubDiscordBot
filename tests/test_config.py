import pytest
from pydantic import ValidationError

from ghdcbot.config.models import BotConfig


def test_role_mappings_optional() -> None:
    payload = {
        "runtime": {
            "mode": "dry-run",
            "log_level": "INFO",
            "data_dir": "/tmp",
            "activity_period_days": 30,
            "github_adapter": "ghdcbot.adapters.github.rest:GitHubRestAdapter",
            "discord_adapter": "ghdcbot.adapters.discord.api:DiscordApiAdapter",
            "storage_adapter": "ghdcbot.adapters.storage.sqlite:SqliteStorage",
        },
        "github": {"org": "x", "token": "t", "api_base": "https://api.github.com"},
        "discord": {"guild_id": "1", "token": "t"},
        "assignments": {"review_roles": [], "issue_assignees": []},
        "identity_mappings": [],
    }
    config = BotConfig.model_validate(payload)
    assert config.role_mappings == []


def test_legacy_scoring_migrates_activity_period() -> None:
    payload = {
        "runtime": {
            "mode": "dry-run",
            "log_level": "INFO",
            "data_dir": "/tmp",
            "github_adapter": "ghdcbot.adapters.github.rest:GitHubRestAdapter",
            "discord_adapter": "ghdcbot.adapters.discord.api:DiscordApiAdapter",
            "storage_adapter": "ghdcbot.adapters.storage.sqlite:SqliteStorage",
        },
        "github": {"org": "x", "token": "t", "api_base": "https://api.github.com"},
        "discord": {"guild_id": "1", "token": "t"},
        "scoring": {"period_days": 14, "weights": {"pr_merged": 5}},
        "assignments": {"review_roles": [], "issue_assignees": []},
        "identity_mappings": [],
    }
    config = BotConfig.model_validate(payload)
    assert config.runtime.activity_period_days == 14
