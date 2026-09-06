from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from ghdcbot.adapters.github.rest import GitHubRestAdapter


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
    adapter._client = client
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


def test_create_issue_timeout_after_acceptance_reconciles_without_duplicate_post() -> None:
    """Test that a timeout during POST when GitHub accepted the issue reconciles and does not re-POST."""
    from unittest.mock import MagicMock

    adapter = GitHubRestAdapter(token="t", org="AOSSIE", api_base="https://api.github.com")
    client = MagicMock()

    created_issue = {
        "number": 42,
        "title": "Bug in Sync",
        "body": "Detailed description",
        "html_url": "https://github.com/AOSSIE/Gitcord/issues/42",
        "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    client.post.side_effect = httpx.TimeoutException(
        "Request timed out",
        request=httpx.Request("POST", "https://api.github.com/repos/AOSSIE/Gitcord/issues"),
    )
    client.get.return_value = httpx.Response(
        200,
        json=[created_issue],
        headers={"X-RateLimit-Remaining": "100"},
    )
    adapter._client = client

    result = adapter.create_issue("AOSSIE", "Gitcord", "Bug in Sync", "Detailed description")

    assert result is not None
    assert result["number"] == 42
    assert result["html_url"] == "https://github.com/AOSSIE/Gitcord/issues/42"
    assert adapter.last_error is None
    assert client.post.call_count == 1
    assert client.get.call_count == 1


def test_create_issue_retry_reconciles_existing_claim_before_second_post() -> None:
    """If timeout occurs and reconciliation initially fails, retry reconciles claim before issuing duplicate POST."""
    from unittest.mock import MagicMock

    adapter = GitHubRestAdapter(token="t", org="AOSSIE", api_base="https://api.github.com")
    client = MagicMock()

    created_issue = {
        "number": 99,
        "title": "Feature Request",
        "body": "Please add X",
        "html_url": "https://github.com/AOSSIE/Gitcord/issues/99",
        "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    client.post.side_effect = httpx.TimeoutException(
        "POST timeout",
        request=httpx.Request("POST", "https://api.github.com/repos/AOSSIE/Gitcord/issues"),
    )
    client.get.side_effect = [
        httpx.TimeoutException(
            "GET timeout",
            request=httpx.Request("GET", "https://api.github.com/repos/AOSSIE/Gitcord/issues"),
        ),
        httpx.Response(
            200,
            json=[created_issue],
            headers={"X-RateLimit-Remaining": "100"},
        ),
    ]
    adapter._client = client

    # 1st call fails due to timeout
    result1 = adapter.create_issue("AOSSIE", "Gitcord", "Feature Request", "Please add X")
    assert result1 is None
    assert client.post.call_count == 1

    # 2nd call (retry) reconciles existing publish claim with GitHub before any POST
    result2 = adapter.create_issue("AOSSIE", "Gitcord", "Feature Request", "Please add X")
    assert result2 is not None
    assert result2["number"] == 99
    assert adapter.last_error is None
    # Verify NO duplicate POST was made
    assert client.post.call_count == 1
    assert client.get.call_count == 2


