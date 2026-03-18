"""Tests for role congratulatory messages."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from ghdcbot.adapters.storage.sqlite import SqliteStorage
from ghdcbot.core.models import ContributionEvent, Score
from ghdcbot.core.modes import MutationPolicy, RunMode
from ghdcbot.engine.orchestrator import apply_discord_roles
from ghdcbot.engine.scoring import WeightedScoreStrategy


def test_congratulation_sent_when_role_added(tmp_path) -> None:
    """Message sent when role is newly assigned."""
    storage = SqliteStorage(str(tmp_path))
    storage.init_schema()
    
    period_end = datetime.now(timezone.utc)
    period_start = period_end - timedelta(days=30)
    
    # Mock Discord writer with send_dm method
    mock_discord_writer = MagicMock()
    mock_discord_writer.add_role = MagicMock()
    mock_discord_writer.remove_role = MagicMock()
    mock_discord_writer.send_dm = MagicMock(return_value=True)
    
    scores = [
        Score(
            github_user="alice",
            period_start=period_start,
            period_end=period_end,
            points=10,  # Meets Contributor threshold
        ),
    ]
    
    from ghdcbot.config.models import IdentityMapping, RoleMappingConfig
    
    identity_mappings = [
        IdentityMapping(github_user="alice", discord_user_id="123"),
    ]
    
    role_mappings = [
        RoleMappingConfig(discord_role="Contributor", min_score=10),
    ]
    
    member_roles = {}  # User has no roles yet
    
    policy = MutationPolicy(
        mode=RunMode.ACTIVE,
        discord_write_allowed=True,
        github_write_allowed=False,
    )
    
    apply_discord_roles(
        discord_writer=mock_discord_writer,
        member_roles=member_roles,
        scores=scores,
        identity_mappings=identity_mappings,
        role_mappings=role_mappings,
        policy=policy,
    )
    
    # Role should be added
    mock_discord_writer.add_role.assert_called_once_with("123", "Contributor")
    
    # Congratulation message should be sent
    mock_discord_writer.send_dm.assert_called_once()
    call_args = mock_discord_writer.send_dm.call_args
    assert call_args[0][0] == "123"  # discord_user_id
    assert "Contributor" in call_args[0][1]  # message contains role name
    assert "alice" in call_args[0][1] or "<@123>" in call_args[0][1]  # mentions user


def test_no_message_in_dry_run(tmp_path) -> None:
    """No message sent in dry-run mode."""
    storage = SqliteStorage(str(tmp_path))
    storage.init_schema()
    
    period_end = datetime.now(timezone.utc)
    period_start = period_end - timedelta(days=30)
    
    mock_discord_writer = MagicMock()
    mock_discord_writer.add_role = MagicMock()
    mock_discord_writer.send_dm = MagicMock(return_value=True)
    
    scores = [
        Score(
            github_user="alice",
            period_start=period_start,
            period_end=period_end,
            points=10,
        ),
    ]
    
    from ghdcbot.config.models import IdentityMapping, RoleMappingConfig
    
    identity_mappings = [
        IdentityMapping(github_user="alice", discord_user_id="123"),
    ]
    
    role_mappings = [
        RoleMappingConfig(discord_role="Contributor", min_score=10),
    ]
    
    member_roles = {}
    
    policy = MutationPolicy(
        mode=RunMode.DRY_RUN,
        discord_write_allowed=False,  # Dry-run disables mutations
        github_write_allowed=False,
    )
    
    apply_discord_roles(
        discord_writer=mock_discord_writer,
        member_roles=member_roles,
        scores=scores,
        identity_mappings=identity_mappings,
        role_mappings=role_mappings,
        policy=policy,
    )
    
    # No role added (dry-run)
    mock_discord_writer.add_role.assert_not_called()
    
    # No message sent
    mock_discord_writer.send_dm.assert_not_called()


def test_no_message_in_observer_mode(tmp_path) -> None:
    """No message sent in observer mode."""
    storage = SqliteStorage(str(tmp_path))
    storage.init_schema()
    
    period_end = datetime.now(timezone.utc)
    period_start = period_end - timedelta(days=30)
    
    mock_discord_writer = MagicMock()
    mock_discord_writer.add_role = MagicMock()
    mock_discord_writer.send_dm = MagicMock(return_value=True)
    
    scores = [
        Score(
            github_user="alice",
            period_start=period_start,
            period_end=period_end,
            points=10,
        ),
    ]
    
    from ghdcbot.config.models import IdentityMapping, RoleMappingConfig
    
    identity_mappings = [
        IdentityMapping(github_user="alice", discord_user_id="123"),
    ]
    
    role_mappings = [
        RoleMappingConfig(discord_role="Contributor", min_score=10),
    ]
    
    member_roles = {}
    
    policy = MutationPolicy(
        mode=RunMode.OBSERVER,
        discord_write_allowed=False,  # Observer disables mutations
        github_write_allowed=False,
    )
    
    apply_discord_roles(
        discord_writer=mock_discord_writer,
        member_roles=member_roles,
        scores=scores,
        identity_mappings=identity_mappings,
        role_mappings=role_mappings,
        policy=policy,
    )
    
    # No message sent
    mock_discord_writer.send_dm.assert_not_called()


def test_no_message_on_role_removal(tmp_path) -> None:
    """No message sent when role is removed."""
    storage = SqliteStorage(str(tmp_path))
    storage.init_schema()
    
    period_end = datetime.now(timezone.utc)
    period_start = period_end - timedelta(days=30)
    
    mock_discord_writer = MagicMock()
    mock_discord_writer.add_role = MagicMock()
    mock_discord_writer.remove_role = MagicMock()
    mock_discord_writer.send_dm = MagicMock(return_value=True)
    
    scores = [
        Score(
            github_user="alice",
            period_start=period_start,
            period_end=period_end,
            points=5,  # Below Contributor threshold
        ),
    ]
    
    from ghdcbot.config.models import IdentityMapping, RoleMappingConfig
    
    identity_mappings = [
        IdentityMapping(github_user="alice", discord_user_id="123"),
    ]
    
    role_mappings = [
        RoleMappingConfig(discord_role="Contributor", min_score=10),
    ]
    
    member_roles = {"123": ["Contributor"]}  # User already has role
    
    policy = MutationPolicy(
        mode=RunMode.ACTIVE,
        discord_write_allowed=True,
        github_write_allowed=False,
    )
    
    apply_discord_roles(
        discord_writer=mock_discord_writer,
        member_roles=member_roles,
        scores=scores,
        identity_mappings=identity_mappings,
        role_mappings=role_mappings,
        policy=policy,
    )
    
    # Role should be removed
    mock_discord_writer.remove_role.assert_called_once_with("123", "Contributor")
    
    # No congratulation message (only for additions)
    mock_discord_writer.send_dm.assert_not_called()


def test_no_duplicate_message_on_second_run(tmp_path) -> None:
    """No duplicate message if role already exists."""
    storage = SqliteStorage(str(tmp_path))
    storage.init_schema()
    
    period_end = datetime.now(timezone.utc)
    period_start = period_end - timedelta(days=30)
    
    mock_discord_writer = MagicMock()
    mock_discord_writer.add_role = MagicMock()
    mock_discord_writer.send_dm = MagicMock(return_value=True)
    
    scores = [
        Score(
            github_user="alice",
            period_start=period_start,
            period_end=period_end,
            points=10,
        ),
    ]
    
    from ghdcbot.config.models import IdentityMapping, RoleMappingConfig
    
    identity_mappings = [
        IdentityMapping(github_user="alice", discord_user_id="123"),
    ]
    
    role_mappings = [
        RoleMappingConfig(discord_role="Contributor", min_score=10),
    ]
    
    # User already has the role (second run scenario)
    member_roles = {"123": ["Contributor"]}
    
    policy = MutationPolicy(
        mode=RunMode.ACTIVE,
        discord_write_allowed=True,
        github_write_allowed=False,
    )
    
    apply_discord_roles(
        discord_writer=mock_discord_writer,
        member_roles=member_roles,
        scores=scores,
        identity_mappings=identity_mappings,
        role_mappings=role_mappings,
        policy=policy,
    )
    
    # No role added (already exists)
    mock_discord_writer.add_role.assert_not_called()
    
    # No message sent (role already existed)
    mock_discord_writer.send_dm.assert_not_called()


def test_dm_failure_does_not_crash(tmp_path) -> None:
    """Failure to send DM does not crash the run."""
    storage = SqliteStorage(str(tmp_path))
    storage.init_schema()
    
    period_end = datetime.now(timezone.utc)
    period_start = period_end - timedelta(days=30)
    
    mock_discord_writer = MagicMock()
    mock_discord_writer.add_role = MagicMock()
    mock_discord_writer.send_dm = MagicMock(return_value=False)  # DM fails
    
    scores = [
        Score(
            github_user="alice",
            period_start=period_start,
            period_end=period_end,
            points=10,
        ),
    ]
    
    from ghdcbot.config.models import IdentityMapping, RoleMappingConfig
    
    identity_mappings = [
        IdentityMapping(github_user="alice", discord_user_id="123"),
    ]
    
    role_mappings = [
        RoleMappingConfig(discord_role="Contributor", min_score=10),
    ]
    
    member_roles = {}
    
    policy = MutationPolicy(
        mode=RunMode.ACTIVE,
        discord_write_allowed=True,
        github_write_allowed=False,
    )
    
    # Should not raise exception
    apply_discord_roles(
        discord_writer=mock_discord_writer,
        member_roles=member_roles,
        scores=scores,
        identity_mappings=identity_mappings,
        role_mappings=role_mappings,
        policy=policy,
    )
    
    # Role should still be added
    mock_discord_writer.add_role.assert_called_once()
    
    # DM attempted but failed gracefully
    mock_discord_writer.send_dm.assert_called_once()


def test_no_message_if_send_dm_not_available(tmp_path) -> None:
    """No message sent if discord_writer doesn't support send_dm."""
    storage = SqliteStorage(str(tmp_path))
    storage.init_schema()
    
    period_end = datetime.now(timezone.utc)
    period_start = period_end - timedelta(days=30)
    
    # Create a simple mock object without send_dm
    class MockWriter:
        def add_role(self, user_id: str, role: str) -> None:
            pass
        
        def remove_role(self, user_id: str, role: str) -> None:
            pass
    
    mock_discord_writer = MockWriter()
    call_count = {"add": 0}
    
    def track_add(user_id: str, role: str) -> None:
        call_count["add"] += 1
    
    mock_discord_writer.add_role = track_add
    
    scores = [
        Score(
            github_user="alice",
            period_start=period_start,
            period_end=period_end,
            points=10,
        ),
    ]
    
    from ghdcbot.config.models import IdentityMapping, RoleMappingConfig
    
    identity_mappings = [
        IdentityMapping(github_user="alice", discord_user_id="123"),
    ]
    
    role_mappings = [
        RoleMappingConfig(discord_role="Contributor", min_score=10),
    ]
    
    member_roles = {}
    
    policy = MutationPolicy(
        mode=RunMode.ACTIVE,
        discord_write_allowed=True,
        github_write_allowed=False,
    )
    
    # Should not raise exception (getattr will return None for send_dm)
    apply_discord_roles(
        discord_writer=mock_discord_writer,
        member_roles=member_roles,
        scores=scores,
        identity_mappings=identity_mappings,
        role_mappings=role_mappings,
        policy=policy,
    )
    
    # Role should still be added
    assert call_count["add"] == 1
    
    # send_dm should not exist on this writer
    assert not hasattr(mock_discord_writer, "send_dm")
