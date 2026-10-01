"""PR channel status dot + review timeline (engine, render, storage, refresh pass)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from ghdcbot.adapters.storage.sqlite import SqliteStorage
from ghdcbot.config.models import NotificationConfig
from ghdcbot.core.models import ContributionEvent
from ghdcbot.core.modes import MutationPolicy, RunMode
from ghdcbot.engine import orchestrator
from ghdcbot.engine import pr_timeline as prt
from ghdcbot.engine.notifications import (
    PR_TIMELINE_MAX_LINES,
    build_pr_timeline_channel_message,
    send_pr_opened_channel_notification,
)
from ghdcbot.engine.pr_timeline_refresh import (
    GIVE_UP_AFTER_FAILURES,
    preview_pr_posts,
    refresh_pr_channel_timelines,
)

ORG = "AOSSIE-Org"
REPO = "Gitcord-GithubDiscordBot"
ACTIVE = MutationPolicy(mode=RunMode.ACTIVE, github_write_allowed=True, discord_write_allowed=True)
DRY_RUN = MutationPolicy(mode=RunMode.DRY_RUN, github_write_allowed=True, discord_write_allowed=False)


def _ts(text: str) -> str:
    return f"2026-09-{text}Z"


def _user(login: str, *, bot: bool = False) -> dict:
    return {"login": login, "type": "Bot" if bot else "User"}


def _pr(
    *,
    author: str = "contrib",
    created: str = "29T08:00:00",
    state: str = "open",
    merged: bool = False,
    merged_at: str | None = None,
    merged_by: str | None = None,
    draft: bool = False,
    title: str = "feat: improve sync",
) -> dict:
    return {
        "user": _user(author),
        "created_at": _ts(created),
        "state": state,
        "merged": merged,
        "merged_at": _ts(merged_at) if merged_at else None,
        "merged_by": _user(merged_by) if merged_by else None,
        "draft": draft,
        "title": title,
    }


def _review(login: str, state: str, at: str, *, bot: bool = False) -> dict:
    return {"event": "reviewed", "user": _user(login, bot=bot), "state": state, "submitted_at": _ts(at)}


def _event(name: str, actor: str, at: str, *, bot: bool = False) -> dict:
    return {"event": name, "actor": _user(actor, bot=bot), "created_at": _ts(at)}


def _commit(sha: str, author: str, at: str, *, bot: bool = False) -> dict:
    return {
        "sha": sha,
        "author": _user(author, bot=bot),
        "commit": {"author": {"name": author}, "committer": {"date": _ts(at)}},
    }


def _pr106_like() -> tuple[dict, list[dict], list[dict]]:
    """Shape of real PR #106: pushes, bot + Copilot reviews, revisions, approval, merge."""
    pr = _pr(
        author="contrib",
        created="29T08:00:00",
        state="closed",
        merged=True,
        merged_at="30T05:40:00",
        merged_by="maintainer",
    )
    timeline = [
        _event("committed", "contrib", "29T08:00:00"),
        _review("coderabbitai[bot]", "COMMENTED", "29T08:10:00", bot=True),
        _review("Copilot", "COMMENTED", "29T08:12:00", bot=True),
        _review("contrib", "COMMENTED", "29T09:00:00"),
        _review("coderabbitai[bot]", "APPROVED", "30T05:20:00", bot=True),
        _event("merged", "maintainer", "30T05:40:00"),
        _event("closed", "maintainer", "30T05:40:01"),
    ]
    commits = [
        _commit("a1", "contrib", "29T07:55:00"),
        _commit("a2", "contrib", "29T08:05:00"),
        _commit("a3", "contrib", "29T09:30:00"),
        _commit("a4", "contrib", "29T10:00:00"),
    ]
    return pr, timeline, commits


# --- engine -----------------------------------------------------------------


def test_pr106_like_history_and_merged_status() -> None:
    pr, timeline, commits = _pr106_like()
    result = prt.build_pr_timeline(pr, timeline, commits)

    assert result.status == prt.STATUS_MERGED
    assert result.draft is False
    assert [(e.kind, e.actor) for e in result.entries] == [
        (prt.CREATED, "contrib"),
        (prt.REVIEWED, "coderabbitai[bot]"),
        (prt.REVIEWED, "Copilot"),
        (prt.REVISED, "contrib"),
        (prt.REVISED, "contrib"),
        (prt.APPROVED, "coderabbitai[bot]"),
        (prt.MERGED, "maintainer"),
    ]