def test_reconcile_issue_claim_ignores_preexisting_same_title_issue() -> None:
    """Old pre-existing issue with the same title must not be returned during claim reconciliation."""
    from datetime import timedelta
    from unittest.mock import MagicMock

    adapter = GitHubRestAdapter(token="t", org="AOSSIE", api_base="https://api.github.com")
    client = MagicMock()

    claim_time = datetime.now(UTC)
    old_issue_time = (claim_time - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")

    preexisting_issue = {
        "number": 10,
        "title": "Existing Topic",
        "html_url": "https://github.com/AOSSIE/Gitcord/issues/10",
        "created_at": old_issue_time,
    }

    client.get.return_value = httpx.Response(
        200,
        json=[preexisting_issue],
        headers={"X-RateLimit-Remaining": "100"},
    )
    adapter._client = client

    # Should ignore pre-existing issue and return None
    result = adapter._reconcile_issue_claim(
        "AOSSIE", "Gitcord", "Existing Topic", claim_created_at=claim_time.timestamp()
    )
    assert result is None


def test_reconcile_issue_claim_ignores_pull_requests() -> None:
    """Pull requests with the same title must be excluded from issue reconciliation."""
    from unittest.mock import MagicMock

    adapter = GitHubRestAdapter(token="t", org="AOSSIE", api_base="https://api.github.com")
    client = MagicMock()

    claim_time = datetime.now(UTC)
    pr_item = {
        "number": 15,
        "title": "PR Title",
        "html_url": "https://github.com/AOSSIE/Gitcord/pull/15",
        "created_at": claim_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "pull_request": {"url": "https://api.github.com/repos/AOSSIE/Gitcord/pulls/15"},
    }

    client.get.return_value = httpx.Response(
        200,
        json=[pr_item],
        headers={"X-RateLimit-Remaining": "100"},
    )
    adapter._client = client

    result = adapter._reconcile_issue_claim(
        "AOSSIE", "Gitcord", "PR Title", claim_created_at=claim_time.timestamp()
    )
    assert result is None


def test_reconcile_issue_claim_matches_issue_at_or_after_claim() -> None:
    """Matching issue created at or after claim time is successfully reconciled."""
    from unittest.mock import MagicMock

    adapter = GitHubRestAdapter(token="t", org="AOSSIE", api_base="https://api.github.com")
    client = MagicMock()

    claim_time = datetime.now(UTC)
    fresh_issue = {
        "number": 25,
        "title": "Fresh Topic",
        "html_url": "https://github.com/AOSSIE/Gitcord/issues/25",
        "created_at": claim_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    client.get.return_value = httpx.Response(
        200,
        json=[fresh_issue],
        headers={"X-RateLimit-Remaining": "100"},
    )
    adapter._client = client

    result = adapter._reconcile_issue_claim(
        "AOSSIE", "Gitcord", "Fresh Topic", claim_created_at=claim_time.timestamp()
    )
    assert result is not None
    assert result["number"] == 25


def test_create_issue_non_httpx_exception_not_masked() -> None:
    """Verify that broad/unexpected exceptions are not caught or masked."""
    from unittest.mock import MagicMock

    adapter = GitHubRestAdapter(token="t", org="AOSSIE", api_base="https://api.github.com")
    client = MagicMock()
    client.post.side_effect = TypeError("Unexpected internal error")
    adapter._client = client

    with pytest.raises(TypeError, match="Unexpected internal error"):
        adapter.create_issue("AOSSIE", "Gitcord", "Title", "Body")


def test_publish_claim_ttl_eviction() -> None:
    """Claims older than TTL are evicted, while fresh claims are retained."""
    import time
    from unittest.mock import MagicMock

    adapter = GitHubRestAdapter(token="t", org="AOSSIE", api_base="https://api.github.com")
    adapter._client = MagicMock()

    now = time.time()
    stale_key = ("AOSSIE", "Gitcord", "Stale Claim")
    fresh_key = ("AOSSIE", "Gitcord", "Fresh Claim")

    adapter._publish_claims[stale_key] = {
        "title": "Stale Claim",
        "created_at": now - 400.0,
    }
    adapter._publish_claims[fresh_key] = {
        "title": "Fresh Claim",
        "created_at": now - 30.0,
    }

    adapter._evict_expired_claims(now=now)

    assert stale_key not in adapter._publish_claims
    assert fresh_key in adapter._publish_claims


def test_create_issue_5xx_retains_claim_when_not_reconciled() -> None:
    """A 5xx response retains the claim in _publish_claims if reconciliation does not find the issue."""
    from unittest.mock import MagicMock

    adapter = GitHubRestAdapter(token="t", org="AOSSIE", api_base="https://api.github.com")
    client = MagicMock()
    client.post.return_value = httpx.Response(502, text="Bad Gateway")
    client.get.return_value = httpx.Response(200, json=[], headers={"X-RateLimit-Remaining": "100"})
    adapter._client = client

    claim_key = ("AOSSIE", "Gitcord", "Server Error Topic")
    result = adapter.create_issue("AOSSIE", "Gitcord", "Server Error Topic", "Body")

    assert result is None
    # Claim must be retained for uncertain 5xx outcome
    assert claim_key in adapter._publish_claims
    assert "502" in adapter.last_error


def test_create_issue_5xx_reconciles_when_issue_exists() -> None:
    """A 5xx response successfully reconciles and pops the claim if GitHub accepted the issue."""
    from unittest.mock import MagicMock

    adapter = GitHubRestAdapter(token="t", org="AOSSIE", api_base="https://api.github.com")
    client = MagicMock()
    client.post.return_value = httpx.Response(503, text="Service Unavailable")

    claim_time = datetime.now(UTC)
    reconciled_issue = {
        "number": 55,
        "title": "Accepted Despite 503",
        "html_url": "https://github.com/AOSSIE/Gitcord/issues/55",
        "created_at": claim_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    client.get.return_value = httpx.Response(
        200,
        json=[reconciled_issue],
        headers={"X-RateLimit-Remaining": "100"},
    )
    adapter._client = client

    claim_key = ("AOSSIE", "Gitcord", "Accepted Despite 503")
    result = adapter.create_issue("AOSSIE", "Gitcord", "Accepted Despite 503", "Body")

    assert result is not None
    assert result["number"] == 55
    # Claim is successfully reconciled and popped
    assert claim_key not in adapter._publish_claims
    assert adapter.last_error is None


def test_create_issue_4xx_removes_claim() -> None:
    """Client errors (4xx) definitively reject issue creation and must remove the claim."""
    from unittest.mock import MagicMock

    adapter = GitHubRestAdapter(token="t", org="AOSSIE", api_base="https://api.github.com")
    client = MagicMock()
    client.post.return_value = httpx.Response(403, text="Forbidden")
    adapter._client = client

    claim_key = ("AOSSIE", "Gitcord", "Forbidden Topic")
    result = adapter.create_issue("AOSSIE", "Gitcord", "Forbidden Topic", "Body")

    assert result is None
    # Claim must be removed on 403
    assert claim_key not in adapter._publish_claims
    assert "Permission denied" in adapter.last_error


