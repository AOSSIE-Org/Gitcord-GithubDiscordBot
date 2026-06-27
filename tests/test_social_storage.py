"""
Tests for Social Profile Storage CRUD Operations

Unit tests for sqlite.py social profile methods
"""

import tempfile

import pytest

from ghdcbot.adapters.storage.sqlite import SqliteStorage


@pytest.fixture
def storage():
    """Create a temporary SQLite database for testing"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        storage = SqliteStorage(tmp_dir)
        storage.init_schema()
        yield storage


class TestSetSocialProfile:
    """Tests for set_social_profile method"""
    
    def test_create_new_profile(self, storage):
        """Should create a new social profile"""
        result = storage.set_social_profile(
            discord_user_id="123456",
            platform="x",
            profile_handle="twitter_user",
            display_value="https://x.com/twitter_user",
        )
        
        assert result["discord_user_id"] == "123456"
        assert result["platform"] == "x"
        assert result["profile_handle"] == "twitter_user"
        assert result["display_value"] == "https://x.com/twitter_user"
        assert result["verified"] == 0
        assert result["id"] is not None
    
    def test_update_existing_profile(self, storage):
        """Should update existing profile with same platform"""
        # Create initial profile
        result1 = storage.set_social_profile(
            discord_user_id="123456",
            platform="x",
            profile_handle="old_user",
            display_value="https://x.com/old_user",
        )
        
        # Update profile
        result2 = storage.set_social_profile(
            discord_user_id="123456",
            platform="x",
            profile_handle="new_user",
            display_value="https://x.com/new_user",
        )
        
        assert result2["profile_handle"] == "new_user"
        assert result2["display_value"] == "https://x.com/new_user"
        # ID should be the same (updated, not new)
        assert result1["id"] == result2["id"]
        # created_at must be preserved across updates
        assert result1["created_at"] == result2["created_at"]
    
    def test_different_platforms_same_user(self, storage):
        """Should allow multiple platforms for same user"""
        x_profile = storage.set_social_profile(
            discord_user_id="123456",
            platform="x",
            profile_handle="twitter_user",
            display_value="https://x.com/twitter_user",
        )
        
        linkedin_profile = storage.set_social_profile(
            discord_user_id="123456",
            platform="linkedin",
            profile_handle="https://linkedin.com/in/john-doe",
            display_value="https://linkedin.com/in/john-doe",
        )
        
        assert x_profile["platform"] == "x"
        assert linkedin_profile["platform"] == "linkedin"
        assert x_profile["id"] != linkedin_profile["id"]
    
    def test_platform_normalized_to_lowercase(self, storage):
        """Should normalize platform name to lowercase"""
        result = storage.set_social_profile(
            discord_user_id="123456",
            platform="X",
            profile_handle="twitter_user",
            display_value="https://x.com/twitter_user",
        )
        
        assert result["platform"] == "x"


class TestGetSocialProfile:
    """Tests for get_social_profile method"""
    
    def test_get_existing_profile(self, storage):
        """Should retrieve existing profile"""
        # Create profile
        storage.set_social_profile(
            discord_user_id="123456",
            platform="x",
            profile_handle="twitter_user",
            display_value="https://x.com/twitter_user",
        )
        
        # Retrieve profile
        result = storage.get_social_profile("123456", "x")
        
        assert result is not None
        assert result["platform"] == "x"
        assert result["profile_handle"] == "twitter_user"
    
    def test_get_nonexistent_profile(self, storage):
        """Should return None for nonexistent profile"""
        result = storage.get_social_profile("999999", "x")
        
        assert result is None
    
    def test_get_with_wrong_platform(self, storage):
        """Should return None if platform doesn't match"""
        storage.set_social_profile(
            discord_user_id="123456",
            platform="x",
            profile_handle="twitter_user",
            display_value="https://x.com/twitter_user",
        )
        
        result = storage.get_social_profile("123456", "linkedin")
        
        assert result is None
    
    def test_get_platform_case_insensitive(self, storage):
        """Should retrieve profile regardless of platform case"""
        storage.set_social_profile(
            discord_user_id="123456",
            platform="x",
            profile_handle="twitter_user",
            display_value="https://x.com/twitter_user",
        )
        
        result = storage.get_social_profile("123456", "X")
        
        assert result is not None
        assert result["platform"] == "x"


