"""Helpers for /issue: list recent open issues for a repository."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import Any

from ghdcbot.config.access import cfg_get
from ghdcbot.engine.issue_assignment import resolve_github_to_discord
from ghdcbot.engine.pr_status import (
    get_configured_repo_names,
    is_repo_allowed,
)

_MAX_MESSAGE_CHARS = 1900
_DEFAULT_LIMIT = 10
_MAX_LIMIT = 50


def clamp_issue_limit(limit: int | None = None) -> int:
    """Normalize limit for /issue (default 10, min 1, max 50)."""
    raw = _DEFAULT_LIMIT if limit is None else int(limit)
    return max(1, min(raw, _MAX_LIMIT))


def filter_open_issues(items: Iterable[dict], limit: int = 10) -> list[dict]:
    """Filter out PRs and non-open issues, returning up to ``limit`` issues."""
    n = clamp_issue_limit(limit)
    filtered: list[dict] = []
    for item in items:
        # GitHub /issues endpoint returns both issues and PRs.
        # PRs always have a "pull_request" key in the payload.
        if "pull_request" in item:
            continue
        state = str(item.get("state") or "").strip().lower()
        if state != "open":
            continue
        filtered.append(item)
        if len(filtered) >= n:
            break
    return filtered


def format_issue_description(body: str | None, max_length: int = 180) -> str:
    """Format issue description into a clean, compact blockquote snippet."""
    if not body or not body.strip():
        return "> _No description provided._"

    text = body.strip()
    # Strip HTML comments <!-- ... -->
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    # Strip image markdown ![...](...)
    text = re.sub(r"!\[.*?\]\(.*?\)", "", text)
    # Collapse consecutive whitespace and newlines
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    cleaned = " ".join(lines).strip()

    if not cleaned:
        return "> _No description provided._"

    if len(cleaned) > max_length:
        cleaned = cleaned[: max_length - 3].rstrip() + "..."

    return f"> {cleaned}"


def _suppress_discord_embed(url: str) -> str:
    """Wrap URL in <> so Discord does not render a link preview embed."""
    text = (url or "").strip()
    if not text:
        return text
    if text.startswith("<") and text.endswith(">"):
        return text
    return f"<{text}>"


def format_single_issue_entry(
    issue: dict,
    org: str,
    repo: str,
    storage: Any = None,
) -> list[str]:
    """Format a single issue into markdown lines.

    Includes:
    - Issue number & title with link (<> embeds suppressed)
    - Status (Open 🟢)
    - Who opened it (resolving Discord ID if linked)
    - Comments count
    - Labels (if any)
    - Description snippet
    """
    number = issue.get("number", "?")
    title = (issue.get("title") or "No title").strip()
    url = issue.get("html_url") or f"https://github.com/{org}/{repo}/issues/{number}"
    suppressed_url = _suppress_discord_embed(str(url))

    # Author resolution
    author = (issue.get("user") or {}).get("login") or "unknown"
    author_display = author
    if storage is not None:
        discord_id = resolve_github_to_discord(storage, author)
        if discord_id:
            author_display = f"<@{discord_id}> ({author})"

    # Comments count
    comments = int(issue.get("comments") or 0)
    comments_str = f"💬 {comments} comment" + ("s" if comments != 1 else "")

    # Labels
    raw_labels = issue.get("labels") or []
    label_names = [
        lbl.get("name")
        for lbl in raw_labels
        if isinstance(lbl, dict) and lbl.get("name")
    ]
    label_str = f"🏷️ {', '.join(label_names)}" if label_names else ""

    meta_parts = [
        "Status: `Open 🟢`",
        f"Opened by: {author_display}",
        comments_str,
    ]
    if label_str:
        meta_parts.append(label_str)

    title_line = f"• [#{number}]({suppressed_url}) — **{title}**"
    meta_line = "  " + " • ".join(meta_parts)
    desc_line = f"  {format_issue_description(issue.get('body'))}"

    return [title_line, meta_line, desc_line]


def _chunk_message_lines(lines: Sequence[str], *, max_chars: int = _MAX_MESSAGE_CHARS) -> list[str]:
    """Pack lines into Discord-sized messages; continue with a short marker."""
    if not lines:
        return [""]

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    def flush() -> None:
        nonlocal current, current_len
        if not current:
            return
        chunks.append("\n".join(current).rstrip())
        current = []
        current_len = 0

    for raw_line in lines:
        line = raw_line
        if len(line) > max_chars:
            line = line[: max_chars - 1] + "…"

        add_len = len(line) + (1 if current else 0)
        if current and current_len + add_len > max_chars:
            flush()
            cont = "*(continued)*"
            current = [cont]
            current_len = len(cont)
            add_len = len(line) + 1

        if current:
            current.append(line)
            current_len += add_len
        else:
            current = [line]
            current_len = len(line)

    flush()
    return chunks or [""]


def format_issue_list_messages(
    issues: Sequence[dict],
    org: str,
    repo: str,
    limit: int = 10,
    storage: Any = None,
) -> list[str]:
    """Build one or more Discord messages under the length limit."""
    n = clamp_issue_limit(limit)
    if not issues:
        return [f"No open issues found in **{repo}**."]

    header = f"Recent Open Issues in **{repo}** — last {len(issues)} (limit {n})"
    lines = [header, ""]

    for issue in issues:
        lines.extend(format_single_issue_entry(issue, org=org, repo=repo, storage=storage))
        lines.append("")

    while lines and lines[-1] == "":
        lines.pop()

    return _chunk_message_lines(lines, max_chars=_MAX_MESSAGE_CHARS)


def resolve_repo_for_issue(
    config: Any,
    channel_id: int | str | None = None,
    channel_name: str | None = None,
    repo: str | None = None,
) -> tuple[str | None, str | None]:
    """Resolve repository name for an issue listing query.

    Resolution order:
    1. Explicit ``repo`` argument (validated against config filters).
    2. Matching channel in ``config.discord.pr_open_channels``.
    3. Matching channel name with configured repositories.
    4. Exactly one configured repository in Gitcord config.
    5. Returns error prompting user to specify repository.
    """
    repo_filter = None
    if config:
        github_cfg = cfg_get(config, "github")
        if github_cfg:
            repo_filter = cfg_get(github_cfg, "repos")

    # 1. Explicit repo argument
    if repo and repo.strip():
        cleaned = repo.strip()
        if not is_repo_allowed(repo_filter, cleaned):
            return None, f"❌ Repository **{cleaned}** is not allowed by Gitcord configuration."
        return cleaned, None

    # Available configured repos
    configured_repos = [
        candidate
        for candidate in get_configured_repo_names(config)
        if is_repo_allowed(repo_filter, candidate)
    ]

    # 2. Check config.discord.pr_open_channels
    if channel_id and config:
        discord_cfg = cfg_get(config, "discord")
        if discord_cfg:
            pr_open_channels = cfg_get(discord_cfg, "pr_open_channels")
            if isinstance(pr_open_channels, dict):
                for r, cid in pr_open_channels.items():
                    if str(cid) == str(channel_id) and is_repo_allowed(repo_filter, r):
                        return r, None

    # 3. Check channel_name matching configured repo
    if channel_name:
        clean_chan = channel_name.strip().lstrip("#").lower().replace("-", "_")
        for r in configured_repos:
            if r.lower().replace("-", "_") == clean_chan:
                return r, None

    # 4. If exactly one repository is configured
    if len(configured_repos) == 1:
        return configured_repos[0], None

    # 5. Cannot resolve
    if not configured_repos:
        return None, "❌ No repositories configured in Gitcord."

    return None, "❌ Could not auto-detect repository from this channel. Please run this command in a project channel or specify repo."
