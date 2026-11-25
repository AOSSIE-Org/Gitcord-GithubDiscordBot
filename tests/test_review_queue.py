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
    assert "`[Draft]`" in embed_aging["fields"][1]["name"]

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


def test_build_review_queue_embed_overflow_and_truncation() -> None:
    now = datetime(2026, 6, 10, 12, 0, 0, tzinfo=UTC)
    long_title = "A" * 100

    many_reviews = [
        {
            "repo": f"Repo{i}",
            "number": i,
            "title": long_title,
            "author": f"user{i}",
            "created_at": (now - timedelta(hours=i * 2)).isoformat(),
            "html_url": f"https://github.com/AOSSIE-Org/Repo{i}/pull/{i}",
            "draft": False,
        }
        for i in range(20)
    ]

    embed = build_review_queue_embed(
        reviews=many_reviews,
        github_user="alice",
        target_mention="<@123>",
        org="AOSSIE-Org",
        now=now,
        max_items=15,
    )

    # Maximum items displayed is capped at 15
    assert len(embed["fields"]) == 15
    # Long titles are truncated
    assert embed["fields"][0]["name"].endswith("...")
    assert len(embed["fields"][0]["name"]) < 80
    # Footer notes remaining items
    assert "…and 5 more PR(s)" in embed["footer"]["text"]
