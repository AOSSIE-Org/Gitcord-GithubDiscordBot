"""
Social Profile Models and Validation

Pydantic models for storing and validating social profiles (X, LinkedIn, etc.)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlparse

from pydantic import BaseModel, field_validator


class SocialProfileConfig(BaseModel):
    """Configuration for social profile features"""
    enabled: bool = True
    allow_x: bool = True
    allow_linkedin: bool = True
    

@dataclass(frozen=True)
class SocialProfile:
    """In-memory representation of a social profile"""
    discord_user_id: str
    platform: str  # 'x', 'linkedin', 'bluesky', etc.
    profile_handle: str  # Normalized handle/URL
    display_value: str  # User-friendly display value
    verified: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def to_dict(self) -> dict:
        return {
            "discord_user_id": self.discord_user_id,
            "platform": self.platform,
            "profile_handle": self.profile_handle,
            "display_value": self.display_value,
            "verified": self.verified,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class SocialProfileInput(BaseModel):
    """User input for adding/updating a social profile"""
    platform: str
    value: str
    
    @field_validator("platform")
    @classmethod
    def validate_platform(cls, value: str) -> str:
        from ghdcbot.core.social_validators import SOCIAL_VALIDATORS

        normalized = value.lower()
        supported = tuple(SOCIAL_VALIDATORS.keys())
        if normalized not in SOCIAL_VALIDATORS:
            raise ValueError(f"Unknown platform: {value}. Supported: {', '.join(supported)}")
        if normalized == "twitter":
            return "x"
        return normalized
    
    @field_validator("value")
    @classmethod
    def validate_value(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("Profile value cannot be empty")
        return value.strip()


class XProfile(BaseModel):
    """Normalized X/Twitter profile"""
    username: str
    url: str | None = None
    
    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        if not value or len(value) < 1 or len(value) > 15:
            raise ValueError("X username must be 1-15 characters")
        # Allow only alphanumeric and underscore
        if not all(c.isalnum() or c == "_" for c in value):
            raise ValueError("X username can only contain letters, numbers, and underscores")
        return value
    
    def display_url(self) -> str:
        return f"https://x.com/{self.username}"


class LinkedInProfile(BaseModel):
    """Normalized LinkedIn profile"""
    profile_url: str  # Full normalized URL
    profile_id: str   # Extracted profile ID
    
    @field_validator("profile_url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme != "https":
            raise ValueError("LinkedIn URL must start with https://")

        host = (parsed.hostname or "").lower()
        if host not in {"linkedin.com", "www.linkedin.com"}:
            raise ValueError("Invalid LinkedIn profile URL")

        path_parts = [part for part in parsed.path.split("/") if part]
        if path_parts and path_parts[0] in {"company", "companies"}:
            raise ValueError("Company pages are not supported, only personal profiles")
        if len(path_parts) != 2 or path_parts[0] != "in" or not path_parts[1]:
            raise ValueError("Invalid LinkedIn profile URL")
        return value
    
    @field_validator("profile_id")
    @classmethod
    def validate_profile_id(cls, value: str) -> str:
        if not value or len(value) < 1:
            raise ValueError("LinkedIn profile ID cannot be empty")
        return value