class TestGetAllSocialProfiles:
    """Tests for get_all_social_profiles method"""
    
    def test_get_all_profiles_empty(self, storage):
        """Should return empty list if user has no profiles"""
        result = storage.get_all_social_profiles("999999")
        
        assert result == []
    
    def test_get_all_profiles_single(self, storage):
        """Should retrieve single profile"""
        storage.set_social_profile(
            discord_user_id="123456",
            platform="x",
            profile_handle="twitter_user",
            display_value="https://x.com/twitter_user",
        )
        
        result = storage.get_all_social_profiles("123456")
        
        assert len(result) == 1
        assert result[0]["platform"] == "x"
    
    def test_get_all_profiles_multiple(self, storage):
        """Should retrieve all profiles for user"""
        storage.set_social_profile(
            discord_user_id="123456",
            platform="x",
            profile_handle="twitter_user",
            display_value="https://x.com/twitter_user",
        )
        
        storage.set_social_profile(
            discord_user_id="123456",
            platform="linkedin",
            profile_handle="https://linkedin.com/in/john-doe",
            display_value="https://linkedin.com/in/john-doe",
        )
        
        storage.set_social_profile(
            discord_user_id="123456",
            platform="bluesky",
            profile_handle="user.bsky.social",
            display_value="https://bsky.app/profile/user.bsky.social",
        )
        
        result = storage.get_all_social_profiles("123456")
        
        assert len(result) == 3
        platforms = [p["platform"] for p in result]
        assert "x" in platforms
        assert "linkedin" in platforms
        assert "bluesky" in platforms
    
    def test_get_all_profiles_ordered_by_platform(self, storage):
        """Should return profiles ordered by platform name"""
        storage.set_social_profile(
            discord_user_id="123456",
            platform="x",
            profile_handle="user1",
            display_value="display1",
        )
        
        storage.set_social_profile(
            discord_user_id="123456",
            platform="bluesky",
            profile_handle="user2",
            display_value="display2",
        )
        
        storage.set_social_profile(
            discord_user_id="123456",
            platform="linkedin",
            profile_handle="user3",
            display_value="display3",
        )
        
        result = storage.get_all_social_profiles("123456")
        
        # Should be in order: bluesky, linkedin, x
        assert result[0]["platform"] == "bluesky"
        assert result[1]["platform"] == "linkedin"
        assert result[2]["platform"] == "x"


class TestRemoveSocialProfile:
    """Tests for remove_social_profile method"""
    
    def test_remove_existing_profile(self, storage):
        """Should remove existing profile"""
        storage.set_social_profile(
            discord_user_id="123456",
            platform="x",
            profile_handle="twitter_user",
            display_value="https://x.com/twitter_user",
        )
        
        result = storage.remove_social_profile("123456", "x")
        
        assert result is True
        
        # Verify it's gone
        profile = storage.get_social_profile("123456", "x")
        assert profile is None
    
    def test_remove_nonexistent_profile(self, storage):
        """Should return False for nonexistent profile"""
        result = storage.remove_social_profile("999999", "x")
        
        assert result is False
    
    def test_remove_specific_platform(self, storage):
        """Should only remove specified platform"""
        storage.set_social_profile(
            discord_user_id="123456",
            platform="x",
            profile_handle="twitter_user",
            display_value="https://x.com/twitter_user",
        )
        
        storage.set_social_profile(
            discord_user_id="123456",
            platform="linkedin",
            profile_handle="https://linkedin.com/in/john-doe",
            display_value="https://linkedin.com/in/john-doe",
        )
        
        storage.remove_social_profile("123456", "x")
        
        # LinkedIn should still exist
        linkedin = storage.get_social_profile("123456", "linkedin")
        assert linkedin is not None
        
        # X should be gone
        x_profile = storage.get_social_profile("123456", "x")
        assert x_profile is None
    
    def test_remove_case_insensitive(self, storage):
        """Should remove profile regardless of platform case"""
        storage.set_social_profile(
            discord_user_id="123456",
            platform="x",
            profile_handle="twitter_user",
            display_value="https://x.com/twitter_user",
        )
        
        result = storage.remove_social_profile("123456", "X")
        
        assert result is True


class TestUpdateSocialProfileDisplay:
    """Tests for update_social_profile_display method"""
    
    def test_update_display_value(self, storage):
        """Should update display value"""
        storage.set_social_profile(
            discord_user_id="123456",
            platform="x",
            profile_handle="twitter_user",
            display_value="https://x.com/twitter_user",
        )
        
        result = storage.update_social_profile_display(
            discord_user_id="123456",
            platform="x",
            display_value="https://x.com/twitter_user (verified)",
        )
        
        assert result is not None
        assert result["display_value"] == "https://x.com/twitter_user (verified)"
    
    def test_update_nonexistent_profile(self, storage):
        """Should return None for nonexistent profile"""
        result = storage.update_social_profile_display(
            discord_user_id="999999",
            platform="x",
            display_value="new_display",
        )
        
        assert result is None
    
    def test_update_preserves_other_fields(self, storage):
        """Should preserve other fields when updating display"""
        original = storage.set_social_profile(
            discord_user_id="123456",
            platform="x",
            profile_handle="twitter_user",
            display_value="https://x.com/twitter_user",
        )
        
        updated = storage.update_social_profile_display(
            discord_user_id="123456",
            platform="x",
            display_value="new_display",
        )
        
        assert updated["profile_handle"] == original["profile_handle"]
        assert updated["platform"] == original["platform"]
        assert updated["discord_user_id"] == original["discord_user_id"]


class TestSocialProfilesIsolation:
    """Tests for data isolation between users"""
    
    def test_profiles_isolated_by_discord_user(self, storage):
        """Should isolate profiles by Discord user ID"""
        storage.set_social_profile(
            discord_user_id="user1",
            platform="x",
            profile_handle="user1_twitter",
            display_value="display1",
        )
        
        storage.set_social_profile(
            discord_user_id="user2",
            platform="x",
            profile_handle="user2_twitter",
            display_value="display2",
        )
        
        user1_profiles = storage.get_all_social_profiles("user1")
        user2_profiles = storage.get_all_social_profiles("user2")
        
        assert len(user1_profiles) == 1
        assert len(user2_profiles) == 1
        assert user1_profiles[0]["profile_handle"] == "user1_twitter"
        assert user2_profiles[0]["profile_handle"] == "user2_twitter"
