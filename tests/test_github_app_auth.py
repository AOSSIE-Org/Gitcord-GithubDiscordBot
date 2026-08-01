"""Tests for GitHub App token resolution helpers."""

from __future__ import annotations

import httpx
import pytest

from ghdcbot.adapters.github.app_auth import (
    DynamicBearerAuth,
    build_github_httpx_client,
    resolve_github_token,
)


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


def test_build_github_httpx_client_callable_token() -> None:
    client = build_github_httpx_client(lambda: "tok", api_base="https://api.github.com")
    assert client.auth is not None
    client.close()