def test_no_reviews_is_white_even_with_pushes() -> None:
    commits = [_commit("a1", "contrib", "29T08:00:00"), _commit("a2", "contrib", "29T09:00:00")]
    result = prt.build_pr_timeline(_pr(), [], commits)
    assert result.status == prt.STATUS_CREATED
    assert [e.kind for e in result.entries] == [prt.CREATED]


@pytest.mark.parametrize(
    ("timeline", "commits", "expected"),
    [
        ([_review("rev", "COMMENTED", "29T09:00:00")], [], prt.STATUS_NOT_APPROVED),
        ([_review("rev", "CHANGES_REQUESTED", "29T09:00:00")], [], prt.STATUS_NOT_APPROVED),
        ([_review("rev", "APPROVED", "29T09:00:00")], [], prt.STATUS_APPROVED),
        (
            [_review("rev", "CHANGES_REQUESTED", "29T09:00:00")],
            [_commit("a2", "contrib", "29T10:00:00")],
            prt.STATUS_REVISED,
        ),
        (
            [_review("rev", "APPROVED", "29T09:00:00")],
            [_commit("a2", "contrib", "29T10:00:00")],
            prt.STATUS_REVISED,
        ),
        (
            [_review("rev", "CHANGES_REQUESTED", "29T09:00:00"), _review("rev", "APPROVED", "29T11:00:00")],
            [_commit("a2", "contrib", "29T10:00:00")],
            prt.STATUS_APPROVED,
        ),
    ],
)
def test_status_follows_latest_review_or_revision(timeline, commits, expected) -> None:
    assert prt.build_pr_timeline(_pr(), timeline, commits).status == expected


def test_latest_review_wins_between_reviewers() -> None:
    approve_then_block = [
        _review("alice", "APPROVED", "29T09:00:00"),
        _review("bob", "CHANGES_REQUESTED", "29T10:00:00"),
    ]
    block_then_approve = [
        _review("bob", "CHANGES_REQUESTED", "29T09:00:00"),
        _review("alice", "APPROVED", "29T10:00:00"),
    ]
    assert prt.build_pr_timeline(_pr(), approve_then_block, []).status == prt.STATUS_NOT_APPROVED
    assert prt.build_pr_timeline(_pr(), block_then_approve, []).status == prt.STATUS_APPROVED


def test_author_self_review_ignored() -> None:
    timeline = [_review("Contrib", "COMMENTED", "29T09:00:00")]
    result = prt.build_pr_timeline(_pr(author="contrib"), timeline, [])
    assert result.status == prt.STATUS_CREATED
    assert [e.kind for e in result.entries] == [prt.CREATED]


def test_pending_review_ignored_and_dismissed_does_not_change_status() -> None:
    timeline = [
        _review("rev", "PENDING", "29T08:30:00"),
        _review("rev", "APPROVED", "29T09:00:00"),
        _review("other", "DISMISSED", "29T10:00:00"),
    ]
    result = prt.build_pr_timeline(_pr(), timeline, [])
    assert result.status == prt.STATUS_APPROVED
    assert [e.kind for e in result.entries] == [prt.CREATED, prt.APPROVED, prt.DISMISSED]


def test_bot_reviews_count_but_bot_commits_do_not() -> None:
    timeline = [
        _review("coderabbitai[bot]", "CHANGES_REQUESTED", "29T09:00:00", bot=True),
        _event("head_ref_force_pushed", "github-actions[bot]", "29T10:30:00", bot=True),
    ]
    commits = [_commit("b1", "pre-commit-ci[bot]", "29T10:00:00", bot=True)]
    result = prt.build_pr_timeline(_pr(), timeline, commits)
    assert result.status == prt.STATUS_NOT_APPROVED
    assert prt.REVISED not in [e.kind for e in result.entries]


def test_human_force_push_after_review_is_revision() -> None:
    timeline = [
        _review("rev", "APPROVED", "29T09:00:00"),
        _event("head_ref_force_pushed", "contrib", "29T10:00:00"),
    ]
    result = prt.build_pr_timeline(_pr(), timeline, [_commit("a1", "contrib", "29T07:00:00")])
    assert result.status == prt.STATUS_REVISED
    assert result.entries[-1] == prt.TimelineEntry(
        prt.REVISED, "contrib", prt.parse_github_time(_ts("29T10:00:00"))
    )


