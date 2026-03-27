"""Tests for per-slash-command Discord permission checks."""

from __future__ import annotations

from types import SimpleNamespace

from ghdcbot.config.models import BotConfig, SlashCommandPermissionRule
from ghdcbot.discord_command_permissions import (
    format_slash_command_permission_denied,
    slash_command_allowed,
)


def _minimal_config_payload(**discord_overrides: object) -> dict:
    discord = {
        "guild_id": "1",
        "token": "t",
        **discord_overrides,
    }
    return {
        "runtime": {
            "mode": "dry-run",
            "log_level": "INFO",
            "data_dir": "/tmp",
            "github_adapter": "ghdcbot.adapters.github.rest:GitHubRestAdapter",
            "discord_adapter": "ghdcbot.adapters.discord.api:DiscordApiAdapter",
            "storage_adapter": "ghdcbot.adapters.storage.sqlite:SqliteStorage",
        },
        "github": {"org": "x", "token": "t", "api_base": "https://api.github.com"},
        "discord": discord,
        "scoring": {"period_days": 30, "weights": {"issue_opened": 1}},
        "role_mappings": [{"discord_role": "Contributor", "min_score": 0}],
        "assignments": {
            "review_roles": [],
            "issue_assignees": ["Mentor"],
        },
        "identity_mappings": [],
    }


def _member(
    *role_specs: tuple[int, str],
    administrator: bool = False,
) -> SimpleNamespace:
    roles = [SimpleNamespace(id=rid, name=name) for rid, name in role_specs]
    return SimpleNamespace(
        roles=roles,
        guild_permissions=SimpleNamespace(administrator=administrator),
    )


def _interaction(member: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(user=member)


def test_legacy_issue_assignees_allows_matching_role_name() -> None:
    config = BotConfig.model_validate(_minimal_config_payload())
    member = _member((1, "Mentor"))
    assert slash_command_allowed(_interaction(member), config, "sync") is True


def test_legacy_issue_assignees_denies_when_no_match() -> None:
    config = BotConfig.model_validate(_minimal_config_payload())
    member = _member((1, "Contributor"))
    assert slash_command_allowed(_interaction(member), config, "sync") is False


def test_non_guild_user_denied() -> None:
    config = BotConfig.model_validate(_minimal_config_payload())
    plain_user = SimpleNamespace(id=123)  # no roles / guild_permissions
    assert slash_command_allowed(_interaction(plain_user), config, "sync") is False


def test_unrestricted_slash_commands_allows_any_guild_member() -> None:
    config = BotConfig.model_validate(
        _minimal_config_payload(
            unrestricted_slash_commands=True,
            command_permissions={
                "sync": SlashCommandPermissionRule(
                    role_names=["Mentor"],
                    role_ids=[],
                ),
            },
        ),
    )
    assert slash_command_allowed(_interaction(_member((1, "Contributor"))), config, "sync") is True
    assert slash_command_allowed(_interaction(_member((1, "Student"))), config, "assign-issue") is True


def test_command_permissions_role_name_match() -> None:
    config = BotConfig.model_validate(
        _minimal_config_payload(
            command_permissions={
                "sync": SlashCommandPermissionRule(
                    role_names=["Lead"],
                    allow_discord_administrators=False,
                ),
            },
        ),
    )
    assert slash_command_allowed(_interaction(_member((1, "Lead"))), config, "sync") is True
    assert slash_command_allowed(_interaction(_member((1, "Mentor"))), config, "sync") is False


def test_command_permissions_role_id_match() -> None:
    config = BotConfig.model_validate(
        _minimal_config_payload(
            command_permissions={
                "assign-issue": SlashCommandPermissionRule(
                    role_ids=["999"],
                    role_names=[],
                ),
            },
        ),
    )
    assert slash_command_allowed(_interaction(_member((999, "X"))), config, "assign-issue") is True
    assert slash_command_allowed(_interaction(_member((1, "Mentor"))), config, "assign-issue") is False


def test_command_permissions_administrator_bypass() -> None:
    config = BotConfig.model_validate(
        _minimal_config_payload(
            command_permissions={
                "sync": SlashCommandPermissionRule(
                    role_names=[],
                    role_ids=[],
                    allow_discord_administrators=True,
                ),
            },
        ),
    )
    admin = _member((1, "Random"), administrator=True)
    assert slash_command_allowed(_interaction(admin), config, "sync") is True
    non_admin = _member((1, "Random"), administrator=False)
    assert slash_command_allowed(_interaction(non_admin), config, "sync") is False


def test_omitted_command_falls_back_to_legacy() -> None:
    """If command_permissions exists but key missing, use issue_assignees."""
    config = BotConfig.model_validate(
        _minimal_config_payload(
            command_permissions={
                "sync": SlashCommandPermissionRule(role_names=["OnlySync"], role_ids=[]),
            },
        ),
    )
    assert slash_command_allowed(_interaction(_member((1, "Mentor"))), config, "issue-requests") is True


def test_empty_explicit_rule_denies_non_admin() -> None:
    config = BotConfig.model_validate(
        _minimal_config_payload(
            command_permissions={
                "sync": SlashCommandPermissionRule(
                    role_names=[],
                    role_ids=[],
                    allow_discord_administrators=False,
                ),
            },
        ),
    )
    assert slash_command_allowed(_interaction(_member((1, "Mentor"))), config, "sync") is False


def test_format_denied_with_legacy_fallback() -> None:
    config = BotConfig.model_validate(_minimal_config_payload())
    msg = format_slash_command_permission_denied(config, "sync")
    assert "Mentor" in msg
    assert "/sync" in msg


def test_format_denied_with_explicit_rule() -> None:
    config = BotConfig.model_validate(
        _minimal_config_payload(
            command_permissions={
                "sync": SlashCommandPermissionRule(
                    role_ids=["111"],
                    role_names=["Lead"],
                    allow_discord_administrators=True,
                ),
            },
        ),
    )
    msg = format_slash_command_permission_denied(config, "sync")
    assert "111" in msg
    assert "Lead" in msg
    assert "Discord Administrators" in msg


def test_format_denied_empty_rule_message() -> None:
    config = BotConfig.model_validate(
        _minimal_config_payload(
            command_permissions={
                "sync": SlashCommandPermissionRule(
                    role_names=[],
                    role_ids=[],
                    allow_discord_administrators=False,
                ),
            },
        ),
    )
    msg = format_slash_command_permission_denied(config, "sync")
    assert "Nobody is allowed" in msg


def test_config_accepts_command_permissions_in_yaml_shape() -> None:
    """Regression: dict[str, dict] from YAML validates to SlashCommandPermissionRule."""
    config = BotConfig.model_validate(
        _minimal_config_payload(
            command_permissions={
                "assign-issue": {
                    "role_names": ["Mentor"],
                    "allow_discord_administrators": True,
                },
            },
        ),
    )
    assert config.discord.command_permissions is not None
    rule = config.discord.command_permissions["assign-issue"]
    assert rule.role_names == ["Mentor"]
    assert rule.allow_discord_administrators is True
