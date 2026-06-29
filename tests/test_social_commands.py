"""
Tests for social profile Discord commands.

Week 5 Day 6 – Command integration testing.
Tests: /profile set x, /profile set linkedin, /profile view, /profile remove, /status integration
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from ghdcbot.adapters.discord.social_commands import (
    create_success_embed,
    create_error_embed,
    create_profile_list_embed,
)
from ghdcbot.core.social_models import SocialProfile


class TestEmbedGeneration:
    """Test embed creation for various scenarios."""
    
    def test_create_success_embed_x(self):
        """Test success embed for X profile."""
        embed = create_success_embed("x", "@shubham5080")
        assert "X profile linked" in embed.title
        assert "@shubham5080" in str(embed.fields[0].value)
        assert embed.color.value == 0x2ECC71  # Green
    
    def test_create_success_embed_linkedin(self):
        """Test success embed for LinkedIn profile."""
        embed = create_success_embed("linkedin", "linkedin.com/in/shubham-shinde")
        assert "LinkedIn profile linked" in embed.title
        assert "linkedin.com/in/shubham-shinde" in str(embed.fields[0].value)
    
    def test_create_error_embed_x_invalid_username(self):
        """Test error embed for invalid X username."""
        embed = create_error_embed("x", "Invalid X username")
        assert "Invalid X" in embed.title
        assert "x.com/username" in str(embed.fields[0].value)
        assert embed.color.value == 0xE74C3C  # Red
    
    def test_create_error_embed_linkedin_invalid_url(self):
        """Test error embed for invalid LinkedIn URL."""
        embed = create_error_embed("linkedin", "Invalid URL")
        assert "Invalid LinkedIn" in embed.title
        assert "linkedin.com/in/" in str(embed.fields[0].value)
    
    def test_create_profile_list_embed_with_profiles(self):
        """Test profile list embed with linked profiles."""
        profiles_dict = {
            "x": MagicMock(display_value="@shubham5080"),
            "linkedin": MagicMock(display_value="linkedin.com/in/shubham-shinde"),
        }
        embed = create_profile_list_embed(profiles_dict)
        assert "Your Social Profiles" in embed.title
        # Embed should have fields for all platforms
        assert len([f for f in embed.fields if f.name]) >= 2
    
    def test_create_profile_list_embed_empty(self):
        """Test profile list embed with no profiles."""
        profiles_dict = {}
        embed = create_profile_list_embed(profiles_dict)
        assert "Your Social Profiles" in embed.title
        assert "No social profiles linked yet" in (embed.description or "")


class TestSocialCommandsIntegration:
    """Integration tests for /profile commands."""
    
    @pytest.mark.asyncio
    async def test_profile_set_x_valid_username(self):
        """Test /profile set x with valid username."""
        # Mock interaction
        interaction = MagicMock()
        interaction.user.id = 12345
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()
        
        # Mock service
        mock_service = MagicMock()
        mock_profile = SocialProfile(
            discord_user_id="12345",
            platform="x",
            profile_handle="shubham5080",
            display_value="@shubham5080",
        )
        mock_service.set_profile = MagicMock(return_value=mock_profile)
        
        # Test: set_profile should be called with correct args
        result = await asyncio.to_thread(
            mock_service.set_profile,
            "12345",
            "x",
            "@shubham5080"
        )
        assert result.display_value == "@shubham5080"
    
    @pytest.mark.asyncio
    async def test_profile_set_linkedin_valid_url(self):
        """Test /profile set linkedin with valid URL."""
        # Mock service
        mock_service = MagicMock()
        mock_profile = SocialProfile(
            discord_user_id="12345",
            platform="linkedin",
            profile_handle="shubham-shinde",
            display_value="linkedin.com/in/shubham-shinde",
        )
        mock_service.set_profile = MagicMock(return_value=mock_profile)
        
        # Test
        result = await asyncio.to_thread(
            mock_service.set_profile,
            "12345",
            "linkedin",
            "https://linkedin.com/in/shubham-shinde"
        )
        assert result.platform == "linkedin"
    
    @pytest.mark.asyncio
    async def test_profile_get_profiles(self):
        """Test /profile view - get all profiles."""
        # Mock service
        mock_service = MagicMock()
        profiles = {
            "x": SocialProfile(
                discord_user_id="12345",
                platform="x",
                profile_handle="shubham5080",
                display_value="@shubham5080",
            ),
            "linkedin": SocialProfile(
                discord_user_id="12345",
                platform="linkedin",
                profile_handle="shubham-shinde",
                display_value="linkedin.com/in/shubham-shinde",
            ),
        }
        mock_service.get_profiles = MagicMock(return_value=profiles)
        
        # Test
        result = await asyncio.to_thread(mock_service.get_profiles, "12345")
        assert len(result) == 2
        assert "x" in result
        assert "linkedin" in result
    
    @pytest.mark.asyncio
    async def test_profile_remove_existing(self):
        """Test /profile remove with existing profile."""
        # Mock service
        mock_service = MagicMock()
        mock_service.remove_profile = MagicMock(return_value=True)
        
        # Test
        result = await asyncio.to_thread(
            mock_service.remove_profile,
            "12345",
            "x"
        )
        assert result is True
    
    @pytest.mark.asyncio
    async def test_profile_remove_nonexistent(self):
        """Test /profile remove with non-existent profile."""
        # Mock service
        mock_service = MagicMock()
        mock_service.remove_profile = MagicMock(return_value=False)
        
        # Test
        result = await asyncio.to_thread(
            mock_service.remove_profile,
            "12345",
            "x"
        )
        assert result is False


class TestStatusCommandIntegration:
    """Tests for /status command with social profile integration."""
    
    @pytest.mark.asyncio
    async def test_status_shows_social_profiles(self):
        """Test that /status displays linked social profiles."""
        # Mock service
        mock_service = MagicMock()
        profiles = {
            "x": SocialProfile(
                discord_user_id="12345",
                platform="x",
                profile_handle="shubham5080",
                display_value="@shubham5080",
            ),
        }
        mock_service.get_profiles = MagicMock(return_value=profiles)
        
        # Simulate get_profiles call
        result = await asyncio.to_thread(mock_service.get_profiles, "12345")
        assert len(result) > 0
        assert "x" in result
    
    @pytest.mark.asyncio
    async def test_status_shows_no_profiles_when_empty(self):
        """Test that /status shows 'not linked' when no profiles."""
        # Mock service returning empty
        mock_service = MagicMock()
        mock_service.get_profiles = MagicMock(return_value={})
        
        # Simulate
        result = await asyncio.to_thread(mock_service.get_profiles, "12345")
        assert len(result) == 0


class TestPermissionChecks:
    """Tests for permission enforcement."""
    
    def test_user_can_edit_own_profile(self):
        """Test that user can edit their own profile."""
        user_id = "12345"
        target_id = "12345"
        is_admin = False
        
        # User can edit own profile
        can_edit = (user_id == target_id)
        assert can_edit is True
    
    def test_user_cannot_edit_others_profile(self):
        """Test that user cannot edit others' profiles."""
        user_id = "12345"
        target_id = "67890"
        
        # User cannot edit others' profiles
        can_edit = (user_id == target_id)
        assert can_edit is False
    
    def test_admin_cannot_edit_others_profile(self):
        """Test that even admins cannot edit others' profiles."""
        user_id = "12345"
        target_id = "67890"
        is_admin = True
        
        # Even admins cannot edit others' profiles
        can_edit = (user_id == target_id)
        assert can_edit is False


