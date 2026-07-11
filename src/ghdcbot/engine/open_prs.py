"""Read-only helpers for listing a contributor's open pull requests."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Sequence


def list_open_prs_for_author(
    open_prs: Iterable[dict],
    github_user: str,
) -> list[dict]:
    """Return open PR dicts authored by github_user, newest first."""
    target = github_user.strip().lower()
    matched = [
        pr
        for pr in open_prs
        if (pr.get("author") or "").strip().lower() == target
    ]
    return sorted(matched, key=_opened_at_sort_key, reverse=True)


def format_open_prs_report(
    *,
    contributor_mention: str,
    github_user: str,
    prs: Sequence[dict],
    org: str,
    max_items: int = 20,
) -> str:
    """Build a Discord message listing open PRs for a contributor."""
    header = f"Open PRs for {contributor_mention} (GitHub: {github_user})"
    if not prs:
        return f"{header}\n\nNo open PRs found in configured repos."

    lines = [header, ""]
    shown = list(prs[:max_items])
    for index, pr in enumerate(shown, start=1):
        repo = pr.get("repo", "?")
        number = pr.get("number", "?")
        title = (pr.get("title") or "No title").strip()
        if len(title) > 80:
            title = f"{title[:77]}..."
        opened = _format_opened_at(pr.get("created_at"))
        url = pr.get("html_url") or f"https://github.com/{org}/{repo}/pull/{number}"
        lines.append(f"{index}. **{repo}**#{number} — {title}")
        lines.append(f"   opened {opened}")
        lines.append(f"   {url}")

    remaining = len(prs) - len(shown)
    if remaining > 0:
        lines.append("")
        lines.append(f"…and {remaining} more open PR(s) not shown.")
    return "\n".join(lines)


def _opened_at_sort_key(pr: dict) -> datetime:
    parsed = _parse_created_at(pr.get("created_at"))
    return parsed or datetime.min.replace(tzinfo=timezone.utc)


def _parse_created_at(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _format_opened_at(value: object) -> str:
    parsed = _parse_created_at(value)
    if parsed is None:
        return "unknown time"
    return parsed.strftime("%Y-%m-%d %H:%M UTC")
