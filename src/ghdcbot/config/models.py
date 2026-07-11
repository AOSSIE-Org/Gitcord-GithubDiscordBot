from __future__ import annotations

from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator

from ghdcbot.core.modes import RunMode


class PermissionConfig(BaseModel):
    read: bool = True
    write: bool = False


class RepoFilterConfig(BaseModel):
    mode: str
    names: list[str]

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, value: str) -> str:
        if value not in {"allow", "deny"}:
            raise ValueError("repos.mode must be either 'allow' or 'deny'")
        return value

    @field_validator("names")
    @classmethod
    def validate_names(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("repos.names must be a non-empty list")
        return value


class RuntimeConfig(BaseModel):
    mode: RunMode = RunMode.DRY_RUN
    log_level: str = "INFO"
    data_dir: str
    github_adapter: str
    discord_adapter: str
    storage_adapter: str
    # Activity window for ingestion reports, snapshots, and merge-based role rules.
    activity_period_days: int = 30
    # When false, skip applying Discord role add/remove (notifications unaffected).
    enable_discord_role_updates: bool = True

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        if value.upper() not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("Unsupported log level")
        return value.upper()

    @field_validator("activity_period_days")
    @classmethod
    def validate_activity_period_days(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("activity_period_days must be positive")
        return value


class GitHubConfig(BaseModel):
    org: str
    token: str
    api_base: HttpUrl = Field(default="https://api.github.com")
    permissions: PermissionConfig = Field(default_factory=PermissionConfig)
    repos: RepoFilterConfig | None = None
    user_fallback: bool = False


class SlashCommandPermissionRule(BaseModel):
    """Who may run a restricted slash command (e.g. assign-issue, issue-requests, sync).

    If a command is omitted from ``discord.command_permissions``, the bot falls back to
    ``assignments.issue_assignees`` (role name match), for backward compatibility.
    """

    role_ids: list[str] = Field(default_factory=list)
    role_names: list[str] = Field(default_factory=list)
    allow_discord_administrators: bool = False


class NotificationConfig(BaseModel):
    """Configuration for verified-only GitHub → Discord notifications."""
    enabled: bool = True
    issue_assignment: bool = True
    pr_review_requested: bool = True
    pr_review_result: bool = True  # APPROVED / CHANGES_REQUESTED
    pr_review_comment: bool = True  # COMMENT reviews on a PR
    pr_merged: bool = True
    pr_closed: bool = True  # PR closed without merge
    issue_reopened: bool = True  # Issue reopened (notifies assignee)
    pr_reopened: bool = True  # PR reopened (notifies author)
    pr_opened: bool = False  # PR opened → repo-mapped Discord channel (see discord.pr_open_channels)
    coderabbit_reminders: bool = False  # Remind PR authors about old CodeRabbit review comments
    coderabbit_reminder_after_hours: int = 48  # Only remind if comment is at least this old
    coderabbit_bot_logins: list[str] | None = None  # Bot logins to treat as CodeRabbit; default ["coderabbitai", "coderabbitai[bot]"]
    # Default to DM; set channel_id to post to a channel instead
    channel_id: str | None = None  # If None, sends DM; if set, posts to channel

    @field_validator("coderabbit_reminder_after_hours")
    @classmethod
    def validate_coderabbit_reminder_hours(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("coderabbit_reminder_after_hours must be positive")
        return value


class DiscordConfig(BaseModel):
    guild_id: str
    token: str
    permissions: PermissionConfig = Field(default_factory=PermissionConfig)
    # Optional: channel ID for read-only activity feed (mentor visibility). If set, one summary message per run.
    activity_channel_id: str | None = None
    # Optional: channel names where PR URLs trigger passive preview (requires message content intent)
    pr_preview_channels: list[str] = Field(default_factory=list)
    # Repo short name → Discord channel/thread ID for pr_opened notifications (verified authors only).
    pr_open_channels: dict[str, str] = Field(default_factory=dict)
    # Optional: verified-only GitHub → Discord notifications
    notifications: NotificationConfig | None = None
    # Optional: per-command slash permission (keys: assign-issue, issue-requests, sync). See SlashCommandPermissionRule.
    command_permissions: dict[str, SlashCommandPermissionRule] | None = None
    # TESTING ONLY: if true, any guild member may run assign-issue / issue-requests / sync. Turn off for production.
    unrestricted_slash_commands: bool = False


class RoleMappingConfig(BaseModel):
    """Deprecated: score-based role thresholds. Ignored; use merge_role_rules or repo_contributor_roles."""

    discord_role: str
    min_score: int = 0


class MergeRoleRuleConfig(BaseModel):
    """Single rule for merge-based role assignment."""
    discord_role: str
    min_merged_prs: int

    @field_validator("min_merged_prs")
    @classmethod
    def validate_min_merged_prs(cls, value: int) -> int:
        if value < 0:
            raise ValueError("min_merged_prs must be non-negative")
        return value


class MergeRoleRulesConfig(BaseModel):
    """Optional merge-based role assignment rules."""
    enabled: bool = False
    rules: list[MergeRoleRuleConfig] = Field(default_factory=list)

    @field_validator("rules")
    @classmethod
    def validate_rules(cls, value: list[MergeRoleRuleConfig]) -> list[MergeRoleRuleConfig]:
        if value:
            # Ensure rules are sorted by threshold (ascending) for deterministic processing
            return sorted(value, key=lambda r: r.min_merged_prs)
        return value


class AssignmentConfig(BaseModel):
    review_roles: list[str] = Field(default_factory=list)
    issue_assignees: list[str] = Field(default_factory=list)
    # Roles that make a contributor eligible for issue assignment (for /request-issue review). Empty = any verified user.
    issue_request_eligible_roles: list[str] = Field(default_factory=list)


class IdentityMapping(BaseModel):
    github_user: str
    discord_user_id: str


class IdentityConfig(BaseModel):
    """Optional identity settings. Backward compatible: missing section uses defaults."""
    unlink_cooldown_hours: int = 24
    verified_max_age_days: int | None = None

    @field_validator("unlink_cooldown_hours")
    @classmethod
    def validate_cooldown(cls, value: int) -> int:
        if value < 0:
            raise ValueError("unlink_cooldown_hours must be non-negative")
        return value

    @field_validator("verified_max_age_days")
    @classmethod
    def validate_max_age(cls, value: int | None) -> int | None:
        if value is not None and value <= 0:
            raise ValueError("verified_max_age_days must be positive if set")
        return value


class SnapshotConfig(BaseModel):
    """Configuration for GitHub-backed JSON snapshots."""
    enabled: bool = False
    repo_path: str = ""  # Format: "owner/repo" (e.g., "org/gitcord-data")
    # Optional: branch to write to (default: main/master)
    branch: str | None = None


class BotConfig(BaseModel):
    runtime: RuntimeConfig
    github: GitHubConfig
    discord: DiscordConfig
    role_mappings: list[RoleMappingConfig] = Field(default_factory=list)
    assignments: AssignmentConfig = Field(default_factory=AssignmentConfig)
    identity_mappings: list[IdentityMapping] = Field(default_factory=list)
    identity: IdentityConfig | None = None
    merge_role_rules: MergeRoleRulesConfig | None = None
    # Optional: GitHub snapshot storage
    snapshots: SnapshotConfig | None = None
    # Optional: repo name -> Discord role for "Contributor-X" (PR merged in repo X grants role)
    repo_contributor_roles: dict[str, str] | None = None

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_scoring(cls, data: object) -> object:
        """Accept legacy scoring blocks; map period_days to runtime.activity_period_days."""
        if not isinstance(data, dict):
            return data
        migrated = dict(data)
        scoring = migrated.pop("scoring", None)
        runtime = migrated.get("runtime")
        if isinstance(scoring, dict) and isinstance(runtime, dict):
            runtime_copy = dict(runtime)
            if "activity_period_days" not in runtime:
                period_days = scoring.get("period_days")
                if period_days is not None:
                    runtime_copy["activity_period_days"] = period_days
            runtime_copy.pop("enable_scoring", None)
            migrated["runtime"] = runtime_copy
        elif isinstance(runtime, dict):
            runtime_copy = dict(runtime)
            runtime_copy.pop("enable_scoring", None)
            migrated["runtime"] = runtime_copy
        migrated.setdefault("role_mappings", [])
        return migrated

    @field_validator("repo_contributor_roles")
    @classmethod
    def validate_repo_contributor_roles(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        if value is not None:
            for repo, role in value.items():
                if not (repo and repo.strip()):
                    raise ValueError("repo_contributor_roles: repo names must be non-empty")
                if not (role and str(role).strip()):
                    raise ValueError("repo_contributor_roles: discord_role must be non-empty")
        return value
