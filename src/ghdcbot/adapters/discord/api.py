from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx


@dataclass(frozen=True)
class DiscordRateLimit:
    remaining: int | None
    reset_at: datetime | None


class DiscordApiAdapter:
    def __init__(self, token: str, guild_id: str) -> None:
        self._logger = logging.getLogger(self.__class__.__name__)
        self._guild_id = guild_id
        self._client = httpx.Client(
            base_url="https://discord.com/api/v10",
            headers={"Authorization": f"Bot {token}"},
            timeout=30.0,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "DiscordApiAdapter":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def list_member_roles(self) -> dict[str, list[str]]:
        """Return mapping of Discord user ID to role names.

        Degrades gracefully when roles or members cannot be listed due to permissions.
        """
        roles, roles_ok = self._list_roles()
        members, members_ok = self._list_members()
        self._log_capabilities(roles_ok=roles_ok, members_ok=members_ok)

        if not roles_ok:
            self._logger.warning(
                "Role listing unavailable; returning empty member roles",
                extra={"guild_id": self._guild_id},
            )
            return {}
        if not members_ok:
            self._logger.warning(
                "Member listing unavailable; returning empty member roles",
                extra={"guild_id": self._guild_id},
            )
            return {}

        role_lookup = {role["id"]: role["name"] for role in roles}
        member_roles: dict[str, list[str]] = {}
        missing_role_ids: set[str] = set()
        for member in members:
            role_names = [role_lookup.get(role_id, "") for role_id in member["roles"]]
            missing_role_ids.update(
                role_id for role_id in member["roles"] if role_id not in role_lookup
            )
            member_roles[member["user"]["id"]] = sorted(
                [name for name in role_names if name]
            )

        self._logger.info(
            "Loaded Discord member roles",
            extra={
                "guild_id": self._guild_id,
                "members": len(member_roles),
                "roles": len(roles),
            },
        )
        if missing_role_ids:
            self._logger.debug(
                "Member roles missing role definitions",
                extra={"missing_role_ids": sorted(missing_role_ids)},
            )
        return member_roles

    def list_members(self) -> list[dict]:
        """Return member objects with user ID, username, and role IDs."""
        members, _ = self._list_members()
        return members

    def list_roles(self) -> list[dict]:
        """Return role objects with ID and name."""
        roles, _ = self._list_roles()
        return roles

    def add_role(self, discord_user_id: str, role_name: str) -> None:
        """Assign a role to a guild member. Calls Discord API unless request fails."""
        role_id = self._resolve_role_id(role_name)
        if role_id is None:
            roles, ok = self._list_roles()
            role_names = [r.get("name") for r in roles if r.get("name")] if ok else []
            self._logger.warning(
                "Discord role not found; cannot add (check name and hierarchy)",
                extra={
                    "user_id": discord_user_id,
                    "role_requested": role_name,
                    "guild_role_names": role_names[:30],
                },
            )
            return
        path = f"/guilds/{self._guild_id}/members/{discord_user_id}/roles/{role_id}"
        try:
            response = self._client.request("PUT", path)
        except httpx.HTTPError as exc:
            self._logger.warning(
                "Discord add role failed",
                extra={"user_id": discord_user_id, "role": role_name, "error": str(exc)},
            )
            return
        if response.status_code in (200, 204):
            self._logger.info(
                "Discord role added",
                extra={"user_id": discord_user_id, "role": role_name},
            )
        else:
            try:
                body = response.text[:500] if response.text else ""
            except Exception:
                body = ""
            self._logger.warning(
                "Discord add role failed (check bot role is above target role and has Manage Roles)",
                extra={
                    "user_id": discord_user_id,
                    "role": role_name,
                    "status_code": response.status_code,
                    "response": body,
                },
            )

    def remove_role(self, discord_user_id: str, role_name: str) -> None:
        """Remove a role from a guild member. Calls Discord API unless request fails."""
        role_id = self._resolve_role_id(role_name)
        if role_id is None:
            self._logger.warning(
                "Discord role not found; cannot remove",
                extra={"user_id": discord_user_id, "role": role_name},
            )
            return
        path = f"/guilds/{self._guild_id}/members/{discord_user_id}/roles/{role_id}"
        try:
            response = self._client.request("DELETE", path)
        except httpx.HTTPError as exc:
            self._logger.warning(
                "Discord remove role failed",
                extra={"user_id": discord_user_id, "role": role_name, "error": str(exc)},
            )
            return
        if response.status_code in (200, 204):
            self._logger.info(
                "Discord role removed",
                extra={"user_id": discord_user_id, "role": role_name},
            )
        else:
            self._logger.warning(
                "Discord remove role failed",
                extra={
                    "user_id": discord_user_id,
                    "role": role_name,
                    "status_code": response.status_code,
                },
            )

    def send_message(self, channel_id: str, content: str) -> bool:
        """Post a read-only message to a channel. Content truncated to 2000 chars. Returns True on success."""
        if not content:
            return True
        text = content[:2000]
        try:
            response = self._client.request(
                "POST",
                f"/channels/{channel_id}/messages",
                json={"content": text},
            )
        except httpx.HTTPError as exc:
            self._logger.warning(
                "Discord send_message failed",
                extra={"channel_id": channel_id, "error": str(exc)},
            )
            return False
        if response.status_code not in (200, 201):
            self._logger.warning(
                "Discord send_message failed",
                extra={"channel_id": channel_id, "status_code": response.status_code},
            )
            return False
        return True

    def send_dm(self, discord_user_id: str, content: str) -> bool:
        """Send a direct message to a user. Returns True on success, False on failure.
        
        Creates a DM channel if needed. Handles privacy settings gracefully.
        """
        if not content:
            return True
        text = content[:2000]
        try:
            # Create or get DM channel
            response = self._client.request(
                "POST",
                "/users/@me/channels",
                json={"recipient_id": discord_user_id},
            )
            if response.status_code not in (200, 201):
                self._logger.warning(
                    "Discord DM channel creation failed",
                    extra={"user_id": discord_user_id, "status_code": response.status_code},
                )
                return False
            channel_data = response.json()
            channel_id = channel_data.get("id")
            if not channel_id:
                self._logger.warning(
                    "Discord DM channel creation returned no channel ID",
                    extra={"user_id": discord_user_id},
                )
                return False
            
            # Send message to DM channel
            msg_response = self._client.request(
                "POST",
                f"/channels/{channel_id}/messages",
                json={"content": text},
            )
            if msg_response.status_code not in (200, 201):
                self._logger.warning(
                    "Discord DM send failed",
                    extra={"user_id": discord_user_id, "status_code": msg_response.status_code},
                )
                return False
            return True
        except httpx.HTTPError as exc:
            self._logger.warning(
                "Discord DM failed",
                extra={"user_id": discord_user_id, "error": str(exc)},
            )
            return False

    def _resolve_role_id(self, role_name: str) -> str | None:
        """Resolve role name to Discord role ID (case-insensitive). Returns None if not found."""
        roles, ok = self._list_roles()
        if not ok:
            return None
        role_name_lower = (role_name or "").strip().lower()
        for role in roles:
            rname = role.get("name") or ""
            if rname.strip().lower() == role_name_lower:
                return role["id"]
        return None

    def _list_roles(self) -> tuple[list[dict], bool]:
        response = self._request("GET", f"/guilds/{self._guild_id}/roles")
        if response is None or response.status_code != 200:
            self._logger.warning(
                "Unable to list roles",
                extra={"guild_id": self._guild_id},
            )
            return [], False
        roles = response.json()
        roles_sorted = sorted(roles, key=lambda role: (role.get("position", 0), role["id"]))
        self._logger.info(
            "Loaded Discord roles",
            extra={"guild_id": self._guild_id, "count": len(roles_sorted)},
        )
        return roles_sorted, True

    def _list_members(self) -> tuple[list[dict], bool]:
        members: list[dict] = []
        after: str | None = None
        while True:
            response = self._request(
                "GET",
                f"/guilds/{self._guild_id}/members",
                params={"limit": 1000, "after": after} if after else {"limit": 1000},
            )
            if response is None or response.status_code != 200:
                self._logger.warning(
                    "Unable to list members",
                    extra={"guild_id": self._guild_id, "after": after},
                )
                return [], False
            page = response.json()
            if not page:
                break
            members.extend(page)
            after = page[-1]["user"]["id"]
        self._logger.info(
            "Loaded Discord members",
            extra={"guild_id": self._guild_id, "count": len(members)},
        )
        return members, True

    def _request(self, method: str, path: str, params: dict | None = None) -> httpx.Response | None:
        try:
            response = self._client.request(method, path, params=params)
        except httpx.HTTPError as exc:
            self._logger.warning("Discord request failed", extra={"path": path, "error": str(exc)})
            return None

        if response.status_code == 429:
            try:
                body = response.json()
            except Exception:
                body = {}
            self._logger.warning(
                "Discord rate limit reached; stopping",
                extra={"path": path, "retry_after": body.get("retry_after")},
            )
            return None

        if response.status_code in {401, 403}:
            self._logger.warning(
                "Discord permission issue",
                extra={"path": path, "status_code": response.status_code},
            )
            return None

        rate_limit = _parse_rate_limit(response.headers)
        if rate_limit.remaining is not None and rate_limit.remaining <= 1:
            self._logger.warning(
                "Discord rate limit nearly exhausted",
                extra={
                    "path": path,
                    "remaining": rate_limit.remaining,
                    "reset_at": rate_limit.reset_at.isoformat()
                    if rate_limit.reset_at
                    else None,
                },
            )
        return response

    def _log_capabilities(self, roles_ok: bool, members_ok: bool) -> None:
        self._logger.info(
            "Discord permission capabilities",
            extra={
                "guild_id": self._guild_id,
                "read_roles": roles_ok,
                "read_members": members_ok,
            },
        )


def _parse_rate_limit(headers: dict) -> DiscordRateLimit:
    remaining = headers.get("X-RateLimit-Remaining")
    reset = headers.get("X-RateLimit-Reset")
    remaining_val = int(remaining) if remaining and remaining.isdigit() else None
    reset_at = (
        datetime.fromtimestamp(int(reset), tz=timezone.utc) if reset and reset.isdigit() else None
    )
    return DiscordRateLimit(remaining=remaining_val, reset_at=reset_at)
