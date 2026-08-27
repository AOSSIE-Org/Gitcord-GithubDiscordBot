"""
Tests for Social Profile Service

Unit tests for SocialProfileService business logic
"""

import tempfile

import pytest
import pytest_asyncio

from ghdcbot.adapters.storage.sqlite import SqliteStorage
from ghdcbot.engine.social_profiles import SocialProfileService


@pytest_asyncio.fixture
async def service(tmp_path):
    """Create a service with temporary storage for testing"""
    storage = SqliteStorage(str(tmp_path))
    storage.init_schema()
    return SocialProfileService(storage)


class TestSetProfile:
    """Tests for set_profile method"""
    
    @pytest.mark.asyncio
    async def test_set_x_profile_from_username(self, service):
        """Should accept and normalize X username"""
        profile = await service.set_profile(
            discord_user_id="123456",
            platform="x",
            input_value="twitter_user",
        )
        
        assert profile.discord_user_id == "123456"
        assert profile.platform == "x"
        assert profile.profile_handle == "twitter_user"
        assert profile.display_value == "https://x.com/twitter_user"
    
    @pytest.mark.asyncio
    async def test_set_x_profile_from_url(self, service):
        """Should accept and normalize X URL"""
        profile = await service.set_profile(
            discord_user_id="123456",
            platform="x",
            input_value="https://x.com/twitter_user",
        )
        
        assert profile.profile_handle == "twitter_user"
        assert profile.display_value == "https://x.com/twitter_user"
    
    @pytest.mark.asyncio
    async def test_set_linkedin_profile(self, service):
        """Should accept and normalize LinkedIn URL"""
        profile = await service.set_profile(
            discord_user_id="123456",
            platform="linkedin",
            input_value="https://linkedin.com/in/john-doe-123",
        )
        
        assert profile.platform == "linkedin"
        assert profile.profile_handle == "https://linkedin.com/in/john-doe-123"
    
    @pytest.mark.asyncio
    async def test_set_bluesky_profile(self, service):
        """Should accept and normalize Bluesky handle"""
        profile = await service.set_profile(
            discord_user_id="123456",
            platform="bluesky",
            input_value="user.bsky.social",
        )
        
        assert profile.platform == "bluesky"
        assert profile.profile_handle == "user.bsky.social"
    
    @pytest.mark.asyncio
    async def test_reject_invalid_platform(self, service):
        """Should reject unknown platform"""
        with pytest.raises(ValueError):
            await service.set_profile(
                discord_user_id="123456",
                platform="unknown_platform",
                input_value="value",
            )
    
    @pytest.mark.asyncio
    async def test_reject_invalid_x_input(self, service):
        """Should reject invalid X profile input"""
        with pytest.raises(ValueError):
            await service.set_profile(
                discord_user_id="123456",
                platform="x",
                input_value="username!@#",  # Invalid characters
            )
    
    @pytest.mark.asyncio
    async def test_reject_invalid_linkedin_input(self, service):
        """Should reject invalid LinkedIn URL"""
        with pytest.raises(ValueError):
            await service.set_profile(
                discord_user_id="123456",
                platform="linkedin",
                input_value="https://linkedin.com/company/acme",  # Company page
            )
    
    @pytest.mark.asyncio
    async def test_update_existing_profile(self, service):
        """Should update existing profile with same platform"""
        profile1 = await service.set_profile(
            discord_user_id="123456",
            platform="x",
            input_value="old_user",
        )
        
        profile2 = await service.set_profile(
            discord_user_id="123456",
            platform="x",
            input_value="new_user",
        )
        
        assert profile2.profile_handle == "new_user"
        assert profile1.created_at == profile2.created_at  # Same creation time


class TestGetProfile:
    """Tests for get_profile method"""
    
    @pytest.mark.asyncio
    async def test_get_existing_profile(self, service):
        """Should retrieve existing profile"""
        await service.set_profile(
            discord_user_id="123456",
            platform="x",
            input_value="twitter_user",
        )
        
        profile = await service.get_profile("123456", "x")
        
        assert profile is not None
        assert profile.platform == "x"
        assert profile.profile_handle == "twitter_user"
    
    @pytest.mark.asyncio
    async def test_get_nonexistent_profile(self, service):
        """Should return None for nonexistent profile"""
        profile = await service.get_profile("999999", "x")
        
        assert profile is None
    
    @pytest.mark.asyncio
    async def test_get_profile_wrong_platform(self, service):
        """Should return None if platform doesn't match"""
        await service.set_profile(
            discord_user_id="123456",
            platform="x",
            input_value="twitter_user",
        )
        
        profile = await service.get_profile("123456", "linkedin")
        
        assert profile is None


class TestGetProfiles:
    """Tests for get_profiles method"""
    
    @pytest.mark.asyncio
    async def test_get_no_profiles(self, service):
        """Should return empty dict if user has no profiles"""
        profiles = await service.get_profiles("999999")
        
        assert profiles == {}
    
    @pytest.mark.asyncio
    async def test_get_single_profile(self, service):
        """Should retrieve single profile"""
        await service.set_profile(
            discord_user_id="123456",
            platform="x",
            input_value="twitter_user",
        )
        
        profiles = await service.get_profiles("123456")
        
        assert len(profiles) == 1
        assert "x" in profiles
        assert profiles["x"].profile_handle == "twitter_user"
    
    @pytest.mark.asyncio
    async def test_get_multiple_profiles(self, service):
        """Should retrieve all profiles for user"""
        await service.set_profile(
            discord_user_id="123456",
            platform="x",
            input_value="twitter_user",
        )
        
        await service.set_profile(
            discord_user_id="123456",
            platform="linkedin",
            input_value="https://linkedin.com/in/john-doe",
        )
        
        await service.set_profile(
            discord_user_id="123456",
            platform="bluesky",
            input_value="user.bsky.social",
        )
        
        profiles = await service.get_profiles("123456")
        
        assert len(profiles) == 3
        assert "x" in profiles
        assert "linkedin" in profiles
        assert "bluesky" in profiles


