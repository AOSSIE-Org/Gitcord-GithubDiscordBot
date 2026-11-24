from __future__ import annotations

from unittest.mock import MagicMock

import discord

from ghdcbot.bot import (
    PRSelectDropdown,
    PRStatusView,
    _pr_status_embed_kwargs,
    build_pr_status_embed,
    build_pr_status_embeds,
)
from ghdcbot.engine.pr_status import PRHealthStatus


def _make_health(
    *,
    ci_status: str = "passing",
    mergeable: bool | None = True,
    review_state: str = "approved",
    approved_count: int = 1,
    changes_requested_count: int = 0,
    has_coderabbit_comments: bool = False,
    coderabbit_comment_count: int = 0,
    is_draft: bool = False,
    repo: str = "my-repo",
    number: int = 7,
    title: str = "Test PR",
    author: str = "contributor",
    html_url: str | None = None,
) -> PRHealthStatus:
    return PRHealthStatus(
        repo=repo,
        number=number,
        title=title,
        author=author,
        html_url=html_url or f"https://github.com/org/{repo}/pull/{number}",
        ci_status=ci_status,
        mergeable=mergeable,
        review_state=review_state,
        approved_count=approved_count,
        changes_requested_count=changes_requested_count,
        has_coderabbit_comments=has_coderabbit_comments,
        coderabbit_comment_count=coderabbit_comment_count,
        is_draft=is_draft,
        state="open",
    )


def test_build_pr_status_embeds_single_embed() -> None:
    statuses = [
        _make_health(number=1, title="PR 1"),
        _make_health(number=2, title="PR 2"),
    ]
    embeds = build_pr_status_embeds(statuses, "org", skip=0, total=2)
    assert len(embeds) == 1
    embed = embeds[0]
    assert len(embed.description) <= 4096
    assert "PR 1" in embed.description
    assert "PR 2" in embed.description
    assert embed.footer.text == "Gitcord • Select a PR from dropdown to inspect details"

    single = build_pr_status_embed(statuses, "org", skip=0, total=2)
    assert single.title == embed.title
    assert single.description == embed.description


def test_build_pr_status_embeds_splits_under_4096_no_entries_dropped() -> None:
    # 25 PRs with verbose details exceeding 4000 chars total description
    statuses = [
        _make_health(
            repo=f"very-long-repository-name-{i}",
            number=i * 100,
            title=f"Feature implementation with substantial description for module #{i}",
            author=f"lead-senior-developer-account-{i}",
            ci_status="failing" if i % 2 == 0 else "passing",
            review_state="changes_requested" if i % 2 == 0 else "approved",
            mergeable=(i % 3 != 0),
            has_coderabbit_comments=True,
            coderabbit_comment_count=i + 1,
            is_draft=(i % 4 == 0),
        )
        for i in range(25)
    ]
    embeds = build_pr_status_embeds(statuses, "org", skip=0, total=50)

    # Must be split into multiple embeds
    assert len(embeds) > 1
    assert len(embeds) <= 10

    # Every embed description must be <= 4096 chars
    for i, e in enumerate(embeds):
        assert len(e.description) <= 4096, f"Embed {i} exceeded 4096 limit: {len(e.description)}"
        if i == 0:
            assert e.title == "📊 PR Status Dashboard"
        else:
            assert e.title == "📊 PR Status Dashboard (cont.)"

    # Only last embed has the footer
    assert embeds[-1].footer.text == "Gitcord • Select a PR from dropdown to inspect details"

    # Verify no PR entries are dropped across all embeds
    all_descriptions = "\n".join(e.description for e in embeds)
    for s in statuses:
        repo_pr = f"{s.repo}#{s.number}"
        assert repo_pr in all_descriptions, f"{repo_pr} was dropped from split embeds"

    # Pagination notice present at end
    assert "…and 25 more. Use **Next** below to paginate." in all_descriptions


def test_embed_kwargs_helper() -> None:
    e1 = discord.Embed(title="1")
    e2 = discord.Embed(title="2")

    assert _pr_status_embed_kwargs([]) == {}
    assert _pr_status_embed_kwargs([e1]) == {"embed": e1}
    assert _pr_status_embed_kwargs([e1, e2]) == {"embeds": [e1, e2]}


def test_dropdown_options_aligned_with_displayed_entries() -> None:
    statuses = [
        _make_health(repo=f"repo-{i}", number=i, title=f"PR {i}")
        for i in range(25)
    ]
    view = PRStatusView("org", MagicMock(), MagicMock())
    view.statuses = statuses
    view.total = 25
    view._build_components()

    dropdown = next(c for c in view.children if isinstance(c, PRSelectDropdown))
    selected_pr_numbers = {opt.value for opt in dropdown.options}
    assert len(selected_pr_numbers) == 25

    embeds = build_pr_status_embeds(statuses, "org", skip=0, total=25)
    all_descriptions = "\n".join(e.description for e in embeds)
    for num in selected_pr_numbers:
        assert f"#{num}" in all_descriptions


def test_single_embed_kwargs_compatibility() -> None:
    statuses = [
        _make_health(repo="my-repo", number=1),
    ]
    embeds = build_pr_status_embeds(statuses, "org", skip=0, total=1)
    kwargs = _pr_status_embed_kwargs(embeds)
    assert "embed" in kwargs
    assert "embeds" not in kwargs
    assert kwargs["embed"].title == "📊 PR Status Dashboard"


def test_split_embeds_kwargs_for_back_and_filter_paths() -> None:
    statuses = [
        _make_health(
            repo=f"repo-with-a-very-long-name-exceeding-standard-length-{i}",
            number=i * 1000,
            title=f"Detailed title for PR #{i} with additional context information",
            author=f"author-person-with-long-username-{i}",
            ci_status="failing" if i % 2 == 0 else "passing",
            has_coderabbit_comments=True,
            coderabbit_comment_count=5,
        )
        for i in range(25)
    ]
    # Test Back path kwargs
    embeds_all = build_pr_status_embeds(statuses, "org", skip=0, total=25)
    kwargs_back = _pr_status_embed_kwargs(embeds_all)
    assert "embeds" in kwargs_back
    assert len(kwargs_back["embeds"]) > 1
    for e in kwargs_back["embeds"]:
        assert len(e.description) <= 4096

    # Test Filter path kwargs (filtered by blocked)
    embeds_filtered = build_pr_status_embeds(statuses, "org", skip=0, total=25, filter_health="blocked")
    kwargs_filter = _pr_status_embed_kwargs(embeds_filtered)
    assert "embeds" in kwargs_filter
    assert len(kwargs_filter["embeds"]) > 1
    for e in kwargs_filter["embeds"]:
        assert len(e.description) <= 4096
        assert "Filtered: 🔴 Blocked" in e.title
