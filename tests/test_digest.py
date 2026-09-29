"""Tests for weekly maintainer digest (auto-only)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ghdcbot.adapters.storage.sqlite import SqliteStorage
from ghdcbot.config.models import DigestConfig, IdentityMapping
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
        identity_mappings=[IdentityMapping(github_user="alice", discord_user_id="111")],
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


def test_top_contributors_with_discord_mention(tmp_path) -> None:
    storage = SqliteStorage(tmp_path / "state.db")
    storage.init_schema()
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
        identity_mappings=[
            IdentityMapping(github_user="alice", discord_user_id="999"),
        ],
    )
    assert len(report.top_contributors) == 2
    assert report.top_contributors[0].github_user == "alice"
    assert report.top_contributors[0].prs_merged == 2
    assert report.top_contributors[0].discord_user_id == "999"
    desc = format_digest_embed(report)["description"]
    assert "`alice` (<@999>)" in desc
    assert "`bob`" in desc


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
