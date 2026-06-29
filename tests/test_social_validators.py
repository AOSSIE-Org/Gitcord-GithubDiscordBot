"""
Tests for Social Profile Validators

Unit tests for X/Twitter, LinkedIn, Bluesky, and Mastodon profile validators
"""

import pytest

from ghdcbot.core.social_validators import (
    XProfileValidator,
    LinkedInProfileValidator,
    BlueskyProfileValidator,
    MastodonProfileValidator,
    get_validator,
)


class TestXProfileValidator:
    """Tests for X/Twitter profile validator"""
    
    def test_validate_username_only(self):
        """Should accept plain username"""
        validator = XProfileValidator()
        result = validator.validate("twitter_user")
        
        assert result["platform"] == "x"
        assert result["normalized"] == "twitter_user"
        assert result["display"] == "https://x.com/twitter_user"
    
    def test_validate_with_at_symbol(self):
        """Should accept @username format"""
        validator = XProfileValidator()
        result = validator.validate("@twitter_user")
        
        assert result["normalized"] == "twitter_user"
        assert result["display"] == "https://x.com/twitter_user"
    
    def test_validate_x_com_url(self):
        """Should accept https://x.com/username URL"""
        validator = XProfileValidator()
        result = validator.validate("https://x.com/twitter_user")
        
        assert result["normalized"] == "twitter_user"
        assert result["display"] == "https://x.com/twitter_user"
    
    def test_validate_twitter_com_url(self):
        """Should accept https://twitter.com/username URL"""
        validator = XProfileValidator()
        result = validator.validate("https://twitter.com/twitter_user")
        
        assert result["normalized"] == "twitter_user"
        assert result["display"] == "https://x.com/twitter_user"

    def test_reject_spoofed_url_host(self):
        """Should not extract username from non-X/Twitter host URLs."""
        validator = XProfileValidator()
        with pytest.raises(ValueError):
            validator.validate("https://example.com/twitter.com/twitter_user")

    def test_reject_non_http_url_scheme(self):
        """Should reject non-HTTP(S) URL schemes for X profile URLs."""
        validator = XProfileValidator()
        with pytest.raises(ValueError, match="http or https"):
            validator.validate("ftp://x.com/twitter_user")
    
    def test_validate_url_with_query_params(self):
        """Should strip query parameters from URL"""
        validator = XProfileValidator()
        result = validator.validate("https://x.com/twitter_user?some=param")
        
        assert result["normalized"] == "twitter_user"
    
    def test_reject_empty_username(self):
        """Should reject empty username"""
        validator = XProfileValidator()
        
        with pytest.raises(ValueError):
            validator.validate("")
    
    def test_reject_whitespace_only(self):
        """Should reject whitespace-only input"""
        validator = XProfileValidator()
        
        with pytest.raises(ValueError):
            validator.validate("   ")
    
    def test_reject_username_too_long(self):
        """Should reject username longer than 15 characters"""
        validator = XProfileValidator()
        
        with pytest.raises(ValueError):
            validator.validate("a" * 16)
    
    def test_reject_invalid_characters(self):
        """Should reject usernames with invalid characters"""
        validator = XProfileValidator()
        
        with pytest.raises(ValueError):
            validator.validate("user-name!")  # Hyphens not allowed
        
        with pytest.raises(ValueError):
            validator.validate("user@name")  # @ in middle not allowed
    
    def test_allow_underscore_in_username(self):
        """Should allow underscores in username"""
        validator = XProfileValidator()
        result = validator.validate("user_name_123")
        
        assert result["normalized"] == "user_name_123"
    
    def test_allow_numbers_in_username(self):
        """Should allow numbers in username"""
        validator = XProfileValidator()
        result = validator.validate("user123")
        
        assert result["normalized"] == "user123"


