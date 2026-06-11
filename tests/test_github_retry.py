from __future__ import annotations

import httpx
import pytest

from ghdcbot.adapters.github.rest import GitHubRestAdapter


class _SequenceMockClient:
    def __init__(self, outcomes: list[httpx.Response | Exception]) -> None:
        self._outcomes = list(outcomes)
        self.call_count = 0

    def request(self, method: str, path: str, params: dict | None = None) -> httpx.Response:
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


def _status_response(status_code: int) -> httpx.Response:
    return httpx.Response(status_code, headers={"X-RateLimit-Remaining": "100"})


def _retry_records(caplog):
    return [r for r in caplog.records if getattr(r, "event", None) == "github_request_retry"]


def _failed_records(caplog):
    return [r for r in caplog.records if getattr(r, "event", None) == "github_request_failed"]


def test_retry_503_twice_then_200(monkeypatch, caplog) -> None:
    client = _SequenceMockClient(
        [
            _status_response(503),
            _status_response(503),
            _ok_response(),
        ]
    )
    adapter = _adapter_with_client(client)
    monkeypatch.setattr("ghdcbot.adapters.github.rest.time.sleep", lambda _s: None)
    caplog.set_level("WARNING")

    response = adapter._request("GET", "/repos/AOSSIE/Gitcord/issues", {})

    assert response is not None
    assert response.status_code == 200
    assert client.call_count == 3
    assert len(_retry_records(caplog)) == 2
    assert _retry_records(caplog)[0].attempt == 2
    assert _retry_records(caplog)[1].attempt == 3


def test_retry_timeout_twice_then_200(monkeypatch, caplog) -> None:
    client = _SequenceMockClient(
        [
            httpx.TimeoutException("timeout"),
            httpx.TimeoutException("timeout"),
            _ok_response(),
        ]
    )
    adapter = _adapter_with_client(client)
    monkeypatch.setattr("ghdcbot.adapters.github.rest.time.sleep", lambda _s: None)
    caplog.set_level("WARNING")

    response = adapter._request("GET", "/repos/AOSSIE/Gitcord/issues", {})

    assert response is not None
    assert response.status_code == 200
    assert client.call_count == 3
    assert len(_retry_records(caplog)) == 2
    assert _retry_records(caplog)[0].reason == "Timeout"


def test_404_no_retry(caplog) -> None:
    client = _SequenceMockClient([_status_response(404)])
    adapter = _adapter_with_client(client)
    caplog.set_level("WARNING")

    response = adapter._request("GET", "/repos/AOSSIE/Gitcord/issues/999", {})

    assert response is None
    assert client.call_count == 1
    assert _retry_records(caplog) == []
    assert _failed_records(caplog) == []


def test_401_no_retry(caplog) -> None:
    client = _SequenceMockClient([_status_response(401)])
    adapter = _adapter_with_client(client)
    caplog.set_level("WARNING")

    response = adapter._request("GET", "/repos/AOSSIE/Gitcord/issues", {})

    assert response is None
    assert client.call_count == 1
    assert _retry_records(caplog) == []
    assert _failed_records(caplog) == []


def test_503_exhausts_max_attempts(monkeypatch, caplog) -> None:
    client = _SequenceMockClient(
        [
            _status_response(503),
            _status_response(503),
            _status_response(503),
            _status_response(503),
        ]
    )
    adapter = _adapter_with_client(client)
    monkeypatch.setattr("ghdcbot.adapters.github.rest.time.sleep", lambda _s: None)
    caplog.set_level("WARNING")

    response = adapter._request("GET", "/repos/AOSSIE/Gitcord/issues", {})

    assert response is None
    assert client.call_count == 4
    assert len(_retry_records(caplog)) == 3
    assert len(_failed_records(caplog)) == 1
    assert _failed_records(caplog)[0].attempts == 4
    assert "503" in _failed_records(caplog)[0].reason


def test_permission_403_no_retry(caplog) -> None:
    client = _SequenceMockClient(
        [
            httpx.Response(
                403,
                headers={"X-RateLimit-Remaining": "4521"},
                text="Forbidden",
            )
        ]
    )
    adapter = _adapter_with_client(client)
    caplog.set_level("WARNING")

    response = adapter._request("GET", "/repos/AOSSIE/private-repo/issues", {})

    assert response is None
    assert client.call_count == 1
    assert _retry_records(caplog) == []


def test_rate_limit_403_missing_reset_no_transient_retry(caplog) -> None:
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
    assert _retry_records(caplog) == []
    assert any(
        getattr(r, "event", None) == "github_rate_limit_missing_reset" for r in caplog.records
    )
