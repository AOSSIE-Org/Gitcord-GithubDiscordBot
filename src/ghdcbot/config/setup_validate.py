"""Preflight validation for Gitcord configuration (read-only, no bot startup).

Backs ``ghdcbot --config config.yaml validate``: loads the config, checks the
GitHub and Discord credentials against their APIs, confirms the guild is
reachable and, when role updates are enabled, that every configured Discord
role exists. Nothing is written and the bot does not start.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum

import httpx

from ghdcbot.adapters.github.app_auth import build_github_httpx_client, resolve_github_token
from ghdcbot.config.loader import load_config
from ghdcbot.config.models import BotConfig
from ghdcbot.core.errors import ConfigError
from ghdcbot.plugins.registry import build_adapter

_PLACEHOLDER_GUILD_IDS = frozenset({"", "0", "000000000000000000"})
_DISCORD_API_BASE = "https://discord.com/api/v10"


class CheckStatus(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


@dataclass(frozen=True)
class CheckResult:
    status: CheckStatus
    label: str
    detail: str = ""


@dataclass
class ValidationReport:
    results: list[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not any(r.status == CheckStatus.FAIL for r in self.results)

    def add(self, status: CheckStatus, label: str, detail: str = "") -> None:
        self.results.append(CheckResult(status=status, label=label, detail=detail))

    def failed_since(self, index: int) -> bool:
        return any(r.status == CheckStatus.FAIL for r in self.results[index:])

    def render(self) -> str:
        lines: list[str] = []
        for result in self.results:
            prefix = {
                CheckStatus.PASS: "✓",
                CheckStatus.WARN: "⚠",
                CheckStatus.FAIL: "✗",
            }[result.status]
            line = f"{prefix} {result.label}"
            if result.detail:
                line = f"{line}\n  {result.detail}"
            lines.append(line)
        lines.append("")
        if self.passed:
            lines.append("Validation passed.")
        else:
            lines.append("Validation failed. Fix the issues above before running the bot.")
        return "\n".join(lines)


def run_validate(config_path: str) -> int:
    """Validate configuration and external connectivity. Returns exit code 0 or 1."""
    report = ValidationReport()

    try:
        config = load_config(config_path)
    except ConfigError as exc:
        report.add(CheckStatus.FAIL, "Config file could not be loaded", str(exc))
        print(report.render())
        return 1

    report.add(CheckStatus.PASS, "Config file loaded", config_path)
    report.add(CheckStatus.PASS, "YAML valid and schema OK")

    github_client: httpx.Client | None = None
    discord_client: httpx.Client | None = None
    discord_adapter = None
    try:
        try:
            github_token = _resolve_github_credentials(config, report)
            if github_token is not None:
                github_client = build_github_httpx_client(
                    github_token,
                    api_base=str(config.github.api_base),
                    timeout=30.0,
                )
                _validate_github(config, report, github_client, app_auth=callable(github_token))

            discord_token = (config.discord.token or "").strip()
            if not discord_token:
                report.add(
                    CheckStatus.FAIL,
                    "DISCORD_TOKEN not configured",
                    "Create a Discord bot in the Developer Portal and set DISCORD_TOKEN "
                    "in your .env file.",
                )
                bot_ok = False
            else:
                report.add(CheckStatus.PASS, "DISCORD_TOKEN configured")
                discord_client = httpx.Client(
                    base_url=_DISCORD_API_BASE,
                    headers={"Authorization": f"Bot {discord_token}"},
                    timeout=30.0,
                )
                bot_ok = _validate_discord_bot(report, discord_client)

            guild_validated = False
            if bot_ok and discord_client is not None:
                guild_id = (config.discord.guild_id or "").strip()
                if guild_id in _PLACEHOLDER_GUILD_IDS:
                    report.add(
                        CheckStatus.FAIL,
                        "Guild ID not configured",
                        "Set discord.guild_id to your Discord server ID "
                        "(Developer Mode → Copy Server ID).",
                    )
                else:
                    results_before = len(report.results)
                    _validate_guild(guild_id, report, discord_client)
                    guild_validated = not report.failed_since(results_before)

            if not config.runtime.enable_discord_role_updates:
                report.add(
                    CheckStatus.WARN,
                    "Role mapping check skipped",
                    "runtime.enable_discord_role_updates is false.",
                )
            elif not guild_validated:
                report.add(
                    CheckStatus.WARN,
                    "Role mapping check skipped",
                    "Guild validation failed.",
                )
            else:
                discord_adapter = build_adapter(
                    config.runtime.discord_adapter,
                    token=discord_token,
                    guild_id=guild_id,
                )
                _validate_role_mappings(config, report, discord_adapter)
        except Exception as exc:  # noqa: BLE001 - report instead of crashing the preflight
            report.add(
                CheckStatus.FAIL,
                "Validation error",
                f"{type(exc).__name__}: {exc}",
            )
    finally:
        if github_client is not None:
            github_client.close()
        if discord_client is not None:
            discord_client.close()
        if discord_adapter is not None:
            close = getattr(discord_adapter, "close", None)
            if callable(close):
                close()

    print(report.render())
    return 0 if report.passed else 1


def _resolve_github_credentials(
    config: BotConfig, report: ValidationReport
) -> str | Callable[[], str] | None:
    """Resolve PAT or GitHub App credentials the same way the bot does."""
    try:
        token = resolve_github_token(
            pat=config.github.token,
            api_base=str(config.github.api_base),
        )
    except RuntimeError as exc:
        report.add(CheckStatus.FAIL, "GitHub credentials not configured", str(exc))
        return None
    if callable(token):
        report.add(
            CheckStatus.PASS,
            "GitHub App credentials configured",
            "GITHUB_APP_ID, GITHUB_APP_INSTALLATION_ID and private key are set.",
        )
    else:
        report.add(CheckStatus.PASS, "GITHUB_TOKEN configured")
    return token


def _validate_github(
    config: BotConfig,
    report: ValidationReport,
    client: httpx.Client,
    *,
    app_auth: bool,
) -> None:
    if app_auth:
        # Installation tokens cannot call /user; the installation endpoint proves the
        # App ID, installation ID and private key work together.
        path = "/installation/repositories"
        params: dict[str, int] | None = {"per_page": 1}
    else:
        path = "/user"
        params = None

    try:
        response = client.get(path, params=params)
    except httpx.HTTPError as exc:
        report.add(CheckStatus.FAIL, "GitHub authentication failed", f"Network error: {exc}")
        return

    if response.status_code == 401:
        if app_auth:
            report.add(
                CheckStatus.FAIL,
                "Invalid GitHub App credentials",
                "Check GITHUB_APP_ID, GITHUB_APP_INSTALLATION_ID and the private key.",
            )
        else:
            report.add(
                CheckStatus.FAIL,
                "Invalid GITHUB_TOKEN",
                "Check GITHUB_TOKEN in your .env file.\n"
                "Ensure the token is not expired and has access to your organization.",
            )
        return
    if response.status_code != 200:
        report.add(
            CheckStatus.FAIL,
            "GitHub authentication failed",
            f"GET {path} returned HTTP {response.status_code}.\n"
            "Verify your GitHub credentials in the .env file.",
        )
        return

    payload = response.json()
    if app_auth:
        total = payload.get("total_count") if isinstance(payload, dict) else None
        detail = f"Installation can see {total} repo(s)." if total is not None else ""
        report.add(CheckStatus.PASS, "GitHub App authentication successful", detail)
    else:
        login = payload.get("login") if isinstance(payload, dict) else None
        report.add(
            CheckStatus.PASS,
            "GitHub authentication successful",
            f"Authenticated as {login or 'unknown'}.",
        )

    org = config.github.org.strip()
    try:
        org_response = client.get(f"/orgs/{org}")
    except httpx.HTTPError as exc:
        report.add(CheckStatus.FAIL, "Organization not reachable", f"Network error: {exc}")
        return

    if org_response.status_code == 404:
        report.add(
            CheckStatus.FAIL,
            "Organization not accessible",
            f'GitHub org "{org}" was not found or the credentials cannot access it.',
        )
        return
    if org_response.status_code in {401, 403}:
        report.add(
            CheckStatus.FAIL,
            "Organization not accessible",
            f'Credentials cannot access org "{org}". '
            "Check the token's organization access or the App installation.",
        )
        return
    if org_response.status_code != 200:
        report.add(
            CheckStatus.FAIL,
            "Organization not accessible",
            f"GET /orgs/{org} returned HTTP {org_response.status_code}.",
        )
        return

    report.add(CheckStatus.PASS, "Organization accessible", org)

    try:
        repos_response = client.get(f"/orgs/{org}/repos", params={"per_page": 5, "page": 1})
    except httpx.HTTPError as exc:
        report.add(CheckStatus.WARN, "Repositories not verified", f"Network error: {exc}")
        return

    if repos_response.status_code != 200:
        report.add(
            CheckStatus.WARN,
            "Repositories not verified",
            f"GET /orgs/{org}/repos returned HTTP {repos_response.status_code}.",
        )
        return

    repos = repos_response.json()
    count = len(repos) if isinstance(repos, list) else 0
    if count == 0:
        report.add(
            CheckStatus.WARN,
            "Repositories visible",
            f'Org "{org}" has no repositories (or none visible to these credentials).',
        )
    else:
        names = ", ".join(r.get("name", "?") for r in repos[:3] if isinstance(r, dict))
        suffix = "..." if count >= 3 else ""
        report.add(
            CheckStatus.PASS,
            "Repositories visible",
            f"{count}+ repo(s) visible (e.g. {names}{suffix})",
        )


def _validate_discord_bot(report: ValidationReport, client: httpx.Client) -> bool:
    try:
        response = client.get("/users/@me")
    except httpx.HTTPError as exc:
        report.add(CheckStatus.FAIL, "Discord authentication failed", f"Network error: {exc}")
        return False

    if response.status_code == 401:
        report.add(
            CheckStatus.FAIL,
            "Invalid DISCORD_TOKEN",
            "Please update DISCORD_TOKEN in your .env file\nand restart Gitcord.",
        )
        return False
    if response.status_code != 200:
        report.add(
            CheckStatus.FAIL,
            "Discord authentication failed",
            f"GET /users/@me returned HTTP {response.status_code}.\n"
            "Verify DISCORD_TOKEN in your .env file.",
        )
        return False

    payload = response.json()
    username = payload.get("username") if isinstance(payload, dict) else None
    report.add(
        CheckStatus.PASS,
        "Discord authentication successful",
        f"Bot user: {username or 'bot'}",
    )
    return True


def _validate_guild(guild_id: str, report: ValidationReport, client: httpx.Client) -> None:
    try:
        response = client.get(f"/guilds/{guild_id}")
    except httpx.HTTPError as exc:
        report.add(CheckStatus.FAIL, "Guild not reachable", f"Network error: {exc}")
        return

    if response.status_code == 404:
        report.add(
            CheckStatus.FAIL,
            "Guild not found",
            "The bot is not in this server or discord.guild_id is wrong.\n"
            "Re-invite the bot and copy the correct Server ID "
            "(Developer Mode → Copy Server ID).",
        )
        return
    if response.status_code in {401, 403}:
        report.add(
            CheckStatus.FAIL,
            "Guild not accessible",
            "The bot cannot access this server.\n"
            "Re-invite the bot with the bot and applications.commands scopes.",
        )
        return
    if response.status_code != 200:
        report.add(
            CheckStatus.FAIL,
            "Guild not accessible",
            f"GET /guilds/{guild_id} returned HTTP {response.status_code}.",
        )
        return

    payload = response.json()
    guild_name = payload.get("name") if isinstance(payload, dict) else None
    report.add(CheckStatus.PASS, "Guild found", f"Guild: {guild_name or guild_id}")


def _validate_role_mappings(
    config: BotConfig, report: ValidationReport, discord_adapter: object
) -> None:
    expected = _collect_configured_role_names(config)
    if not expected:
        report.add(CheckStatus.WARN, "No Discord roles configured in YAML")
        return

    list_roles = getattr(discord_adapter, "list_roles", None)
    if not callable(list_roles):
        report.add(CheckStatus.WARN, "Role validation skipped", "Discord adapter has no list_roles().")
        return

    guild_roles = list_roles() or []
    guild_names = {
        str(r.get("name") or "").strip().lower()
        for r in guild_roles
        if isinstance(r, dict) and r.get("name")
    }
    if not guild_names:
        report.add(
            CheckStatus.WARN,
            "Discord roles not listed",
            "Bot may lack View Server or Manage Roles permission.",
        )
        return

    for name in sorted(expected):
        if name.lower() in guild_names:
            report.add(CheckStatus.PASS, f'Role "{name}" found')
        else:
            report.add(
                CheckStatus.FAIL,
                f'Role "{name}" not found',
                f'Create the role "{name}" in Discord Server Settings → Roles,\n'
                "or update the role name in config (case-insensitive match).",
            )


def _collect_configured_role_names(config: BotConfig) -> set[str]:
    """Every Discord role name the config expects to exist in the guild."""
    names: set[str] = set()

    def _add(value: object) -> None:
        text = str(value or "").strip()
        if text:
            names.add(text)

    for mapping in config.role_mappings:
        _add(mapping.discord_role)
    for role in config.assignments.review_roles:
        _add(role)
    for role in config.assignments.issue_assignees:
        _add(role)
    for role in config.assignments.issue_request_eligible_roles:
        _add(role)
    for rule in (config.discord.command_permissions or {}).values():
        for role_name in rule.role_names:
            _add(role_name)
    for role in (config.repo_contributor_roles or {}).values():
        _add(role)
    merge_rules = config.merge_role_rules
    if merge_rules is not None and merge_rules.enabled:
        for rule in merge_rules.rules:
            _add(rule.discord_role)
    return names
