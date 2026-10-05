"""Weekly maintainer digest — auto-only, once per ISO week.

Built from SQLite contributions + tracked PR announcements. No slash command.
Distinct from discord.activity_channel_id (per-sync activity.md dump).
Contributors are shown by GitHub login only; the digest never reveals
which Discord account is linked to which GitHub account.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Sequence

from ghdcbot.config.models import DigestConfig
from ghdcbot.core.models import ContributionEvent
from ghdcbot.engine.metrics import UserMetrics, get_contribution_metrics, rank_by_activity

if TYPE_CHECKING:
    from ghdcbot.core.interfaces import DiscordWriter, Storage
    from ghdcbot.core.modes import MutationPolicy

logger = logging.getLogger("ghdcbot.engine.digest")

# Neutral info color (not Bruno yellow/green/red lifecycle colors).
_DIGEST_EMBED_COLOR = 0x2F81F7
DIGEST_ATTENTION_LIMIT = 5
_TITLE_MAX = 60

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


@dataclass(frozen=True)
class DigestOpenPR:
    repo: str
    pr_number: int
    title: str
    opened_at: datetime
    channel_id: str
    message_id: str


@dataclass(frozen=True)
class DigestAttention:
    open_tracked_prs: int
    new_prs_still_open: int
    oldest_open: tuple[DigestOpenPR, ...] = ()


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
    top_n: int,
) -> tuple[DigestContributor, ...]:
    ranked = [
        m
        for m in rank_by_activity(list(metrics))
        if not is_bot_login(m.github_user) and m.prs_merged > 0
    ]
    return tuple(
        DigestContributor(github_user=m.github_user, prs_merged=m.prs_merged)
        for m in ranked[:top_n]
    )


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


def list_oldest_open_prs(storage: Storage, limit: int) -> tuple[DigestOpenPR, ...]:
    """Oldest open tracked PRs by human authors (unknown authors are kept)."""
    list_fn = getattr(storage, "list_oldest_open_pr_announcements", None)
    if not callable(list_fn):
        return ()
    out: list[DigestOpenPR] = []
    for row in list_fn():
        author = row.get("author_github")
        if author and is_bot_login(author):
            continue
        out.append(
            DigestOpenPR(
                repo=row["repo"],
                pr_number=int(row["pr_number"]),
                title=row.get("pr_title") or "",
                opened_at=row["created_at"],
                channel_id=str(row["channel_id"]),
                message_id=str(row["message_id"]),
            )
        )
        if len(out) >= limit:
            break
    return tuple(out)


def build_digest_report(
    storage: Storage,
    *,
    org: str,
    period_start: datetime,
    period_end: datetime,
    top_n: int,
) -> DigestReport:
    events = list(storage.list_contributions(period_start))
    in_window = _events_in_window(events, period_start, period_end)
    pulse = build_digest_pulse(in_window)
    metrics = get_contribution_metrics(storage, period_start, period_end)
    top = build_top_contributors(metrics, top_n)
    attention = DigestAttention(
        open_tracked_prs=count_open_tracked_prs(storage),
        new_prs_still_open=count_new_prs_still_open(in_window),
        oldest_open=list_oldest_open_prs(storage, DIGEST_ATTENTION_LIMIT),
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


def _short_title(title: str) -> str:
    title = " ".join(title.replace("`", "'").split())
    if len(title) > _TITLE_MAX:
        return title[: _TITLE_MAX - 1] + "…"
    return title


def _format_open_pr(pr: DigestOpenPR, *, org: str, guild_id: str | None, now: datetime) -> str:
    days = max(0, (now - pr.opened_at).days)
    age = f"open {days} day{'s' if days != 1 else ''}"
    url = f"https://github.com/{org}/{pr.repo}/pull/{pr.pr_number}"
    line = f"• [{pr.repo} #{pr.pr_number}]({url})"
    if pr.title:
        line += f" {_short_title(pr.title)}"
    line += f" — {age}"
    if guild_id:
        line += f" · [post](https://discord.com/channels/{guild_id}/{pr.channel_id}/{pr.message_id})"
    return line


def format_digest_embed(report: DigestReport, *, guild_id: str | None = None) -> dict[str, Any]:
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
            lines.append(f"{i}. `{c.github_user}` — {c.prs_merged} merged")
    else:
        lines.append("No merges by human contributors in this period.")
    lines.append("")
    lines.append("**Needs attention**")
    attention = report.attention
    if attention.open_tracked_prs == 0 and attention.new_prs_still_open == 0:
        lines.append("• Nothing flagged — no open tracked PRs and no new still-open PRs this week.")
    else:
        lines.append(f"• **{attention.open_tracked_prs}** open PRs Gitcord is tracking")
        lines.append(f"• **{attention.new_prs_still_open}** new PRs still open this week")
        if attention.oldest_open:
            lines.append("")
            lines.append("**Oldest open PRs**")
            for pr in attention.oldest_open:
                lines.append(
                    _format_open_pr(pr, org=report.org, guild_id=guild_id, now=report.period_end)
                )
    description = "\n".join(lines)
    if len(description) > 3900:
        description = description[:3897] + "..."
    return {"description": description, "color": _DIGEST_EMBED_COLOR}


def preview_weekly_digest(
    *,
    storage: Storage,
    org: str,
    digest_config: DigestConfig,
    guild_id: str | None = None,
    now: datetime | None = None,
) -> str:
    """Render the digest as it would be posted now (read-only; no Discord, no dedupe claim)."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    report = build_digest_report(
        storage,
        org=org,
        period_start=now - timedelta(days=digest_config.lookback_days),
        period_end=now,
        top_n=digest_config.top_n,
    )
    return format_digest_embed(report, guild_id=guild_id)["description"]


def maybe_post_weekly_digest(
    *,
    storage: Storage,
    discord_writer: DiscordWriter,
    policy: MutationPolicy,
    org: str,
    digest_config: DigestConfig,
    guild_id: str | None = None,
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
        )
        embed = format_digest_embed(report, guild_id=guild_id)
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
