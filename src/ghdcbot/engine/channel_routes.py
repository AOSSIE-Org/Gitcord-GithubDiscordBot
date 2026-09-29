"""Discord-set repo → channel routes (``/pr-channel``) layered on top of YAML config.

Routes live in SQLite (``repo_channel_routes``). They are applied in place to the loaded
config so every reader (repo filter in the GitHub adapter, PR/issue channel posts,
``/pr-status`` repo inference) sees them without further changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

import discord

PR_CHANNEL_COMMAND = "pr-channel"
PR_CHANNEL_FALLBACK_COMMAND = "sync"

SOURCE_CONFIG = "config"
SOURCE_DISCORD = "discord"

# Discord embed descriptions max out at 4096 characters.
ROUTE_LIST_CHAR_LIMIT = 3800

_TEXT_CHANNEL_TYPES = {discord.ChannelType.text, discord.ChannelType.news}
_THREAD_CHANNEL_TYPES = {
    discord.ChannelType.public_thread,
    discord.ChannelType.private_thread,
    discord.ChannelType.news_thread,
}
_FORUM_CHANNEL_TYPES = {discord.ChannelType.forum, discord.ChannelType.media}


@dataclass(frozen=True)
class RoutingBase:
    """YAML-only routing state, captured once per loaded config."""

    repo_mode: str | None
    repo_names: tuple[str, ...]
    channels: Mapping[str, str]


@dataclass(frozen=True)
class RouteEntry:
    repo: str
    channel_id: str
    source: str


def capture_routing_base(config: Any) -> RoutingBase:
    repos_cfg = getattr(config.github, "repos", None)
    channels = {str(k): str(v) for k, v in (getattr(config.discord, "pr_open_channels", None) or {}).items()}
    if repos_cfg is None:
        return RoutingBase(repo_mode=None, repo_names=(), channels=channels)
    return RoutingBase(
        repo_mode=repos_cfg.mode,
        repo_names=tuple(repos_cfg.names),
        channels=channels,
    )


def routing_base_for(config: Any) -> RoutingBase:
    """Return the YAML routing snapshot for ``config``, capturing it on first use."""
    base = getattr(config, "_routing_base", None)
    if isinstance(base, RoutingBase):
        return base
    base = capture_routing_base(config)
    try:
        config._routing_base = base
    except (AttributeError, ValueError):
        pass
    return base


def routes_from_rows(rows: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    return {
        str(row["repo"]): str(row["channel_id"])
        for row in rows
        if row.get("repo") and row.get("channel_id")
    }


def apply_channel_routes(config: Any, base: RoutingBase, routes: Mapping[str, str]) -> None:
    """Rebuild ``pr_open_channels`` and the allowlist from ``base`` plus Discord ``routes``.

    Discord routes override YAML channels for the same repo. In ``allow`` mode routed repos
    are appended to ``repos.names`` so they get scanned; ``deny`` mode and "no filter" need
    nothing (deny-listed repos are refused when the route is set).
    """
    routed_lower = {repo.lower() for repo in routes}
    channels = {
        repo: channel_id
        for repo, channel_id in base.channels.items()
        if repo.lower() not in routed_lower
    }
    channels.update({repo: str(channel_id) for repo, channel_id in routes.items()})
    config.discord.pr_open_channels = channels

    repos_cfg = getattr(config.github, "repos", None)
    if repos_cfg is None or base.repo_mode != "allow":
        return
    names = list(base.repo_names)
    for repo in routes:
        if repo not in names:
            names.append(repo)
    repos_cfg.names = names


def load_channel_routes(storage: Any) -> dict[str, str]:
    list_routes = getattr(storage, "list_repo_channel_routes", None)
    if not callable(list_routes):
        return {}
    return routes_from_rows(list_routes())


def apply_stored_channel_routes(config: Any, storage: Any) -> dict[str, str]:
    """Apply the routes stored in ``storage`` to ``config``. Returns the applied routes."""
    routes = load_channel_routes(storage)
    apply_channel_routes(config, routing_base_for(config), routes)
    return routes


def config_channel_for(base: RoutingBase, repo: str) -> str | None:
    """Channel mapped to ``repo`` in the YAML (case-insensitive), if any."""
    wanted = repo.lower()
    for name, channel_id in base.channels.items():
        if name.lower() == wanted:
            return channel_id
    return None


def resolve_org_repo_name(requested: str, org_repo_names: Iterable[str]) -> str | None:
    """Match ``requested`` case-insensitively against org repos; return GitHub's casing."""
    wanted = (requested or "").strip().lower()
    if not wanted:
        return None
    for name in org_repo_names:
        if name.lower() == wanted:
            return name
    return None


def is_repo_deny_listed(base: RoutingBase, repo: str) -> bool:
    if base.repo_mode != "deny":
        return False
    return repo.lower() in {name.strip().lower() for name in base.repo_names}


def _as_channel_type(channel_type: Any) -> discord.ChannelType | None:
    if isinstance(channel_type, discord.ChannelType):
        return channel_type
    try:
        return discord.ChannelType(int(channel_type))
    except (TypeError, ValueError):
        return None


