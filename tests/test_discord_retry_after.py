"""Tests for DiscordApiAdapter._request retrying on HTTP 429."""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx

from ghdcbot.adapters.discord import api as discord_api
from ghdcbot.adapters.discord.api import DiscordApiAdapter


def _response(status: int, json: object | None = None) -> httpx.Response:
    return httpx.Response(
        status, json=json, request=httpx.Request("GET", "https://discord.com/api/v10/x")
    )


def _adapter(monkeypatch, responses: list[httpx.Response]) -> tuple[DiscordApiAdapter, list[float]]:
    sleeps: list[float] = []
    monkeypatch.setattr(discord_api.time, "sleep", sleeps.append)
    adapter = DiscordApiAdapter(token="test-token", guild_id="guild-1")
    client = MagicMock()
    client.request.side_effect = responses
    adapter._client = client
    return adapter, sleeps


def test_request_retries_after_429_then_succeeds(monkeypatch) -> None:
    adapter, sleeps = _adapter(
        monkeypatch, [_response(429, {"retry_after": 0.5}), _response(200, [])]
    )

    response = adapter._request("GET", "/guilds/guild-1/members")

    assert response is not None
    assert response.status_code == 200
    assert sleeps == [0.5]
    assert adapter._client.request.call_count == 2


def test_request_gives_up_after_bounded_retries(monkeypatch) -> None:
    adapter, sleeps = _adapter(
        monkeypatch, [_response(429, {"retry_after": 1.5}) for _ in range(4)]
    )

    assert adapter._request("GET", "/guilds/guild-1/roles") is None
    assert sleeps == [1.5, 1.5, 1.5]
    assert adapter._client.request.call_count == 4


def test_request_uses_default_wait_when_retry_after_missing(monkeypatch) -> None:
    adapter, sleeps = _adapter(monkeypatch, [_response(429), _response(200, [])])

    assert adapter._request("GET", "/x") is not None
    assert sleeps == [1.0]


def test_request_caps_a_very_long_retry_after(monkeypatch) -> None:
    adapter, sleeps = _adapter(
        monkeypatch, [_response(429, {"retry_after": 900}), _response(200, [])]
    )

    assert adapter._request("GET", "/x") is not None
    assert sleeps == [10.0]


def test_list_members_keeps_pages_when_a_429_is_retried(monkeypatch) -> None:
    page1 = [{"user": {"id": "1"}}]
    adapter, sleeps = _adapter(
        monkeypatch,
        [_response(200, page1), _response(429, {"retry_after": 0.2}), _response(200, []), ],
    )

    members, ok = adapter._list_members()

    assert ok is True
    assert members == page1
    assert sleeps == [0.2]


def test_request_uses_default_wait_when_retry_after_is_nan(monkeypatch) -> None:
    adapter, sleeps = _adapter(
        monkeypatch, [_response(429, {"retry_after": "NaN"}), _response(200, [])]
    )

    response = adapter._request("GET", "/x")

    assert response is not None and response.status_code == 200
    assert sleeps == [1.0]


def test_request_retries_a_5xx_then_succeeds(monkeypatch) -> None:
    adapter, sleeps = _adapter(monkeypatch, [_response(503), _response(200, [])])

    response = adapter._request("GET", "/x")

    assert response is not None and response.status_code == 200
    assert sleeps == [1.0]


def test_request_backs_off_on_repeated_5xx_and_returns_the_last_response(monkeypatch) -> None:
    adapter, sleeps = _adapter(monkeypatch, [_response(502) for _ in range(4)])

    response = adapter._request("GET", "/x")

    assert response is not None and response.status_code == 502
    assert sleeps == [1.0, 2.0, 4.0]


def test_request_stops_cleanly_when_429_body_is_not_a_dict(monkeypatch) -> None:
    limit = discord_api._MAX_RATE_LIMIT_RETRIES
    adapter, sleeps = _adapter(monkeypatch, [_response(429, ["slow down"])] * (limit + 1))

    assert adapter._request("GET", "/guilds/guild-1/members") is None
    assert len(sleeps) == limit
