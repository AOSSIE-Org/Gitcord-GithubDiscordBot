"""
Social Profile Validators

Validators for different social media platforms with normalization
"""

from __future__ import annotations

import re
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
        if not re.fullmatch(r"[A-Za-z0-9_]{1,15}", username):
            raise ValueError("X username can only contain ASCII letters, numbers, and underscores")
        
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


_BLUESKY_DOMAIN_LABEL_RE = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$"
)
_MASTODON_DOMAIN_LABEL_RE = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)
_MASTODON_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]+$")


def _validate_bluesky_handle(handle: str) -> str:
    """Validate an AT Protocol handle and return its lowercase form."""
    if not handle or len(handle) > 253:
        raise ValueError("Bluesky handle must be a valid domain name")

    labels = handle.split(".")
    if (
        len(labels) < 2
        or len(labels[-1]) < 2
        or labels[-1].isdigit()
        or any(
            not label
            or len(label) > 63
            or not _BLUESKY_DOMAIN_LABEL_RE.fullmatch(label)
            for label in labels
        )
    ):
        raise ValueError("Bluesky handle must be a valid domain name")

    return handle.lower()


def _normalize_mastodon_instance(instance: str) -> str:
    """Validate an instance authority and return its canonical host[:port]."""
    try:
        parsed = urlparse(f"//{instance}")
        hostname = parsed.hostname or ""
        port = parsed.port
    except ValueError:
        raise ValueError("Invalid Mastodon instance hostname or port") from None

    if (
        not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or (port is not None and port == 0)
    ):
        raise ValueError("Invalid Mastodon instance hostname or port")

    try:
        hostname = hostname.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError:
        raise ValueError("Invalid Mastodon instance hostname") from None

    labels = hostname.split(".")
    if (
        not hostname
        or len(hostname) > 253
        or any(
            not label
            or len(label) > 63
            or not _MASTODON_DOMAIN_LABEL_RE.fullmatch(label)
            for label in labels
        )
    ):
        raise ValueError("Invalid Mastodon instance hostname")

    return f"{hostname}:{port}" if port is not None else hostname


class BlueskyProfileValidator(PlatformValidator):
    """Validator for Bluesky profiles."""

    def validate(self, input_value: str) -> dict:
        """
        Accept:
        - https://bsky.app/profile/handle
        - bsky.app/profile/handle
        - handle
        - @handle

        URL inputs must use the bsky.app host and contain exactly one profile
        handle. Plain handles are checked against the AT Protocol domain rules.
        """
        value = input_value.strip()
        if not value:
            raise ValueError("Bluesky handle cannot be empty")

        is_url = "://" in value or value.lower().startswith("bsky.app/")
        if is_url:
            url_candidate = value
            if "://" not in url_candidate:
                url_candidate = f"https://{url_candidate}"

            try:
                parsed = urlparse(url_candidate)
                port = parsed.port
            except ValueError:
                raise ValueError("Bluesky profile URL is malformed") from None

            if parsed.scheme.lower() not in {"http", "https"}:
                raise ValueError("Bluesky profile URL must use http or https scheme")
            if (
                parsed.hostname != "bsky.app"
                or parsed.username is not None
                or parsed.password is not None
                or port is not None
            ):
                raise ValueError("Bluesky profile URL must use the bsky.app host")

            path_parts = parsed.path.split("/")
            if path_parts and path_parts[-1] == "":
                path_parts = path_parts[:-1]
            if (
                len(path_parts) != 3
                or path_parts[0] != ""
                or path_parts[1] != "profile"
                or not path_parts[2]
            ):
                raise ValueError("Bluesky URL must use /profile/<handle>")

            handle = path_parts[2]
        else:
            handle = value[1:] if value.startswith("@") else value

        handle = _validate_bluesky_handle(handle)

        return {
            "normalized": handle,
            "display": f"https://bsky.app/profile/{handle}",
            "platform": "bluesky",
        }


class MastodonProfileValidator(PlatformValidator):
    """Validator for Mastodon profiles across arbitrary instances."""

    def validate(self, input_value: str) -> dict:
        """
        Accept:
        - https://mastodon.social/@username
        - http://another.instance/@username
        - @username@instance.social

        The instance is validated syntactically, not restricted to a particular
        provider, because Mastodon is a federated platform.
        """
        value = input_value.strip()
        if not value:
            raise ValueError("Mastodon profile cannot be empty")

        if value.lower().startswith(("https://", "http://")):
            try:
                parsed = urlparse(value)
                # Accessing .port validates malformed and out-of-range ports.
                _ = parsed.port
            except ValueError:
                raise ValueError("Mastodon profile URL is malformed") from None

            if parsed.scheme.lower() not in {"http", "https"}:
                raise ValueError("Mastodon profile URL must use http or https scheme")
            if parsed.username is not None or parsed.password is not None:
                raise ValueError("Mastodon profile URL must not contain user information")

            instance = _normalize_mastodon_instance(parsed.netloc)
            path = parsed.path
            if path.endswith("/"):
                path = path[:-1]
            if not path.startswith("/@") or path.count("/") != 1:
                raise ValueError("Mastodon URL must use /@<username>")

            username = path[2:]
        else:
            if not value.startswith("@"):
                raise ValueError("Mastodon handle should be @username@instance")

            parts = value[1:].split("@")
            if len(parts) != 2:
                raise ValueError("Mastodon handle must be in @username@instance format")

            username, raw_instance = parts
            instance = _normalize_mastodon_instance(raw_instance)

        if not username or not _MASTODON_USERNAME_RE.fullmatch(username):
            raise ValueError("Mastodon username can only contain letters, numbers, and underscores")

        handle = f"{username.lower()}@{instance}"

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
    platform_lower = platform.strip().lower()
    validator_class = SOCIAL_VALIDATORS.get(platform_lower)
    if not validator_class:
        raise ValueError(
            f"Unknown platform: {platform}. "
            f"Supported: {', '.join(SOCIAL_VALIDATORS.keys())}"
        )
    return validator_class()
