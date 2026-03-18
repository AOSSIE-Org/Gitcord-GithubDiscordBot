"""Audit export filtering and formatting (read-only, pure functions)."""

from __future__ import annotations

from datetime import datetime, timezone


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
        user: Match github_user in context OR actor_id (discord_user_id)
        event_type: Exact match on event_type field
        from_time: Inclusive lower bound (UTC)
        to_time: Inclusive upper bound (UTC)
    
    Returns:
        Filtered list of events (same structure, no mutation)
    """
    filtered = events
    if user:
        filtered = [
            e
            for e in filtered
            if (
                e.get("actor_id") == user
                or e.get("context", {}).get("github_user") == user
            )
        ]
    if event_type:
        filtered = [e for e in filtered if e.get("event_type") == event_type]
    if from_time:
        from_time_utc = _ensure_utc(from_time)
        max_datetime = datetime.max.replace(tzinfo=timezone.utc)
        filtered = [
            e
            for e in filtered
            if _parse_timestamp(e.get("timestamp", "")) != max_datetime
            and _parse_timestamp(e.get("timestamp", "")) >= from_time_utc
        ]
    if to_time:
        to_time_utc = _ensure_utc(to_time)
        max_datetime = datetime.max.replace(tzinfo=timezone.utc)
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
    import json
    
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["ts", "event_type", "github_user", "discord_user_id", "repo", "target", "details"])
    for e in events:
        context = e.get("context", {})
        github_user = context.get("github_user", "")
        discord_user_id = e.get("actor_id", "") if e.get("actor_type") == "discord_user" else ""
        repo = context.get("repo", "")
        target = context.get("target", "") or context.get("location", "") or ""
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
        group_events = sorted(by_type[event_type], key=lambda x: x.get("timestamp", ""))
        lines.append(f"## {event_type}\n")
        lines.append("| Timestamp | Actor | GitHub User | Details |")
        lines.append("|-----------|-------|-------------|---------|")
        for e in group_events:
            context = e.get("context", {})
            github_user = context.get("github_user", "")
            actor_type = e.get("actor_type", "")
            actor_id = e.get("actor_id", "")
            actor = f"{actor_type}:{actor_id}" if actor_id else actor_type
            details_parts = []
            for k, v in context.items():
                if k not in ("github_user",):
                    details_parts.append(f"{k}={v}")
            details = ", ".join(details_parts) or "—"
            ts = e.get("timestamp", "")
            lines.append(f"| {ts} | {actor} | {github_user} | {details} |")
        lines.append("")
    return "\n".join(lines)


def _ensure_utc(value: datetime) -> datetime:
    """Normalize timestamps to UTC with tzinfo."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_timestamp(value: str) -> datetime:
    """Parse ISO-8601 timestamp to UTC datetime.
    
    Returns datetime.max for empty values to ensure events with missing timestamps
    are excluded from time-range checks (>= from_time and <= to_time).
    """
    if not value:
        return datetime.max.replace(tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