class TestLinkedInProfileValidator:
    """Tests for LinkedIn profile validator"""
    
    def test_validate_linkedin_url(self):
        """Should accept standard LinkedIn URL"""
        validator = LinkedInProfileValidator()
        result = validator.validate("https://linkedin.com/in/john-doe-123")
        
        assert result["platform"] == "linkedin"
        assert result["normalized"] == "https://linkedin.com/in/john-doe-123"
        assert result["display"] == "https://linkedin.com/in/john-doe-123"
    
    def test_validate_linkedin_url_with_www(self):
        """Should accept LinkedIn URL with www and normalize it"""
        validator = LinkedInProfileValidator()
        result = validator.validate("https://www.linkedin.com/in/john-doe-123")
        
        assert result["normalized"] == "https://linkedin.com/in/john-doe-123"
    
    def test_validate_http_url(self):
        """Should accept http URLs and upgrade to https"""
        validator = LinkedInProfileValidator()
        result = validator.validate("http://linkedin.com/in/john-doe-123")
        
        assert result["normalized"] == "https://linkedin.com/in/john-doe-123"
    
    def test_validate_url_with_trailing_slash(self):
        """Should accept and normalize URLs with trailing slashes"""
        validator = LinkedInProfileValidator()
        result = validator.validate("https://linkedin.com/in/john-doe-123/")
        
        assert result["normalized"] == "https://linkedin.com/in/john-doe-123"
    
    def test_validate_url_with_query_params(self):
        """Should strip query parameters"""
        validator = LinkedInProfileValidator()
        result = validator.validate("https://linkedin.com/in/john-doe-123?utm_source=share")
        
        assert result["normalized"] == "https://linkedin.com/in/john-doe-123"
    
    def test_reject_company_page(self):
        """Should reject LinkedIn company pages"""
        validator = LinkedInProfileValidator()
        
        with pytest.raises(ValueError):
            validator.validate("https://linkedin.com/company/acme-corp")
    
    def test_reject_companies_plural(self):
        """Should reject LinkedIn companies pages"""
        validator = LinkedInProfileValidator()
        
        with pytest.raises(ValueError):
            validator.validate("https://linkedin.com/companies/acme-corp")
    
    def test_reject_school_page(self):
        """Should reject LinkedIn school pages"""
        validator = LinkedInProfileValidator()
        
        with pytest.raises(ValueError):
            validator.validate("https://linkedin.com/school/mit")
    
    def test_reject_invalid_path(self):
        """Should reject URLs without /in/ path"""
        validator = LinkedInProfileValidator()
        
        with pytest.raises(ValueError):
            validator.validate("https://linkedin.com/profile/john-doe")

    def test_reject_non_linkedin_hostname(self):
        """Should reject spoofed domains that only contain linkedin.com in the URL."""
        validator = LinkedInProfileValidator()
        with pytest.raises(ValueError):
            validator.validate("https://linkedin.com.evil.example/in/john-doe")
    
    def test_reject_empty_profile_id(self):
        """Should reject URLs with empty profile ID"""
        validator = LinkedInProfileValidator()
        
        with pytest.raises(ValueError):
            validator.validate("https://linkedin.com/in/")


class TestBlueskyProfileValidator:
    """Tests for Bluesky profile validator"""
    
    def test_validate_username(self):
        """Should accept plain Bluesky handle"""
        validator = BlueskyProfileValidator()
        result = validator.validate("user.bsky.social")
        
        assert result["platform"] == "bluesky"
        assert result["normalized"] == "user.bsky.social"
        assert result["display"] == "https://bsky.app/profile/user.bsky.social"
    
    def test_validate_with_at_symbol(self):
        """Should accept @handle format"""
        validator = BlueskyProfileValidator()
        result = validator.validate("@user.bsky.social")
        
        assert result["normalized"] == "user.bsky.social"
    
    def test_validate_bsky_app_url(self):
        """Should accept Bluesky app URL"""
        validator = BlueskyProfileValidator()
        result = validator.validate("https://bsky.app/profile/user.bsky.social")
        
        assert result["normalized"] == "user.bsky.social"
    
    def test_reject_empty_handle(self):
        """Should reject empty handle"""
        validator = BlueskyProfileValidator()
        
        with pytest.raises(ValueError):
            validator.validate("")


class TestMastodonProfileValidator:
    """Tests for Mastodon profile validator"""
    
    def test_validate_mastodon_url(self):
        """Should accept Mastodon instance URL"""
        validator = MastodonProfileValidator()
        result = validator.validate("https://mastodon.social/@username")
        
        assert result["platform"] == "mastodon"
        assert result["normalized"] == "username@mastodon.social"
        assert "username@mastodon.social" in result["display"]
    
    def test_validate_mastodon_handle_format(self):
        """Should accept @username@instance format"""
        validator = MastodonProfileValidator()
        result = validator.validate("@username@mastodon.social")
        
        assert result["normalized"] == "username@mastodon.social"
    
    def test_reject_invalid_format(self):
        """Should reject invalid handle format"""
        validator = MastodonProfileValidator()
        
        with pytest.raises(ValueError):
            validator.validate("just_username")


class TestGetValidator:
    """Tests for validator factory function"""
    
    def test_get_x_validator(self):
        """Should return X validator for 'x' platform"""
        validator = get_validator("x")
        assert isinstance(validator, XProfileValidator)
    
    def test_get_twitter_alias(self):
        """Should support 'twitter' as alias for 'x'"""
        validator = get_validator("twitter")
        assert isinstance(validator, XProfileValidator)
    
    def test_get_linkedin_validator(self):
        """Should return LinkedIn validator"""
        validator = get_validator("linkedin")
        assert isinstance(validator, LinkedInProfileValidator)
    
    def test_get_bluesky_validator(self):
        """Should return Bluesky validator"""
        validator = get_validator("bluesky")
        assert isinstance(validator, BlueskyProfileValidator)
    
    def test_get_mastodon_validator(self):
        """Should return Mastodon validator"""
        validator = get_validator("mastodon")
        assert isinstance(validator, MastodonProfileValidator)
    
    def test_reject_unknown_platform(self):
        """Should raise ValueError for unknown platform"""
        with pytest.raises(ValueError):
            get_validator("unknown_platform")
    
    def test_case_insensitive_platform(self):
        """Should accept platform names in any case"""
        validator_lower = get_validator("x")
        validator_upper = get_validator("X")
        validator_mixed = get_validator("LinkedIn")
        
        assert isinstance(validator_lower, XProfileValidator)
        assert isinstance(validator_upper, XProfileValidator)
        assert isinstance(validator_mixed, LinkedInProfileValidator)
