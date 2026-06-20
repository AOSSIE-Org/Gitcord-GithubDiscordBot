from __future__ import annotations

from ghdcbot.config.models import BotConfig
from ghdcbot.core.errors import ConfigError
from ghdcbot.core.modes import RunMode

_PLACEHOLDER_GUILD_IDS = frozenset({"0", "000000000000000000"})

_ENV_SETUP_HINTS: dict[str, str] = {
    "GITHUB_TOKEN": (
        "Create a fine-grained GitHub Personal Access Token with access to your org repos "
        "and set GITHUB_TOKEN in your .env file."
    ),
    "DISCORD_TOKEN": (
        "Create a Discord bot in the Developer Portal and set DISCORD_TOKEN in your .env file."
    ),
}


def missing_env_var_message(name: str) -> str:
    hint = _ENV_SETUP_HINTS.get(name, f"Set {name} in your .env file.")
    return f"{name} is missing.\n{hint}"


def empty_env_var_message(name: str) -> str:
    if name == "GITHUB_TOKEN":
        return (
            f"{name} is configured but empty.\n"
            "Please set a valid GitHub Personal Access Token."
        )
    if name == "DISCORD_TOKEN":
        return (
            f"{name} is configured but empty.\n"
            "Please set a valid Discord bot token."
        )
    return f"{name} is configured but empty.\nPlease set a valid value in your .env file."


def config_not_found_message(path: str) -> str:
    return (
        f"Config file not found: {path}\n"
        "Copy config/example.yaml (local) or config/docker-example.yaml (Docker) "
        "to config/config.yaml and edit github.org and discord.guild_id."
    )


def invalid_yaml_message(path: str, detail: str) -> str:
    return f"Invalid YAML syntax detected in {path}:\n{detail}"


def validate_active_mode(config: BotConfig) -> None:
    """Ensure active mode has the settings needed for the features it enables."""
    if config.runtime.mode != RunMode.ACTIVE:
        return

    problems: list[str] = []

    guild_id = (config.discord.guild_id or "").strip()
    if not guild_id or guild_id in _PLACEHOLDER_GUILD_IDS:
        problems.append(
            "discord.guild_id (set your server ID — Developer Mode → Copy Server ID)"
        )

    if getattr(config.runtime, "enable_discord_role_updates", True):
        if not config.role_mappings:
            problems.append("role_mappings (at least one discord_role entry)")
        if not config.discord.permissions.write:
            problems.append(
                "discord.permissions.write must be true when enable_discord_role_updates is true"
            )

    if not problems:
        return

    bullet_list = "\n".join(f"  - {item}" for item in problems)
    raise ConfigError(
        "Active mode requires:\n"
        f"{bullet_list}\n"
        "See INSTALLATION.md or docs/TESTING_DISCORD.md before enabling active mode."
    )
