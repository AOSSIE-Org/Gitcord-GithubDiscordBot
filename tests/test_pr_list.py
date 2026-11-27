"""Tests for /pr list helpers (grouped closed / merged / open)."""

from __future__ import annotations

from ghdcbot.engine.pr_list import (
    clamp_pr_list_args,
    filter_prs_by_repo,
    format_pr_list_messages,
    format_pr_list_report,
    group_prs_by_status,
    select_recent_prs,
)


def _pr(
    *,
    repo: str,
    number: int,
    status: str,
    title: str,
    updated_at: str,
    html_url: str | None = None,
) -> dict:
    return {
        "repo": repo,
        "number": number,
        "author": "alice",
        "title": title,
        "status": status,
        "updated_at": updated_at,
        "created_at": updated_at,
        "html_url": html_url,
    }


def test_clamp_pr_list_args() -> None:
    assert clamp_pr_list_args(count=None, skip=-1) == (10, 0)
    assert clamp_pr_list_args(count=0, skip=-1) == (1, 0)
    assert clamp_pr_list_args(count=100, skip=999) == (100, 500)
    assert clamp_pr_list_args(count=200, skip=0) == (100, 0)


def test_select_recent_prs_skips_then_takes() -> None:
    prs = [
        _pr(repo="a", number=i, status="open", title=f"PR {i}", updated_at=f"2026-07-{i:02d}T10:00:00Z")
        for i in range(1, 6)
    ]
    # Newest first: 5,4,3,2,1 — skip 1 → start at 4, take 2 → 4,3
    selected = select_recent_prs(prs, count=2, skip=1)
    assert [pr["number"] for pr in selected] == [4, 3]


def test_filter_prs_by_repo_case_insensitive() -> None:
    prs = [
        _pr(repo="Ell-ena", number=1, status="open", title="A", updated_at="2026-07-03T10:00:00Z"),
        _pr(repo="PictoPy", number=2, status="merged", title="B", updated_at="2026-07-02T10:00:00Z"),
        _pr(repo="ell-ena", number=3, status="closed", title="C", updated_at="2026-07-01T10:00:00Z"),
    ]
    assert filter_prs_by_repo(prs, None) == prs
    assert filter_prs_by_repo(prs, "  ") == prs
    filtered = filter_prs_by_repo(prs, "ell-ena")
    assert [pr["number"] for pr in filtered] == [1, 3]


def test_group_prs_by_status() -> None:
    prs = [
        _pr(repo="a", number=1, status="open", title="O", updated_at="2026-07-03T10:00:00Z"),
        _pr(repo="a", number=2, status="merged", title="M", updated_at="2026-07-02T10:00:00Z"),
        _pr(repo="a", number=3, status="closed", title="C", updated_at="2026-07-01T10:00:00Z"),
    ]
    grouped = group_prs_by_status(prs)
    assert [pr["number"] for pr in grouped["open"]] == [1]
    assert [pr["number"] for pr in grouped["merged"]] == [2]
    assert [pr["number"] for pr in grouped["closed"]] == [3]


def test_format_pr_list_report_groups_like_bruno() -> None:
    prs = [
        _pr(
            repo="Ell-ena",
            number=10,
            status="closed",
            title="Closed work",
            updated_at="2026-07-05T10:00:00Z",
            html_url="https://github.com/AOSSIE-Org/Ell-ena/pull/10",
        ),
        _pr(
            repo="Ell-ena",
            number=11,
            status="merged",
            title="Merged work",
            updated_at="2026-07-04T10:00:00Z",
        ),
        _pr(
            repo="PictoPy",
            number=12,
            status="open",
            title="Open work",
            updated_at="2026-07-03T10:00:00Z",
        ),
    ]
    message = format_pr_list_report(
        contributor_mention="<@123>",
        github_user="alice",
        prs=prs,
        org="AOSSIE-Org",
        count=3,
        skip=0,
    )
    assert "Recent PRs for <@123> ([alice](https://github.com/alice))" in message
    assert "**Closed PRs:**" in message
    assert "**Merged PRs:**" in message
    assert "**Open PRs:**" in message
    # Section order: Closed, then Merged, then Open
    assert message.index("**Closed PRs:**") < message.index("**Merged PRs:**")
    assert message.index("**Merged PRs:**") < message.index("**Open PRs:**")
    assert "1. [Ell-ena #10](<https://github.com/AOSSIE-Org/Ell-ena/pull/10>) -- Closed work (" in message
    assert "1. [Ell-ena #11](<https://github.com/AOSSIE-Org/Ell-ena/pull/11>) -- Merged work (" in message
    assert "1. [PictoPy #12](<" in message


