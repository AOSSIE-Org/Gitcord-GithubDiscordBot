from __future__ import annotations

import logging
from typing import Iterable

import httpx

from ghdcbot.core.models import DiscordRolePlan
from ghdcbot.core.modes import MutationPolicy, RunMode


class DiscordPlanWriter:
    """Thin executor for DiscordRolePlan objects.

    This adapter intentionally contains no business logic. It only executes
    precomputed plans when MutationPolicy allows it.
    """

    def __init__(self, token: str, guild_id: str) -> None:
        self._logger = logging.getLogger(self.__class__.__name__)
        self._guild_id = guild_id
        self._client = httpx.Client(
            base_url="https://discord.com/api/v10",
            headers={"Authorization": f"Bot {token}"},
            timeout=30.0,
        )

    def apply_plans(self, plans: Iterable[DiscordRolePlan], policy: MutationPolicy) -> None:
        for plan in plans:
            skip_reason = _skip_reason(policy, policy.allow_discord_mutations)
            if skip_reason:
                self._log_plan(plan, result=skip_reason)
                continue
            self._apply_plan(plan)

    def _apply_plan(self, plan: DiscordRolePlan) -> None:
        role_id = self._resolve_role_id(plan.role)
        if role_id is None:
            self._log_plan(plan, result="failed (role not found)")
            return

        path = f"/guilds/{self._guild_id}/members/{plan.discord_user_id}/roles/{role_id}"
        method = "PUT" if plan.action == "add" else "DELETE"
        try:
            response = self._client.request(method, path)
        except httpx.HTTPError as exc:
            self._log_plan(plan, result="failed (network error)", error=str(exc))
            return

        if response.status_code in {401, 403, 404}:
            self._log_plan(plan, result="failed (permission denied)", status=response.status_code)
            return
        if response.status_code >= 300:
            self._log_plan(plan, result="failed (http error)", status=response.status_code)
            return

        self._log_plan(plan, result="applied")

    def _resolve_role_id(self, role_name: str) -> str | None:
        """Resolve a role name to a role ID. Returns None if not found."""
        try:
            response = self._client.request(
                "GET", f"/guilds/{self._guild_id}/roles"
            )
        except httpx.HTTPError as exc:
            self._logger.warning(
                "Role lookup failed",
                extra={"guild_id": self._guild_id, "error": str(exc)},
            )
            return None
        if response.status_code != 200:
            self._logger.warning(
                "Role lookup failed",
                extra={"guild_id": self._guild_id, "status": response.status_code},
            )
            return None

        roles = response.json()
        for role in roles:
            if role.get("name") == role_name:
                return role.get("id")
        return None

    def _log_plan(
        self,
        plan: DiscordRolePlan,
        result: str,
        status: int | None = None,
        error: str | None = None,
    ) -> None:
        self._logger.info(
            "Discord role plan execution",
            extra={
                "discord_user_id": plan.discord_user_id,
                "role": plan.role,
                "action": plan.action,
                "reason": plan.reason,
                "result": result,
                "status": status,
                "error": error,
            },
        )


def _skip_reason(policy: MutationPolicy, allow_mutations: bool) -> str | None:
    if policy.mode == RunMode.DRY_RUN:
        return "skipped (dry-run)"
    if policy.mode == RunMode.OBSERVER:
        return "skipped (observer mode)"
    if not allow_mutations:
        return "skipped (write disabled)"
    return None
