"""
Social Profile Validators

Validators for different social media platforms with normalization
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from urllib.parse import urlparse

from ghdcbot.core.social_models import (
    LinkedInProfile,
    XProfile,
    parse_linkedin_profile_url,
)


class PlatformValidator(ABC):
    """Base class for platform-specific validators"""
    
    @abstractmethod
    def validate(self, input_value: str) -> dict:
        """
        Validate and normalize user input for this platform
        
        Args:
            input_value: Raw user input (URL, handle, username, etc.)
            
        Returns:
            dict with 'normalized' (internal identifier) and 'display' (user-friendly)
            
        Raises:
            ValueError: If input is invalid
        """
        pass


class XProfileValidator(PlatformValidator):
    """Validator for X/Twitter profiles"""
    
    @staticmethod
    def _extract_username(input_value: str) -> str:
        """Extract username from various X input formats"""
        value = input_value.strip()
        
        # Remove @mention prefix if present
        if value.startswith("@"):
            value = value[1:]
        
        url_candidate = value
        lowered = value.lower()
        if "://" not in url_candidate and lowered.startswith(
            ("x.com/", "twitter.com/", "www.x.com/", "www.twitter.com/")
        ):
            url_candidate = f"https://{url_candidate}"

        if "://" in url_candidate:
            parsed = urlparse(url_candidate)
            scheme = (parsed.scheme or "").lower()
            if scheme not in ("http", "https"):
                raise ValueError("X profile URL must use http or https scheme")
            host = (parsed.hostname or "").lower()
            if host.startswith("www."):
                host = host[4:]
            if host in {"x.com", "twitter.com"}:
                profile_path = parsed.path.strip("/")
                if not profile_path or "/" in profile_path:
                    raise ValueError("Could not extract username from URL")
                username = profile_path
                if username:
                    return username
                raise ValueError("Could not extract username from URL")
        
        # Direct username
        return value
    
    def validate(self, input_value: str) -> dict:
        """
        Accept:
        - https://x.com/username
        - https://twitter.com/username
        - @username
        - username
        
        Returns normalized username and display URL
        """
        username = self._extract_username(input_value)
        
        # Validate username format
        if not username or len(username) < 1 or len(username) > 15:
            raise ValueError("X username must be 1-15 characters")
        
        if not all(c.isalnum() or c == "_" for c in username):
            raise ValueError("X username can only contain letters, numbers, and underscores")
        
        # Create profile object to trigger Pydantic validation
        profile = XProfile(username=username)
        
        return {
            "normalized": profile.username,  # lowercase username
            "display": profile.display_url(),
            "platform": "x",
        }


class LinkedInProfileValidator(PlatformValidator):
    """Validator for LinkedIn profiles.

    All parsing rules live in ghdcbot.core.social_models.parse_linkedin_profile_url
    so the validator and the LinkedInProfile model can never disagree.
    """

    @staticmethod
    def _normalize_url(url: str) -> str:
        """Canonicalize a LinkedIn profile URL to https://linkedin.com/in/<id>."""
        return parse_linkedin_profile_url(url)[0]

    @staticmethod
    def _extract_profile_id(url: str) -> str:
        """Extract the profile ID from a LinkedIn profile URL (host is validated too)."""
        return parse_linkedin_profile_url(url)[1]

    def validate(self, input_value: str) -> dict:
        """
        Accept:
        - https://linkedin.com/in/profile-id
        - https://www.linkedin.com/in/profile-id (also m. and regional uk., in., ...)
        - http://linkedin.com/in/profile-id
        - linkedin.com/in/profile-id

        Reject anything that is not a LinkedIn host (including lookalike
        domains such as linkedin.com.attacker.com or fake-linkedin.com), any
        non-http(s) scheme, and profile IDs containing characters that could
        be rendered as Markdown or a link in Discord embeds.

        Returns normalized URL and profile ID
        """
        url, profile_id = parse_linkedin_profile_url(input_value)

        # Create profile object to trigger Pydantic validation
        profile = LinkedInProfile(profile_url=url, profile_id=profile_id)

        return {
            "normalized": profile.profile_url,  # Full URL as identifier
            "display": profile.profile_url,
            "platform": "linkedin",
        }


class BlueskyProfileValidator(PlatformValidator):
    """Validator for Bluesky profiles"""
    
    def validate(self, input_value: str) -> dict:
        """
        Accept:
        - https://bsky.app/profile/handle
        - handle
        - @handle
        """
        value = input_value.strip()
        
        # Remove @mention prefix
        if value.startswith("@"):
            value = value[1:]
        
        # Extract handle from URL
        if "bsky.app/profile/" in value:
            handle = value.split("bsky.app/profile/")[1].split("?")[0]
        else:
            handle = value
        
        # Basic validation
        if not handle or len(handle) < 1:
            raise ValueError("Bluesky handle cannot be empty")
        
        return {
            "normalized": handle,
            "display": f"https://bsky.app/profile/{handle}",
            "platform": "bluesky",
        }


class MastodonProfileValidator(PlatformValidator):
    """Validator for Mastodon profiles"""
    
    def validate(self, input_value: str) -> dict:
        """
        Accept:
        - https://mastodon.social/@username
        - @username@instance.social
        """
        value = input_value.strip()
        
        # URL format
        if value.startswith("https://") or value.startswith("http://"):
            # Extract username from URL
            if "/@" in value:
                parts = value.split("/@")
                instance = parts[0].replace("https://", "").replace("http://", "")
                username = parts[1].split("?")[0]
                handle = f"{username}@{instance}"
            else:
                raise ValueError("Invalid Mastodon URL format")
        else:
            # Already in @username@instance format
            if not value.startswith("@"):
                raise ValueError("Mastodon handle should be @username@instance")
            handle = value[1:]  # Remove leading @
        
        if not handle or "@" not in handle:
            raise ValueError("Mastodon handle must be in @username@instance format")
        
        return {
            "normalized": handle,
            "display": f"Mastodon: {handle}",
            "platform": "mastodon",
        }


# Validator registry for extensibility
SOCIAL_VALIDATORS: dict[str, type[PlatformValidator]] = {
    "x": XProfileValidator,
    "twitter": XProfileValidator,  # Alias for backward compatibility
    "linkedin": LinkedInProfileValidator,
    "bluesky": BlueskyProfileValidator,
    "mastodon": MastodonProfileValidator,
}


def get_validator(platform: str) -> PlatformValidator:
    """Get validator instance for a platform"""
    platform_lower = platform.lower()
    validator_class = SOCIAL_VALIDATORS.get(platform_lower)
    if not validator_class:
        raise ValueError(
            f"Unknown platform: {platform}. "
            f"Supported: {', '.join(SOCIAL_VALIDATORS.keys())}"
        )
    return validator_class()
