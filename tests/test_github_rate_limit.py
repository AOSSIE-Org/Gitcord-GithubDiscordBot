from __future__ import annotations

import httpx

from ghdcbot.adapters.github.rest import (
    _GITHUB_DEFAULT_429_RETRY_AFTER_SECONDS,
    _GITHUB_MAX_RATE_LIMIT_RECOVERIES,
    GitHubRestAdapter,
)


class _SequenceMockClient:
    def __init__(self, outcomes: list[httpx.Response | Exception]) -> None:
        self._outcomes = list(outcomes)
        self.call_count = 0

    def request(
        self, method: str, path: str, params: dict | None = None, **kwargs: object
    ) -> httpx.Response:
        self.call_count += 1
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _adapter_with_client(client: _SequenceMockClient) -> GitHubRestAdapter:
    adapter = GitHubRestAdapter(token="t", org="org", api_base="https://api.github.com")
    adapter._client = client  # noqa: SLF001
    return adapter


def _ok_response() -> httpx.Response:
    return httpx.Response(200, json=[], headers={"X-RateLimit-Remaining": "100"})


def _event_records(caplog, event_name: str):
    return [r for r in caplog.records if getattr(r, "event", None) == event_name]


def test_rate_limit_sleep_then_success(monkeypatch, caplog) -> None:
    fixed_now = 1_000_000.0
    reset_ts = int(fixed_now) + 2
    client = _SequenceMockClient(
        [
            httpx.Response(
                403,
                headers={
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(reset_ts),
                },
                text="rate limit",
            ),
            _ok_response(),
        ]
    )
    adapter = _adapter_with_client(client)
    sleep_calls: list[float] = []
    monkeypatch.setattr("ghdcbot.adapters.github.rest.time.time", lambda: fixed_now)
    monkeypatch.setattr("ghdcbot.adapters.github.rest.time.sleep", lambda s: sleep_calls.append(s))
    caplog.set_level("WARNING")

    response = adapter._request("GET", "/repos/AOSSIE/Gitcord/issues", {})

    assert response is not None
    assert response.status_code == 200
    assert client.call_count == 2
    assert sleep_calls == [2.0]
    assert len(_event_records(caplog, "github_rate_limit_exhausted")) == 1
    assert _event_records(caplog, "github_rate_limit_exhausted")[0].sleep_seconds == 2.0
    assert len(_event_records(caplog, "github_rate_limit_recovered")) == 1


def test_rate_limit_missing_reset_header_returns_none(caplog) -> None:
    client = _SequenceMockClient(
        [
            httpx.Response(
                403,
                headers={"X-RateLimit-Remaining": "0"},
                text="rate limit",
            )
        ]
    )
    adapter = _adapter_with_client(client)
    caplog.set_level("WARNING")

    response = adapter._request("GET", "/repos/AOSSIE/Gitcord/issues", {})

    assert response is None
    assert client.call_count == 1
    assert len(_event_records(caplog, "github_rate_limit_missing_reset")) == 1


def test_permission_403_remaining_nonzero_no_sleep(monkeypatch, caplog) -> None:
    client = _SequenceMockClient(
        [
            httpx.Response(
                403,
                headers={"X-RateLimit-Remaining": "5"},
                text="Forbidden",
            )
        ]
    )
    adapter = _adapter_with_client(client)
    sleep_calls: list[float] = []
    monkeypatch.setattr("ghdcbot.adapters.github.rest.time.sleep", lambda s: sleep_calls.append(s))
    caplog.set_level("WARNING")

    response = adapter._request("GET", "/repos/AOSSIE/private-repo/issues", {})

    assert response is None
    assert client.call_count == 1
    assert sleep_calls == []
    assert _event_records(caplog, "github_rate_limit_exhausted") == []
    assert any("permission or visibility issue" in r.message.lower() for r in caplog.records)


def test_rate_limit_malformed_reset_header_returns_none(caplog) -> None:
    client = _SequenceMockClient(
        [
            httpx.Response(
                403,
                headers={
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": "not-a-timestamp",
                },
                text="rate limit",
            )
        ]
    )
    adapter = _adapter_with_client(client)
    caplog.set_level("WARNING")

    response = adapter._request("GET", "/repos/AOSSIE/Gitcord/issues", {})

    assert response is None
    assert client.call_count == 1
    assert len(_event_records(caplog, "github_rate_limit_missing_reset")) == 1


def test_rate_limit_recovery_cap_returns_none(monkeypatch, caplog) -> None:
    fixed_now = 1_000_000.0
    reset_ts = int(fixed_now) + 1
    client = _SequenceMockClient(
        [
            httpx.Response(
                403,
                headers={
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(reset_ts),
                },
                text="rate limit",
            )
        ]
        * (_GITHUB_MAX_RATE_LIMIT_RECOVERIES + 1)
    )
    adapter = _adapter_with_client(client)
    sleep_calls: list[float] = []
    monkeypatch.setattr("ghdcbot.adapters.github.rest.time.time", lambda: fixed_now)
    monkeypatch.setattr("ghdcbot.adapters.github.rest.time.sleep", lambda s: sleep_calls.append(s))
    caplog.set_level("WARNING")

    response = adapter._request("GET", "/repos/AOSSIE/Gitcord/issues", {})

    assert response is None
    assert client.call_count == _GITHUB_MAX_RATE_LIMIT_RECOVERIES + 1
    assert len(sleep_calls) == _GITHUB_MAX_RATE_LIMIT_RECOVERIES
    assert len(_event_records(caplog, "github_rate_limit_recovery_exhausted")) == 1