def is_thread_channel_type(channel_type: Any) -> bool:
    return _as_channel_type(channel_type) in _THREAD_CHANNEL_TYPES


def channel_type_error(channel_type: Any) -> str | None:
    """Return a user-facing error when announcements cannot target this channel type."""
    kind = _as_channel_type(channel_type)
    if kind in _TEXT_CHANNEL_TYPES or kind in _THREAD_CHANNEL_TYPES:
        return None
    if kind in _FORUM_CHANNEL_TYPES:
        return (
            "❌ That is a forum channel. Pick a post inside the forum "
            "(or run the command inside the post)."
        )
    return "❌ Pick a text channel, announcement channel, or thread."


def missing_bot_permissions(permissions: Any, *, is_thread: bool) -> list[str]:
    """Names of the permissions the bot lacks to post announcements in the channel."""
    send_attr, send_label = (
        ("send_messages_in_threads", "Send Messages in Threads")
        if is_thread
        else ("send_messages", "Send Messages")
    )
    required = [
        ("view_channel", "View Channel"),
        (send_attr, send_label),
        ("embed_links", "Embed Links"),
    ]
    return [label for attr, label in required if not getattr(permissions, attr, False)]


def format_set_reply(
    repo: str,
    channel_id: str,
    *,
    previous_channel_id: str | None,
    config_channel_id: str | None,
) -> str:
    lines = [f"✅ **{repo}** → <#{channel_id}>"]
    if previous_channel_id == str(channel_id):
        lines.append("This route was already set; nothing changed.")
    elif previous_channel_id:
        lines.append(f"Previously posted to <#{previous_channel_id}>.")
    elif config_channel_id and config_channel_id != str(channel_id):
        lines.append(
            f"Overrides <#{config_channel_id}> from `gitcord.yaml` while this route exists."
        )
    lines.append(
        "PRs and issues opened from now on will post here (from the next sync). "
        "Already-open PRs are not re-posted."
    )
    return "\n".join(lines)


def format_remove_reply(
    repo: str,
    *,
    removed_channel_id: str | None,
    config_channel_id: str | None,
) -> str:
    if removed_channel_id is None:
        if config_channel_id:
            return (
                f"ℹ️ **{repo}** has no route set from Discord. It posts to <#{config_channel_id}> "
                "from `gitcord.yaml`, which can only be changed in the config file."
            )
        return f"ℹ️ **{repo}** has no route set from Discord."
    lines = [f"🗑️ Removed the Discord route for **{repo}** (was <#{removed_channel_id}>)."]
    if config_channel_id:
        lines.append(f"It falls back to <#{config_channel_id}> from `gitcord.yaml`.")
    else:
        lines.append("New PRs and issues for this repo will no longer be posted to a channel.")
    return "\n".join(lines)


def build_route_entries(base: RoutingBase, routes: Mapping[str, str]) -> list[RouteEntry]:
    """Effective routes (Discord overrides YAML), sorted by repo name."""
    entries: dict[str, RouteEntry] = {}
    for repo, channel_id in base.channels.items():
        entries[repo.lower()] = RouteEntry(repo, str(channel_id), SOURCE_CONFIG)
    for repo, channel_id in routes.items():
        entries[repo.lower()] = RouteEntry(repo, str(channel_id), SOURCE_DISCORD)
    return sorted(entries.values(), key=lambda e: e.repo.lower())


def _entry_line(entry: RouteEntry) -> str:
    return f"• **{entry.repo}** `{entry.source}`"


def _join_with_limit(lines: list[str], limit: int) -> str:
    budget = limit - len("…and 9999 more")
    out: list[str] = []
    used = 0
    for index, line in enumerate(lines):
        cost = len(line) + 1
        if used + cost > budget:
            remaining = sum(1 for rest in lines[index:] if rest.startswith("• "))
            if remaining:
                out.append(f"…and {remaining} more")
            break
        out.append(line)
        used += cost
    return "\n".join(out)


def format_route_list(
    entries: list[RouteEntry],
    *,
    channel_id: str | None = None,
    limit: int = ROUTE_LIST_CHAR_LIMIT,
) -> str:
    """Routes for one channel (``channel_id``) or every route grouped by channel."""
    if channel_id is not None:
        here = [e for e in entries if e.channel_id == str(channel_id)]
        if not here:
            return (
                f"No repos post to <#{channel_id}>. Use `/pr-channel set` to connect one, "
                "or `/pr-channel list all:true` to see every route."
            )
        lines = [f"Repos posting to <#{channel_id}>:"] + [_entry_line(e) for e in here]
        return _join_with_limit(lines, limit)

    if not entries:
        return "No repo → channel routes yet. Use `/pr-channel set` to connect one."
    grouped: dict[str, list[RouteEntry]] = {}
    for entry in entries:
        grouped.setdefault(entry.channel_id, []).append(entry)
    lines: list[str] = []
    for group_channel_id, group in grouped.items():
        lines.append(f"<#{group_channel_id}>")
        lines.extend(_entry_line(e) for e in group)
    return _join_with_limit(lines, limit)
