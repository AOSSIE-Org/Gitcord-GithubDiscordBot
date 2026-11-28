"""Pending review requests backlog and triage engine."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

# Discord embeds enforce a hard aggregate maximum of 6000 characters across all text fields.
# We reserve 200 characters for safety and Discord-internal formatting overhead.
EMBED_SAFE_BUDGET = 5800


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
    page: int = 1,
    incomplete: bool = False,
) -> dict[str, Any]:
    """Build a rich Discord embed dictionary for pending review requests.

    Enforces Discord's 6000-character embed limit and partitions items across budget-aware pages.
    Returns dict suitable for discord.Embed.from_dict().
    """
    if now is None:
        now = datetime.now(UTC)

    if not reviews:
        if incomplete:
            return {
                "title": "📋 Pending Review Requests",
                "description": (
                    f"⚠️ Search reached the GitHub API limit without completing all matches for "
                    f"{target_mention} (`{github_user}`). "
                    f"Please specify the `repo` parameter to inspect a specific repository."
                ),
                "color": 0xFEE75C,  # Yellow warning
                "footer": {"text": f"Organization: {org} • /reviews"},
            }
        return {
            "title": "📋 Pending Review Requests",
            "description": (
                f"🎉 **All caught up!** No pending review requests found for "
                f"{target_mention} (`{github_user}`) across configured repositories."
            ),
            "color": 0x57F287,  # Green
            "footer": {"text": f"Organization: {org} • /reviews"},
        }

    # Sort reviews by active review request timestamp (or creation timestamp as fallback), oldest first
    sorted_reviews = sorted(
        reviews,
        key=lambda r: _parse_iso_datetime(r.get("requested_at") or r.get("created_at"))
        or datetime.max.replace(tzinfo=UTC),
    )

    fresh_count = 0
    aging_count = 0
    stale_count = 0
    classified: list[tuple[dict, str, str, str]] = []

    for pr in sorted_reviews:
        timestamp = pr.get("requested_at") or pr.get("created_at")
        tier, emoji = classify_review_staleness(timestamp, now)
        wait_str = format_review_wait_time(timestamp, now)
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
    incomplete_notice = "⚠️ *Search limit reached; results may be partial (narrow by repo).*\n" if incomplete else ""
    description = (
        f"Review backlog for {target_mention} (`{github_user}`)\n"
        f"{incomplete_notice}"
        f"Total: **{total}** pull request(s) awaiting review\n"
        f"{summary_counts}"
    )

    title = f"📋 Pending Review Requests ({total})"
    base_overhead = len(title) + len(description)

    # Budget-aware partitioning: group items into pages such that every item appears
    # on exactly one page and no page exceeds the character budget or max_items cap.
    pages = _partition_into_pages(
        classified,
        org=org,
        base_overhead=base_overhead,
        max_items=max_items,
        budget=EMBED_SAFE_BUDGET,
    )

    total_pages = max(1, len(pages))
    current_page = min(max(1, page), total_pages)
    fields = pages[current_page - 1] if pages else []

    footer_parts: list[str] = []
    if incomplete:
        footer_parts.append("⚠️ Partial results")
    if total_pages > 1:
        page_str = f"Page {current_page} of {total_pages}"
        if current_page < total_pages:
            page_str += f" (use page:{current_page + 1})"
        footer_parts.append(page_str)
    footer_parts.append(f"Organization: {org} • /reviews")

    footer_text = " • ".join(footer_parts)

    return {
        "title": title,
        "description": description,
        "color": embed_color,
        "fields": fields,
        "footer": {"text": footer_text},
    }


def _format_item_field(pr: dict, emoji: str, wait_str: str, org: str) -> tuple[str, str]:
    repo = pr.get("repo", "?")
    number = pr.get("number", "?")
    title_text = (pr.get("title") or "No title").strip()
    if len(title_text) > 65:
        title_text = f"{title_text[:62]}..."
    author = pr.get("author", "unknown")
    url = pr.get("html_url") or f"https://github.com/{org}/{repo}/pull/{number}"
    files_url = f"{url}/files"
    draft_badge = " `[Draft]`" if pr.get("draft") else ""

    field_name = f"{emoji} {repo}#{number}{draft_badge} — {title_text}"
    if len(field_name) > 256:
        field_name = field_name[:253] + "..."

    field_value = (
        f"Author: `{author}` • **{wait_str}**\n"
        f"[View PR]({url}) • [Review Diff]({files_url})"
    )
    if len(field_value) > 1024:
        field_value = field_value[:1021] + "..."
    return (field_name, field_value)


def _partition_into_pages(
    classified: list[tuple[dict, str, str, str]],
    *,
    org: str,
    base_overhead: int,
    max_items: int = 15,
    budget: int = EMBED_SAFE_BUDGET,
) -> list[list[dict[str, Any]]]:
    """Partition items into pages accounting for item count cap and character budget.

    Guarantees every item appears on exactly one page and no page exceeds the character budget.
    """
    if not classified:
        return []

    max_fields_per_page = min(max(1, max_items), 25)
    available_fields_budget = max(500, budget - base_overhead - 150)

    pages: list[list[dict[str, Any]]] = []
    current_page_fields: list[dict[str, Any]] = []
    current_page_chars = 0

    for pr, _tier, emoji, wait_str in classified:
        field_name, field_value = _format_item_field(pr, emoji, wait_str, org)
        field_chars = len(field_name) + len(field_value)

        if current_page_fields and (
            len(current_page_fields) >= max_fields_per_page
            or (current_page_chars + field_chars) > available_fields_budget
        ):
            pages.append(current_page_fields)
            current_page_fields = []
            current_page_chars = 0

        current_page_fields.append({
            "name": field_name,
            "value": field_value,
            "inline": False,
        })
        current_page_chars += field_chars

    if current_page_fields:
        pages.append(current_page_fields)

    return pages


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
