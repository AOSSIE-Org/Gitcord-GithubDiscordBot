from __future__ import annotations

import logging
import secrets
import string
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ghdcbot.adapters.github.identity import GitHubIdentityReader, VerificationMatch
from ghdcbot.adapters.storage.sqlite import SqliteStorage


@dataclass(frozen=True)
class LinkClaim:
    discord_user_id: str
    github_user: str
    verification_code: str
    expires_at: datetime


class IdentityLinkService:
    """Phase-1 identity linking via verification code (no OAuth, no server)."""

    def __init__(
        self,
        storage: SqliteStorage,
        github_identity: GitHubIdentityReader,
        *,
        ttl_minutes: int = 10,
    ) -> None:
        self._logger = logging.getLogger(self.__class__.__name__)
        self._storage = storage
        self._github = github_identity
        self._ttl = timedelta(minutes=ttl_minutes)

    def create_claim(self, discord_user_id: str, github_user: str, *, max_age_days: int | None = None) -> LinkClaim:
        code = _generate_verification_code()
        expires_at = datetime.now(timezone.utc) + self._ttl
        # Ensure schema exists for identity_links before writing.
        try:
            self._storage.init_schema()
        except Exception as e:  # noqa: BLE001
            # init_schema is idempotent; failures will surface on insert if schema is missing.
            self._logger.debug("init_schema call failed (will retry on insert)", extra={"error": str(e)})
        self._storage.create_identity_claim(
            discord_user_id=discord_user_id,
            github_user=github_user,
            verification_code=code,
            expires_at=expires_at,
            max_age_days=max_age_days,
        )
        append_audit = getattr(self._storage, "append_audit_event", None)
        if callable(append_audit):
            append_audit({
                "actor_type": "discord_user",
                "actor_id": discord_user_id,
                "event_type": "identity_claim_created",
                "context": {"github_user": github_user, "expires_at": expires_at.isoformat()},
            })
        self._logger.info(
            "Created identity claim",
            extra={
                "discord_user_id": discord_user_id,
                "github_user": github_user,
                "expires_at": expires_at.isoformat(),
            },
        )
        return LinkClaim(
            discord_user_id=discord_user_id,
            github_user=github_user,
            verification_code=code,
            expires_at=expires_at,
        )

    def verify_claim(self, discord_user_id: str, github_user: str) -> tuple[bool, str | None]:
        row = self._storage.get_identity_link(discord_user_id, github_user)
        if not row:
            raise ValueError("No identity claim found for this Discord user and GitHub user")
        if int(row.get("verified") or 0) == 1:
            return True, "already-verified"

        code = row.get("verification_code")
        expires_at_raw = row.get("expires_at")
        if not code or not expires_at_raw:
            raise ValueError("Identity claim is missing verification_code or expires_at")

        expires_at = datetime.fromisoformat(expires_at_raw)
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        expires_at = expires_at.astimezone(timezone.utc)

        now = datetime.now(timezone.utc)
        if expires_at <= now:
            append_audit = getattr(self._storage, "append_audit_event", None)
            if callable(append_audit):
                append_audit({
                    "actor_type": "discord_user",
                    "actor_id": discord_user_id,
                    "event_type": "identity_verification_expired",
                    "context": {"github_user": github_user},
                })
            self._logger.info(
                "Identity claim expired",
                extra={"discord_user_id": discord_user_id, "github_user": github_user},
            )
            return False, "expired"

        match: VerificationMatch = self._github.search_verification_code(github_user, code)
        if not match.found:
            append_audit = getattr(self._storage, "append_audit_event", None)
            if callable(append_audit):
                append_audit({
                    "actor_type": "discord_user",
                    "actor_id": discord_user_id,
                    "event_type": "identity_verification_not_found",
                    "context": {"github_user": github_user},
                })
            self._logger.info(
                "Identity verification not found yet",
                extra={"discord_user_id": discord_user_id, "github_user": github_user},
            )
            return False, None

        self._storage.mark_identity_verified(discord_user_id, github_user)
        append_audit = getattr(self._storage, "append_audit_event", None)
        if callable(append_audit):
            append_audit({
                "actor_type": "discord_user",
                "actor_id": discord_user_id,
                "event_type": "identity_verified",
                "context": {"github_user": github_user, "location": match.location},
            })
        self._logger.info(
            "Identity verified",
            extra={
                "discord_user_id": discord_user_id,
                "github_user": github_user,
                "location": match.location,
            },
        )
        return True, match.location

    def unlink(self, discord_user_id: str, cooldown_hours: int = 24) -> None:
        """Unlink the verified identity for this Discord user. Cooldown enforced.
        Raises ValueError if no verified link or inside cooldown. Emits identity_unlinked audit event.
        """
        unlinker = getattr(self._storage, "unlink_identity", None)
        if not callable(unlinker):
            raise ValueError("Storage does not support unlink")
        info = unlinker(discord_user_id, cooldown_hours)
        if info is None:
            raise ValueError("No verified identity link found for this Discord user.")
        append_audit = getattr(self._storage, "append_audit_event", None)
        if callable(append_audit):
            append_audit({
                "actor_type": "discord_user",
                "actor_id": discord_user_id,
                "event_type": "identity_unlinked",
                "context": {
                    "github_user": info["github_user"],
                    "verified_at": info["verified_at"],
                    "unlinked_at": info["unlinked_at"],
                },
            })
        self._logger.info(
            "Identity unlinked",
            extra={
                "discord_user_id": discord_user_id,
                "github_user": info["github_user"],
                "unlinked_at": info["unlinked_at"],
            },
        )


def _generate_verification_code(length: int = 10) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))

