"""
Social Profile Service

Service layer for managing social profiles with validation and normalization
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from ghdcbot.core.social_models import SocialProfile
from ghdcbot.core.social_validators import get_validator

if TYPE_CHECKING:
    from ghdcbot.adapters.storage.sqlite import SqliteStorage


class SocialProfileService:
    """High-level service for managing social profiles"""
    
    def __init__(self, storage: SqliteStorage) -> None:
        self.storage = storage
    
    async def set_profile(
        self,
        discord_user_id: str,
        platform: str,
        input_value: str,
    ) -> SocialProfile:
        """Add or update a social profile for a user.
        
        Validates and normalizes the input, then stores it.
        
        Args:
            discord_user_id: Discord user ID
            platform: Platform name (x, linkedin, bluesky, etc.)
            input_value: Raw user input (URL, handle, etc.)
            
        Returns:
            SocialProfile dataclass with normalized data
            
        Raises:
            ValueError: If platform is unknown or input is invalid
        """
        # Validate and normalize
        validator = get_validator(platform)
        result = validator.validate(input_value)
        
        # Store in database
        profile_dict = self.storage.set_social_profile(
            discord_user_id=discord_user_id,
            platform=result["platform"],
            profile_handle=result["normalized"],
            display_value=result["display"],
        )
        
        # Convert to SocialProfile dataclass
        return SocialProfile(
            discord_user_id=profile_dict["discord_user_id"],
            platform=profile_dict["platform"],
            profile_handle=profile_dict["profile_handle"],
            display_value=profile_dict["display_value"],
            verified=bool(profile_dict.get("verified", False)),
            created_at=self._parse_datetime(profile_dict.get("created_at")),
            updated_at=self._parse_datetime(profile_dict.get("updated_at")),
        )
    
    async def get_profiles(self, discord_user_id: str) -> dict[str, SocialProfile]:
        """Get all social profiles for a user.
        
        Args:
            discord_user_id: Discord user ID
            
        Returns:
            dict mapping platform -> SocialProfile
        """
        profiles_data = self.storage.get_all_social_profiles(discord_user_id)
        
        result = {}
        for profile_dict in profiles_data:
            platform = profile_dict["platform"]
            result[platform] = SocialProfile(
                discord_user_id=profile_dict["discord_user_id"],
                platform=profile_dict["platform"],
                profile_handle=profile_dict["profile_handle"],
                display_value=profile_dict["display_value"],
                verified=bool(profile_dict.get("verified", False)),
                created_at=self._parse_datetime(profile_dict.get("created_at")),
                updated_at=self._parse_datetime(profile_dict.get("updated_at")),
            )
        
        return result
    
    async def get_profile(self, discord_user_id: str, platform: str) -> SocialProfile | None:
        """Get a specific social profile for a user.
        
        Args:
            discord_user_id: Discord user ID
            platform: Platform name
            
        Returns:
            SocialProfile or None if not found
        """
        profile_dict = self.storage.get_social_profile(discord_user_id, platform)
        
        if not profile_dict:
            return None
        
        return SocialProfile(
            discord_user_id=profile_dict["discord_user_id"],
            platform=profile_dict["platform"],
            profile_handle=profile_dict["profile_handle"],
            display_value=profile_dict["display_value"],
            verified=bool(profile_dict.get("verified", False)),
            created_at=self._parse_datetime(profile_dict.get("created_at")),
            updated_at=self._parse_datetime(profile_dict.get("updated_at")),
        )
    
    async def remove_profile(self, discord_user_id: str, platform: str) -> bool:
        """Remove a social profile.
        
        Args:
            discord_user_id: Discord user ID
            platform: Platform name
            
        Returns:
            True if profile was removed, False if not found
        """
        return self.storage.remove_social_profile(discord_user_id, platform)
    
    async def validate_profile(self, platform: str, input_value: str) -> dict:
        """Validate and normalize a profile input without storing it.
        
        Useful for preview/confirmation UIs.
        
        Args:
            platform: Platform name
            input_value: Raw user input
            
        Returns:
            dict with normalized (identifier) and display (user-friendly) values
            
        Raises:
            ValueError: If platform is unknown or input is invalid
        """
        validator = get_validator(platform)
        return validator.validate(input_value)
    
    @staticmethod
    def _parse_datetime(value: str | None) -> datetime | None:
        """Parse ISO format datetime string."""
        if not value:
            return None
        try:
            dt = datetime.fromisoformat(value)
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt
        except (ValueError, TypeError):
            return None
