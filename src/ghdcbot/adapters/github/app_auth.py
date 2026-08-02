"""GitHub App authentication: JWT → installation access tokens.

When ``GITHUB_APP_ID``, ``GITHUB_APP_INSTALLATION_ID``, and a private key are set,
Gitcord uses short-lived installation tokens (``GitcordApp[bot]``) instead of a PAT.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import jwt

logger = logging.getLogger(__name__)

# Refresh a few minutes before GitHub's ~1h expiry.
_TOKEN_REFRESH_SKEW_SECONDS = 120

# One provider per (app_id, installation_id, api_base) so mint/refresh is shared.
_provider_cache: GitHubAppTokenProvider | None = None
_provider_cache_key: tuple[str, str, str] | None = None


@dataclass
class GitHubAppTokenProvider:
    """Caches installation access tokens minted with a GitHub App private key."""

    app_id: str
    installation_id: str
    private_key_pem: str = field(repr=False)
    api_base: str = "https://api.github.com"

    _token: str | None = field(default=None, init=False, repr=False)
    _expires_at: float = field(default=0.0, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def get_token(self) -> str:
        with self._lock:
            now = time.time()
            if self._token and now < (self._expires_at - _TOKEN_REFRESH_SKEW_SECONDS):
                return self._token
            return self._mint_installation_token()

    def _app_jwt(self) -> str:
        now = int(time.time())
        payload = {
            "iat": now - 60,
            "exp": now + 540,
            "iss": self.app_id,
        }
        token = jwt.encode(payload, self.private_key_pem, algorithm="RS256")
        return token.decode() if isinstance(token, bytes) else token

    def _mint_installation_token(self) -> str:
        url = f"{self.api_base.rstrip('/')}/app/installations/{self.installation_id}/access_tokens"
        headers = {
            "Authorization": f"Bearer {self._app_jwt()}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "GitcordApp",
        }
        with httpx.Client(timeout=30.0) as client:
            response = client.post(url, headers=headers)
        if response.status_code not in {200, 201}:
            raise RuntimeError(
                f"GitHub App installation token failed "
                f"(status={response.status_code}): {response.text[:300]}"
            )
        data = response.json()
        token = data.get("token")
        if not token or not isinstance(token, str):
            raise RuntimeError("GitHub App installation token response missing token")
        expires_at_raw = data.get("expires_at")
        if isinstance(expires_at_raw, str) and expires_at_raw:
            # 2026-07-30T08:00:00Z
            try:
                from datetime import datetime

                expires = datetime.fromisoformat(expires_at_raw.replace("Z", "+00:00"))
                self._expires_at = expires.timestamp()
            except ValueError:
                self._expires_at = time.time() + 3500
        else:
            self._expires_at = time.time() + 3500
        self._token = token
        logger.info(
            "Minted GitHub App installation token",
            extra={"installation_id": self.installation_id, "app_id": self.app_id},
        )
        return token


def _load_private_key() -> str | None:
    pem = (os.environ.get("GITHUB_APP_PRIVATE_KEY") or "").strip()
    if pem:
        # Support escaped newlines from env files.
        return pem.replace("\\n", "\n")
    path_raw = (os.environ.get("GITHUB_APP_PRIVATE_KEY_PATH") or "").strip()
    if not path_raw:
        return None
    path = Path(path_raw)
    if not path.is_file():
        raise RuntimeError(f"GITHUB_APP_PRIVATE_KEY_PATH not found: {path}")
    return path.read_text(encoding="utf-8")


def github_app_provider_from_env(*, api_base: str = "https://api.github.com") -> GitHubAppTokenProvider | None:
    """Build a provider when App env vars are present; otherwise return None (use PAT)."""
    global _provider_cache, _provider_cache_key

    app_id = (os.environ.get("GITHUB_APP_ID") or "").strip()
    installation_id = (os.environ.get("GITHUB_APP_INSTALLATION_ID") or "").strip()
    if not app_id and not installation_id:
        return None
    if bool(app_id) ^ bool(installation_id):
        logger.warning(
            "Incomplete GitHub App configuration: set both GITHUB_APP_ID and "
            "GITHUB_APP_INSTALLATION_ID (or neither); falling back to PAT if set",
            extra={
                "has_app_id": bool(app_id),
                "has_installation_id": bool(installation_id),
            },
        )
        return None
    private_key = _load_private_key()
    if not private_key:
        raise RuntimeError(
            "GITHUB_APP_ID and GITHUB_APP_INSTALLATION_ID are set, but "
            "GITHUB_APP_PRIVATE_KEY_PATH / GITHUB_APP_PRIVATE_KEY is missing"
        )
    api_base_normalized = api_base.rstrip("/")
    cache_key = (app_id, installation_id, api_base_normalized)
    if _provider_cache is not None and _provider_cache_key == cache_key:
        return _provider_cache
    provider = GitHubAppTokenProvider(
        app_id=app_id,
        installation_id=installation_id,
        private_key_pem=private_key,
        api_base=api_base_normalized,
    )
    _provider_cache = provider
    _provider_cache_key = cache_key
    return provider


def resolve_github_token(
    *,
    pat: str,
    api_base: str = "https://api.github.com",
) -> str | Callable[[], str]:
    """Prefer GitHub App installation token; fall back to PAT string."""
    provider = github_app_provider_from_env(api_base=api_base)
    if provider is not None:
        return provider.get_token
    token = (pat or "").strip()
    if not token:
        raise RuntimeError(
            "No GitHub credentials: set GITHUB_TOKEN or GitHub App env "
            "(GITHUB_APP_ID, GITHUB_APP_INSTALLATION_ID, GITHUB_APP_PRIVATE_KEY_PATH)"
        )
    return token


class DynamicBearerAuth(httpx.Auth):
    """httpx auth that refreshes the Bearer token on each request."""

    def __init__(self, get_token: Callable[[], str]) -> None:
        self._get_token = get_token

    def auth_flow(self, request: httpx.Request):
        try:
            token = self._get_token()
        except Exception as exc:
            raise httpx.RequestError(
                f"Failed to obtain GitHub token: {exc}",
                request=request,
            ) from exc
        request.headers["Authorization"] = f"Bearer {token}"
        yield request


def build_github_httpx_client(
    token: str | Callable[[], str],
    *,
    api_base: str,
    timeout: float = 30.0,
) -> httpx.Client:
    """Build an httpx client for GitHub REST (PAT string or App token getter)."""
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "GitcordApp",
    }
    if callable(token):
        return httpx.Client(
            base_url=api_base,
            headers=headers,
            auth=DynamicBearerAuth(token),
            timeout=timeout,
        )
    headers["Authorization"] = f"Bearer {token}"
    return httpx.Client(base_url=api_base, headers=headers, timeout=timeout)
