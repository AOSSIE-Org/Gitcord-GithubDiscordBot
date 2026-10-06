"""
Tests for Social Profile Validators

Unit tests for X/Twitter, LinkedIn, Bluesky, and Mastodon profile validators
"""

import pytest

from ghdcbot.core.social_models import LinkedInProfile, parse_linkedin_profile_url
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

    def test_reject_non_profile_url_path(self):
        """Should reject X URLs that are not a single /username profile path."""
        validator = XProfileValidator()
        with pytest.raises(ValueError):
            validator.validate("https://x.com/i/user/123")
        with pytest.raises(ValueError):
            validator.validate("https://x.com/intent/user")
    
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


    def test_validate_bare_domain_without_scheme(self):
        """Should accept linkedin.com/in/... without a scheme and canonicalize to https"""
        validator = LinkedInProfileValidator()

        assert validator.validate("linkedin.com/in/john-doe-123")["normalized"] == (
            "https://linkedin.com/in/john-doe-123"
        )
        assert validator.validate("www.linkedin.com/in/john-doe-123")["normalized"] == (
            "https://linkedin.com/in/john-doe-123"
        )

    def test_validate_strips_port_fragment_and_host_case(self):
        """Should canonicalize host case, drop port and fragment, keep profile id case"""
        validator = LinkedInProfileValidator()
        result = validator.validate("https://LinkedIn.com:443/in/John-Doe?utm=1#top")

        assert result["normalized"] == "https://linkedin.com/in/John-Doe"

    @pytest.mark.parametrize(
        "url",
        [
            "https://evil-phishing.com/in/target",
            "https://linkedin.com.attacker.org/in/target",
            "https://fake-linkedin.com/in/target",
            "https://notlinkedin.com/in/target",
            "https://linkedin.com@evil.com/in/target",  # userinfo trick
            "https://evil.com/#@linkedin.com/in/target",  # fragment trick
            "https://evil.com/linkedin.com/in/target",  # host in path
            "https://sub.linkedin.com/in/target",  # not www/m/<two-letter region>
        ],
    )
    def test_reject_non_linkedin_domains_containing_in_path(self, url):
        """Should reject any host other than linkedin.com even if the path contains /in/"""
        validator = LinkedInProfileValidator()

        with pytest.raises(ValueError, match=r"on linkedin\.com"):
            validator.validate(url)

    @pytest.mark.parametrize(
        "url",
        [
            "javascript:alert(1)//linkedin.com/in/target",
            "ftp://linkedin.com/in/target",
            "file:///linkedin.com/in/target",
            "data:text/html,linkedin.com/in/target",
        ],
    )
    def test_reject_non_http_schemes(self, url):
        """Should reject non-http(s) schemes instead of prefixing https:// onto them"""
        validator = LinkedInProfileValidator()

        with pytest.raises(ValueError, match="http or https"):
            validator.validate(url)

    def test_reject_malformed_url_with_clean_message(self):
        """urlparse errors (e.g. unclosed IPv6 bracket) must not leak raw messages"""
        validator = LinkedInProfileValidator()

        with pytest.raises(ValueError, match="malformed"):
            validator.validate("https://[::1/in/x")

    def test_reject_extra_path_segments(self):
        """Should reject /in/<id>/<more> paths"""
        validator = LinkedInProfileValidator()

        with pytest.raises(ValueError):
            validator.validate("https://linkedin.com/in/john-doe/details")

    def test_reject_empty_input(self):
        """Should reject empty and whitespace-only input"""
        validator = LinkedInProfileValidator()

        with pytest.raises(ValueError):
            validator.validate("")
        with pytest.raises(ValueError):
            validator.validate("   ")

    def test_spoofed_host_rejected_before_pydantic_model(self):
        """Hostname validation must happen in the validator, not only in the Pydantic model.

        Regression test for the original bug: _normalize_url/_extract_profile_id
        accepted https://evil-phishing.com/in/target and only the downstream
        LinkedInProfile model rejected it, surfacing a raw pydantic ValidationError.
        """
        validator = LinkedInProfileValidator()

        with pytest.raises(ValueError, match=r"on linkedin\.com") as exc_info:
            validator._normalize_url("https://evil-phishing.com/in/target")
        # A plain ValueError from the validator, not pydantic's ValidationError subclass
        assert type(exc_info.value) is ValueError

        with pytest.raises(ValueError, match=r"on linkedin\.com") as exc_info:
            validator._extract_profile_id("https://evil-phishing.com/in/target")
        assert type(exc_info.value) is ValueError

    def test_error_message_is_user_friendly(self):
        """Error surfaced to Discord users should be a plain sentence, not a pydantic dump"""
        validator = LinkedInProfileValidator()

        with pytest.raises(ValueError) as exc_info:
            validator.validate("https://linkedin.com.attacker.org/in/target")

        message = str(exc_info.value)
        assert "validation error" not in message.lower()
        assert "\n" not in message


    @pytest.mark.parametrize(
        "url",
        [
            "https://www.linkedin.com/in/john-doe-123",
            "https://m.linkedin.com/in/john-doe-123",
            "https://uk.linkedin.com/in/john-doe-123",
            "https://in.linkedin.com/in/john-doe-123",
            "https://de.linkedin.com/in/john-doe-123",
        ],
    )
    def test_accept_linkedin_served_subdomains(self, url):
        """Should accept www, mobile and regional LinkedIn subdomains and canonicalize"""
        validator = LinkedInProfileValidator()

        assert validator.validate(url)["normalized"] == "https://linkedin.com/in/john-doe-123"

    @pytest.mark.parametrize(
        "url",
        [
            "https://evil.linkedin.com/in/john-doe-123",  # not www/m/<cc>
            "https://wwww.linkedin.com/in/john-doe-123",
            "https://a.b.linkedin.com/in/john-doe-123",
            "https://xlinkedin.com/in/john-doe-123",
        ],
    )
    def test_reject_other_linkedin_subdomains(self, url):
        """Only www, m and two-letter regional subdomains are LinkedIn profile hosts"""
        validator = LinkedInProfileValidator()

        with pytest.raises(ValueError, match=r"on linkedin\.com"):
            validator.validate(url)

    def test_accept_bare_url_with_port(self):
        """A bare host:port input must not be mistaken for a URL scheme"""
        validator = LinkedInProfileValidator()

        assert validator.validate("linkedin.com:443/in/john-doe-123")["normalized"] == (
            "https://linkedin.com/in/john-doe-123"
        )

    def test_accept_uppercase_path_section(self):
        """/IN/ should be treated like /in/; the profile ID keeps its case"""
        validator = LinkedInProfileValidator()

        assert validator.validate("https://linkedin.com/IN/John-Doe")["normalized"] == (
            "https://linkedin.com/in/John-Doe"
        )

    @pytest.mark.parametrize(
        "url",
        [
            "https://linkedin.com:abc/in/john-doe-123",
            "https://linkedin.com:99999/in/john-doe-123",
        ],
    )
    def test_reject_invalid_port(self, url):
        """Invalid ports should be rejected rather than silently dropped"""
        validator = LinkedInProfileValidator()

        with pytest.raises(ValueError, match="port"):
            validator.validate(url)

    @pytest.mark.parametrize(
        "profile_id",
        [
            "[click](<https:evil.com>)",  # Markdown link injection into Discord embeds
            "john doe",
            "john<script>",
            "john.doe",
            "john(doe)",
            "ab",  # too short
            "a" * 101,  # too long
            "-john",  # must start with a letter or digit
        ],
    )
    def test_reject_unsafe_or_out_of_range_profile_ids(self, profile_id):
        """Profile ID must be 3-100 chars of [A-Za-z0-9_%-] so it is safe to embed"""
        validator = LinkedInProfileValidator()

        with pytest.raises(ValueError, match="3-100 characters"):
            validator.validate(f"https://linkedin.com/in/{profile_id}")

    @pytest.mark.parametrize(
        "profile_id",
        [
            "abc",
            "a" * 100,
            "john-doe-1a2b3c4d5",
            "john_doe",
            "%E5%BC%A0%E4%BC%9F-123",  # percent-encoded non-ASCII ID
        ],
    )
    def test_accept_well_formed_profile_ids(self, profile_id):
        """Typical LinkedIn IDs including percent-encoded ones are accepted"""
        validator = LinkedInProfileValidator()

        assert validator.validate(f"https://linkedin.com/in/{profile_id}")["normalized"] == (
            f"https://linkedin.com/in/{profile_id}"
        )

    def test_markdown_link_injection_never_reaches_stored_value(self):
        """Regression: a Markdown link disguised as a profile ID must not be stored"""
        validator = LinkedInProfileValidator()

        with pytest.raises(ValueError):
            validator.validate("https://linkedin.com/in/[click](<https:evil.com>)")


