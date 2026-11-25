"""Pending review requests backlog and triage engine."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any


def classify_review_staleness(
    created_at: datetime | str | None,
    now: datetime | None = None,
) -> tuple[str, str]:
    """Classify review staleness tier and emoji indicator.

    Returns:
        (tier, emoji):
        - ("fresh", "🟢") if < 48 hours
        - ("aging", "🟡") if 2-5 days (48h - 120h)
        - ("stale", "🔴") if >= 5 days (120h+)
    """
    if now is None:
        now = datetime.now(UTC)

    dt = _parse_iso_datetime(created_at)
    if dt is None:
        return ("fresh", "🟢")

    wait_seconds = max(0, (now - dt).total_seconds())
    wait_hours = wait_seconds / 3600.0

    if wait_hours < 48:
        return ("fresh", "🟢")
    elif wait_hours < 120:
        return ("aging", "🟡")
    else:
        return ("stale", "🔴")


def format_review_wait_time(
    created_at: datetime | str | None,
    now: datetime | None = None,
) -> str:
    """Format human-readable wait time for pending review."""
    if now is None:
        now = datetime.now(UTC)

    dt = _parse_iso_datetime(created_at)
    if dt is None:
        return "unknown wait time"

    wait_seconds = max(0, int((now - dt).total_seconds()))
    if wait_seconds < 3600:
        mins = max(1, wait_seconds // 60)
        return f"{mins}m waiting"
    elif wait_seconds < 86400:
        hours = wait_seconds // 3600
        return f"{hours}h waiting"
    else:
        days = wait_seconds // 86400
        return f"{days}d waiting"


def build_review_queue_embed(
    *,
    reviews: Sequence[dict],
    github_user: str,
    target_mention: str,
    org: str,
    now: datetime | None = None,
    max_items: int = 15,
) -> dict[str, Any]:
    """Build a rich Discord embed dictionary for pending review requests.

    Returns dict suitable for discord.Embed.from_dict().
    """
    if now is None:
        now = datetime.now(UTC)

    if not reviews:
        return {
            "title": "📋 Pending Review Requests",
            "description": (
                f"🎉 **All caught up!** No pending review requests found for "
                f"{target_mention} (`{github_user}`) across configured repositories."
            ),
            "color": 0x57F287,  # Green
            "footer": {"text": f"Organization: {org} • /reviews"},
        }

    fresh_count = 0
    aging_count = 0
    stale_count = 0
    classified: list[tuple[dict, str, str, str]] = []

    for pr in reviews:
        tier, emoji = classify_review_staleness(pr.get("created_at"), now)
        wait_str = format_review_wait_time(pr.get("created_at"), now)
        if tier == "stale":
            stale_count += 1
        elif tier == "aging":
            aging_count += 1
        else:
            fresh_count += 1
        classified.append((pr, tier, emoji, wait_str))

    total = len(reviews)

    if stale_count > 0:
        embed_color = 0xED4245  # Red
    elif aging_count > 0:
        embed_color = 0xFEE75C  # Yellow
    else:
        embed_color = 0x57F287  # Green

    summary_counts = (
        f"🟢 **{fresh_count}** fresh (<48h) • "
        f"🟡 **{aging_count}** aging (2–5d) • "
        f"🔴 **{stale_count}** stale (>5d)"
    )
    description = (
        f"Review backlog for {target_mention} (`{github_user}`)\n"
        f"Total: **{total}** pull request(s) awaiting review\n"
        f"{summary_counts}"
    )

    fields: list[dict[str, Any]] = []
    shown = classified[:max_items]

    for pr, _tier, emoji, wait_str in shown:
        repo = pr.get("repo", "?")
        number = pr.get("number", "?")
        title = (pr.get("title") or "No title").strip()
        if len(title) > 65:
            title = f"{title[:62]}..."
        author = pr.get("author", "unknown")
        url = pr.get("html_url") or f"https://github.com/{org}/{repo}/pull/{number}"
        files_url = f"{url}/files"
        draft_badge = " `[Draft]`" if pr.get("draft") else ""

        field_name = f"{emoji} {repo}#{number}{draft_badge} — {title}"
        if len(field_name) > 256:
            field_name = field_name[:253] + "..."

        field_value = (
            f"Author: `{author}` • **{wait_str}**\n"
            f"[View PR]({url}) • [Review Diff]({files_url})"
        )

        fields.append({
            "name": field_name,
            "value": field_value,
            "inline": False,
        })

    remaining = total - len(shown)
    footer_text = f"Organization: {org} • /reviews"
    if remaining > 0:
        footer_text = f"…and {remaining} more PR(s) • " + footer_text

    return {
        "title": f"📋 Pending Review Requests ({total})",
        "description": description,
        "color": embed_color,
        "fields": fields,
        "footer": {"text": footer_text},
    }


def _parse_iso_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    if not isinstance(value, str) or not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
