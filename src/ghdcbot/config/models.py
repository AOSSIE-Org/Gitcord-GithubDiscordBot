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


class ScoringConfig(BaseModel):
    period_days: int = 30
    weights: dict[str, int]

    @field_validator("period_days")
    @classmethod
    def validate_period_days(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("period_days must be positive")
        return value


class RoleMappingConfig(BaseModel):
    discord_role: str
    min_score: int = 0


class AssignmentConfig(BaseModel):
    review_roles: list[str] = Field(default_factory=list)
    issue_assignees: list[str] = Field(default_factory=list)


class IdentityMapping(BaseModel):
    github_user: str
    discord_user_id: str


class BotConfig(BaseModel):
    runtime: RuntimeConfig
    github: GitHubConfig
    discord: DiscordConfig
    scoring: ScoringConfig
    role_mappings: list[RoleMappingConfig] = Field(default_factory=list)
    assignments: AssignmentConfig = Field(default_factory=AssignmentConfig)
    identity_mappings: list[IdentityMapping] = Field(default_factory=list)

    @field_validator("role_mappings")
    @classmethod
    def validate_role_mappings(cls, value: list[RoleMappingConfig]) -> list[RoleMappingConfig]:
        if not value:
            raise ValueError("role_mappings must not be empty")
        return value