def test_old_dated_commit_pushed_after_review_uses_observation() -> None:
    timeline = [_review("rev", "APPROVED", "29T09:00:00")]
    commits = [_commit("late", "contrib", "29T08:30:00")]
    seen_after = prt.parse_github_time(_ts("29T09:30:00"))

    without = prt.build_pr_timeline(_pr(), timeline, commits)
    with_obs = prt.build_pr_timeline(_pr(), timeline, commits, {"late": seen_after})

    assert without.status == prt.STATUS_APPROVED
    assert with_obs.status == prt.STATUS_REVISED
    assert with_obs.entries[-1].at == seen_after


def test_same_second_tie_review_decides() -> None:
    timeline = [_review("rev", "APPROVED", "29T09:00:00"), _review("rev", "COMMENTED", "29T08:00:00")]
    commits = [_commit("a2", "contrib", "29T09:00:00")]
    assert prt.build_pr_timeline(_pr(), timeline, commits).status == prt.STATUS_APPROVED


def test_closed_unmerged_and_reopened() -> None:
    timeline = [
        _review("rev", "CHANGES_REQUESTED", "29T09:00:00"),
        _event("closed", "contrib", "29T10:00:00"),
        _event("reopened", "contrib", "29T11:00:00"),
    ]
    reopened = prt.build_pr_timeline(_pr(), timeline, [])
    assert reopened.status == prt.STATUS_NOT_APPROVED
    assert [e.kind for e in reopened.entries][-2:] == [prt.CLOSED, prt.REOPENED]

    closed = prt.build_pr_timeline(_pr(state="closed"), timeline[:2], [])
    assert closed.status == prt.STATUS_CLOSED


def test_merge_without_timeline_event_falls_back_to_pr_fields() -> None:
    pr = _pr(state="closed", merged=True, merged_at="29T12:00:00", merged_by="maintainer")
    result = prt.build_pr_timeline(pr, [_event("closed", "maintainer", "29T12:00:02")], [])
    assert result.status == prt.STATUS_MERGED
    assert [(e.kind, e.actor) for e in result.entries][-1] == (prt.MERGED, "maintainer")
    assert prt.CLOSED not in [e.kind for e in result.entries]


def test_draft_only_while_open() -> None:
    assert prt.build_pr_timeline(_pr(draft=True), [], []).draft is True
    merged = _pr(draft=True, state="closed", merged=True, merged_at="29T12:00:00")
    assert prt.build_pr_timeline(merged, [], []).draft is False


def test_collapse_and_display_login() -> None:
    t0 = datetime(2026, 9, 29, tzinfo=timezone.utc)
    entries = [
        prt.TimelineEntry(prt.REVISED, "contrib", t0),
        prt.TimelineEntry(prt.REVISED, "Contrib", t0 + timedelta(hours=1)),
        prt.TimelineEntry(prt.REVIEWED, "rev", t0 + timedelta(hours=2)),
        prt.TimelineEntry(prt.REVISED, "contrib", t0 + timedelta(hours=3)),
    ]
    lines = prt.collapse_timeline(entries)
    assert [(line.kind, line.count) for line in lines] == [
        (prt.REVISED, 2),
        (prt.REVIEWED, 1),
        (prt.REVISED, 1),
    ]
    assert lines[0].at == t0 + timedelta(hours=1)
    assert prt.display_login("coderabbitai[bot]") == "coderabbitai"


# --- render -----------------------------------------------------------------


def _render(timeline: prt.PRTimeline, discord_ids: dict | None = None) -> tuple[str, list[dict]]:
    return build_pr_timeline_channel_message(
        timeline, github_org=ORG, repo=REPO, pr_number=106, discord_ids=discord_ids or {}
    )


