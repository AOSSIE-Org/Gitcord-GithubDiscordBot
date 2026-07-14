"""Tests for open PR listing helpers."""

from ghdcbot.engine.open_prs import format_open_prs_report, list_open_prs_for_author


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
