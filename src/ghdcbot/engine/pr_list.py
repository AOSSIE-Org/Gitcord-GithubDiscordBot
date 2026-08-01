"""Helpers for /pr: list a contributor's recent PRs grouped by status."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Sequence

# Discord message hard limit is 2000; keep room for headers.
_MAX_MESSAGE_CHARS = 1900
_DEFAULT_COUNT = 10
_DEFAULT_MAX_N = 100


def clamp_pr_list_args(*, count: int | None = None, skip: int = 0) -> tuple[int, int]:
    """Normalize N/M for /pr (count = N, skip = M).

    ``count`` defaults to 10 when omitted; max is 100 (message may truncate).
    """
    raw = _DEFAULT_COUNT if count is None else int(count)
    n = max(1, min(raw, _DEFAULT_MAX_N))
    m = max(0, min(int(skip), 500))
    return n, m


def select_recent_prs(
    prs: Iterable[dict],
    *,
    count: int | None = None,
    skip: int = 0,
) -> list[dict]:
    """Return the last ``count`` PRs after skipping ``skip``, newest first."""
    n, m = clamp_pr_list_args(count=count, skip=skip)
    ordered = sorted(prs, key=_recency_sort_key, reverse=True)
    return list(ordered[m : m + n])


def group_prs_by_status(prs: Sequence[dict]) -> dict[str, list[dict]]:
    """Split PRs into closed / merged / open buckets (Bruno display order)."""
    grouped: dict[str, list[dict]] = {"closed": [], "merged": [], "open": []}
    for pr in prs:
        status = str(pr.get("status") or "").strip().lower()
        if status not in grouped:
            status = "closed"
        grouped[status].append(pr)
    return grouped


def format_pr_list_report(
    *,
    contributor_mention: str,
    github_user: str,
    prs: Sequence[dict],
    org: str,
    count: int | None = None,
    skip: int = 0,
) -> str:
    """Build a single Discord-oriented report string (may exceed 2000 chars)."""
    return "\n".join(
        _build_pr_list_lines(
            contributor_mention=contributor_mention,
            github_user=github_user,
            prs=prs,
            org=org,
            count=count,
            skip=skip,
        )
    ).rstrip()


def format_pr_list_messages(
    *,
    contributor_mention: str,
    github_user: str,
    prs: Sequence[dict],
    org: str,
    count: int | None = None,
    skip: int = 0,
) -> list[str]:
    """Build one or more Discord messages under the length limit (no silent truncation)."""
    lines = _build_pr_list_lines(
        contributor_mention=contributor_mention,
        github_user=github_user,
        prs=prs,
        org=org,
        count=count,
        skip=skip,
    )
    return _chunk_message_lines(lines, max_chars=_MAX_MESSAGE_CHARS)


def _build_pr_list_lines(
    *,
    contributor_mention: str,
    github_user: str,
    prs: Sequence[dict],
    org: str,
    count: int | None = None,
    skip: int = 0,
) -> list[str]:
    n, m = clamp_pr_list_args(count=count, skip=skip)
    # Bruno: show GitHub username as a profile link (not "GitHub: name").
    github_link = f"[{github_user}](https://github.com/{github_user})"
    header = f"Recent PRs for {contributor_mention} ({github_link})"
    if m > 0:
        header += f" — showing {n} after skipping {m}"
    else:
        header += f" — last {n}"

    if not prs:
        return [header, "", "No PRs found in configured repos."]

    grouped = group_prs_by_status(prs)
    sections = [
        ("Closed PRs", grouped["closed"]),
        ("Merged PRs", grouped["merged"]),
        ("Open PRs", grouped["open"]),
    ]

    lines = [header, ""]
    for title, bucket in sections:
        lines.append(f"**{title}:**")
        if not bucket:
            lines.append("_None_")
            lines.append("")
            continue
        for index, pr in enumerate(bucket, start=1):
            lines.extend(_format_pr_lines(pr, org=org, index=index))
        lines.append("")
    # Drop trailing blank lines for cleaner chunking.
    while lines and lines[-1] == "":
        lines.pop()
    return lines


def _chunk_message_lines(lines: Sequence[str], *, max_chars: int) -> list[str]:
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

        # +1 for the newline that will join lines
        add_len = len(line) + (1 if current else 0)
        if current and current_len + add_len > max_chars:
            flush()
            # Continuation marker for follow-up messages.
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


def _format_pr_lines(pr: dict, *, org: str, index: int) -> list[str]:
    """One-line Bruno format: ``1. Repo #35 -- title (2026-07-26 12:49 UTC)``."""
    repo = pr.get("repo", "?")
    number = pr.get("number", "?")
    title = (pr.get("title") or "No title").strip()
    if len(title) > 80:
        title = f"{title[:77]}..."
    url = pr.get("html_url") or f"https://github.com/{org}/{repo}/pull/{number}"
    when = _format_timestamp(pr.get("updated_at") or pr.get("created_at"))
    # Keep link on repo#N only (<> suppresses Discord preview embed).
    linked = f"[{repo} #{number}]({_suppress_discord_embed(str(url))})"
    return [f"{index}. {linked} -- {title} ({when})"]


def _suppress_discord_embed(url: str) -> str:
    """Wrap URL in <> so Discord does not render a link preview embed."""
    text = (url or "").strip()
    if not text:
        return text
    if text.startswith("<") and text.endswith(">"):
        return text
    return f"<{text}>"


def _recency_sort_key(pr: dict) -> datetime:
    parsed = _parse_timestamp(pr.get("updated_at") or pr.get("created_at"))
    return parsed or datetime.min.replace(tzinfo=timezone.utc)


def _parse_timestamp(value: object) -> datetime | None:
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


def _format_timestamp(value: object) -> str:
    parsed = _parse_timestamp(value)
    if parsed is None:
        return "unknown time"
    return parsed.strftime("%Y-%m-%d %H:%M UTC")