@pytest.mark.parametrize(
    ("status", "dot"),
    [
        (prt.STATUS_CREATED, "⚪"),
        (prt.STATUS_NOT_APPROVED, "🟠"),
        (prt.STATUS_REVISED, "🟡"),
        (prt.STATUS_APPROVED, "🟢"),
        (prt.STATUS_MERGED, "🔵"),
        (prt.STATUS_CLOSED, "🔴"),
    ],
)
def test_render_status_dot(status: str, dot: str) -> None:
    timeline = prt.PRTimeline(status=status, draft=False, author="contrib", title="T", entries=[])
    _, embeds = _render(timeline)
    assert embeds[0]["description"].startswith(f"{dot} ")
    assert isinstance(embeds[0]["color"], int)


def test_render_pr106_newest_first_with_timestamps_and_truncation() -> None:
    pr, timeline, commits = _pr106_like()
    result = prt.build_pr_timeline(pr, timeline, commits)
    content, embeds = _render(result, {"maintainer": "42"})
    lines = embeds[0]["description"].split("\n")

    merged_ts = int(prt.parse_github_time(_ts("30T05:40:00")).timestamp())
    assert lines[1] == f"Merged by @maintainer (<@42>) on <t:{merged_ts}:d>"
    assert lines[2].startswith("Approved by ") and "coderabbitai" in lines[2]
    assert "[bot]" not in embeds[0]["description"]
    assert lines[3].startswith("Revised by ") and lines[3].endswith("(×2)")
    assert lines[-1].startswith("Created by ")
    assert content == ""


def test_render_truncates_to_max_lines_keeping_created() -> None:
    t0 = datetime(2026, 9, 29, tzinfo=timezone.utc)
    entries = [prt.TimelineEntry(prt.CREATED, "contrib", t0)]
    for i in range(10):
        kind = prt.REVIEWED if i % 2 == 0 else prt.REVISED
        actor = "rev" if i % 2 == 0 else "contrib"
        entries.append(prt.TimelineEntry(kind, actor, t0 + timedelta(hours=i + 1)))
    timeline = prt.PRTimeline(prt.STATUS_REVISED, False, "contrib", "T", entries)
    _, embeds = _render(timeline)
    lines = embeds[0]["description"].split("\n")[1:]

    assert len(lines) == PR_TIMELINE_MAX_LINES + 1
    assert lines[-2] == "… 5 earlier updates"
    assert lines[-1].startswith("Created by ")


def test_render_nudge_only_for_open_unverified_human_author() -> None:
    open_pr = prt.PRTimeline(prt.STATUS_CREATED, False, "contrib", "T", [])
    assert "/link contrib" in _render(open_pr)[0]
    assert _render(open_pr, {"contrib": "7"})[0] == ""

    merged = prt.PRTimeline(prt.STATUS_MERGED, False, "contrib", "T", [])
    assert _render(merged)[0] == ""

    bot_pr = prt.PRTimeline(prt.STATUS_CREATED, False, "dependabot[bot]", "T", [])
    assert _render(bot_pr)[0] == ""


def test_render_draft_label_and_dismissed_suffix() -> None:
    t0 = datetime(2026, 9, 29, tzinfo=timezone.utc)
    entries = [
        prt.TimelineEntry(prt.CREATED, "contrib", t0),
        prt.TimelineEntry(prt.DISMISSED, "rev", t0 + timedelta(hours=1)),
    ]
    timeline = prt.PRTimeline(prt.STATUS_CREATED, True, "contrib", "T", entries)
    description = _render(timeline)[1][0]["description"]
    header, newest = description.split("\n")[:2]
    assert header.endswith(" · Draft")
    assert newest.startswith("Reviewed by ") and newest.endswith("(dismissed)")


# --- storage ----------------------------------------------------------------


def _storage(tmp_path) -> SqliteStorage:
    storage = SqliteStorage(str(tmp_path))
    storage.init_schema()
    return storage


def _track(storage: SqliteStorage, pr_number: int = 106, status: str = "open") -> None:
    storage.save_pr_channel_announcement(
        repo=REPO,
        pr_number=pr_number,
        channel_id="chan",
        message_id=f"msg{pr_number}",
        pr_title="feat: improve sync",
        author_github="contrib",
        status=status,
        created_at=_ts("29T08:00:00"),
    )