def test_format_pr_list_report_with_repo_filter() -> None:
    prs = [
        _pr(
            repo="Ell-ena",
            number=10,
            status="open",
            title="Scoped",
            updated_at="2026-07-05T10:00:00Z",
        ),
    ]
    message = format_pr_list_report(
        contributor_mention="<@123>",
        github_user="alice",
        prs=prs,
        org="AOSSIE-Org",
        count=10,
        skip=0,
        repo="Ell-ena",
    )
    assert "in `Ell-ena`" in message
    assert "last 10" in message


def test_format_pr_list_report_empty() -> None:
    message = format_pr_list_report(
        contributor_mention="<@123>",
        github_user="alice",
        prs=[],
        org="AOSSIE-Org",
        count=5,
        skip=0,
    )
    assert "No PRs found in configured repos." in message


def test_format_pr_list_report_empty_for_repo() -> None:
    message = format_pr_list_report(
        contributor_mention="<@123>",
        github_user="alice",
        prs=[],
        org="AOSSIE-Org",
        count=5,
        skip=0,
        repo="PictoPy",
    )
    assert "No PRs found in `PictoPy`." in message
    assert "in `PictoPy`" in message


def test_format_pr_list_messages_splits_instead_of_truncating() -> None:
    prs = [
        _pr(
            repo="GitCord-GithubDiscordBot",
            number=i,
            status="merged" if i % 2 == 0 else "closed",
            title=f"Long title for pull request number {i} that takes space",
            updated_at=f"2026-07-{(i % 28) + 1:02d}T12:00:00Z",
            html_url=f"https://github.com/AOSSIE-Org/GitCord-GithubDiscordBot/pull/{i}",
        )
        for i in range(1, 30)
    ]
    messages = format_pr_list_messages(
        contributor_mention="<@123>",
        github_user="alice",
        prs=prs,
        org="AOSSIE-Org",
        count=26,
        skip=0,
    )
    assert len(messages) >= 2
    assert all(len(msg) <= 1900 for msg in messages)
    joined = "\n".join(messages)
    assert "…truncated for Discord length limit." not in joined
    assert "last 26" in messages[0]
    assert "*(continued)*" in messages[1]


def test_pr_list_command_defaults_to_invoking_user() -> None:
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
            repos=RepoFilterConfig(mode="allow", names=["Gitcord"]),
        ),
        discord=DiscordConfig(guild_id="123", token="fake"),
    )

    captured: list[Any] = []
    orig_tree_init = discord.app_commands.CommandTree.__init__

    def mock_tree_init(tree_self: Any, client: Any) -> None:
        captured.append(tree_self)
        orig_tree_init(tree_self, client)

    mock_gh = MagicMock()
    mock_gh.list_pull_requests_for_author.return_value = [
        _pr(
            repo="Gitcord",
            number=1,
            status="open",
            title="Fix bot",
            updated_at="2026-07-01T10:00:00Z",
        )
    ]

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
    cmd = next(c for c in tree.get_commands(guild=discord.Object(id=123)) if c.name == "pr")

    # Contributor parameter is optional (Issue #214)
    contributor_param = next(p for p in cmd.parameters if p.name == "contributor")
    assert contributor_param.required is False

    mock_interaction = MagicMock()
    mock_interaction.response.defer = AsyncMock()
    mock_interaction.followup.send = AsyncMock()
    mock_interaction.user.id = 88888
    mock_interaction.user.mention = "<@88888>"

    # 1. When contributor is omitted (None), defaults to invoking user
    with patch("ghdcbot.bot.resolve_discord_to_github", return_value="alice") as mock_resolve:
        asyncio.run(cmd.callback(mock_interaction, None))

    assert mock_resolve.call_args[0][1] == "88888"
    mock_interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    mock_interaction.followup.send.assert_called_once()
    assert "<@88888>" in mock_interaction.followup.send.call_args.args[0]

    # 2. When contributor is explicitly provided, uses that contributor
    mock_contributor = MagicMock()
    mock_contributor.id = 77777
    mock_contributor.mention = "<@77777>"
    mock_interaction.followup.send.reset_mock()
    with patch("ghdcbot.bot.resolve_discord_to_github", return_value="bob") as mock_resolve:
        asyncio.run(cmd.callback(mock_interaction, mock_contributor))

    assert mock_resolve.call_args[0][1] == "77777"
    mock_interaction.followup.send.assert_called_once()
    assert "<@77777>" in mock_interaction.followup.send.call_args.args[0]

    # 3. When contributor is omitted and invoker is unverified, error mentions invoker
    mock_interaction.followup.send.reset_mock()
    with patch("ghdcbot.bot.resolve_discord_to_github", return_value=None):
        asyncio.run(cmd.callback(mock_interaction, None))
    mock_interaction.followup.send.assert_called_once()
    assert "<@88888>" in mock_interaction.followup.send.call_args.args[0]
    assert "has not verified their GitHub identity" in mock_interaction.followup.send.call_args.args[0]

