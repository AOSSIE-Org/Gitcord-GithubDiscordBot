"""PR review timeline and status for PR channel posts.

Pure functions: GitHub PR data in, ordered timeline and a neutral status out. No
assumptions about who reviews (bots and humans count the same) or org roles.

Status (Bruno's scheme): merged > closed > latest of review / revision > created.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Mapping

CREATED = "created"
REVIEWED = "reviewed"
CHANGES_REQUESTED = "changes_requested"
APPROVED = "approved"
DISMISSED = "dismissed"
REVISED = "revised"
MERGED = "merged"
CLOSED = "closed"
REOPENED = "reopened"

STATUS_CREATED = "created"
STATUS_NOT_APPROVED = "not_approved"
STATUS_REVISED = "revised"
STATUS_APPROVED = "approved"
STATUS_MERGED = "merged"
STATUS_CLOSED = "closed"

_REVIEW_STATES = {
    "approved": APPROVED,
    "changes_requested": CHANGES_REQUESTED,
    "commented": REVIEWED,
    "dismissed": DISMISSED,
}
_STATUS_FOR_KIND = {
    APPROVED: STATUS_APPROVED,
    CHANGES_REQUESTED: STATUS_NOT_APPROVED,
    REVIEWED: STATUS_NOT_APPROVED,
    REVISED: STATUS_REVISED,
}
# Same-second ties: a revision sorts before a review so the review decides the status.
_TIE_ORDER = {CREATED: 0, REVISED: 1, REOPENED: 2}
# GitHub records merged and closed a moment apart; treat closes this close to the merge as the merge.
_MERGE_CLOSE_SLACK_SECONDS = 10


@dataclass(frozen=True)
class TimelineEntry:
    kind: str
    actor: str
    at: datetime


@dataclass(frozen=True)
class PRTimeline:
    status: str
    draft: bool
    author: str
    title: str
    entries: list[TimelineEntry] = field(default_factory=list)


def parse_github_time(value: object) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def is_bot_account(user: Mapping | None) -> bool:
    if not user:
        return False
    if str(user.get("type") or "").lower() == "bot":
        return True
    return str(user.get("login") or "").strip().lower().endswith("[bot]")


def display_login(login: str) -> str:
    """``coderabbitai[bot]`` → ``coderabbitai`` for display."""
    text = (login or "").strip().lstrip("@")
    if text.lower().endswith("[bot]"):
        text = text[: -len("[bot]")]
    return text


def _login(user: Mapping | None) -> str:
    return str((user or {}).get("login") or "").strip()


def _review_entries(timeline: Iterable[Mapping], author_lower: str) -> list[TimelineEntry]:
    entries: list[TimelineEntry] = []
    for event in timeline:
        if event.get("event") != "reviewed":
            continue
        kind = _REVIEW_STATES.get(str(event.get("state") or "").lower())
        at = parse_github_time(event.get("submitted_at"))
        reviewer = _login(event.get("user"))
        if kind is None or at is None or not reviewer:
            continue
        # Authors replying in their own review threads show up as "commented" reviews.
        if reviewer.lower() == author_lower:
            continue
        entries.append(TimelineEntry(kind, reviewer, at))
    return entries


def _revision_entries(
    commits: Iterable[Mapping],
    timeline: Iterable[Mapping],
    head_first_seen_after: Mapping[str, datetime],
) -> list[TimelineEntry]:
    entries: list[TimelineEntry] = []
    for commit in commits:
        author_user = commit.get("author")
        if is_bot_account(author_user):
            continue
        git = commit.get("commit") or {}
        at = parse_github_time((git.get("committer") or {}).get("date"))
        if at is None:
            continue
        # Commits made locally before a review but pushed after it carry an old date;
        # Gitcord saw this head appear after ``first_seen_after``, so it was pushed later.
        seen_after = head_first_seen_after.get(str(commit.get("sha") or ""))
        if seen_after is not None and seen_after > at:
            at = seen_after
        actor = _login(author_user) or str((git.get("author") or {}).get("name") or "")
        entries.append(TimelineEntry(REVISED, actor, at))
    for event in timeline:
        if event.get("event") != "head_ref_force_pushed":
            continue
        actor_user = event.get("actor")
        at = parse_github_time(event.get("created_at"))
        if at is None or is_bot_account(actor_user):
            continue
        entries.append(TimelineEntry(REVISED, _login(actor_user), at))
    return entries


def _lifecycle_entries(timeline: Iterable[Mapping], pr: Mapping) -> list[TimelineEntry]:
    merged_at = parse_github_time(pr.get("merged_at")) if pr.get("merged") else None
    entries: list[TimelineEntry] = []
    saw_merge = False
    for event in timeline:
        name = event.get("event")
        at = parse_github_time(event.get("created_at"))
        if at is None:
            continue
        actor = _login(event.get("actor"))
        if name == "merged":
            saw_merge = True
            entries.append(TimelineEntry(MERGED, actor, at))
        elif name == "closed":
            if merged_at and abs((at - merged_at).total_seconds()) <= _MERGE_CLOSE_SLACK_SECONDS:
                continue
            entries.append(TimelineEntry(CLOSED, actor, at))
        elif name == "reopened":
            entries.append(TimelineEntry(REOPENED, actor, at))
    if merged_at and not saw_merge:
        entries.append(TimelineEntry(MERGED, _login(pr.get("merged_by")), merged_at))
    return entries


def _sort_key(entry: TimelineEntry) -> tuple[datetime, int]:
    return entry.at, _TIE_ORDER.get(entry.kind, 3)


def build_pr_timeline(
    pr: Mapping,
    timeline: Iterable[Mapping],
    commits: Iterable[Mapping],
    head_first_seen_after: Mapping[str, datetime] | None = None,
) -> PRTimeline:
    """Build the ordered timeline (oldest first) and current status for one PR.

    ``pr`` is the single-PR REST payload, ``timeline`` the issue timeline, ``commits`` the
    PR commit list. ``head_first_seen_after`` maps head SHAs to the last time Gitcord saw a
    different head, a lower bound on when that head was pushed.
    """
    timeline = list(timeline)
    author = _login(pr.get("user"))
    created_at = parse_github_time(pr.get("created_at")) or datetime.now(timezone.utc)
    reviews = _review_entries(timeline, author.lower())
    first_review_at = min((r.at for r in reviews), default=None)
    revisions = [
        r
        for r in _revision_entries(commits, timeline, head_first_seen_after or {})
        if first_review_at is not None and r.at > first_review_at
    ]
    entries = sorted(
        [TimelineEntry(CREATED, author, created_at), *reviews, *revisions, *_lifecycle_entries(timeline, pr)],
        key=_sort_key,
    )

    if pr.get("merged"):
        status = STATUS_MERGED
    elif str(pr.get("state") or "").lower() == "closed":
        status = STATUS_CLOSED
    else:
        status = STATUS_CREATED
        for entry in entries:
            status = _STATUS_FOR_KIND.get(entry.kind, status)

    return PRTimeline(
        status=status,
        draft=bool(pr.get("draft")) and status not in {STATUS_MERGED, STATUS_CLOSED},
        author=author,
        title=str(pr.get("title") or ""),
        entries=entries,
    )


@dataclass(frozen=True)
class TimelineLine:
    kind: str
    actor: str
    at: datetime
    count: int = 1


def render_fingerprint(content: str, embeds: list[dict]) -> str:
    """Stable hash of a rendered post, to skip Discord edits that change nothing."""
    raw = json.dumps({"content": content, "embeds": embeds}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def collapse_timeline(entries: Iterable[TimelineEntry]) -> list[TimelineLine]:
    """Merge consecutive entries with the same kind and actor (oldest first in, out)."""
    lines: list[TimelineLine] = []
    for entry in entries:
        prev = lines[-1] if lines else None
        if prev and prev.kind == entry.kind and prev.actor.lower() == entry.actor.lower():
            lines[-1] = TimelineLine(prev.kind, prev.actor, entry.at, prev.count + 1)
        else:
            lines.append(TimelineLine(entry.kind, entry.actor, entry.at))
    return lines