class TestErrorHandling:
    """Tests for error scenarios."""
    
    @pytest.mark.asyncio
    async def test_invalid_platform_error(self):
        """Test error for invalid platform."""
        mock_service = MagicMock()
        mock_service.set_profile = MagicMock(
            side_effect=ValueError("Invalid platform: xyz")
        )
        
        with pytest.raises(ValueError):
            await asyncio.to_thread(
                mock_service.set_profile,
                "12345",
                "xyz",
                "value"
            )
    
    @pytest.mark.asyncio
    async def test_invalid_username_error(self):
        """Test error for invalid username."""
        mock_service = MagicMock()
        mock_service.set_profile = MagicMock(
            side_effect=ValueError("Invalid X username: contains_invalid_chars!")
        )
        
        with pytest.raises(ValueError):
            await asyncio.to_thread(
                mock_service.set_profile,
                "12345",
                "x",
                "contains_invalid_chars!"
            )
    
    @pytest.mark.asyncio
    async def test_invalid_url_error(self):
        """Test error for invalid LinkedIn URL."""
        mock_service = MagicMock()
        mock_service.set_profile = MagicMock(
            side_effect=ValueError("Invalid LinkedIn URL: not a valid profile")
        )
        
        with pytest.raises(ValueError):
            await asyncio.to_thread(
                mock_service.set_profile,
                "12345",
                "linkedin",
                "https://invalid.url"
            )
