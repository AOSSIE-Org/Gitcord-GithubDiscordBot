"""Tests for /pr-channel: Discord-set repo → channel routes layered on YAML config."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from ghdcbot.adapters.storage.sqlite import SqliteStorage
from ghdcbot.config.models import (
    AssignmentConfig,
    BotConfig,
    DiscordConfig,
    GitHubConfig,
    NotificationConfig,
    PermissionConfig,
    RepoFilterConfig,
    RuntimeConfig,
    SlashCommandPermissionRule,
)
from ghdcbot.discord_command_permissions import (
    format_slash_command_permission_denied,
    permission_rule_name,
    slash_command_allowed,
)
from ghdcbot.engine import channel_routes as cr
from ghdcbot.engine.orchestrator import Orchestrator

GUILD_ID = 123


def _config(
    tmp_path,
    *,
    repos: RepoFilterConfig | None = None,
    channels: dict[str, str] | None = None,
    command_permissions: dict[str, SlashCommandPermissionRule] | None = None,
    notifications: NotificationConfig | None = None,
) -> BotConfig:
    return BotConfig(
        runtime=RuntimeConfig(
            mode="active",
            data_dir=str(tmp_path),
            storage_adapter="ghdcbot.adapters.storage.sqlite:SqliteStorage",
            github_adapter="ghdcbot.adapters.github.rest:GitHubRestAdapter",
            discord_adapter="ghdcbot.adapters.discord.api:DiscordApiAdapter",
        ),
        github=GitHubConfig(org="test-org", token="fake", repos=repos),
        discord=DiscordConfig(
            guild_id=str(GUILD_ID),
            token="fake",
            permissions=PermissionConfig(write=True),
            pr_open_channels=channels or {},
            command_permissions=command_permissions,
            notifications=notifications,
        ),
        assignments=AssignmentConfig(issue_assignees=[], review_roles=[]),
    )


def _storage(tmp_path) -> SqliteStorage:
    storage = SqliteStorage(data_dir=str(tmp_path))
    storage.init_schema()
    return storage


# -- storage ------------------------------------------------------------------


def test_storage_set_list_upsert_and_delete(tmp_path) -> None:
    storage = _storage(tmp_path)

    assert storage.set_repo_channel_route("Website", "111", set_by_discord_id="42") is None
    assert storage.set_repo_channel_route("website", "222", set_by_discord_id="43") == "111"

    routes = storage.list_repo_channel_routes()
    assert len(routes) == 1
    assert routes[0]["repo"] == "website"
    assert routes[0]["channel_id"] == "222"
    assert routes[0]["set_by_discord_id"] == "43"

    assert storage.delete_repo_channel_route("WEBSITE", removed_by_discord_id="44") == "222"
    assert storage.delete_repo_channel_route("Website") is None
    assert storage.list_repo_channel_routes() == []


def test_storage_writes_audit_events(tmp_path) -> None:
    storage = _storage(tmp_path)
    storage.set_repo_channel_route("Info", "111", set_by_discord_id="42")
    storage.set_repo_channel_route("Info", "222", set_by_discord_id="42")
    storage.delete_repo_channel_route("Info", removed_by_discord_id="43")

    events = [e for e in storage.list_audit_events() if e["event_type"].startswith("repo_channel_route")]
    assert [e["event_type"] for e in events] == [
        "repo_channel_route_set",
        "repo_channel_route_set",
        "repo_channel_route_removed",
    ]
    assert events[1]["context"] == {"repo": "Info", "old_channel_id": "111", "new_channel_id": "222"}
    assert events[2]["actor_id"] == "43"
    assert events[2]["context"]["old_channel_id"] == "222"


# -- apply_channel_routes -----------------------------------------------------


def test_apply_allow_mode_adds_routed_repo_to_scan_list(tmp_path) -> None:
    config = _config(
        tmp_path,
        repos=RepoFilterConfig(mode="allow", names=["Agora"]),
        channels={"Agora": "100"},
    )
    base = cr.routing_base_for(config)

    cr.apply_channel_routes(config, base, {"Website": "200"})

    assert config.github.repos.names == ["Agora", "Website"]
    assert config.discord.pr_open_channels == {"Agora": "100", "Website": "200"}


def test_apply_deny_mode_leaves_names_unchanged(tmp_path) -> None:
    config = _config(tmp_path, repos=RepoFilterConfig(mode="deny", names=["Secret"]))
    cr.apply_channel_routes(config, cr.routing_base_for(config), {"Website": "200"})

    assert config.github.repos.names == ["Secret"]
    assert config.discord.pr_open_channels == {"Website": "200"}


def test_apply_without_filter_only_sets_channels(tmp_path) -> None:
    config = _config(tmp_path)
    cr.apply_channel_routes(config, cr.routing_base_for(config), {"Website": "200"})

    assert config.github.repos is None
    assert config.discord.pr_open_channels == {"Website": "200"}


def test_discord_route_overrides_yaml_case_insensitively(tmp_path) -> None:
    config = _config(
        tmp_path,
        repos=RepoFilterConfig(mode="allow", names=["website"]),
        channels={"website": "100"},
    )
    cr.apply_channel_routes(config, cr.routing_base_for(config), {"Website": "200"})

    assert config.discord.pr_open_channels == {"Website": "200"}
    assert config.github.repos.names == ["website"]


def test_removal_falls_back_to_yaml_and_apply_is_idempotent(tmp_path) -> None:
    config = _config(
        tmp_path,
        repos=RepoFilterConfig(mode="allow", names=["Agora"]),
        channels={"Agora": "100"},
    )
    base = cr.routing_base_for(config)

    routes = {"Agora": "300", "Website": "200"}
    cr.apply_channel_routes(config, base, routes)
    cr.apply_channel_routes(config, cr.routing_base_for(config), routes)
    assert config.github.repos.names == ["Agora", "Website"]
    assert config.discord.pr_open_channels == {"Agora": "300", "Website": "200"}

    cr.apply_channel_routes(config, cr.routing_base_for(config), {})
    assert config.github.repos.names == ["Agora"]
    assert config.discord.pr_open_channels == {"Agora": "100"}


def test_apply_stored_channel_routes_reads_storage(tmp_path) -> None:
    storage = _storage(tmp_path)
    storage.set_repo_channel_route("Website", "200")
    config = _config(tmp_path, repos=RepoFilterConfig(mode="allow", names=["Agora"]))

    applied = cr.apply_stored_channel_routes(config, storage)

    assert applied == {"Website": "200"}
    assert "Website" in config.github.repos.names
    assert cr.apply_stored_channel_routes(config, object()) == {}


# -- orchestrator -------------------------------------------------------------

ORG_REPOS = [{"name": "Agora"}, {"name": "Website"}, {"name": "Other"}]


class _ScanRecordingReader:
    """GitHub reader stub that records which repos the real repo filter would scan."""

    def __init__(self) -> None:
        self.scanned: list[str] = []

    def peek_repos_for_sync(self) -> int:
        from ghdcbot.adapters.github.rest import _apply_repo_filter, _load_repo_filter

        self.scanned = [
            r["name"] for r in _apply_repo_filter(ORG_REPOS, _load_repo_filter(), logging.getLogger("t"))
        ]
        return len(self.scanned)

    sync_repos_processed = 0
    sync_request_count = 0

    def list_contributions(self, since: datetime) -> list:
        return []

    def list_open_issues(self) -> list:
        return []

    def list_open_pull_requests(self) -> list:
        return []

    def close(self) -> None:
        return None


def test_orchestrator_applies_routes_to_scan_list_and_channels(tmp_path, monkeypatch) -> None:
    storage = _storage(tmp_path)
    storage.set_repo_channel_route("Website", "200")
    config = _config(
        tmp_path,
        repos=RepoFilterConfig(mode="allow", names=["Agora"]),
        channels={"Agora": "100"},
        notifications=NotificationConfig(enabled=True, pr_opened=True),
    )
    monkeypatch.setattr("ghdcbot.config.loader._ACTIVE_CONFIG", config)

    captured: dict[str, Any] = {}

    def fake_send(contributions, storage_, writer, policy, notif, org, pr_open_channels, **kwargs):
        captured["channels"] = dict(pr_open_channels)

    monkeypatch.setattr("ghdcbot.engine.orchestrator._send_notifications_for_new_events", fake_send)

    reader = _ScanRecordingReader()
    orch = Orchestrator(
        github_reader=reader,
        github_writer=MagicMock(),
        discord_reader=SimpleNamespace(list_member_roles=lambda: {}, close=lambda: None),
        discord_writer=MagicMock(),
        storage=storage,
        config=config,
    )
    orch.run_once()

    assert reader.scanned == ["Agora", "Website"]
    assert captured["channels"] == {"Agora": "100", "Website": "200"}


def test_list_all_org_repo_names_ignores_filter_and_skips_archived(tmp_path, monkeypatch) -> None:
    from ghdcbot.adapters.github.rest import GitHubRestAdapter

    config = _config(tmp_path, repos=RepoFilterConfig(mode="allow", names=["Agora"]))
    monkeypatch.setattr("ghdcbot.config.loader._ACTIVE_CONFIG", config)
    adapter = GitHubRestAdapter(token="fake", org="test-org", api_base="https://api.github.com")
    try:
        repos = [{"name": "Agora"}, {"name": "Website"}, {"name": "Old", "archived": True}]
        monkeypatch.setattr(adapter, "_list_repos_from_path", lambda path: (repos, 200))
        assert adapter.list_all_org_repo_names() == ["Agora", "Website"]

        monkeypatch.setattr(adapter, "_list_repos_from_path", lambda path: ([], 403))
        with pytest.raises(RuntimeError):
            adapter.list_all_org_repo_names()
    finally:
        adapter.close()


# -- validation helpers -------------------------------------------------------


def test_channel_type_validation() -> None:
    assert cr.channel_type_error(discord.ChannelType.text) is None
    assert cr.channel_type_error(discord.ChannelType.news) is None
    assert cr.channel_type_error(discord.ChannelType.public_thread) is None
    assert "forum" in cr.channel_type_error(discord.ChannelType.forum)
    assert "post inside the forum" in cr.channel_type_error(15)
    assert cr.channel_type_error(discord.ChannelType.voice) is not None
    assert cr.channel_type_error(None) is not None
    assert cr.is_thread_channel_type(discord.ChannelType.public_thread)
    assert not cr.is_thread_channel_type(discord.ChannelType.text)


def test_missing_bot_permissions_names_each_permission() -> None:
    perms = SimpleNamespace(view_channel=True, send_messages=False, embed_links=False)
    assert cr.missing_bot_permissions(perms, is_thread=False) == ["Send Messages", "Embed Links"]

    thread_perms = SimpleNamespace(
        view_channel=True, send_messages=True, send_messages_in_threads=False, embed_links=True
    )
    assert cr.missing_bot_permissions(thread_perms, is_thread=True) == ["Send Messages in Threads"]

    ok = SimpleNamespace(view_channel=True, send_messages=True, embed_links=True)
    assert cr.missing_bot_permissions(ok, is_thread=False) == []


def test_resolve_org_repo_name_and_deny_list(tmp_path) -> None:
    names = ["Website", "Info"]
    assert cr.resolve_org_repo_name("website", names) == "Website"
    assert cr.resolve_org_repo_name(" INFO ", names) == "Info"
    assert cr.resolve_org_repo_name("Nope", names) is None
    assert cr.resolve_org_repo_name("", names) is None

    deny = cr.routing_base_for(_config(tmp_path, repos=RepoFilterConfig(mode="deny", names=["Secret"])))
    assert cr.is_repo_deny_listed(deny, "secret")
    assert not cr.is_repo_deny_listed(deny, "Website")
    allow = cr.routing_base_for(_config(tmp_path, repos=RepoFilterConfig(mode="allow", names=["Secret"])))
    assert not cr.is_repo_deny_listed(allow, "Secret")


def test_set_and_remove_replies(tmp_path) -> None:
    text = cr.format_set_reply("Website", "200", previous_channel_id=None, config_channel_id="100")
    assert "<#200>" in text and "Overrides <#100>" in text and "from now on" in text

    moved = cr.format_set_reply("Website", "200", previous_channel_id="150", config_channel_id=None)
    assert "Previously posted to <#150>" in moved

    same = cr.format_set_reply("Website", "200", previous_channel_id="200", config_channel_id=None)
    assert "already set" in same

    fallback = cr.format_remove_reply("Agora", removed_channel_id="300", config_channel_id="100")
    assert "falls back to <#100>" in fallback

    yaml_only = cr.format_remove_reply("Agora", removed_channel_id=None, config_channel_id="100")
    assert "only be changed in the config file" in yaml_only


def test_route_list_formatting_and_truncation(tmp_path) -> None:
    config = _config(tmp_path, channels={"Agora": "100", "Info": "200"})
    entries = cr.build_route_entries(cr.routing_base_for(config), {"Website": "200", "Agora": "300"})

    assert [(e.repo, e.channel_id, e.source) for e in entries] == [
        ("Agora", "300", "discord"),
        ("Info", "200", "config"),
        ("Website", "200", "discord"),
    ]

    here = cr.format_route_list(entries, channel_id="200")
    assert "**Info** `config`" in here and "**Website** `discord`" in here
    assert "Agora" not in here
    assert "No repos post to <#999>" in cr.format_route_list(entries, channel_id="999")

    grouped = cr.format_route_list(entries)
    assert grouped.index("<#300>") < grouped.index("<#200>")

    many = [cr.RouteEntry(f"repo-{i:03d}", "1", "discord") for i in range(300)]
    truncated = cr.format_route_list(many, limit=500)
    assert len(truncated) <= 500
    assert truncated.endswith("more")


# -- permissions ----------------------------------------------------------------


def _member(*role_names: str, admin: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        id=42,
        roles=[SimpleNamespace(name=n, id=i) for i, n in enumerate(role_names, start=1)],
        guild_permissions=SimpleNamespace(administrator=admin),
    )


def test_pr_channel_falls_back_to_sync_rule(tmp_path) -> None:
    config = _config(
        tmp_path,
        command_permissions={"sync": SlashCommandPermissionRule(role_names=["Mentor"])},
    )
    rule = permission_rule_name(config, "pr-channel", "sync")
    assert rule == "sync"
    assert slash_command_allowed(SimpleNamespace(user=_member("Mentor")), config, rule)
    assert not slash_command_allowed(SimpleNamespace(user=_member("Contributor")), config, rule)

    denied = format_slash_command_permission_denied(config, "pr-channel", rule_name=rule)
    assert "/pr-channel" in denied and "Mentor" in denied


def test_explicit_pr_channel_rule_wins(tmp_path) -> None:
    config = _config(
        tmp_path,
        command_permissions={
            "sync": SlashCommandPermissionRule(role_names=["Mentor"]),
            "pr-channel": SlashCommandPermissionRule(role_names=["Maintainer"]),
        },
    )
    rule = permission_rule_name(config, "pr-channel", "sync")
    assert rule == "pr-channel"
    assert not slash_command_allowed(SimpleNamespace(user=_member("Mentor")), config, rule)
    assert slash_command_allowed(SimpleNamespace(user=_member("Maintainer")), config, rule)


def test_no_rules_keeps_command_name(tmp_path) -> None:
    assert permission_rule_name(_config(tmp_path), "pr-channel", "sync") == "pr-channel"


# -- bot integration ------------------------------------------------------------


def _run_bot_capture(config: BotConfig, storage: SqliteStorage, github: MagicMock):
    from ghdcbot.bot import run_bot

    captured: list[Any] = []
    orig_tree_init = discord.app_commands.CommandTree.__init__

    def mock_tree_init(tree_self: Any, client: Any) -> None:
        captured.append(tree_self)
        orig_tree_init(tree_self, client)

    def fake_build_adapter(name: str, **kwargs: Any) -> Any:
        return storage if "data_dir" in kwargs else github

    with (
        patch("ghdcbot.bot.load_config", return_value=config),
        patch("ghdcbot.bot.resolve_github_token", return_value="fake"),
        patch("ghdcbot.bot.build_adapter", side_effect=fake_build_adapter),
        patch("ghdcbot.bot.GitHubIdentityReader"),
        patch("ghdcbot.bot.IdentityLinkService"),
        patch("ghdcbot.bot.SocialProfileService"),
        patch("discord.app_commands.CommandTree.__init__", mock_tree_init),
        patch("discord.Client.run", side_effect=SystemExit(0)),
    ):
        with pytest.raises(SystemExit):
            run_bot("dummy.yaml")

    group = next(
        c for c in captured[0].get_commands(guild=discord.Object(id=GUILD_ID)) if c.name == "pr-channel"
    )
    return {c.name: c for c in group.commands}


def _interaction(target: Any, guild: Any, *, member: SimpleNamespace, channel_id: int) -> MagicMock:
    interaction = MagicMock()
    interaction.user = member
    interaction.guild = guild
    interaction.channel_id = channel_id
    interaction.response.defer = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


def _guild_with(target: Any) -> SimpleNamespace:
    guild = SimpleNamespace(id=GUILD_ID, me=object())
    guild.get_channel_or_thread = lambda cid: target if target and cid == target.id else None
    guild.fetch_channel = AsyncMock(side_effect=discord.NotFound(MagicMock(status=404), "nope"))
    if target is not None:
        target.guild = guild
    return guild


def _thread(channel_id: int = 555, **perm_overrides: bool) -> SimpleNamespace:
    perms = {"view_channel": True, "send_messages_in_threads": True, "embed_links": True}
    perms.update(perm_overrides)
    return SimpleNamespace(
        id=channel_id,
        type=discord.ChannelType.public_thread,
        locked=False,
        permissions_for=lambda member: SimpleNamespace(**perms),
    )


def test_bot_registers_pr_channel_group(tmp_path) -> None:
    commands = _run_bot_capture(_config(tmp_path), _storage(tmp_path), MagicMock())
    assert set(commands) == {"set", "remove", "list"}
    repo_param = next(p for p in commands["set"].parameters if p.name == "repo")
    assert repo_param.autocomplete is True
    channel_param = next(p for p in commands["set"].parameters if p.name == "channel")
    assert channel_param.required is False
    assert discord.ChannelType.public_thread in channel_param.channel_types
    assert discord.ChannelType.text in channel_param.channel_types
    assert [p.display_name for p in commands["list"].parameters] == ["all"]


def test_bot_set_stores_route_and_updates_config(tmp_path) -> None:
    config = _config(
        tmp_path,
        repos=RepoFilterConfig(mode="allow", names=["Agora"]),
        command_permissions={"sync": SlashCommandPermissionRule(role_names=["Mentor"])},
    )
    storage = _storage(tmp_path)
    github = MagicMock()
    github.list_all_org_repo_names.return_value = ["Agora", "Website"]
    commands = _run_bot_capture(config, storage, github)

    target = _thread()
    guild = _guild_with(target)
    interaction = _interaction(target, guild, member=_member("Mentor"), channel_id=target.id)

    asyncio.run(commands["set"].callback(interaction, repo="website", channel=None))

    reply = interaction.followup.send.call_args[0][0]
    assert "✅ **Website** → <#555>" in reply
    assert storage.list_repo_channel_routes()[0]["repo"] == "Website"
    assert storage.list_repo_channel_routes()[0]["set_by_discord_id"] == "42"
    assert config.discord.pr_open_channels == {"Website": "555"}
    assert config.github.repos.names == ["Agora", "Website"]

    list_interaction = _interaction(target, guild, member=_member("Mentor"), channel_id=target.id)
    asyncio.run(commands["list"].callback(list_interaction, all_routes=False))
    list_interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    embed = list_interaction.followup.send.call_args.kwargs["embed"]
    assert "**Website** `discord`" in embed.description

    remove_interaction = _interaction(target, guild, member=_member("Mentor"), channel_id=target.id)
    asyncio.run(commands["remove"].callback(remove_interaction, repo="WEBSITE"))
    remove_interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    assert "Removed the Discord route for **Website**" in remove_interaction.followup.send.call_args[0][0]
    assert storage.list_repo_channel_routes() == []
    assert config.discord.pr_open_channels == {}
    assert config.github.repos.names == ["Agora"]


def test_bot_set_rejects_unauthorized_unknown_repo_forum_and_missing_perms(tmp_path) -> None:
    config = _config(
        tmp_path,
        repos=RepoFilterConfig(mode="deny", names=["Secret"]),
        command_permissions={"sync": SlashCommandPermissionRule(role_names=["Mentor"])},
    )
    storage = _storage(tmp_path)
    github = MagicMock()
    github.list_all_org_repo_names.return_value = ["Website", "Secret"]
    commands = _run_bot_capture(config, storage, github)
    set_cb = commands["set"].callback

    target = _thread()
    guild = _guild_with(target)

    denied = _interaction(target, guild, member=_member("Contributor"), channel_id=target.id)
    asyncio.run(set_cb(denied, repo="Website", channel=None))
    assert "Permission denied for `/pr-channel`" in denied.response.send_message.call_args[0][0]

    unknown = _interaction(target, guild, member=_member("Mentor"), channel_id=target.id)
    asyncio.run(set_cb(unknown, repo="Nope", channel=None))
    assert "not an active repository" in unknown.followup.send.call_args[0][0]

    deny_listed = _interaction(target, guild, member=_member("Mentor"), channel_id=target.id)
    asyncio.run(set_cb(deny_listed, repo="secret", channel=None))
    assert "deny list" in deny_listed.followup.send.call_args[0][0]

    forum = SimpleNamespace(id=777, type=discord.ChannelType.forum, locked=False)
    forum_guild = _guild_with(forum)
    forum_inter = _interaction(forum, forum_guild, member=_member("Mentor"), channel_id=forum.id)
    asyncio.run(set_cb(forum_inter, repo="Website", channel=None))
    assert "post inside the forum" in forum_inter.followup.send.call_args[0][0]

    no_embed = _thread(888, embed_links=False)
    no_embed_guild = _guild_with(no_embed)
    no_embed_inter = _interaction(no_embed, no_embed_guild, member=_member("Mentor"), channel_id=no_embed.id)
    asyncio.run(set_cb(no_embed_inter, repo="Website", channel=None))
    assert "Missing permission(s): **Embed Links**" in no_embed_inter.followup.send.call_args[0][0]

    assert storage.list_repo_channel_routes() == []
