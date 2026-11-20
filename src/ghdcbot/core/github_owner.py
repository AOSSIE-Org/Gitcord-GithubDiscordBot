"""GitHub repository-owner abstraction (personal accounts and organizations).

Gitcord monitors repositories that live under one GitHub *owner*. The owner is either a
personal account (``user``) or an ``organization``. Everything that differs between the
two (repository listing endpoints, search qualifiers, membership checks) is centralised
here so callers never branch on raw API quirks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

OWNER_TYPE_USER = "user"
OWNER_TYPE_ORGANIZATION = "organization"
OWNER_TYPE_AUTO = "auto"

ResolvedOwnerType = Literal["user", "organization"]


class OwnerResolutionError(RuntimeError):
    """The configured GitHub owner could not be resolved or accessed.

    The message always says *why* (not found vs. bad credentials vs. rate limit vs.
    network) so operators are not misled into thinking a valid account does not exist.
    """


@dataclass(frozen=True)
class GitHubOwner:
    """A resolved repository owner."""

    login: str
    type: ResolvedOwnerType

    @property
    def is_user(self) -> bool:
        return self.type == OWNER_TYPE_USER

    @property
    def is_organization(self) -> bool:
        return self.type == OWNER_TYPE_ORGANIZATION

    @property
    def search_qualifier(self) -> str:
        """GitHub search qualifier scoping a query to this owner's repositories."""
        return f"org:{self.login}" if self.is_organization else f"user:{self.login}"

    def owns_login(self, login: str | None) -> bool:
        """Case-insensitive comparison against another account login."""
        return bool(login) and str(login).strip().lower() == self.login.lower()

    def owns_repo(self, repo: Any) -> bool:
        """True when a GitHub repository payload belongs to this owner.

        Uses ``owner.login`` (stable identity), falling back to the ``full_name`` prefix.
        """
        return repo_owner_login(repo) == self.login.lower()


def normalize_owner_type(value: str | None) -> str:
    """Normalise a configured owner type; raises ``ValueError`` for unknown values."""
    raw = (value or OWNER_TYPE_AUTO).strip().lower()
    aliases = {
        "org": OWNER_TYPE_ORGANIZATION,
        "organisation": OWNER_TYPE_ORGANIZATION,
        "organization": OWNER_TYPE_ORGANIZATION,
        "user": OWNER_TYPE_USER,
        "personal": OWNER_TYPE_USER,
        "auto": OWNER_TYPE_AUTO,
    }
    if raw not in aliases:
        raise ValueError(
            "github.owner_type must be one of: 'auto', 'user', 'organization' "
            f"(got {value!r})"
        )
    return aliases[raw]


def repo_owner_login(repo: Any) -> str:
    """Lower-cased owner login of a repository payload ('' when unknown)."""
    if not isinstance(repo, dict):
        return ""
    owner = repo.get("owner")
    if isinstance(owner, dict) and owner.get("login"):
        return str(owner["login"]).strip().lower()
    full_name = repo.get("full_name")
    if isinstance(full_name, str) and "/" in full_name:
        return full_name.split("/", 1)[0].strip().lower()
    return ""


def owner_from_users_response(login: str, status: int | None, payload: Any) -> GitHubOwner:
    """Classify ``GET /users/{login}`` (works for both users and organizations).

    ``status`` is the HTTP status (``None`` when no response was obtained). Distinct
    failures raise ``OwnerResolutionError`` with distinct messages: a 404 means the
    account does not exist, while 401/403/429/network errors never imply that.
    """
    if status == 200 and isinstance(payload, dict):
        kind = str(payload.get("type") or "").strip().lower()
        canonical = str(payload.get("login") or login).strip() or login
        if kind == "organization":
            return GitHubOwner(canonical, OWNER_TYPE_ORGANIZATION)
        if kind in {"user", "bot"}:
            return GitHubOwner(canonical, OWNER_TYPE_USER)
        raise OwnerResolutionError(
            f"GitHub account '{login}' has unexpected type {kind!r}; "
            "set github.owner_type to 'user' or 'organization' explicitly."
        )
    if status == 404:
        raise OwnerResolutionError(
            f"GitHub account '{login}' was not found (HTTP 404). "
            "Check the spelling of github.owner (or github.org)."
        )
    if status == 401:
        raise OwnerResolutionError(
            f"GitHub rejected the configured credentials (HTTP 401) while looking up "
            f"'{login}'. The account may exist; check GITHUB_TOKEN / GitHub App settings."
        )
    if status == 403 or status == 429:
        raise OwnerResolutionError(
            f"GitHub refused the lookup of '{login}' (HTTP {status}): rate limited or the "
            "token lacks access. The account may exist; retry later or check token scopes."
        )
    if status is None:
        raise OwnerResolutionError(
            f"Could not reach GitHub to look up '{login}' (network error or rate limit "
            "exhausted). The account may exist; retry later."
        )
    raise OwnerResolutionError(
        f"Unexpected GitHub response (HTTP {status}) while looking up '{login}'."
    )
