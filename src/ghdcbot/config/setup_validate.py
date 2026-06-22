"""Preflight validation for Gitcord configuration (read-only, no bot startup)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import httpx

from ghdcbot.config.loader import load_config
from ghdcbot.config.models import BotConfig
from ghdcbot.config.validation import _PLACEHOLDER_GUILD_IDS
from ghdcbot.core.errors import ConfigError
from ghdcbot.core.modes import RunMode
from ghdcbot.plugins.registry import build_adapter


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
    report.add(CheckStatus.PASS, "GITHUB_TOKEN configured")
    report.add(CheckStatus.PASS, "DISCORD_TOKEN configured")

    github_client: httpx.Client | None = None
    discord_client: httpx.Client | None = None
    discord_adapter = None
    guild_validated = False
    try:
        try:
            github_client = httpx.Client(
                base_url=str(config.github.api_base),
                headers={
                    "Authorization": f"Bearer {config.github.token}",
                    "Accept": "application/vnd.github+json",
                },
                timeout=30.0,
            )
            _validate_github(config, report, github_client)
            discord_client = httpx.Client(
                base_url="https://discord.com/api/v10",
                headers={"Authorization": f"Bot {config.discord.token}"},
                timeout=30.0,
            )
            _validate_discord_bot(report, discord_client)

            guild_id = (config.discord.guild_id or "").strip()
            if guild_id in _PLACEHOLDER_GUILD_IDS:
                report.add(
                    CheckStatus.FAIL,
                    "Guild ID not configured",
                    "Set discord.guild_id to your Discord server ID (Developer Mode → Copy Server ID).",
                )
            else:
                discord_adapter = build_adapter(
                    config.runtime.discord_adapter,
                    token=config.discord.token,
                    guild_id=config.discord.guild_id,
                )
                guild_results_before = len(report.results)
                _validate_guild(config, report, discord_client, discord_adapter)
                guild_validated = not any(
                    result.status == CheckStatus.FAIL
                    for result in report.results[guild_results_before:]
                )

            if config.runtime.enable_discord_role_updates:
                if guild_validated and discord_adapter is not None:
                    _validate_role_mappings(config, report, discord_adapter)
                elif discord_adapter is not None:
                    report.add(
                        CheckStatus.WARN,
                        "Role mapping check skipped",
                        "Guild validation failed.",
                    )
            else:
                report.add(
                    CheckStatus.WARN,
                    "Role mapping check skipped",
                    "runtime.enable_discord_role_updates is false.",
                )
        except Exception as exc:
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


def _validate_github(config: BotConfig, report: ValidationReport, client: httpx.Client) -> None:
    try:
        response = client.get("/user")
    except httpx.HTTPError as exc:
        report.add(CheckStatus.FAIL, "GitHub authentication failed", f"Network error: {exc}")
        return

    if response.status_code == 401:
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
            f"GET /user returned HTTP {response.status_code}.\n"
            "Verify GITHUB_TOKEN in your .env file.",
        )
        return

    login = response.json().get("login") or "unknown"
    report.add(CheckStatus.PASS, "GitHub authentication successful", f"Authenticated as {login}.")

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
            f'GitHub org "{org}" was not found or the token cannot access it.',
        )
        return
    if org_response.status_code in {401, 403}:
        report.add(
            CheckStatus.FAIL,
            "Organization not accessible",
            f'Token cannot access org "{org}". Check PAT organization access.',
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
        repos_response = client.get(
            f"/orgs/{org}/repos",
            params={"per_page": 5, "page": 1},
        )
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
            f'Org "{org}" has no repositories (or none visible to this token).',
        )
    else:
        names = ", ".join(r.get("name", "?") for r in repos[:3] if isinstance(r, dict))
        suffix = "..." if count >= 3 else ""
        report.add(CheckStatus.PASS, "Repositories visible", f"{count}+ repo(s) visible (e.g. {names}{suffix})")


def _validate_discord_bot(report: ValidationReport, client: httpx.Client) -> None:
    try:
        response = client.get("/users/@me")
    except httpx.HTTPError as exc:
        report.add(CheckStatus.FAIL, "Discord authentication failed", f"Network error: {exc}")
        return

    if response.status_code == 401:
        report.add(
            CheckStatus.FAIL,
            "Invalid DISCORD_TOKEN",
            "Please update DISCORD_TOKEN in your .env file\n"
            "and restart Gitcord.",
        )
        return
    if response.status_code != 200:
        report.add(
            CheckStatus.FAIL,
            "Discord authentication failed",
            f"GET /users/@me returned HTTP {response.status_code}.\n"
            "Verify DISCORD_TOKEN in your .env file.",
        )
        return

    username = response.json().get("username") or "bot"
    report.add(CheckStatus.PASS, "Discord authentication successful", f"Bot user: {username}")


def _validate_guild(
    config: BotConfig,
    report: ValidationReport,
    client: httpx.Client,
    discord_adapter: object,
) -> None:
    guild_id = config.discord.guild_id
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
            "Re-invite the bot and copy the correct Server ID (Developer Mode → Copy Server ID).",
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

    guild_name = response.json().get("name") or guild_id
    report.add(CheckStatus.PASS, "Guild found", f"Guild: {guild_name}")

    if config.runtime.mode == RunMode.ACTIVE:
        list_roles = getattr(discord_adapter, "list_roles", None)
        if callable(list_roles):
            roles = list_roles()
            if not roles:
                report.add(
                    CheckStatus.WARN,
                    "Discord roles not listed",
                    "Bot may lack View Server or Manage Roles permission.",
                )


def _validate_role_mappings(config: BotConfig, report: ValidationReport, discord_adapter: object) -> None:
    expected = _collect_configured_role_names(config)
    if not expected:
        report.add(CheckStatus.WARN, "No Discord roles configured in YAML")
        return

    list_roles = getattr(discord_adapter, "list_roles", None)
    if not callable(list_roles):
        report.add(CheckStatus.WARN, "Role validation skipped", "Discord adapter has no list_roles().")
        return

    guild_roles = list_roles()
    guild_names = {(r.get("name") or "").strip().lower() for r in guild_roles if r.get("name")}

    for name in sorted(expected):
        if name.strip().lower() in guild_names:
            report.add(CheckStatus.PASS, f'Role "{name}" found')
        else:
            report.add(
                CheckStatus.FAIL,
                f'Role "{name}" not found',
                f'Create the role "{name}" in Discord Server Settings → Roles,\n'
                "or update the role name in config (case-insensitive match).",
            )


def _collect_configured_role_names(config: BotConfig) -> set[str]:
    names: set[str] = set()
    for mapping in config.role_mappings:
        if mapping.discord_role.strip():
            names.add(mapping.discord_role.strip())
    for role in config.assignments.review_roles:
        if role.strip():
            names.add(role.strip())
    for role in config.assignments.issue_assignees:
        if role.strip():
            names.add(role.strip())
    for role in config.assignments.issue_request_eligible_roles:
        if role.strip():
            names.add(role.strip())
    perms = getattr(config.discord, "command_permissions", None)
    if perms:
        for rule in perms.values():
            for role_name in rule.role_names:
                if role_name.strip():
                    names.add(role_name.strip())
    repo_roles = getattr(config, "repo_contributor_roles", None) or {}
    for role in repo_roles.values():
        if str(role).strip():
            names.add(str(role).strip())
    merge_rules = getattr(config, "merge_role_rules", None)
    if merge_rules and merge_rules.enabled:
        for rule in merge_rules.rules:
            if rule.discord_role.strip():
                names.add(rule.discord_role.strip())
    return names
