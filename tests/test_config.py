from pathlib import Path

import pytest

from ghdcbot.config.loader import load_config
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


def test_legacy_scoring_migration_does_not_mutate_input_payload() -> None:
    payload = {
        "runtime": {
            "mode": "dry-run",
            "log_level": "INFO",
            "data_dir": "/tmp",
            "github_adapter": "ghdcbot.adapters.github.rest:GitHubRestAdapter",
            "discord_adapter": "ghdcbot.adapters.discord.api:DiscordApiAdapter",
            "storage_adapter": "ghdcbot.adapters.storage.sqlite:SqliteStorage",
            "enable_scoring": False,
        },
        "github": {"org": "x", "token": "t", "api_base": "https://api.github.com"},
        "discord": {"guild_id": "1", "token": "t"},
        "scoring": {"period_days": 14, "weights": {"pr_merged": 5}},
        "assignments": {"review_roles": [], "issue_assignees": []},
        "identity_mappings": [],
    }

    BotConfig.model_validate(payload)

    assert "scoring" in payload
    assert payload["runtime"]["enable_scoring"] is False
    assert "activity_period_days" not in payload["runtime"]


def test_aussie_config_loads_shared_repo_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "test-token-github")
    monkeypatch.setenv("DISCORD_TOKEN", "test-token-discord")
    config_path = Path(__file__).resolve().parent.parent / "config" / "aussie.yaml"
    config = load_config(str(config_path))
    assert config.github.repos is not None
    assert config.github.repos.mode == "allow"
    assert len(config.github.repos.names) == 19
    assert "EduAid" in config.github.repos.names
    assert "SkillBot" in config.github.repos.names


def test_windows_double_quoted_path_in_yaml(tmp_path: Path) -> None:
    from ghdcbot.config.loader import _load_yaml

    cfg_file = tmp_path / "win_config.yaml"
    cfg_file.write_text(
        'data_dir: "C:\\Users\\username\\AppData\\Local\\Temp\\data"\n'
        'rel_dir: ".\\data\\storage"\n'
        'message: "hello\\nworld"\n',
        encoding="utf-8",
    )
    loaded = _load_yaml(cfg_file)
    assert loaded["data_dir"] == "C:\\Users\\username\\AppData\\Local\\Temp\\data"
    assert loaded["rel_dir"] == ".\\data\\storage"
    assert loaded["message"] == "hello\nworld"


def test_sanitize_windows_yaml_paths_preserves_standard_escapes() -> None:
    from ghdcbot.config.loader import _sanitize_windows_yaml_paths

    content = 'path: "C:\\test\\new\\folder"\nmsg: "tab\\there"\n'
    sanitized = _sanitize_windows_yaml_paths(content)
    assert 'path: "C:\\\\test\\\\new\\\\folder"' in sanitized
    assert 'msg: "tab\\there"' in sanitized
