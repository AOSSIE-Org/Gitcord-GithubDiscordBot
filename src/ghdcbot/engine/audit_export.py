"""Audit export filtering and formatting (read-only, pure functions)."""

from __future__ import annotations

import json
from datetime import UTC, datetime


def _safe_context(e: dict) -> dict:
    """Extract a dictionary context safely, handling None, JSON strings, or non-dict values."""
    ctx = e.get("context")
    if isinstance(ctx, dict):
        return ctx
    if isinstance(ctx, str):
        try:
            parsed = json.loads(ctx)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, TypeError, ValueError):
            return {"raw": ctx}
        return {"raw": ctx}
    return {}


def _escape_md_cell(text: object) -> str:
    """Sanitize cell contents for Markdown table columns, preventing layout breakage."""
    s = str(text if text is not None else "").replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    return s.replace("|", "\\|").strip()


def filter_audit_events(
    events: list[dict],
    *,
    user: str | None = None,
    event_type: str | None = None,
    from_time: datetime | None = None,
    to_time: datetime | None = None,
) -> list[dict]:
    """Filter audit events by user, event_type, and time range.
    
    Args:
        events: List of audit event dicts
        user: Match github_user in context (case-insensitive) OR actor_id (discord_user_id)
        event_type: Exact match on event_type field
        from_time: Inclusive lower bound (UTC)
        to_time: Inclusive upper bound (UTC)
    
    Returns:
        Filtered list of events (same structure, no mutation)
    """
    filtered = events
    if user:
        u_str = str(user).strip()
        u_lower = u_str.lower()
        filtered = [
            e
            for e in filtered
            if (
                str(e.get("actor_id") or "").strip() == u_str
                or str(_safe_context(e).get("github_user") or e.get("creator_github") or "").strip().lower() == u_lower
            )
        ]
    if event_type:
        filtered = [e for e in filtered if e.get("event_type") == event_type]
    if from_time:
        from_time_utc = _ensure_utc(from_time)
        max_datetime = datetime.max.replace(tzinfo=UTC)
        filtered = [
            e
            for e in filtered
            if _parse_timestamp(e.get("timestamp", "")) != max_datetime
            and _parse_timestamp(e.get("timestamp", "")) >= from_time_utc
        ]
    if to_time:
        to_time_utc = _ensure_utc(to_time)
        max_datetime = datetime.max.replace(tzinfo=UTC)
        filtered = [
            e
            for e in filtered
            if _parse_timestamp(e.get("timestamp", "")) != max_datetime
            and _parse_timestamp(e.get("timestamp", "")) <= to_time_utc
        ]
    return filtered


def format_audit_csv(events: list[dict]) -> str:
    """Format audit events as CSV.
    
    Columns: ts, event_type, github_user, discord_user_id, repo, target, details
    """
    import csv
    import io
    
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["ts", "event_type", "github_user", "discord_user_id", "repo", "target", "details"])
    for e in events:
        context = _safe_context(e)
        github_user = context.get("github_user") or e.get("creator_github") or ""
        discord_user_id = (
            str(e.get("actor_id") or "")
            if e.get("actor_type") == "discord_user"
            else str(e.get("creator_discord_id") or "")
        )
        repo = context.get("repo") or e.get("repo") or ""
        target = context.get("target") or context.get("location") or ""
        details = json.dumps(context, separators=(",", ":"))
        w.writerow([
            e.get("timestamp", ""),
            e.get("event_type", ""),
            github_user,
            discord_user_id,
            repo,
            target,
            details,
        ])
    return buf.getvalue()


def format_audit_markdown(events: list[dict]) -> str:
    """Format audit events as Markdown table, grouped by event_type.
    
    Groups events by event_type, sorts by timestamp ascending.
    """
    if not events:
        return "# Audit Events\n\nNo events found.\n"
    
    # Group by event_type
    by_type: dict[str, list[dict]] = {}
    for e in events:
        et = e.get("event_type", "unknown")
        by_type.setdefault(et, []).append(e)
    
    # Sort event types, then sort events within each group by timestamp
    lines = ["# Audit Events\n"]
    for event_type in sorted(by_type.keys()):
        group_events = sorted(by_type[event_type], key=lambda x: str(x.get("timestamp", "")))
        lines.append(f"## {event_type}\n")
        lines.append("| Timestamp | Actor | GitHub User | Details |")
        lines.append("|-----------|-------|-------------|---------|")
        for e in group_events:
            context = _safe_context(e)
            github_user = context.get("github_user") or e.get("creator_github") or ""
            actor_type = e.get("actor_type", "")
            actor_id = e.get("actor_id", "") or e.get("creator_discord_id", "")
            actor = f"{actor_type}:{actor_id}" if actor_id else actor_type
            details_parts = []
            for k, v in context.items():
                if k not in ("github_user",):
                    details_parts.append(f"{k}={v}")
            details = ", ".join(details_parts) or "—"
            ts = e.get("timestamp", "")
            lines.append(
                f"| {_escape_md_cell(ts)} | {_escape_md_cell(actor)} | "
                f"{_escape_md_cell(github_user)} | {_escape_md_cell(details)} |"
            )
        lines.append("")
    return "\n".join(lines)


def _ensure_utc(value: datetime) -> datetime:
    """Normalize timestamps to UTC with tzinfo."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _parse_timestamp(value: object) -> datetime:
    """Parse ISO-8601 timestamp to UTC datetime safely.
    
    Returns datetime.max for empty or invalid values to ensure events with missing
    or malformed timestamps are excluded from time-range checks (>= from_time and <= to_time).
    """
    if not isinstance(value, str) or not value.strip():
        return datetime.max.replace(tzinfo=UTC)
    try:
        val = value.strip()
        parsed = datetime.fromisoformat(val[:-1] + "+00:00" if val.endswith("Z") else val)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    except (ValueError, TypeError, AttributeError):
        return datetime.max.replace(tzinfo=UTC)
