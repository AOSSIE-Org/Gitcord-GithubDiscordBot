"""Tests for open PR listing helpers."""

from ghdcbot.engine.open_prs import (
    format_open_prs_messages,
    format_open_prs_report,
    list_open_prs_for_author,
)


def _pr(
    *,
    repo: str,
    number: int,
    author: str,
    title: str,
    created_at: str,
    html_url: str | None = None,
) -> dict:
    return {
        "repo": repo,
        "number": number,
        "author": author,
        "title": title,
        "created_at": created_at,
        "html_url": html_url,
    }


def test_list_open_prs_for_author_filters_case_insensitively() -> None:
    prs = [
        _pr(repo="a", number=1, author="Alice", title="One", created_at="2026-07-01T10:00:00Z"),
        _pr(repo="b", number=2, author="bob", title="Two", created_at="2026-07-02T10:00:00Z"),
        _pr(repo="c", number=3, author="alice", title="Three", created_at="2026-07-03T10:00:00Z"),
    ]

    matched = list_open_prs_for_author(prs, "ALICE")

    assert [pr["number"] for pr in matched] == [3, 1]


def test_list_open_prs_for_author_returns_empty_for_unknown_user() -> None:
    prs = [_pr(repo="a", number=1, author="bob", title="One", created_at="2026-07-01T10:00:00Z")]

    assert list_open_prs_for_author(prs, "alice") == []


def test_format_open_prs_report_includes_links_and_opened_dates() -> None:
    prs = [
        _pr(
            repo="Gitcord",
            number=42,
            author="alice",
            title="Add sync safety",
            created_at="2026-07-05T10:14:00Z",
            html_url="https://github.com/AOSSIE-Org/Gitcord/pull/42",
        )
    ]

    message = format_open_prs_report(
        contributor_mention="<@123>",
        github_user="alice",
        prs=prs,
        org="AOSSIE-Org",
    )

    assert "Open PRs for <@123> (GitHub: alice)" in message
    assert "**Gitcord**#42 — Add sync safety" in message
    assert "opened 2026-07-05 10:14 UTC" in message
    assert "https://github.com/AOSSIE-Org/Gitcord/pull/42" in message


def test_format_open_prs_report_builds_url_when_html_url_missing() -> None:
    prs = [
        _pr(
            repo="EduAid",
            number=7,
            author="alice",
            title="Fix flow",
            created_at="2026-07-01T08:02:00Z",
        )
    ]

    message = format_open_prs_report(
        contributor_mention="<@123>",
        github_user="alice",
        prs=prs,
        org="AOSSIE-Org",
    )

    assert "https://github.com/AOSSIE-Org/EduAid/pull/7" in message


def test_format_open_prs_report_empty_result() -> None:
    message = format_open_prs_report(
        contributor_mention="<@123>",
        github_user="alice",
        prs=[],
        org="AOSSIE-Org",
    )

    assert "No open PRs found in configured repos." in message


def test_format_open_prs_report_truncates_long_lists() -> None:
    prs = [
        _pr(
            repo="repo",
            number=i,
            author="alice",
            title=f"PR {i}",
            created_at=f"2026-07-{i:02d}T10:00:00Z",
        )
        for i in range(1, 23)
    ]

    message = format_open_prs_report(
        contributor_mention="<@123>",
        github_user="alice",
        prs=prs,
        org="AOSSIE-Org",
        max_items=20,
    )

    assert "…and 2 more open PR(s) not shown." in message


def test_format_open_prs_messages_chunks_over_2000_chars() -> None:
    prs = [
        _pr(
            repo=f"Gitcord-GithubDiscordBot-Service-{i:02d}",
            number=100 + i,
            author="alice",
            title=f"Fix critical vulnerability in authentication token verification mechanism part {i}",
            created_at=f"2026-07-{(i % 28) + 1:02d}T10:14:00Z",
            html_url=f"https://github.com/AOSSIE-Org/Gitcord-GithubDiscordBot-Service-{i:02d}/pull/{100 + i}",
        )
        for i in range(1, 21)
    ]

    raw_report = format_open_prs_report(
        contributor_mention="<@123>",
        github_user="alice",
        prs=prs,
        org="AOSSIE-Org",
    )
    # The full report for 20 PRs is well over Discord's 2,000 char limit (around 4,400 chars)
    assert len(raw_report) > 2000
    assert len(raw_report) > 4000

    chunks = format_open_prs_messages(
        contributor_mention="<@123>",
        github_user="alice",
        prs=prs,
        org="AOSSIE-Org",
    )

    # Must be split into multiple chunks under the 1,900 limit
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= 1900

    # No PR is lost
    joined = "\n".join(chunks)
    for i in range(1, 21):
        assert f"{i}. **Gitcord-GithubDiscordBot-Service-{i:02d}**#{100 + i}" in joined

    # Order is preserved
    positions = [
        joined.index(f"{i}. **Gitcord-GithubDiscordBot-Service-{i:02d}**#{100 + i}")
        for i in range(1, 21)
    ]
    assert positions == sorted(positions)

    # No PR entry is cut in half across two chunks
    for chunk in chunks:
        lines = chunk.splitlines()
        assert not lines[0].startswith("   opened ")
        assert not lines[0].startswith("   http")
        for idx, line in enumerate(lines):
            if line.startswith("   opened "):
                assert idx > 0 and lines[idx - 1]
            if line.startswith("   http"):
                assert idx > 1 and lines[idx - 1].startswith("   opened ")

    # Content joined back equals the original lines
    assert joined == raw_report


