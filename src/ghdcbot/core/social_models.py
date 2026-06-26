"""
Social Profile Models and Validation

Pydantic models for storing and validating social profiles (X, LinkedIn, etc.)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pydantic import BaseModel, HttpUrl, field_validator


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
        valid_platforms = {"x", "linkedin", "bluesky", "mastodon", "website"}
        if value.lower() not in valid_platforms:
            raise ValueError(f"Platform must be one of: {', '.join(valid_platforms)}")
        return value.lower()
    
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
        if not value.startswith("https://"):
            raise ValueError("LinkedIn URL must start with https://")
        if "linkedin.com/in/" not in value:
            raise ValueError("Invalid LinkedIn profile URL")
        if "company" in value.lower() or "companies" in value.lower():
            raise ValueError("Company pages are not supported, only personal profiles")
        return value
    
    @field_validator("profile_id")
    @classmethod
    def validate_profile_id(cls, value: str) -> str:
        if not value or len(value) < 1:
            raise ValueError("LinkedIn profile ID cannot be empty")
        return value
