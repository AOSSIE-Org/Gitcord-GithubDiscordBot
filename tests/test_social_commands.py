"""Tests for social connect/disconnect Discord command registration helpers."""

from __future__ import annotations

import inspect

from ghdcbot.adapters.discord import social_commands


def test_register_social_commands_defines_connect_and_disconnect() -> None:
    source = inspect.getsource(social_commands.register_social_commands)
    assert "connect-social" in source
    assert "disconnect-social" in source
    assert "social_service.set_profile" in source


def test_platform_choices_are_x_and_linkedin() -> None:
    values = {c.value for c in social_commands.PLATFORM_CHOICES}
    assert values == {"x", "linkedin"}


def test_no_oauth_references() -> None:
    source = inspect.getsource(social_commands)
    assert "oauth" not in source.lower()
    assert "Authorize" not in source


def test_old_profile_group_commands_removed() -> None:
    source = inspect.getsource(social_commands)
    assert 'name="profile"' not in source
    assert "profile set" not in source.lower()
    assert "profile_set_x" not in source
