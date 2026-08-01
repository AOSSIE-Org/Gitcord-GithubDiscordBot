"""Tests for /pr list helpers (grouped closed / merged / open)."""

from __future__ import annotations

from ghdcbot.engine.pr_list import (
    clamp_pr_list_args,
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