class TestRemoveProfile:
    """Tests for remove_profile method"""
    
    @pytest.mark.asyncio
    async def test_remove_existing_profile(self, service):
        """Should remove existing profile"""
        await service.set_profile(
            discord_user_id="123456",
            platform="x",
            input_value="twitter_user",
        )
        
        result = await service.remove_profile("123456", "x")
        
        assert result is True
        
        # Verify it's gone
        profile = await service.get_profile("123456", "x")
        assert profile is None
    
    @pytest.mark.asyncio
    async def test_remove_nonexistent_profile(self, service):
        """Should return False for nonexistent profile"""
        result = await service.remove_profile("999999", "x")
        
        assert result is False
    
    @pytest.mark.asyncio
    async def test_remove_specific_platform(self, service):
        """Should only remove specified platform"""
        await service.set_profile(
            discord_user_id="123456",
            platform="x",
            input_value="twitter_user",
        )
        
        await service.set_profile(
            discord_user_id="123456",
            platform="linkedin",
            input_value="https://linkedin.com/in/john-doe",
        )
        
        await service.remove_profile("123456", "x")
        
        # LinkedIn should still exist
        profiles = await service.get_profiles("123456")
        assert len(profiles) == 1
        assert "linkedin" in profiles


class TestValidateProfile:
    """Tests for validate_profile method"""
    
    @pytest.mark.asyncio
    async def test_validate_without_storing(self, service):
        """Should validate without storing to database"""
        result = await service.validate_profile("x", "twitter_user")
        
        assert result["normalized"] == "twitter_user"
        assert result["display"] == "https://x.com/twitter_user"
        
        # Should not be stored
        profile = await service.get_profile("no_user", "x")
        assert profile is None
    
    @pytest.mark.asyncio
    async def test_validate_returns_normalized_and_display(self, service):
        """Should return both normalized and display values"""
        result = await service.validate_profile(
            "linkedin",
            "https://www.linkedin.com/in/john-doe-123/",
        )
        
        assert result["normalized"] == "https://linkedin.com/in/john-doe-123"
        assert result["display"] == "https://linkedin.com/in/john-doe-123"
    
    @pytest.mark.asyncio
    async def test_validate_invalid_input(self, service):
        """Should reject invalid input"""
        with pytest.raises(ValueError):
            await service.validate_profile("x", "invalid!@#$")


class TestServiceIntegration:
    """Integration tests for full workflows"""
    
    @pytest.mark.asyncio
    async def test_complete_user_workflow(self, service):
        """Test complete workflow: add X, add LinkedIn, get all, remove one"""
        # Add X profile
        x_profile = await service.set_profile(
            discord_user_id="123456",
            platform="x",
            input_value="https://x.com/john_doe",
        )
        assert x_profile.profile_handle == "john_doe"
        
        # Add LinkedIn profile
        linkedin_profile = await service.set_profile(
            discord_user_id="123456",
            platform="linkedin",
            input_value="https://linkedin.com/in/john-doe-123",
        )
        assert "linkedin.com" in linkedin_profile.display_value
        
        # Get all profiles
        all_profiles = await service.get_profiles("123456")
        assert len(all_profiles) == 2
        
        # Remove X profile
        removed = await service.remove_profile("123456", "x")
        assert removed is True
        
        # Verify X is gone but LinkedIn remains
        remaining = await service.get_profiles("123456")
        assert len(remaining) == 1
        assert "linkedin" in remaining
        assert "x" not in remaining
    
    @pytest.mark.asyncio
    async def test_multiple_users_isolated(self, service):
        """Test that profiles are isolated between users"""
        # User 1 adds profiles
        await service.set_profile(
            discord_user_id="user1",
            platform="x",
            input_value="user1_twitter",
        )
        
        # User 2 adds profiles
        await service.set_profile(
            discord_user_id="user2",
            platform="x",
            input_value="user2_twitter",
        )
        
        # Get profiles for each user
        user1_profiles = await service.get_profiles("user1")
        user2_profiles = await service.get_profiles("user2")
        
        assert user1_profiles["x"].profile_handle == "user1_twitter"
        assert user2_profiles["x"].profile_handle == "user2_twitter"
    
    @pytest.mark.asyncio
    async def test_validate_then_store(self, service):
        """Test validating before storing"""
        # Validate
        validation = await service.validate_profile(
            "x",
            "https://twitter.com/john_doe",
        )
        
        # Should show what will be stored
        assert validation["normalized"] == "john_doe"
        
        # Store
        profile = await service.set_profile(
            discord_user_id="123456",
            platform="x",
            input_value="https://twitter.com/john_doe",
        )
        
        # Should match validation
        assert profile.profile_handle == validation["normalized"]