def test_migration_adds_columns_to_legacy_table_and_is_idempotent(tmp_path) -> None:
    db = tmp_path / "state.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            """
            CREATE TABLE pr_channel_announcements (
                repo TEXT NOT NULL, pr_number INTEGER NOT NULL, channel_id TEXT NOT NULL,
                message_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open', pr_title TEXT,
                author_github TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                PRIMARY KEY (repo, pr_number)
            )
            """
        )
        conn.execute(
            "INSERT INTO pr_channel_announcements VALUES (?, ?, ?, ?, 'open', 'T', 'contrib', ?, ?)",
            (REPO, 106, "chan", "msg106", _ts("29T08:00:00"), _ts("29T08:00:00")),
        )

    storage = SqliteStorage(str(tmp_path))
    storage.init_schema()
    storage.init_schema()

    with sqlite3.connect(db) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(pr_channel_announcements)")}
        row = conn.execute(
            "SELECT message_id, needs_refresh, refresh_failures, render_hash FROM pr_channel_announcements"
        ).fetchone()
    assert {"last_head_sha", "head_checked_at", "render_hash", "needs_refresh", "refresh_failures"} <= columns
    assert row == ("msg106", 0, 0, None)


def test_observe_pr_heads_silent_first_then_detects_push(tmp_path) -> None:
    storage = _storage(tmp_path)
    _track(storage)
    t1 = datetime(2026, 9, 29, 9, tzinfo=timezone.utc)
    t2 = t1 + timedelta(minutes=10)

    assert storage.observe_pr_heads([(REPO, 106, "a1")], now=t1) == []
    assert storage.list_pr_announcements_needing_refresh(10) == []
    assert storage.observe_pr_heads([(REPO, 106, "a1")], now=t2) == []

    t3 = t2 + timedelta(minutes=10)
    assert storage.observe_pr_heads([(REPO, 106, "a2")], now=t3) == [(REPO, 106)]
    assert storage.get_pr_head_observations(REPO, 106) == {"a2": t2}
    assert [r["pr_number"] for r in storage.list_pr_announcements_needing_refresh(10)] == [106]


def test_observe_pr_heads_ignores_untracked_and_closed(tmp_path) -> None:
    storage = _storage(tmp_path)
    _track(storage, 107, status="merged")
    storage.observe_pr_heads([(REPO, 107, "a1"), (REPO, 999, "a1")])
    assert storage.observe_pr_heads([(REPO, 107, "a2"), (REPO, 999, "a2")]) == []


def test_refresh_failures_give_up(tmp_path) -> None:
    storage = _storage(tmp_path)
    _track(storage)
    storage.mark_pr_announcements_for_refresh([(REPO, 106)])
    for expected in range(1, 4):
        assert storage.record_pr_timeline_refresh_failure(REPO, 106, give_up_after=3) == expected
    assert storage.list_pr_announcements_needing_refresh(10) == []

    storage.mark_pr_announcements_for_refresh([(REPO, 106)])
    storage.record_pr_timeline_render(REPO, 106, render_hash="h", status="merged", pr_title=None)
    row = storage.get_pr_channel_announcement(REPO, 106)
    assert row["status"] == "merged" and row["pr_title"] == "feat: improve sync"
    assert storage.list_pr_announcements_needing_refresh(10) == []


# --- refresh pass -----------------------------------------------------------


class FakeGitHub:
    def __init__(self, snapshot: dict | None) -> None:
        self.snapshot = snapshot
        self.calls: list[tuple[str, int]] = []

    def get_pr_timeline_snapshot(self, repo: str, pr_number: int) -> dict | None:
        self.calls.append((repo, pr_number))
        return self.snapshot


class FakeDiscord:
    def __init__(self, edit_ok: bool = True) -> None:
        self.edit_ok = edit_ok
        self.edits: list[tuple[str, str, str, list[dict] | None]] = []
        self.created: list[tuple[str, str, list[dict] | None]] = []
        self.dms: list[tuple[str, str]] = []
        self.sent: list[tuple[str, str]] = []

    def edit_message(self, channel_id, message_id, content, *, embeds=None) -> bool:
        self.edits.append((channel_id, message_id, content, embeds))
        return self.edit_ok

    def create_message(self, channel_id, content, *, embeds=None) -> str:
        self.created.append((channel_id, content, embeds))
        return str(5000 + len(self.created))

    def send_message(self, channel_id, content) -> bool:
        self.sent.append((channel_id, content))
        return True

    def send_dm(self, discord_user_id, content) -> bool:
        self.dms.append((discord_user_id, content))
        return True


def _snapshot() -> dict:
    pr, timeline, commits = _pr106_like()
    return {"pr": pr, "timeline": timeline, "commits": commits}


def _merged_event(pr_number: int = 106) -> ContributionEvent:
    return ContributionEvent(
        github_user="contrib",
        event_type="pr_merged",
        repo=REPO,
        created_at=datetime(2026, 9, 30, 5, 40, tzinfo=timezone.utc),
        payload={"pr_number": pr_number},
    )


def _config(**overrides) -> NotificationConfig:
    return NotificationConfig(pr_channel_timeline=True, **overrides)


def _run(storage, github, discord, *, config=None, policy=ACTIVE, events=None, open_prs=()) -> int:
    return refresh_pr_channel_timelines(
        contributions=events if events is not None else [_merged_event()],
        open_prs=list(open_prs),
        storage=storage,
        github_reader=github,
        discord_writer=discord,
        policy=policy,
        config=config or _config(),
        github_org=ORG,
    )


def test_refresh_flag_off_is_noop(tmp_path) -> None:
    storage = _storage(tmp_path)
    _track(storage)
    github, discord = FakeGitHub(_snapshot()), FakeDiscord()
    assert _run(storage, github, discord, config=NotificationConfig()) == 0
    assert github.calls == [] and discord.edits == []


def test_refresh_edits_once_then_skips_unchanged(tmp_path) -> None:
    storage = _storage(tmp_path)
    _track(storage)
    github, discord = FakeGitHub(_snapshot()), FakeDiscord()

    assert _run(storage, github, discord) == 1
    channel, message_id, _, embeds = discord.edits[0]
    assert (channel, message_id) == ("chan", "msg106")
    assert embeds[0]["description"].startswith("🔵 ")
    assert storage.get_pr_channel_announcement(REPO, 106)["status"] == "merged"

    assert _run(storage, github, discord) == 0
    assert len(discord.edits) == 1
    assert discord.created == [] and discord.sent == [] and discord.dms == []


def test_refresh_dry_run_never_edits(tmp_path) -> None:
    storage = _storage(tmp_path)
    _track(storage)
    github, discord = FakeGitHub(_snapshot()), FakeDiscord()
    assert _run(storage, github, discord, policy=DRY_RUN) == 0
    assert discord.edits == []
    assert storage.list_pr_announcements_needing_refresh(10) == []


def test_refresh_gives_up_when_github_unavailable(tmp_path) -> None:
    storage = _storage(tmp_path)
    _track(storage)
    github, discord = FakeGitHub(None), FakeDiscord()
    _run(storage, github, discord)
    for _ in range(GIVE_UP_AFTER_FAILURES + 2):
        _run(storage, github, discord, events=[])
    assert len(github.calls) == GIVE_UP_AFTER_FAILURES
    assert discord.edits == []


def test_refresh_failed_edit_is_retried_without_new_posts(tmp_path) -> None:
    storage = _storage(tmp_path)
    _track(storage)
    github, discord = FakeGitHub(_snapshot()), FakeDiscord(edit_ok=False)
    assert _run(storage, github, discord) == 0
    assert [r["refresh_failures"] for r in storage.list_pr_announcements_needing_refresh(10)] == [1]

    discord.edit_ok = True
    assert _run(storage, github, discord, events=[]) == 1
    assert discord.created == [] and discord.sent == [] and discord.dms == []


def test_refresh_untracked_pr_is_ignored(tmp_path) -> None:
    storage = _storage(tmp_path)
    github, discord = FakeGitHub(_snapshot()), FakeDiscord()
    assert _run(storage, github, discord) == 0
    assert github.calls == []


def test_refresh_detects_push_from_open_pr_heads(tmp_path) -> None:
    storage = _storage(tmp_path)
    _track(storage)
    snapshot = {
        "pr": _pr(),
        "timeline": [_review("rev", "APPROVED", "29T09:00:00")],
        "commits": [_commit("a1", "contrib", "29T08:00:00")],
    }
    github, discord = FakeGitHub(snapshot), FakeDiscord()
    open_prs = [{"repo": REPO, "number": 106, "head_sha": "a1"}]

    _run(storage, github, discord, events=[], open_prs=open_prs)
    assert github.calls == []

    snapshot["commits"].append(_commit("a2", "contrib", "29T08:30:00"))
    _run(storage, github, discord, events=[], open_prs=[{"repo": REPO, "number": 106, "head_sha": "a2"}])
    assert github.calls == [(REPO, 106)]
    assert discord.edits[-1][3][0]["description"].startswith("🟡 ")


def test_preview_is_read_only(tmp_path) -> None:
    storage = _storage(tmp_path)
    text = preview_pr_posts(
        [(REPO, 106), (REPO, 5)],
        github_reader=type("G", (), {"get_pr_timeline_snapshot": lambda self, r, n: _snapshot() if n == 106 else None})(),
        storage=storage,
        github_org=ORG,
    )
    assert f"=== {REPO}#106 status=merged" in text
    assert f"{REPO}#5: could not read the PR from GitHub" in text


# --- github snapshot --------------------------------------------------------


class _Resp:
    def __init__(self, data, *, status: int = 200, next_page: bool = False) -> None:
        self.status_code = status
        self._data = data
        self.headers = {"Link": '<https://api.github.com/x?page=2>; rel="next"'} if next_page else {}

    def json(self):
        return self._data


def _adapter(responses: dict):
    from ghdcbot.adapters.github.rest import GitHubRestAdapter

    adapter = GitHubRestAdapter(token="token", org=ORG, api_base="https://api.github.com")
    adapter.get_pull_request = lambda owner, repo, number: {"number": number}
    adapter._request = lambda method, path, params=None, **kw: responses.get((path.rsplit("/", 1)[-1], params["page"]))
    return adapter


def test_snapshot_reads_every_page() -> None:
    adapter = _adapter(
        {
            ("timeline", 1): _Resp([{"event": "reviewed"}], next_page=True),
            ("timeline", 2): _Resp([{"event": "merged"}]),
            ("commits", 1): _Resp([{"sha": "a1"}]),
        }
    )
    snapshot = adapter.get_pr_timeline_snapshot(REPO, 106)
    assert [e["event"] for e in snapshot["timeline"]] == ["reviewed", "merged"]
    assert snapshot["commits"] == [{"sha": "a1"}]


def test_snapshot_is_none_when_a_page_fails() -> None:
    adapter = _adapter(
        {
            ("timeline", 1): _Resp([{"event": "reviewed"}], next_page=True),
            ("timeline", 2): _Resp({"message": "boom"}, status=502),
            ("commits", 1): _Resp([{"sha": "a1"}]),
        }
    )
    assert adapter.get_pr_timeline_snapshot(REPO, 106) is None


# --- wiring -----------------------------------------------------------------


def test_opened_post_is_white_and_first_refresh_is_skipped(tmp_path) -> None:
    storage = _storage(tmp_path)
    discord = FakeDiscord()
    event = ContributionEvent(
        github_user="contrib",
        event_type="pr_opened",
        repo=REPO,
        created_at=datetime(2026, 9, 29, 8, tzinfo=timezone.utc),
        payload={"pr_number": 106, "title": "feat: improve sync", "created_at": _ts("29T08:00:00")},
    )
    assert send_pr_opened_channel_notification(
        event, storage, discord, ACTIVE, _config(pr_opened=True), {REPO: "chan"}, ORG
    )
    _, content, embeds = discord.created[0]
    assert embeds[0]["description"].startswith("⚪ ")
    assert "/link contrib" in content

    github = FakeGitHub({"pr": _pr(), "timeline": [], "commits": []})
    assert _run(storage, github, discord, events=[event]) == 0
    assert github.calls == [(REPO, 106)]
    assert discord.edits == []


@pytest.mark.parametrize("timeline_on", [True, False])
def test_lifecycle_edit_skipped_only_when_timeline_on(monkeypatch, tmp_path, timeline_on) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        orchestrator,
        "update_pr_channel_announcement_for_event",
        lambda event, *a, **k: calls.append(event.event_type) or True,
    )
    monkeypatch.setattr(orchestrator, "send_notification_for_event", lambda *a, **k: False, raising=False)
    orchestrator._send_notifications_for_new_events(
        [_merged_event()],
        _storage(tmp_path),
        FakeDiscord(),
        ACTIVE,
        NotificationConfig(pr_channel_timeline=timeline_on),
        ORG,
    )
    assert calls == ([] if timeline_on else ["pr_merged"])
