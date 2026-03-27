"""Per-slash-command permission checks (Discord roles + optional Administrator bypass)."""

from __future__ import annotations

import discord

from ghdcbot.config.models import BotConfig, SlashCommandPermissionRule


def _legacy_issue_assignee_allowed(member: discord.Member, config: BotConfig) -> bool:
    """Backward compatible: assignments.issue_assignees matched by role name."""
    mentor_roles = getattr(config, "assignments", None)
    if not mentor_roles:
        return False
    issue_assignee_roles = getattr(mentor_roles, "issue_assignees", [])
    if not issue_assignee_roles:
        return False
    user_roles = [role.name for role in member.roles]
    return any(role in issue_assignee_roles for role in user_roles)


def _is_guild_member_like(user: object) -> bool:
    """True for Discord Member in a guild (has roles + guild_permissions). Duck-typed for tests."""
    return hasattr(user, "roles") and hasattr(user, "guild_permissions")


def slash_command_allowed(
    interaction: discord.Interaction,
    config: BotConfig,
    command_name: str,
) -> bool:
    """Return True if the member may run this slash command."""
    member = interaction.user
    if not _is_guild_member_like(member):
        return False

    if getattr(config.discord, "unrestricted_slash_commands", False):
        return True

    perms = getattr(config.discord, "command_permissions", None)
    rule: SlashCommandPermissionRule | None = None
    if perms and command_name in perms:
        rule = perms[command_name]

    if rule is None:
        return _legacy_issue_assignee_allowed(member, config)

    if rule.allow_discord_administrators and member.guild_permissions.administrator:
        return True

    id_allow = {str(rid).strip() for rid in rule.role_ids if str(rid).strip()}
    for role in member.roles:
        if str(role.id) in id_allow:
            return True

    if rule.role_names:
        allowed_names = set(rule.role_names)
        user_role_names = {r.name for r in member.roles}
        if user_role_names & allowed_names:
            return True

    return False


def format_slash_command_permission_denied(config: BotConfig, command_name: str) -> str:
    """User-facing message listing who may use the command."""
    perms = getattr(config.discord, "command_permissions", None)
    rule: SlashCommandPermissionRule | None = None
    if perms and command_name in perms:
        rule = perms[command_name]

    if rule is None:
        mentor_roles = getattr(config, "assignments", None)
        issue_assignee_roles = getattr(mentor_roles, "issue_assignees", []) if mentor_roles else []
        role_list = ", ".join(issue_assignee_roles) if issue_assignee_roles else "configure assignments.issue_assignees"
        return (
            f"❌ Permission denied. Only members with roles **{role_list}** "
            f"can use `/{command_name}` (or set `discord.command_permissions`)."
        )

    bits: list[str] = []
    if rule.role_ids:
        bits.append("role ID(s): " + ", ".join(str(r) for r in rule.role_ids))
    if rule.role_names:
        bits.append("role name(s): " + ", ".join(rule.role_names))
    if rule.allow_discord_administrators:
        bits.append("Discord Administrators")
    if not bits:
        return f"❌ Permission denied. Nobody is allowed to use `/{command_name}` with the current rule (fix `discord.command_permissions`)."
    return f"❌ Permission denied for `/{command_name}`. Allowed: {', '.join(bits)}."
