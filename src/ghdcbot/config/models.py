from __future__ import annotations

from pydantic import BaseModel, Field, HttpUrl, field_validator

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

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        if value.upper() not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("Unsupported log level")
        return value.upper()


class GitHubConfig(BaseModel):
    org: str
    token: str
    api_base: HttpUrl = Field(default="https://api.github.com")
    permissions: PermissionConfig = Field(default_factory=PermissionConfig)
    repos: RepoFilterConfig | None = None
    user_fallback: bool = False


class DiscordConfig(BaseModel):
    guild_id: str
    token: str
    permissions: PermissionConfig = Field(default_factory=PermissionConfig)
    # Optional: channel ID for read-only activity feed (mentor visibility). If set, one summary message per run.
    activity_channel_id: str | None = None


class QualityAdjustmentsConfig(BaseModel):
    """Optional quality adjustments for contribution scoring."""
    penalties: dict[str, int] = Field(default_factory=dict)
    bonuses: dict[str, int] = Field(default_factory=dict)

    @field_validator("penalties", "bonuses")
    @classmethod
    def validate_adjustments(cls, value: dict[str, int]) -> dict[str, int]:
        for key, val in value.items():
            if not isinstance(val, int):
                raise ValueError(f"quality_adjustments.{key} must be an integer")
        return value


class ScoringConfig(BaseModel):
    period_days: int = 30
    weights: dict[str, int]
    difficulty_weights: dict[str, int] | None = None
    quality_adjustments: QualityAdjustmentsConfig | None = None

    @field_validator("period_days")
    @classmethod
    def validate_period_days(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("period_days must be positive")
        return value

    @field_validator("difficulty_weights")
    @classmethod
    def validate_difficulty_weights(cls, value: dict[str, int] | None) -> dict[str, int] | None:
        if value is not None:
            for label, weight in value.items():
                if weight < 0:
                    raise ValueError(f"difficulty_weights[{label}] must be non-negative")
        return value


class RoleMappingConfig(BaseModel):
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


class BotConfig(BaseModel):
    runtime: RuntimeConfig
    github: GitHubConfig
    discord: DiscordConfig
    scoring: ScoringConfig
    role_mappings: list[RoleMappingConfig]
    assignments: AssignmentConfig = Field(default_factory=AssignmentConfig)
    identity_mappings: list[IdentityMapping] = Field(default_factory=list)
    identity: IdentityConfig | None = None
    merge_role_rules: MergeRoleRulesConfig | None = None

    @field_validator("role_mappings")
    @classmethod
    def validate_role_mappings(cls, value: list[RoleMappingConfig]) -> list[RoleMappingConfig]:
        if not value:
            raise ValueError("role_mappings must not be empty")
        return value