class TestLinkedInProfileModel:
    """The stored model shares the validator's rules and only accepts canonical input"""

    def test_parser_returns_canonical_url_and_id(self):
        assert parse_linkedin_profile_url("www.LinkedIn.com/IN/John-Doe/?x=1") == (
            "https://linkedin.com/in/John-Doe",
            "John-Doe",
        )

    def test_accepts_canonical_url(self):
        profile = LinkedInProfile(
            profile_url="https://linkedin.com/in/john-doe-123", profile_id="john-doe-123"
        )
        assert profile.profile_url == "https://linkedin.com/in/john-doe-123"

    @pytest.mark.parametrize(
        "url",
        [
            "https://www.linkedin.com/in/john-doe-123",  # not canonical
            "http://linkedin.com/in/john-doe-123",  # not https
            "https://linkedin.com/in/john-doe-123/",  # trailing slash
            "https://evil.com/in/john-doe-123",
            "https://linkedin.com/school/mit",
        ],
    )
    def test_rejects_non_canonical_or_invalid_url(self, url):
        with pytest.raises(ValueError):
            LinkedInProfile(profile_url=url, profile_id="john-doe-123")

    def test_rejects_unsafe_profile_id(self):
        with pytest.raises(ValueError):
            LinkedInProfile(
                profile_url="https://linkedin.com/in/john-doe-123", profile_id="[x](<y>)"
            )

    def test_rejects_mismatched_profile_id(self):
        with pytest.raises(ValueError, match="does not match"):
            LinkedInProfile(profile_url="https://linkedin.com/in/john-doe-123", profile_id="other")


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