def test_rate_limit_negative_sleep_clamped_to_minimum(monkeypatch, caplog) -> None:
    fixed_now = 2_000_000.0
    reset_ts = int(fixed_now) - 10
    client = _SequenceMockClient(
        [
            httpx.Response(
                403,
                headers={
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(reset_ts),
                },
                text="rate limit",
            ),
            _ok_response(),
        ]
    )
    adapter = _adapter_with_client(client)
    sleep_calls: list[float] = []
    monkeypatch.setattr("ghdcbot.adapters.github.rest.time.time", lambda: fixed_now)
    monkeypatch.setattr("ghdcbot.adapters.github.rest.time.sleep", lambda s: sleep_calls.append(s))
    caplog.set_level("WARNING")

    response = adapter._request("GET", "/repos/AOSSIE/Gitcord/issues", {})

    assert response is not None
    assert response.status_code == 200
    assert sleep_calls == [1.0]


def test_rate_limit_429_with_retry_after_sleep_then_success(monkeypatch, caplog) -> None:
    client = _SequenceMockClient(
        [
            httpx.Response(
                429,
                headers={"Retry-After": "4"},
                text="Too Many Requests",
            ),
            _ok_response(),
        ]
    )
    adapter = _adapter_with_client(client)
    sleep_calls: list[float] = []
    monkeypatch.setattr("ghdcbot.adapters.github.rest.time.sleep", lambda s: sleep_calls.append(s))
    caplog.set_level("WARNING")

    response = adapter._request("GET", "/repos/AOSSIE/Gitcord/issues", {})

    assert response is not None
    assert response.status_code == 200
    assert client.call_count == 2
    assert sleep_calls == [4.0]
    assert len(_event_records(caplog, "github_rate_limit_exhausted")) == 1
    assert _event_records(caplog, "github_rate_limit_exhausted")[0].sleep_seconds == 4.0
    assert len(_event_records(caplog, "github_rate_limit_recovered")) == 1


def test_rate_limit_429_with_reset_header_sleep_then_success(monkeypatch, caplog) -> None:
    fixed_now = 3_000_000.0
    reset_ts = int(fixed_now) + 5
    client = _SequenceMockClient(
        [
            httpx.Response(
                429,
                headers={"X-RateLimit-Reset": str(reset_ts)},
                text="Too Many Requests",
            ),
            _ok_response(),
        ]
    )
    adapter = _adapter_with_client(client)
    sleep_calls: list[float] = []
    monkeypatch.setattr("ghdcbot.adapters.github.rest.time.time", lambda: fixed_now)
    monkeypatch.setattr("ghdcbot.adapters.github.rest.time.sleep", lambda s: sleep_calls.append(s))
    caplog.set_level("WARNING")

    response = adapter._request("GET", "/repos/AOSSIE/Gitcord/issues", {})

    assert response is not None
    assert response.status_code == 200
    assert client.call_count == 2
    assert sleep_calls == [5.0]
    assert len(_event_records(caplog, "github_rate_limit_exhausted")) == 1
    assert len(_event_records(caplog, "github_rate_limit_recovered")) == 1


def test_rate_limit_429_missing_headers_defaults_to_fallback_sleep(monkeypatch, caplog) -> None:
    client = _SequenceMockClient(
        [
            httpx.Response(
                429,
                headers={},
                text="Too Many Requests",
            ),
            _ok_response(),
        ]
    )
    adapter = _adapter_with_client(client)
    sleep_calls: list[float] = []
    monkeypatch.setattr("ghdcbot.adapters.github.rest.time.sleep", lambda s: sleep_calls.append(s))
    caplog.set_level("WARNING")

    response = adapter._request("GET", "/repos/AOSSIE/Gitcord/issues", {})

    assert response is not None
    assert response.status_code == 200
    assert client.call_count == 2
    assert sleep_calls == [_GITHUB_DEFAULT_429_RETRY_AFTER_SECONDS]
    assert len(_event_records(caplog, "github_rate_limit_exhausted")) == 1
    assert (
        _event_records(caplog, "github_rate_limit_exhausted")[0].sleep_seconds
        == _GITHUB_DEFAULT_429_RETRY_AFTER_SECONDS
    )
    assert len(_event_records(caplog, "github_rate_limit_recovered")) == 1


def test_rate_limit_429_recovery_cap_exceeded(monkeypatch, caplog) -> None:
    client = _SequenceMockClient(
        [
            httpx.Response(
                429,
                headers={"Retry-After": "2"},
                text="Too Many Requests",
            )
        ]
        * (_GITHUB_MAX_RATE_LIMIT_RECOVERIES + 1)
    )
    adapter = _adapter_with_client(client)
    sleep_calls: list[float] = []
    monkeypatch.setattr("ghdcbot.adapters.github.rest.time.sleep", lambda s: sleep_calls.append(s))
    caplog.set_level("WARNING")

    response = adapter._request("GET", "/repos/AOSSIE/Gitcord/issues", {})

    assert response is None
    assert client.call_count == _GITHUB_MAX_RATE_LIMIT_RECOVERIES + 1
    assert len(sleep_calls) == _GITHUB_MAX_RATE_LIMIT_RECOVERIES
    assert len(_event_records(caplog, "github_rate_limit_recovery_exhausted")) == 1

