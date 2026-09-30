"""
Social Profile Models and Validation

Pydantic models for storing and validating social profiles (X, LinkedIn, etc.)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlparse

from pydantic import BaseModel, field_validator, model_validator


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


# ---------------------------------------------------------------------------
# LinkedIn profile URL parsing
#
# Single source of truth for what counts as a LinkedIn personal profile URL.
# Both LinkedInProfileValidator (user input) and LinkedInProfile (stored model)
# use it, so the rules cannot drift apart.
# ---------------------------------------------------------------------------

# LinkedIn vanity IDs are 3-100 characters. We allow ASCII letters, digits,
# hyphen, underscore and percent-encoding (for non-ASCII IDs copied from a
# browser). Everything else is rejected so the stored value can never carry
# Markdown, angle brackets, spaces or other characters Discord could render
# as a disguised link in the /profile embed.
LINKEDIN_PROFILE_ID_RE = re.compile(r"^[A-Za-z0-9%][A-Za-z0-9_%-]{2,99}$")

# Matches a URL scheme prefix such as "https:", "javascript:" or "data:".
# The negative lookahead keeps "linkedin.com:443/in/x" from being read as
# scheme "linkedin.com" (a colon followed by digits is a port, not a scheme).
_SCHEME_PREFIX_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:(?!\d)")

# Subdomains LinkedIn itself serves profiles from: www, mobile (m), and
# two-letter regional prefixes such as uk., in., de. All canonicalize to
# linkedin.com. Anything else under linkedin.com is not a profile host.
_LINKEDIN_SUBDOMAIN_RE = re.compile(r"^(www|m|[a-z]{2})$")

_UNSUPPORTED_LINKEDIN_SECTIONS = frozenset({"company", "companies", "school"})


def _is_linkedin_host(host: str) -> bool:
    if host == "linkedin.com":
        return True
    suffix = ".linkedin.com"
    if host.endswith(suffix):
        return bool(_LINKEDIN_SUBDOMAIN_RE.fullmatch(host[: -len(suffix)]))
    return False


def parse_linkedin_profile_url(url: str) -> tuple[str, str]:
    """
    Parse and canonicalize a LinkedIn personal profile URL.

    Accepts:
    - https://linkedin.com/in/<id>, http://..., with or without www./m./<cc>.
    - Bare "linkedin.com/in/<id>" (https is assumed).
    - Upper-case host or path section ("/IN/"); the profile ID keeps its case.
    - Query string, fragment, port and trailing slash are dropped.

    Rejects with ValueError:
    - Non http(s) schemes (javascript:, ftp:, data:, file:, ...).
    - Any host that is not linkedin.com or a LinkedIn-served subdomain,
      including lookalikes (linkedin.com.evil.org, fake-linkedin.com) and
      userinfo tricks (linkedin.com@evil.org).
    - Invalid ports.
    - Company, companies and school pages.
    - Paths that are not exactly /in/<id>.
    - Profile IDs outside 3-100 chars of [A-Za-z0-9_%-].

    Returns:
        (canonical_url, profile_id) where canonical_url is
        "https://linkedin.com/in/<profile_id>".
    """
    url = url.strip()
    if not url:
        raise ValueError("LinkedIn profile URL cannot be empty")

    if not _SCHEME_PREFIX_RE.match(url):
        url = f"https://{url}"

    parsed = urlparse(url)

    if parsed.scheme.lower() not in {"http", "https"}:
        raise ValueError("LinkedIn profile URL must use http or https scheme")

    # .hostname strips userinfo and port and lowercases, so
    # "https://linkedin.com@evil.com/in/x" resolves to "evil.com".
    host = parsed.hostname or ""
    if not _is_linkedin_host(host):
        raise ValueError(
            "Only LinkedIn profile URLs on linkedin.com are supported "
            "(e.g. https://linkedin.com/in/your-profile)"
        )

    try:
        parsed.port  # raises ValueError for non-numeric or out-of-range ports
    except ValueError:
        raise ValueError("LinkedIn profile URL has an invalid port") from None

    path_parts = [part for part in parsed.path.split("/") if part]
    section = path_parts[0].lower() if path_parts else ""
    if section in _UNSUPPORTED_LINKEDIN_SECTIONS:
        raise ValueError("Company and school pages are not supported, only personal profiles")
    if len(path_parts) != 2 or section != "in":
        raise ValueError("Only LinkedIn profile URLs (linkedin.com/in/...) are supported")

    profile_id = path_parts[1]
    if not LINKEDIN_PROFILE_ID_RE.fullmatch(profile_id):
        raise ValueError(
            "LinkedIn profile ID must be 3-100 characters of letters, numbers, "
            "hyphens or underscores"
        )

    return f"https://linkedin.com/in/{profile_id}", profile_id


class LinkedInProfile(BaseModel):
    """Normalized LinkedIn profile.

    profile_url must already be canonical (https://linkedin.com/in/<id>) and
    profile_id must match it. Use parse_linkedin_profile_url() to produce both.
    """
    profile_url: str  # Full normalized URL
    profile_id: str   # Extracted profile ID

    @field_validator("profile_url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        canonical, _ = parse_linkedin_profile_url(value)
        if canonical != value:
            raise ValueError(
                "LinkedIn profile URL must be canonical: https://linkedin.com/in/<profile-id>"
            )
        return value

    @field_validator("profile_id")
    @classmethod
    def validate_profile_id(cls, value: str) -> str:
        if not LINKEDIN_PROFILE_ID_RE.fullmatch(value):
            raise ValueError(
                "LinkedIn profile ID must be 3-100 characters of letters, numbers, "
                "hyphens or underscores"
            )
        return value

    @model_validator(mode="after")
    def validate_id_matches_url(self) -> "LinkedInProfile":
        if self.profile_url != f"https://linkedin.com/in/{self.profile_id}":
            raise ValueError("LinkedIn profile ID does not match profile URL")
        return self
