"""Tests for review queue engine (staleness classification, formatting, and embeds)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ghdcbot.engine.review_queue import (
    build_review_queue_embed,
    classify_review_staleness,
    format_review_wait_time,
)


def test_classify_review_staleness() -> None:
    now = datetime(2026, 6, 10, 12, 0, 0, tzinfo=UTC)

    # 1. Fresh (< 48h)
    fresh_time = now - timedelta(hours=24)
    tier, emoji = classify_review_staleness(fresh_time.isoformat(), now=now)
    assert tier == "fresh"
    assert emoji == "🟢"

    # Just under 48h (47 hours)
    tier, emoji = classify_review_staleness((now - timedelta(hours=47)).isoformat(), now=now)
    assert tier == "fresh"
    assert emoji == "🟢"

    # 2. Aging (48h - 120h)
    tier, emoji = classify_review_staleness((now - timedelta(hours=48)).isoformat(), now=now)
    assert tier == "aging"
    assert emoji == "🟡"

    tier, emoji = classify_review_staleness((now - timedelta(days=3)).isoformat(), now=now)
    assert tier == "aging"
    assert emoji == "🟡"

    # 3. Stale (>= 120h / 5 days)
    tier, emoji = classify_review_staleness((now - timedelta(hours=120)).isoformat(), now=now)
    assert tier == "stale"
    assert emoji == "🔴"

    tier, emoji = classify_review_staleness((now - timedelta(days=10)).isoformat(), now=now)
    assert tier == "stale"
    assert emoji == "🔴"

    # 4. Fallback / invalid
    tier, emoji = classify_review_staleness(None, now=now)
    assert tier == "fresh"
    assert emoji == "🟢"

    tier, emoji = classify_review_staleness("not-a-date", now=now)
    assert tier == "fresh"
    assert emoji == "🟢"


def test_format_review_wait_time() -> None:
    now = datetime(2026, 6, 10, 12, 0, 0, tzinfo=UTC)

    # Minutes
    mins = format_review_wait_time((now - timedelta(minutes=45)).isoformat(), now=now)
    assert mins == "45m waiting"

    # Hours
    hours = format_review_wait_time((now - timedelta(hours=6)).isoformat(), now=now)
    assert hours == "6h waiting"

    # Days
    days = format_review_wait_time((now - timedelta(days=4)).isoformat(), now=now)
    assert days == "4d waiting"

    # Invalid / None
    assert format_review_wait_time(None, now=now) == "unknown wait time"
    assert format_review_wait_time("bad-date", now=now) == "unknown wait time"


def test_build_review_queue_embed_empty() -> None:
    embed = build_review_queue_embed(
        reviews=[],
        github_user="alice",
        target_mention="<@123>",
        org="AOSSIE-Org",
    )
    assert "Pending Review Requests" in embed["title"]
    assert "All caught up" in embed["description"]
    assert "<@123>" in embed["description"]
    assert "alice" in embed["description"]
    assert embed["color"] == 0x57F287  # Green
    assert embed["footer"]["text"] == "Organization: AOSSIE-Org • /reviews"
    assert "fields" not in embed


def test_build_review_queue_embed_tiers_and_colors() -> None:
    now = datetime(2026, 6, 10, 12, 0, 0, tzinfo=UTC)

    # All fresh -> Green embed
    reviews_fresh = [
        {
            "repo": "Gitcord",
            "number": 101,
            "title": "Fresh Feature",
            "author": "bob",
            "created_at": (now - timedelta(hours=10)).isoformat(),
            "html_url": "https://github.com/AOSSIE-Org/Gitcord/pull/101",
            "draft": False,
        }
    ]
    embed_fresh = build_review_queue_embed(
        reviews=reviews_fresh,
        github_user="alice",
        target_mention="<@123>",
        org="AOSSIE-Org",
        now=now,
    )
    assert embed_fresh["color"] == 0x57F287  # Green
    assert "🟢 **1** fresh" in embed_fresh["description"]
    assert len(embed_fresh["fields"]) == 1
    assert "🟢 Gitcord#101 — Fresh Feature" in embed_fresh["fields"][0]["name"]
    assert "Author: `bob` • **10h waiting**" in embed_fresh["fields"][0]["value"]
    assert "[View PR](https://github.com/AOSSIE-Org/Gitcord/pull/101)" in embed_fresh["fields"][0]["value"]
    assert "[Review Diff](https://github.com/AOSSIE-Org/Gitcord/pull/101/files)" in embed_fresh["fields"][0]["value"]

    # Contains aging -> Yellow embed
    reviews_aging = reviews_fresh + [
        {
            "repo": "PictoPy",
            "number": 55,
            "title": "Aging PR",
            "author": "charlie",
            "created_at": (now - timedelta(days=3)).isoformat(),
            "html_url": "https://github.com/AOSSIE-Org/PictoPy/pull/55",
            "draft": True,
        }
    ]
    embed_aging = build_review_queue_embed(
        reviews=reviews_aging,
        github_user="alice",
        target_mention="<@123>",
        org="AOSSIE-Org",
        now=now,
    )
    assert embed_aging["color"] == 0xFEE75C  # Yellow
    assert "🟡 **1** aging" in embed_aging["description"]
    assert any("`[Draft]`" in f["name"] for f in embed_aging["fields"])

    # Contains stale -> Red embed
    reviews_stale = reviews_aging + [
        {
            "repo": "EduAid",
            "number": 12,
            "title": "Stale Fix",
            "author": "dave",
            "created_at": (now - timedelta(days=7)).isoformat(),
            "html_url": "https://github.com/AOSSIE-Org/EduAid/pull/12",
            "draft": False,
        }
    ]
    embed_stale = build_review_queue_embed(
        reviews=reviews_stale,
        github_user="alice",
        target_mention="<@123>",
        org="AOSSIE-Org",
        now=now,
    )
    assert embed_stale["color"] == 0xED4245  # Red
    assert "🔴 **1** stale" in embed_stale["description"]
    assert "Total: **3**" in embed_stale["description"]


def test_build_review_queue_embed_requested_at_precedence() -> None:
    """requested_at timestamp takes precedence over PR created_at for review age and sorting."""
    now = datetime(2026, 6, 10, 12, 0, 0, tzinfo=UTC)

    # PR created 6 months ago, but review requested 5 hours ago
    reviews = [
        {
            "repo": "Gitcord",
            "number": 10,
            "title": "Old PR Recent Request",
            "author": "bob",
            "created_at": (now - timedelta(days=180)).isoformat(),
            "requested_at": (now - timedelta(hours=5)).isoformat(),
            "html_url": "https://github.com/AOSSIE-Org/Gitcord/pull/10",
        }
    ]

    embed = build_review_queue_embed(
        reviews=reviews,
        github_user="alice",
        target_mention="<@123>",
        org="AOSSIE-Org",
        now=now,
    )

    assert "🟢 **1** fresh" in embed["description"]
    assert "5h waiting" in embed["fields"][0]["value"]


def test_build_review_queue_embed_pagination() -> None:
    now = datetime(2026, 6, 10, 12, 0, 0, tzinfo=UTC)
    reviews = [
        {
            "repo": f"Repo{i}",
            "number": i,
            "title": f"PR {i}",
            "author": f"user{i}",
            "created_at": (now - timedelta(hours=i + 1)).isoformat(),
            "html_url": f"https://github.com/AOSSIE-Org/Repo{i}/pull/{i}",
        }
        for i in range(25)
    ]

    # Page 1 (items 0 to 14)
    embed_p1 = build_review_queue_embed(
        reviews=reviews,
        github_user="alice",
        target_mention="<@123>",
        org="AOSSIE-Org",
        now=now,
        max_items=15,
        page=1,
    )
    assert len(embed_p1["fields"]) == 15
    assert "Page 1 of 2" in embed_p1["footer"]["text"]
    assert "use page:2" in embed_p1["footer"]["text"]

    # Page 2 (items 15 to 24)
    embed_p2 = build_review_queue_embed(
        reviews=reviews,
        github_user="alice",
        target_mention="<@123>",
        org="AOSSIE-Org",
        now=now,
        max_items=15,
        page=2,
    )
    assert len(embed_p2["fields"]) == 10
    assert "Page 2 of 2" in embed_p2["footer"]["text"]


def test_build_review_queue_embed_character_budget() -> None:
    now = datetime(2026, 6, 10, 12, 0, 0, tzinfo=UTC)
    huge_title = "X" * 200
    reviews = [
        {
            "repo": f"VeryLongRepositoryNameWithManyCharactersToTestEmbedLimits{i}",
            "number": i,
            "title": huge_title,
            "author": f"user_with_a_long_username_{i}",
            "created_at": (now - timedelta(hours=i + 1)).isoformat(),
            "html_url": f"https://github.com/AOSSIE-Org/VeryLongRepositoryNameWithManyCharactersToTestEmbedLimits{i}/pull/{i}",
        }
        for i in range(25)
    ]

    embed = build_review_queue_embed(
        reviews=reviews,
        github_user="alice",
        target_mention="<@123>",
        org="AOSSIE-Org",
        now=now,
        max_items=15,
    )

    total_chars = (
        len(embed["title"])
        + len(embed["description"])
        + len(embed["footer"]["text"])
        + sum(len(f["name"]) + len(f["value"]) for f in embed["fields"])
    )
    assert total_chars <= 6000


def test_partition_into_pages_no_omitted_or_duplicate_items() -> None:
    """Every item must appear on exactly one page, even when character budgets force pagination."""
    now = datetime(2026, 6, 10, 12, 0, 0, tzinfo=UTC)
    total_items = 45
    reviews = [
        {
            "repo": f"LongOrganizationRepositoryNameTestingEmbedPartitioning_{i}",
            "number": i,
            "title": f"Feature PR title with descriptive text to test Discord embed limits and page budgeting {i}",
            "author": f"contributor_with_a_fairly_long_username_{i}",
            "created_at": (now - timedelta(hours=i + 1)).isoformat(),
            "html_url": f"https://github.com/AOSSIE-Org/LongOrganizationRepositoryNameTestingEmbedPartitioning_{i}/pull/{i}",
        }
        for i in range(total_items)
    ]

    # Collect items and page sizes across all pages
    page_num = 1
    seen_pr_numbers: list[int] = []
    page_sizes: list[int] = []
    while True:
        embed = build_review_queue_embed(
            reviews=reviews,
            github_user="alice",
            target_mention="<@123>",
            org="AOSSIE-Org",
            now=now,
            max_items=15,
            page=page_num,
        )
        fields = embed.get("fields", [])
        assert len(fields) <= 25  # Discord hard field limit
        page_sizes.append(len(fields))

        total_chars = (
            len(embed["title"])
            + len(embed["description"])
            + len(embed["footer"]["text"])
            + sum(len(f["name"]) + len(f["value"]) for f in fields)
        )
        assert total_chars <= 6000  # Discord aggregate limit

        for f in fields:
            # Name format: "🟢 LongOrganizationRepositoryNameTestingEmbedPartitioning_{i}#{i} — ..."
            num_part = f["name"].split("#")[1].split(" ")[0]
            seen_pr_numbers.append(int(num_part))

        footer_text = embed["footer"]["text"]
        if "Page " not in footer_text:
            break
        # Format: "Page X of Y ..."
        parts = footer_text.split("Page ")[1].split(" of ")
        curr = int(parts[0])
        total_p = int(parts[1].split(" ")[0])
        if curr >= total_p:
            break
        page_num += 1

    # Verify that the character budget forced at least one non-final page to have fewer than max_items
    assert len(page_sizes) > 1
    assert any(size < 15 for size in page_sizes[:-1])

    # Verify 100% of items are accounted for: zero dropped, zero duplicated
    assert len(seen_pr_numbers) == total_items
    assert sorted(seen_pr_numbers) == list(range(total_items))


def test_build_review_queue_embed_incomplete_results() -> None:
    now = datetime(2026, 6, 10, 12, 0, 0, tzinfo=UTC)

    # 1. Empty queue with incomplete=True shows yellow warning embed
    embed_empty = build_review_queue_embed(
        reviews=[],
        github_user="alice",
        target_mention="<@123>",
        org="AOSSIE-Org",
        incomplete=True,
    )
    assert embed_empty["color"] == 0xFEE75C  # Yellow warning
    assert "Search reached the GitHub API limit" in embed_empty["description"]
    assert "specify the `repo` parameter" in embed_empty["description"]

    # 2. Non-empty queue with incomplete=True shows partial notice
    reviews = [
        {
            "repo": "Gitcord",
            "number": 1,
            "title": "PR 1",
            "author": "bob",
            "created_at": (now - timedelta(hours=1)).isoformat(),
            "html_url": "https://github.com/AOSSIE-Org/Gitcord/pull/1",
        }
    ]
    embed_partial = build_review_queue_embed(
        reviews=reviews,
        github_user="alice",
        target_mention="<@123>",
        org="AOSSIE-Org",
        now=now,
        incomplete=True,
    )
    assert "Search limit reached; results may be partial" in embed_partial["description"]
    assert "⚠️ Partial results" in embed_partial["footer"]["text"]
