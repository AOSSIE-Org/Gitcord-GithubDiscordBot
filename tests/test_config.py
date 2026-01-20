from ghdcbot.config.models import BotConfig


def test_role_mappings_required() -> None:
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
        "scoring": {"period_days": 30, "weights": {"issue_opened": 1}},
        "role_mappings": [],
        "assignments": {"review_roles": [], "issue_assignees": []},
        "identity_mappings": [],
    }
    try:
        BotConfig.model_validate(payload)
    except Exception as exc:  # noqa: BLE001
        assert "role_mappings" in str(exc)
    else:
        raise AssertionError("Expected role_mappings validation error")
