from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable

import httpx


@dataclass(frozen=True)
class VerificationMatch:
    found: bool
    location: str | None = None  # e.g. "bio", "gist:<id>", "gist:<id>:<filename>"


class GitHubIdentityReader:
    """Read-only helper for Phase-1 identity verification.

    This adapter contains no decision logic; it only fetches public GitHub data
    (profile bio and public gists) and searches for a verification code.
    """

    def __init__(self, token: str, api_base: str = "https://api.github.com") -> None:
        self._logger = logging.getLogger(self.__class__.__name__)
        self._client = httpx.Client(
            base_url=api_base,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            },
            timeout=20.0,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "GitHubIdentityReader":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def search_verification_code(self, github_user: str, code: str) -> VerificationMatch:
        """Search for code in GitHub bio or public gists."""
        bio = self._fetch_bio(github_user)
        if bio and code in bio:
            return VerificationMatch(found=True, location="bio")

        for match in self._search_public_gists(github_user, code):
            return match

        return VerificationMatch(found=False, location=None)

    def _fetch_bio(self, github_user: str) -> str | None:
        response = self._request("GET", f"/users/{github_user}", params={})
        if response is None or response.status_code != 200:
            return None
        data = response.json()
        bio = data.get("bio")
        return bio if isinstance(bio, str) else None

    def _search_public_gists(self, github_user: str, code: str) -> Iterable[VerificationMatch]:
        # List public gists for the user.
        response = self._request("GET", f"/users/{github_user}/gists", params={"per_page": 20, "page": 1})
        if response is None or response.status_code != 200:
            return []

        gists = response.json()
        if not isinstance(gists, list):
            return []

        for gist in gists[:20]:
            gist_id = gist.get("id")
            if not gist_id:
                continue
            description = gist.get("description") or ""
            if isinstance(description, str) and code in description:
                yield VerificationMatch(found=True, location=f"gist:{gist_id}:description")
                return

            # Fetch gist details to get file raw URLs.
            gist_resp = self._request("GET", f"/gists/{gist_id}", params={})
            if gist_resp is None or gist_resp.status_code != 200:
                continue
            gist_data = gist_resp.json()
            files = gist_data.get("files") or {}
            if not isinstance(files, dict):
                continue
            for filename, meta in files.items():
                if not isinstance(meta, dict):
                    continue
                raw_url = meta.get("raw_url")
                if not raw_url or not isinstance(raw_url, str):
                    continue
                if self._raw_contains_code(raw_url, code):
                    yield VerificationMatch(found=True, location=f"gist:{gist_id}:{filename}")
                    return
        return []

    def _raw_contains_code(self, raw_url: str, code: str) -> bool:
        try:
            resp = self._client.get(raw_url, headers={"Accept": "text/plain"})
        except httpx.HTTPError as exc:
            self._logger.warning("GitHub raw fetch failed", extra={"url": raw_url, "error": str(exc)})
            return False
        if resp.status_code != 200:
            return False
        text = resp.text
        # Keep this simple; searching is deterministic.
        return code in text

    def _request(self, method: str, path: str, params: dict) -> httpx.Response | None:
        try:
            response = self._client.request(method, path, params=params)
        except httpx.HTTPError as exc:
            self._logger.warning("GitHub identity request failed", extra={"path": path, "error": str(exc)})
            return None
        if response.status_code in {401, 403, 404}:
            self._logger.warning(
                "GitHub identity request denied",
                extra={"path": path, "status_code": response.status_code},
            )
            return None
        return response

