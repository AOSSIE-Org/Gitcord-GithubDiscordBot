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

# Combined Discord limit across all embeds in one message payload.
_DISCORD_COMBINED_LIMIT = 6000


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


def _combined_chars(embeds: list[discord.Embed]) -> int:
    """Mirror of Discord's combined character count for a message payload."""
    return sum(
        len(e.title or "") + len(e.description or "") + (len(e.footer.text) if e.footer else 0)
        for e in embeds
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


def test_build_pr_status_embeds_respects_combined_6000_char_limit() -> None:
    """25 verbose PRs must produce a payload whose combined chars stay <= 6000."""
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

    # Combined limit
    total_chars = _combined_chars(embeds)
    assert total_chars <= _DISCORD_COMBINED_LIMIT, (
        f"Combined embed chars {total_chars} exceeds Discord {_DISCORD_COMBINED_LIMIT}-char limit"
    )

    # Per-embed description limit
    for i, e in enumerate(embeds):
        assert len(e.description or "") <= 4096, (
            f"Embed {i} description exceeded 4096: {len(e.description or '')}"
        )

    # At most 10 embeds per message
    assert len(embeds) <= 10

    # Title structure
    assert embeds[0].title == "📊 PR Status Dashboard"
    for e in embeds[1:]:
        assert e.title == "📊 PR Status Dashboard (cont.)"

    # Footer only on last embed
    assert embeds[-1].footer.text == "Gitcord • Select a PR from dropdown to inspect details"
    for e in embeds[:-1]:
        # discord.py returns an EmbedProxy sentinel (not None) when footer is unset
        assert not getattr(e.footer, "text", None), (
            f"Non-terminal embed should not have a footer; got: {e.footer.text!r}"
        )

    # Because 25 verbose entries exceed the combined limit, a truncation notice
    # must be present so the user knows to use Next to see more.
    all_descriptions = "\n".join(e.description or "" for e in embeds)
    assert "Next" in all_descriptions or "paginate" in all_descriptions.lower(), (
        "Expected a 'Next' / pagination notice when entries were cut by the combined limit"
    )


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
    # Values are now encoded as "{repo}:{number}".
    option_values = [opt.value for opt in dropdown.options]
    assert len(option_values) == 25
    for val in option_values:
        assert ":" in val, f"Option value {val!r} is not in 'repo:number' format"

    embeds = build_pr_status_embeds(statuses, "org", skip=0, total=25)
    all_descriptions = "\n".join(e.description or "" for e in embeds)
    for val in option_values:
        _repo, _, num_str = val.partition(":")
        assert f"#{num_str}" in all_descriptions, f"PR #{num_str} missing from embed descriptions"


def test_dropdown_selects_correct_pr_when_numbers_duplicate_across_repos() -> None:
    """Regression: two repos share PR number 42 — selector must resolve the right one."""
    pr_a = _make_health(repo="repo-alpha", number=42, title="Alpha PR")
    pr_b = _make_health(repo="repo-beta", number=42, title="Beta PR")
    statuses = [pr_a, pr_b]

    view = PRStatusView("org", MagicMock(), MagicMock())
    view.statuses = statuses
    view.total = 2
    view._build_components()

    dropdown = next(c for c in view.children if isinstance(c, PRSelectDropdown))
    values = {opt.value for opt in dropdown.options}

    # Both PRs must have distinct, repo-qualified values.
    assert "repo-alpha:42" in values, "repo-alpha:42 missing from dropdown options"
    assert "repo-beta:42" in values, "repo-beta:42 missing from dropdown options"

    # Simulate the callback resolution for each value.
    def _resolve(raw: str, all_statuses: list) -> PRHealthStatus | None:
        repo, _, num_str = raw.partition(":")
        num = int(num_str)
        return next((s for s in all_statuses if s.repo == repo and s.number == num), None)

    resolved_a = _resolve("repo-alpha:42", statuses)
    resolved_b = _resolve("repo-beta:42", statuses)

    assert resolved_a is pr_a, "Selecting repo-alpha:42 should resolve to pr_a"
    assert resolved_b is pr_b, "Selecting repo-beta:42 should resolve to pr_b"
    assert resolved_a is not resolved_b, "Duplicate PR numbers must resolve to different objects"


def test_single_embed_kwargs_compatibility() -> None:
    statuses = [
        _make_health(repo="my-repo", number=1),
    ]
    embeds = build_pr_status_embeds(statuses, "org", skip=0, total=1)
    kwargs = _pr_status_embed_kwargs(embeds)
    assert "embed" in kwargs
    assert "embeds" not in kwargs
    assert kwargs["embed"].title == "📊 PR Status Dashboard"


def test_split_embeds_kwargs_combined_limit() -> None:
    """Back and filter callback payloads must respect the combined 6000-char limit."""
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
    # Back path
    embeds_all = build_pr_status_embeds(statuses, "org", skip=0, total=25)
    total_chars = _combined_chars(embeds_all)
    assert total_chars <= _DISCORD_COMBINED_LIMIT, (
        f"Back-path combined chars {total_chars} exceeds {_DISCORD_COMBINED_LIMIT}"
    )
    for e in embeds_all:
        assert len(e.description or "") <= 4096

    # Filter path (blocked — will likely produce 1 embed since most are needs_attention/safe)
    embeds_filtered = build_pr_status_embeds(
        statuses, "org", skip=0, total=25, filter_health="blocked"
    )
    total_chars_f = _combined_chars(embeds_filtered)
    assert total_chars_f <= _DISCORD_COMBINED_LIMIT, (
        f"Filter-path combined chars {total_chars_f} exceeds {_DISCORD_COMBINED_LIMIT}"
    )
