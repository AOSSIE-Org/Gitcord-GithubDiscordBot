"""Weekly maintainer digest — auto-only, once per ISO week.

Built from SQLite contributions + tracked PR announcements. No slash command.
Distinct from discord.activity_channel_id (per-sync activity.md dump).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Sequence

from ghdcbot.config.models import DigestConfig, IdentityMapping
from ghdcbot.core.models import ContributionEvent
from ghdcbot.engine.metrics import UserMetrics, get_contribution_metrics, rank_by_activity

if TYPE_CHECKING:
    from ghdcbot.core.interfaces import DiscordWriter, Storage
    from ghdcbot.core.modes import MutationPolicy

logger = logging.getLogger("ghdcbot.engine.digest")

# Neutral info color (not Bruno yellow/green/red lifecycle colors).
_DIGEST_EMBED_COLOR = 0x2F81F7

_BOT_LOGINS = frozenset(
    {
        "dependabot",
        "dependabot[bot]",
        "github-actions",
        "github-actions[bot]",
        "renovate",
        "renovate[bot]",
        "coderabbitai",
        "coderabbitai[bot]",
        "imgbot",
        "imgbot[bot]",
        "greenkeeper[bot]",
        "snyk-bot",
    }
)


@dataclass(frozen=True)
class DigestPulse:
    prs_merged: int
    issues_closed: int
    issues_opened: int
    active_repos: int


@dataclass(frozen=True)
class DigestContributor:
    github_user: str
    prs_merged: int
    discord_user_id: str | None


@dataclass(frozen=True)
class DigestAttention:
    open_tracked_prs: int
    new_prs_still_open: int


@dataclass(frozen=True)
class DigestReport:
    org: str
    period_start: datetime
    period_end: datetime
    pulse: DigestPulse
    top_contributors: tuple[DigestContributor, ...]
    attention: DigestAttention
    quiet_week: bool


def is_bot_login(github_user: str) -> bool:
    login = (github_user or "").strip().lower()
    if not login:
        return True
    if login in _BOT_LOGINS:
        return True
    return login.endswith("[bot]")


def digest_week_key(org: str, when: datetime) -> str:
    """Idempotency key for one post per org per ISO week."""
    year, week, _ = when.isocalendar()
    return f"weekly_digest:{org}:{year}-W{week:02d}"


def digest_is_due(config: DigestConfig, now: datetime) -> bool:
    """True when UTC weekday matches and local UTC hour has reached the configured hour."""
    if not config.enabled or not config.channel_id:
        return False
    if now.weekday() != config.weekday_utc:
        return False
    return now.hour >= config.hour_utc


def _events_in_window(
    events: Sequence[ContributionEvent],
    period_start: datetime,
    period_end: datetime,
) -> list[ContributionEvent]:
    return [e for e in events if period_start <= e.created_at <= period_end]


def build_digest_pulse(events: Sequence[ContributionEvent]) -> DigestPulse:
    prs_merged = 0
    issues_closed = 0
    issues_opened = 0
    repos: set[str] = set()
    for e in events:
        if e.event_type == "pr_merged":
            prs_merged += 1
            repos.add(e.repo)
        elif e.event_type == "issue_closed":
            issues_closed += 1
            repos.add(e.repo)
        elif e.event_type == "issue_opened":
            issues_opened += 1
            repos.add(e.repo)
    return DigestPulse(
        prs_merged=prs_merged,
        issues_closed=issues_closed,
        issues_opened=issues_opened,
        active_repos=len(repos),
    )


def build_top_contributors(
    metrics: Sequence[UserMetrics],
    identity_by_github: dict[str, str],
    top_n: int,
) -> tuple[DigestContributor, ...]:
    ranked = [
        m
        for m in rank_by_activity(list(metrics))
        if not is_bot_login(m.github_user) and m.prs_merged > 0
    ]
    out: list[DigestContributor] = []
    for m in ranked[:top_n]:
        discord_id = identity_by_github.get(m.github_user.lower())
        out.append(
            DigestContributor(
                github_user=m.github_user,
                prs_merged=m.prs_merged,
                discord_user_id=discord_id,
            )
        )
    return tuple(out)


def count_new_prs_still_open(events: Sequence[ContributionEvent]) -> int:
    """PRs with pr_opened in window and no pr_merged/pr_closed for same repo+number."""
    opened: set[tuple[str, int]] = set()
    closed: set[tuple[str, int]] = set()
    for e in events:
        raw = e.payload.get("pr_number")
        if raw is None:
            continue
        try:
            num = int(raw)
        except (TypeError, ValueError):
            continue
        key = (e.repo, num)
        if e.event_type == "pr_opened":
            opened.add(key)
        elif e.event_type in {"pr_merged", "pr_closed"}:
            closed.add(key)
    return len(opened - closed)


def count_open_tracked_prs(storage: Storage) -> int:
    count_fn = getattr(storage, "count_pr_channel_announcements", None)
    if callable(count_fn):
        return int(count_fn(status="open"))
    return 0


def build_digest_report(
    storage: Storage,
    *,
    org: str,
    period_start: datetime,
    period_end: datetime,
    top_n: int,
    identity_mappings: Sequence[IdentityMapping] | None = None,
) -> DigestReport:
    events = list(storage.list_contributions(period_start))
    in_window = _events_in_window(events, period_start, period_end)
    pulse = build_digest_pulse(in_window)
    metrics = get_contribution_metrics(storage, period_start, period_end)
    identity_by_github: dict[str, str] = {}
    if identity_mappings is None:
        list_fn = getattr(storage, "list_verified_identity_mappings", None)
        mappings = list_fn() if callable(list_fn) else []
    else:
        mappings = identity_mappings
    for m in mappings:
        gh = getattr(m, "github_user", None) or (m.get("github_user") if isinstance(m, dict) else None)
        did = getattr(m, "discord_user_id", None) or (
            m.get("discord_user_id") if isinstance(m, dict) else None
        )
        if gh and did:
            identity_by_github[str(gh).lower()] = str(did)
    top = build_top_contributors(metrics, identity_by_github, top_n)
    attention = DigestAttention(
        open_tracked_prs=count_open_tracked_prs(storage),
        new_prs_still_open=count_new_prs_still_open(in_window),
    )
    quiet = pulse.prs_merged == 0 and pulse.issues_closed == 0
    return DigestReport(
        org=org,
        period_start=period_start,
        period_end=period_end,
        pulse=pulse,
        top_contributors=top,
        attention=attention,
        quiet_week=quiet,
    )


def _format_person(c: DigestContributor) -> str:
    if c.discord_user_id:
        return f"`{c.github_user}` (<@{c.discord_user_id}>)"
    return f"`{c.github_user}`"


def format_digest_embed(report: DigestReport) -> dict[str, Any]:
    """Single Discord embed dict (no title field — description carries the header)."""
    start = report.period_start.date().isoformat()
    end = report.period_end.date().isoformat()
    lines: list[str] = [
        f"**Weekly Digest — {report.org}**",
        f"Period: {start} → {end} (UTC)",
        "",
        "**Pulse**",
        f"• PRs merged: **{report.pulse.prs_merged}**",
        f"• Issues closed: **{report.pulse.issues_closed}**",
        f"• Issues opened: **{report.pulse.issues_opened}**",
        f"• Active repos: **{report.pulse.active_repos}**",
        "",
    ]
    if report.quiet_week:
        lines.append("Quiet week: no merges or issue closes in this period.")
        lines.append("")
    lines.append("**Top contributors (by merges)**")
    if report.top_contributors:
        for i, c in enumerate(report.top_contributors, start=1):
            lines.append(f"{i}. {_format_person(c)} — {c.prs_merged} merged")
    else:
        lines.append("No merges by human contributors in this period.")
    lines.append("")
    lines.append("**Needs attention**")
    if report.attention.open_tracked_prs == 0 and report.attention.new_prs_still_open == 0:
        lines.append("• Nothing flagged — no open tracked PRs and no new still-open PRs this week.")
    else:
        lines.append(
            f"• **{report.attention.open_tracked_prs}** open PRs Gitcord is tracking"
        )
        lines.append(
            f"• **{report.attention.new_prs_still_open}** new PRs still open this week"
        )
    description = "\n".join(lines)
    if len(description) > 3900:
        description = description[:3897] + "..."
    return {"description": description, "color": _DIGEST_EMBED_COLOR}


def maybe_post_weekly_digest(
    *,
    storage: Storage,
    discord_writer: DiscordWriter,
    policy: MutationPolicy,
    org: str,
    digest_config: DigestConfig,
    identity_mappings: Sequence[IdentityMapping] | None = None,
    now: datetime | None = None,
) -> bool:
    """Post the weekly digest if due and not yet sent this ISO week.

    Returns True if a message was posted.
    """
    if not policy.allow_discord_mutations:
        return False
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if not digest_is_due(digest_config, now):
        return False

    channel_id = digest_config.channel_id
    assert channel_id  # digest_is_due requires it

    dedupe_key = digest_week_key(org, now)
    was_sent = getattr(storage, "was_notification_sent", None)
    if callable(was_sent) and was_sent(dedupe_key):
        return False

    claim = getattr(storage, "claim_notification_sent", None)
    fake_event = ContributionEvent(
        github_user="system",
        event_type="weekly_digest",
        repo="",
        created_at=now,
        payload={},
    )
    if callable(claim):
        claimed = claim(
            dedupe_key,
            fake_event,
            discord_user_id="",
            channel_id=channel_id,
            target_github_user="system",
        )
        if not claimed:
            return False
    elif callable(was_sent):
        # Fallback without atomic claim
        mark = getattr(storage, "mark_notification_sent", None)
        if callable(mark):
            mark(dedupe_key, fake_event, "", channel_id, target_github_user="system")
    else:
        logger.warning("Digest skipped: storage has no notification dedupe API")
        return False

    period_end = now
    period_start = period_end - timedelta(days=digest_config.lookback_days)
    try:
        report = build_digest_report(
            storage,
            org=org,
            period_start=period_start,
            period_end=period_end,
            top_n=digest_config.top_n,
            identity_mappings=identity_mappings,
        )
        embed = format_digest_embed(report)
        create_msg = getattr(discord_writer, "create_message", None)
        if callable(create_msg):
            message_id = create_msg(channel_id, "", embeds=[embed])
            ok = message_id is not None
        else:
            send_msg = getattr(discord_writer, "send_message", None)
            if not callable(send_msg):
                raise RuntimeError("Discord writer cannot send messages")
            ok = bool(send_msg(channel_id, embed["description"][:1900]))
        if not ok:
            release = getattr(storage, "release_notification_claim", None)
            if callable(release):
                release(dedupe_key)
            logger.warning(
                "Weekly digest post failed",
                extra={"org": org, "channel_id": channel_id, "dedupe_key": dedupe_key},
            )
            return False
        logger.info(
            "Weekly digest posted",
            extra={"org": org, "channel_id": channel_id, "dedupe_key": dedupe_key},
        )
        return True
    except Exception:
        release = getattr(storage, "release_notification_claim", None)
        if callable(release):
            release(dedupe_key)
        logger.exception(
            "Weekly digest failed",
            extra={"org": org, "dedupe_key": dedupe_key},
        )
        return False
