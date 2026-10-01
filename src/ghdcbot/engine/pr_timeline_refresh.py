"""Keep tracked PR channel posts in sync with each PR's review timeline.

Each sync: record open PR heads (to detect pushes), queue tracked posts whose PR had
activity, then rebuild each queued post from GitHub and edit it only when the rendered
post changed. Read-only on GitHub; never posts new messages or DMs.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

from ghdcbot.core.interfaces import DiscordWriter, Storage
from ghdcbot.core.modes import MutationPolicy
from ghdcbot.core.models import ContributionEvent
from ghdcbot.engine import pr_timeline as prt
from ghdcbot.engine.notifications import (
    _resolve_github_to_discord,
    build_pr_timeline_channel_message,
)

logger = logging.getLogger("PRTimeline")

PR_TIMELINE_EVENT_TYPES = frozenset(
    {"pr_opened", "pr_reviewed", "pr_merged", "pr_closed", "pr_reopened"}
)
MAX_REFRESHES_PER_SYNC = 40
GIVE_UP_AFTER_FAILURES = 5


def timeline_enabled(config: Any) -> bool:
    return bool(
        config is not None
        and getattr(config, "enabled", False)
        and getattr(config, "pr_channel_timeline", False)
    )


def _status_for_row(timeline: prt.PRTimeline) -> str:
    if timeline.status == prt.STATUS_MERGED:
        return "merged"
    if timeline.status == prt.STATUS_CLOSED:
        return "closed"
    return "open"


def _discord_ids_for(storage: Storage, timeline: prt.PRTimeline) -> dict[str, str]:
    ids: dict[str, str] = {}
    for login in {timeline.author, *(entry.actor for entry in timeline.entries)}:
        if not login or login.lower().endswith("[bot]") or login.lower() in ids:
            continue
        discord_id = _resolve_github_to_discord(storage, login)
        if discord_id:
            ids[login.lower()] = discord_id
    return ids


def render_pr_post(
    snapshot: dict,
    *,
    repo: str,
    pr_number: int,
    github_org: str,
    storage: Storage,
) -> tuple[prt.PRTimeline, str, list[dict]]:
    observations: dict = {}
    get_obs = getattr(storage, "get_pr_head_observations", None)
    if callable(get_obs):
        try:
            observations = get_obs(repo, pr_number)
        except Exception:
            logger.warning(
                "Could not read PR head observations; using commit dates only",
                exc_info=True,
                extra={"repo": repo, "pr_number": pr_number},
            )
    timeline = prt.build_pr_timeline(
        snapshot["pr"], snapshot["timeline"], snapshot["commits"], observations
    )
    content, embeds = build_pr_timeline_channel_message(
        timeline,
        github_org=github_org,
        repo=repo,
        pr_number=pr_number,
        discord_ids=_discord_ids_for(storage, timeline),
    )
    return timeline, content, embeds


def preview_pr_posts(
    targets: Iterable[tuple[str, int]],
    *,
    github_reader: Any,
    storage: Storage,
    github_org: str,
) -> str:
    """Text preview of the timeline posts for ``(repo, pr_number)`` targets. Read-only."""
    blocks: list[str] = []
    for repo, pr_number in targets:
        snapshot = github_reader.get_pr_timeline_snapshot(repo, pr_number)
        if snapshot is None:
            blocks.append(f"{repo}#{pr_number}: could not read the PR from GitHub")
            continue
        timeline, content, embeds = render_pr_post(
            snapshot, repo=repo, pr_number=pr_number, github_org=github_org, storage=storage
        )
        parts = [f"=== {repo}#{pr_number} status={timeline.status}"]
        if content:
            parts.append(content)
        parts.append(embeds[0]["description"])
        blocks.append("\n".join(parts))
    return "\n\n".join(blocks)


def queue_pr_timeline_refreshes(
    contributions: Iterable[ContributionEvent],
    open_prs: Iterable[dict],
    storage: Storage,
) -> int:
    """Queue tracked posts for PRs with new events or a new head commit."""
    observe = getattr(storage, "observe_pr_heads", None)
    if callable(observe):
        observe(
            (str(pr.get("repo")), int(pr.get("number")), str(pr.get("head_sha") or ""))
            for pr in open_prs
            if pr.get("repo") and pr.get("number") is not None
        )
    keys = {
        (event.repo, int(event.payload["pr_number"]))
        for event in contributions
        if event.event_type in PR_TIMELINE_EVENT_TYPES
        and event.payload.get("pr_number") is not None
    }
    mark = getattr(storage, "mark_pr_announcements_for_refresh", None)
    if keys and callable(mark):
        mark(sorted(keys))
    return len(keys)


def refresh_pr_channel_timelines(
    *,
    contributions: Iterable[ContributionEvent],
    open_prs: Iterable[dict],
    storage: Storage,
    github_reader: Any,
    discord_writer: DiscordWriter,
    policy: MutationPolicy,
    config: Any,
    github_org: str,
) -> int:
    """Run one refresh pass. Returns the number of posts edited."""
    if not timeline_enabled(config):
        return 0
    list_queued = getattr(storage, "list_pr_announcements_needing_refresh", None)
    get_snapshot = getattr(github_reader, "get_pr_timeline_snapshot", None)
    if not callable(list_queued) or not callable(get_snapshot):
        return 0

    queue_pr_timeline_refreshes(contributions, open_prs, storage)
    rows = list_queued(MAX_REFRESHES_PER_SYNC)
    edited = 0
    for row in rows:
        repo, pr_number = row["repo"], int(row["pr_number"])
        try:
            if _refresh_one(row, storage, get_snapshot, discord_writer, policy, github_org):
                edited += 1
        except Exception:
            logger.warning(
                "PR timeline refresh failed",
                exc_info=True,
                extra={"repo": repo, "pr_number": pr_number},
            )
            storage.record_pr_timeline_refresh_failure(
                repo, pr_number, give_up_after=GIVE_UP_AFTER_FAILURES
            )
    if rows:
        logger.info(
            "PR timeline refresh pass",
            extra={"queued": len(rows), "edited": edited},
        )
    return edited


def _refresh_one(
    row: dict,
    storage: Storage,
    get_snapshot: Any,
    discord_writer: DiscordWriter,
    policy: MutationPolicy,
    github_org: str,
) -> bool:
    repo, pr_number = row["repo"], int(row["pr_number"])
    snapshot = get_snapshot(repo, pr_number)
    if snapshot is None:
        failures = storage.record_pr_timeline_refresh_failure(
            repo, pr_number, give_up_after=GIVE_UP_AFTER_FAILURES
        )
        logger.info(
            "PR timeline: GitHub data unavailable; will retry",
            extra={"repo": repo, "pr_number": pr_number, "failures": failures},
        )
        return False

    timeline, content, embeds = render_pr_post(
        snapshot, repo=repo, pr_number=pr_number, github_org=github_org, storage=storage
    )
    fingerprint = prt.render_fingerprint(content, embeds)
    if fingerprint == row.get("render_hash"):
        storage.clear_pr_announcement_refresh(repo, pr_number)
        return False
    if not policy.allow_discord_mutations:
        logger.info(
            "PR timeline: would edit post (Discord writes disabled)",
            extra={"repo": repo, "pr_number": pr_number, "status": timeline.status},
        )
        storage.clear_pr_announcement_refresh(repo, pr_number)
        return False

    edit_msg = getattr(discord_writer, "edit_message", None)
    if not callable(edit_msg):
        return False
    ok = bool(
        edit_msg(str(row["channel_id"]), str(row["message_id"]), content, embeds=embeds)
    )
    if not ok:
        failures = storage.record_pr_timeline_refresh_failure(
            repo, pr_number, give_up_after=GIVE_UP_AFTER_FAILURES
        )
        logger.warning(
            "PR timeline: Discord edit failed",
            extra={
                "repo": repo,
                "pr_number": pr_number,
                "channel_id": row["channel_id"],
                "failures": failures,
                "gave_up": failures >= GIVE_UP_AFTER_FAILURES,
            },
        )
        return False
    storage.record_pr_timeline_render(
        repo,
        pr_number,
        render_hash=fingerprint,
        status=_status_for_row(timeline),
        pr_title=timeline.title or None,
    )
    return True
