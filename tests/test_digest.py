"""Tests for weekly maintainer digest (auto-only)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ghdcbot.adapters.storage.sqlite import SqliteStorage
from ghdcbot.config.models import DigestConfig
from ghdcbot.core.models import ContributionEvent
from ghdcbot.core.modes import MutationPolicy, RunMode
from ghdcbot.engine.digest import (
    build_digest_pulse,
    build_digest_report,
    count_new_prs_still_open,
    digest_is_due,
    digest_week_key,
    format_digest_embed,
    is_bot_login,
    maybe_post_weekly_digest,
    preview_weekly_digest,
)


class _MockDiscord:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str, list[dict] | None]] = []

    def create_message(
        self,
        channel_id: str,
        content: str,
        *,
        embeds: list[dict] | None = None,
    ) -> str | None:
        self.messages.append((channel_id, content, embeds))
        return f"msg-{len(self.messages)}"


def _ev(
    user: str,
    event_type: str,
    repo: str,
    when: datetime,
    **payload: object,
) -> ContributionEvent:
    return ContributionEvent(
        github_user=user,
        event_type=event_type,
        repo=repo,
        created_at=when,
        payload=dict(payload),
    )


def test_is_bot_login() -> None:
    assert is_bot_login("dependabot[bot]")
    assert is_bot_login("github-actions[bot]")
    assert is_bot_login("some-tool[bot]")
    assert not is_bot_login("alice")


def test_digest_is_due_schedule_gate() -> None:
    cfg = DigestConfig(enabled=True, channel_id="chan-1", weekday_utc=6, hour_utc=12)
    sunday_noon = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)  # Sunday
    sunday_morning = datetime(2026, 9, 20, 11, 0, tzinfo=UTC)
    monday = datetime(2026, 9, 21, 15, 0, tzinfo=UTC)
    assert digest_is_due(cfg, sunday_noon) is True
    assert digest_is_due(cfg, sunday_morning) is False
    assert digest_is_due(cfg, monday) is False
    assert digest_is_due(DigestConfig(enabled=False, channel_id="chan-1"), sunday_noon) is False
    assert digest_is_due(DigestConfig(enabled=True, channel_id=None), sunday_noon) is False


def test_digest_week_key_iso() -> None:
    when = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
    year, week, _ = when.isocalendar()
    assert digest_week_key("AOSSIE-Org", when) == f"weekly_digest:AOSSIE-Org:{year}-W{week:02d}"


def test_build_digest_pulse_and_still_open() -> None:
    now = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
    events = [
        _ev("alice", "pr_merged", "RepoA", now, pr_number=1),
        _ev("bob", "issue_closed", "RepoA", now, issue_number=2),
        _ev("carol", "issue_opened", "RepoB", now, issue_number=3),
        _ev("dave", "pr_opened", "RepoB", now, pr_number=10),
        _ev("eve", "pr_opened", "RepoB", now, pr_number=11),
        _ev("eve", "pr_merged", "RepoB", now, pr_number=11),
    ]
    pulse = build_digest_pulse(events)
    assert pulse.prs_merged == 2
    assert pulse.issues_closed == 1
    assert pulse.issues_opened == 1
    assert pulse.active_repos == 2
    assert count_new_prs_still_open(events) == 1  # #10 only


def test_build_digest_report_quiet_week_and_bot_filter(tmp_path) -> None:
    storage = SqliteStorage(tmp_path / "state.db")
    storage.init_schema()
    end = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
    start = end - timedelta(days=7)
    mid = end - timedelta(days=1)
    storage.record_contributions(
        [
            _ev("dependabot[bot]", "pr_merged", "RepoA", mid, pr_number=1, title="deps"),
            _ev("alice", "issue_opened", "RepoA", mid, issue_number=9),
        ]
    )
    storage.save_pr_channel_announcement(
        repo="RepoA",
        pr_number=99,
        channel_id="c1",
        message_id="m1",
        status="open",
    )
    report = build_digest_report(
        storage,
        org="AOSSIE-Org",
        period_start=start,
        period_end=end,
        top_n=5,
    )
    # Bot merge counts in pulse but quiet_week is False because of the merge
    assert report.pulse.prs_merged == 1
    assert report.quiet_week is False
    assert report.top_contributors == ()  # bot excluded; alice has 0 merges
    assert report.attention.open_tracked_prs == 1
    embed = format_digest_embed(report)
    assert "Weekly Digest — AOSSIE-Org" in embed["description"]
    assert "Needs attention" in embed["description"]
    assert "open PRs Gitcord is tracking" in embed["description"]


def test_quiet_week_message(tmp_path) -> None:
    storage = SqliteStorage(tmp_path / "state.db")
    storage.init_schema()
    end = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
    start = end - timedelta(days=7)
    report = build_digest_report(
        storage,
        org="AOSSIE-Org",
        period_start=start,
        period_end=end,
        top_n=5,
    )
    assert report.quiet_week is True
    desc = format_digest_embed(report)["description"]
    assert "Quiet week" in desc


def test_top_contributors_show_github_login_only(tmp_path) -> None:
    storage = SqliteStorage(tmp_path / "state.db")
    storage.init_schema()
    storage.create_identity_claim("999", "alice", "tok", datetime.now(UTC) + timedelta(hours=1))
    storage.mark_identity_verified("999", "alice")
    end = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
    start = end - timedelta(days=7)
    mid = end - timedelta(hours=1)
    storage.record_contributions(
        [
            _ev("alice", "pr_merged", "RepoA", mid, pr_number=1),
            _ev("alice", "pr_merged", "RepoA", mid, pr_number=2),
            _ev("bob", "pr_merged", "RepoB", mid, pr_number=3),
        ]
    )
    report = build_digest_report(
        storage,
        org="Org",
        period_start=start,
        period_end=end,
        top_n=5,
    )
    assert [(c.github_user, c.prs_merged) for c in report.top_contributors] == [
        ("alice", 2),
        ("bob", 1),
    ]
    desc = format_digest_embed(report)["description"]
    assert "1. `alice` — 2 merged" in desc
    assert "2. `bob` — 1 merged" in desc
    assert "<@" not in desc
    assert "999" not in desc


def test_oldest_open_prs_listed_with_links(tmp_path) -> None:
    storage = SqliteStorage(tmp_path / "state.db")
    storage.init_schema()
    end = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
    for number, days_old in [(10, 3), (11, 30), (12, 1), (13, 12), (14, 8), (15, 20)]:
        storage.save_pr_channel_announcement(
            repo="Agora",
            pr_number=number,
            channel_id="chan",
            message_id=f"msg{number}",
            pr_title=f"PR {number}",
            created_at=end - timedelta(days=days_old),
        )
    storage.save_pr_channel_announcement(
        repo="Agora",
        pr_number=99,
        channel_id="chan",
        message_id="msg99",
        status="merged",
        created_at=end - timedelta(days=90),
    )
    storage.save_pr_channel_announcement(
        repo="Agora",
        pr_number=98,
        channel_id="chan",
        message_id="msg98",
        pr_title="chore(deps): bump react",
        author_github="dependabot[bot]",
        created_at=end - timedelta(days=60),
    )
    report = build_digest_report(
        storage,
        org="AOSSIE-Org",
        period_start=end - timedelta(days=7),
        period_end=end,
        top_n=5,
    )
    assert report.attention.open_tracked_prs == 7
    assert [pr.pr_number for pr in report.attention.oldest_open] == [11, 15, 13, 14, 10]
    desc = format_digest_embed(report, guild_id="g1")["description"]
    assert "**Oldest open PRs**" in desc
    assert (
        "• [Agora #11](https://github.com/AOSSIE-Org/Agora/pull/11) PR 11 — open 30 days"
        " · [post](https://discord.com/channels/g1/chan/msg11)"
    ) in desc
    assert "#12" not in desc
    assert "#99" not in desc
    assert "#98" not in desc
    no_guild = format_digest_embed(report)["description"]
    assert "[post]" not in no_guild


def test_preview_renders_without_posting_or_claiming(tmp_path) -> None:
    storage = SqliteStorage(tmp_path / "state.db")
    storage.init_schema()
    now = datetime(2026, 9, 21, 9, 0, tzinfo=UTC)  # Monday: not due
    storage.record_contributions(
        [_ev("alice", "pr_merged", "RepoA", now - timedelta(days=1), pr_number=1)]
    )
    cfg = DigestConfig(enabled=False, channel_id=None)
    text = preview_weekly_digest(storage=storage, org="Org", digest_config=cfg, now=now)
    assert "Weekly Digest — Org" in text
    assert "1. `alice` — 1 merged" in text
    assert storage.was_notification_sent(digest_week_key("Org", now)) is False


def test_maybe_post_skips_midweek_and_dedupes(tmp_path) -> None:
    storage = SqliteStorage(tmp_path / "state.db")
    storage.init_schema()
    writer = _MockDiscord()
    policy = MutationPolicy(
        mode=RunMode.ACTIVE, github_write_allowed=False, discord_write_allowed=True
    )
    cfg = DigestConfig(enabled=True, channel_id="digest-chan", weekday_utc=6, hour_utc=12)
    monday = datetime(2026, 9, 21, 15, 0, tzinfo=UTC)
    assert (
        maybe_post_weekly_digest(
            storage=storage,
            discord_writer=writer,  # type: ignore[arg-type]
            policy=policy,
            org="Org",
            digest_config=cfg,
            now=monday,
        )
        is False
    )
    assert writer.messages == []

    sunday = datetime(2026, 9, 20, 13, 0, tzinfo=UTC)
    assert (
        maybe_post_weekly_digest(
            storage=storage,
            discord_writer=writer,  # type: ignore[arg-type]
            policy=policy,
            org="Org",
            digest_config=cfg,
            now=sunday,
        )
        is True
    )
    assert len(writer.messages) == 1
    assert writer.messages[0][0] == "digest-chan"
    assert writer.messages[0][2] and "Weekly Digest" in writer.messages[0][2][0]["description"]

    # Second call same ISO week → deduped
    assert (
        maybe_post_weekly_digest(
            storage=storage,
            discord_writer=writer,  # type: ignore[arg-type]
            policy=policy,
            org="Org",
            digest_config=cfg,
            now=sunday + timedelta(hours=2),
        )
        is False
    )
    assert len(writer.messages) == 1


def test_maybe_post_skips_without_discord_write(tmp_path) -> None:
    storage = SqliteStorage(tmp_path / "state.db")
    storage.init_schema()
    writer = _MockDiscord()
    policy = MutationPolicy(
        mode=RunMode.DRY_RUN, github_write_allowed=False, discord_write_allowed=True
    )
    cfg = DigestConfig(enabled=True, channel_id="digest-chan", weekday_utc=6, hour_utc=12)
    sunday = datetime(2026, 9, 20, 13, 0, tzinfo=UTC)
    assert (
        maybe_post_weekly_digest(
            storage=storage,
            discord_writer=writer,  # type: ignore[arg-type]
            policy=policy,
            org="Org",
            digest_config=cfg,
            now=sunday,
        )
        is False
    )
    assert writer.messages == []


def test_digest_config_defaults_in_discord_config() -> None:
    from ghdcbot.config.models import DiscordConfig

    discord = DiscordConfig(guild_id="1", token="t")
    assert discord.digest.enabled is False
    assert discord.digest.channel_id is None
    assert discord.digest.weekday_utc == 6
    assert discord.digest.hour_utc == 12