def test_open_prs_command_chunks_followup_send() -> None:
    import asyncio
    from typing import Any
    from unittest.mock import AsyncMock, MagicMock, patch

    import discord

    from ghdcbot.bot import run_bot
    from ghdcbot.config.models import (
        BotConfig,
        DiscordConfig,
        GitHubConfig,
        RepoFilterConfig,
        RuntimeConfig,
    )

    cfg = BotConfig(
        runtime=RuntimeConfig(
            data_dir="./data",
            github_adapter="ghdcbot.adapters.github.rest:GitHubRestAdapter",
            discord_adapter="ghdcbot.adapters.discord.api:DiscordApiAdapter",
            storage_adapter="ghdcbot.adapters.storage.sqlite:SqliteStorage",
        ),
        github=GitHubConfig(
            org="AOSSIE-Org",
            repos=RepoFilterConfig(mode="allow", names=["Repository-01"]),
        ),
        discord=DiscordConfig(guild_id="123", token="fake"),
    )

    captured: list[Any] = []
    orig_tree_init = discord.app_commands.CommandTree.__init__

    def mock_tree_init(tree_self: Any, client: Any) -> None:
        captured.append(tree_self)
        orig_tree_init(tree_self, client)

    mock_gh = MagicMock()
    fake_prs = [
        _pr(
            repo=f"Repository-{i:02d}",
            number=100 + i,
            author="alice",
            title=f"Fix issue with data validation and synchronization pipeline part {i}",
            created_at=f"2026-07-{(i % 28) + 1:02d}T10:14:00Z",
            html_url=f"https://github.com/AOSSIE-Org/Repository-{i:02d}/pull/{100 + i}",
        )
        for i in range(1, 21)
    ]
    mock_gh.list_open_pull_requests_for_author.return_value = fake_prs

    with (
        patch("ghdcbot.bot.load_config", return_value=cfg),
        patch("ghdcbot.bot.resolve_github_token", return_value="fake"),
        patch("ghdcbot.bot.build_adapter", return_value=mock_gh),
        patch("ghdcbot.bot.GitHubIdentityReader"),
        patch("ghdcbot.bot.IdentityLinkService"),
        patch("ghdcbot.bot.SocialProfileService"),
        patch("discord.app_commands.CommandTree.__init__", mock_tree_init),
        patch("discord.Client.run", side_effect=SystemExit(0)),
    ):
        try:
            run_bot("dummy.yaml")
        except SystemExit:
            pass

    tree = captured[0]
    cmd = next(c for c in tree.get_commands(guild=discord.Object(id=123)) if c.name == "open-prs")

    mock_interaction = MagicMock()
    mock_interaction.response.defer = AsyncMock()
    mock_interaction.followup.send = AsyncMock()

    mock_contributor = MagicMock()
    mock_contributor.id = 12345
    mock_contributor.mention = "<@12345>"

    with patch("ghdcbot.bot.resolve_discord_to_github", return_value="alice"):
        asyncio.run(cmd.callback(mock_interaction, mock_contributor))

    mock_interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    assert mock_interaction.followup.send.call_count > 1
    for call in mock_interaction.followup.send.call_args_list:
        sent_text = call.args[0]
        assert len(sent_text) <= 1900
        assert call.kwargs == {"ephemeral": True, "suppress_embeds": True}

    # Short report (3 PRs) still sends exactly one message
    mock_gh.list_open_pull_requests_for_author.return_value = fake_prs[:3]
    mock_interaction.followup.send.reset_mock()
    with patch("ghdcbot.bot.resolve_discord_to_github", return_value="alice"):
        asyncio.run(cmd.callback(mock_interaction, mock_contributor))

    mock_interaction.followup.send.assert_called_once()
    assert mock_interaction.followup.send.call_args.kwargs == {
        "ephemeral": True,
        "suppress_embeds": True,
    }


def test_format_open_prs_messages_short_report_single_message() -> None:
    prs = [
        _pr(
            repo=f"repo-{i}",
            number=i,
            author="alice",
            title=f"Short PR title {i}",
            created_at=f"2026-07-0{i}T10:00:00Z",
        )
        for i in range(1, 4)
    ]
    expected_report = format_open_prs_report(
        contributor_mention="<@123>",
        github_user="alice",
        prs=prs,
        org="AOSSIE-Org",
    )
    chunks = format_open_prs_messages(
        contributor_mention="<@123>",
        github_user="alice",
        prs=prs,
        org="AOSSIE-Org",
    )
    assert len(chunks) == 1
    assert chunks[0] == expected_report


def test_chunk_open_prs_report_exact_limit_and_over() -> None:
    from ghdcbot.engine.open_prs import chunk_open_prs_report

    # Exactly at limit (1,900 chars) -> 1 chunk
    line1 = "A" * 1000
    line2 = "B" * 899
    text_exact = f"{line1}\n{line2}"
    assert len(text_exact) == 1900

    chunks_exact = chunk_open_prs_report(text_exact, max_chars=1900)
    assert len(chunks_exact) == 1
    assert chunks_exact[0] == text_exact

    # Exactly one character over limit (1,901 chars) -> 2 chunks
    line2_over = "B" * 900
    text_over = f"{line1}\n{line2_over}"
    assert len(text_over) == 1901

    chunks_over = chunk_open_prs_report(text_over, max_chars=1900)
    assert len(chunks_over) == 2
    assert all(len(c) <= 1900 for c in chunks_over)
    assert "\n".join(chunks_over) == text_over


def test_format_open_prs_messages_empty_list_unchanged() -> None:
    expected_report = format_open_prs_report(
        contributor_mention="<@123>",
        github_user="alice",
        prs=[],
        org="AOSSIE-Org",
    )
    chunks = format_open_prs_messages(
        contributor_mention="<@123>",
        github_user="alice",
        prs=[],
        org="AOSSIE-Org",
    )
    assert len(chunks) == 1
    assert chunks[0] == expected_report
    assert "No open PRs found in configured repos." in chunks[0]
