"""Tests for GitHub App token resolution helpers."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import httpx
import pytest

from ghdcbot.adapters.github import app_auth
from ghdcbot.adapters.github.app_auth import (
    DynamicBearerAuth,
    GitHubAppTokenProvider,
    _load_private_key,
    build_github_httpx_client,
    github_app_provider_from_env,
    resolve_github_token,
)


@pytest.fixture(autouse=True)
def _clear_provider_cache() -> None:
    app_auth._provider_cache = None
    app_auth._provider_cache_key = None
    yield
    app_auth._provider_cache = None
    app_auth._provider_cache_key = None


def test_resolve_github_token_falls_back_to_pat(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_APP_ID", raising=False)
    monkeypatch.delenv("GITHUB_APP_INSTALLATION_ID", raising=False)
    token = resolve_github_token(pat="ghp_test_pat")
    assert token == "ghp_test_pat"


def test_resolve_github_token_requires_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_APP_ID", raising=False)
    monkeypatch.delenv("GITHUB_APP_INSTALLATION_ID", raising=False)
    with pytest.raises(RuntimeError, match="No GitHub credentials"):
        resolve_github_token(pat="")


def test_dynamic_bearer_auth_sets_header() -> None:
    auth = DynamicBearerAuth(lambda: "tok_abc")
    request = httpx.Request("GET", "https://api.github.com/user")
    flow = auth.auth_flow(request)
    next_req = next(flow)
    assert next_req.headers["Authorization"] == "Bearer tok_abc"


def test_dynamic_bearer_auth_wraps_token_errors() -> None:
    def boom() -> str:
        raise RuntimeError("mint failed")

    auth = DynamicBearerAuth(boom)
    request = httpx.Request("GET", "https://api.github.com/user")
    with pytest.raises(httpx.RequestError) as exc_info:
        next(auth.auth_flow(request))
    assert isinstance(exc_info.value.__cause__, RuntimeError)


def test_build_github_httpx_client_callable_token() -> None:
    client = build_github_httpx_client(lambda: "tok", api_base="https://api.github.com")
    assert client.auth is not None
    client.close()


def test_get_token_reuses_cache_and_refreshes_at_skew() -> None:
    provider = GitHubAppTokenProvider(
        app_id="1",
        installation_id="2",
        private_key_pem="-----BEGIN PRIVATE KEY-----\nX\n-----END PRIVATE KEY-----",
    )
    provider._token = "cached"
    provider._expires_at = time.time() + 600
    assert provider.get_token() == "cached"

    provider._expires_at = time.time() + 60  # inside skew window
    with patch.object(provider, "_mint_installation_token", return_value="fresh") as mint:
        assert provider.get_token() == "fresh"
        mint.assert_called_once()


def test_mint_installation_token_parses_expires_at_and_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = GitHubAppTokenProvider(
        app_id="1",
        installation_id="2",
        private_key_pem="-----BEGIN PRIVATE KEY-----\nX\n-----END PRIVATE KEY-----",
    )
    monkeypatch.setattr(provider, "_app_jwt", lambda: "jwt")

    response = MagicMock()
    response.status_code = 201
    response.json.return_value = {
        "token": "inst_tok",
        "expires_at": "2099-01-01T00:00:00Z",
    }
    with patch("httpx.Client") as client_cls:
        client_cls.return_value.__enter__.return_value.post.return_value = response
        token = provider._mint_installation_token()
    assert token == "inst_tok"
    assert provider._expires_at > time.time() + 1000

    response.json.return_value = {"token": "inst_tok2", "expires_at": "not-a-date"}
    before = time.time()
    with patch("httpx.Client") as client_cls:
        client_cls.return_value.__enter__.return_value.post.return_value = response
        provider._mint_installation_token()
    assert abs(provider._expires_at - (before + 3500)) < 5


def test_github_app_provider_requires_private_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_APP_ID", "1")
    monkeypatch.setenv("GITHUB_APP_INSTALLATION_ID", "2")
    monkeypatch.delenv("GITHUB_APP_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("GITHUB_APP_PRIVATE_KEY_PATH", raising=False)
    with pytest.raises(RuntimeError, match="PRIVATE_KEY"):
        github_app_provider_from_env()


def test_github_app_provider_warns_when_incomplete(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setenv("GITHUB_APP_ID", "1")
    monkeypatch.delenv("GITHUB_APP_INSTALLATION_ID", raising=False)
    with caplog.at_level("WARNING"):
        assert github_app_provider_from_env() is None
    assert any("Incomplete GitHub App configuration" in r.message for r in caplog.records)


def test_load_private_key_converts_escaped_newlines(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", "line1\\nline2")
    monkeypatch.delenv("GITHUB_APP_PRIVATE_KEY_PATH", raising=False)
    assert _load_private_key() == "line1\nline2"


def test_provider_repr_hides_secrets() -> None:
    provider = GitHubAppTokenProvider(
        app_id="1",
        installation_id="2",
        private_key_pem="SECRET_PEM_MATERIAL",
    )
    provider._token = "SECRET_TOKEN"
    text = repr(provider)
    assert "SECRET_PEM_MATERIAL" not in text
    assert "SECRET_TOKEN" not in text
