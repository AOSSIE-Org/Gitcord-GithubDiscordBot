"""Tests for handle_app_command_error handler in bot.py.

Verifies that all three error branches (CommandOnCooldown, CheckFailure,
generic Exception) correctly dispatch user-visible feedback via
interaction.followup.send() when the interaction has been deferred, and
via interaction.response.send_message() when it has not.

Directly imports and tests the production `handle_app_command_error`
from `ghdcbot.bot`.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from discord import app_commands

from ghdcbot.bot import handle_app_command_error

# ---------------------------------------------------------------------------
# Fakes -- mirrors _FakeFollowup / _FakeResponse / _FakeInteraction used
# across Gitcord tests, extended with send_message tracking and is_done() state.
# ---------------------------------------------------------------------------


class _FakeFollowup:
    """Records messages sent via interaction.followup.send()."""

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def send(self, content: str | None = None, **kwargs: Any) -> None:
        self.messages.append({"content": content, **kwargs})


class _FakeResponse:
    """Tracks whether the interaction was deferred and records send_message calls."""

    def __init__(self, *, deferred: bool = False) -> None:
        self._deferred = deferred
        self.sent_messages: list[dict[str, Any]] = []

    def is_done(self) -> bool:
        return self._deferred

    async def defer(self, *, ephemeral: bool = False) -> None:
        self._deferred = True

    async def send_message(self, content: str, **kwargs: Any) -> None:
        self.sent_messages.append({"content": content, **kwargs})


class _FakeInteraction:
    """Minimal interaction fake with configurable deferred state."""

    def __init__(self, *, deferred: bool = False) -> None:
        self.response = _FakeResponse(deferred=deferred)
        self.followup = _FakeFollowup()
        self.user = MagicMock(name="fakeuser", id=12345)
        self.user.name = "fakeuser"
        self.command = MagicMock(name="fakecommand")
        self.command.name = "test-cmd"


# ---------------------------------------------------------------------------
# Helper to create discord.py error objects
# ---------------------------------------------------------------------------


def _cooldown_error(retry_after: float = 9.0) -> app_commands.CommandOnCooldown:
    """Create a CommandOnCooldown error with the given retry_after value."""
    cooldown = MagicMock()
    cooldown.per = 30.0
    cooldown.rate = 1
    return app_commands.CommandOnCooldown(cooldown, retry_after)


def _check_failure() -> app_commands.CheckFailure:
    """Create a generic CheckFailure error."""
    return app_commands.CheckFailure("Permission check failed")


def _generic_error() -> app_commands.AppCommandError:
    """Create a generic AppCommandError (not cooldown, not check failure)."""
    return app_commands.AppCommandError("Something went wrong internally")


# ===================================================================
# Tests for the GENERAL / GENERIC error branch (the main bug fix)
# ===================================================================


class TestGenericErrorHandler:
    """Tests for the else branch -- the primary bug that was fixed."""

    @pytest.mark.asyncio
    async def test_deferred_sends_followup(self) -> None:
        """When the interaction was deferred, the error message must go
        through followup.send() instead of response.send_message()."""
        interaction = _FakeInteraction(deferred=True)

        await handle_app_command_error(interaction, _generic_error())  # type: ignore[arg-type]

        # followup.send must have been called exactly once
        assert len(interaction.followup.messages) == 1
        # response.send_message must NOT have been called
        assert len(interaction.response.sent_messages) == 0

    @pytest.mark.asyncio
    async def test_not_deferred_sends_response(self) -> None:
        """When the interaction was NOT deferred, the error message must go
        through response.send_message()."""
        interaction = _FakeInteraction(deferred=False)

        await handle_app_command_error(interaction, _generic_error())  # type: ignore[arg-type]

        # response.send_message must have been called exactly once
        assert len(interaction.response.sent_messages) == 1
        # followup.send must NOT have been called
        assert len(interaction.followup.messages) == 0

    @pytest.mark.asyncio
    async def test_message_is_ephemeral(self) -> None:
        """The error message must always be ephemeral so only the
        invoking user sees it."""
        # Test deferred path
        interaction = _FakeInteraction(deferred=True)
        await handle_app_command_error(interaction, _generic_error())  # type: ignore[arg-type]
        assert interaction.followup.messages[0]["ephemeral"] is True

        # Test non-deferred path
        interaction = _FakeInteraction(deferred=False)
        await handle_app_command_error(interaction, _generic_error())  # type: ignore[arg-type]
        assert interaction.response.sent_messages[0]["ephemeral"] is True

    @pytest.mark.asyncio
    async def test_message_content(self) -> None:
        """The error message must contain a user-friendly error string."""
        interaction = _FakeInteraction(deferred=True)
        await handle_app_command_error(interaction, _generic_error())  # type: ignore[arg-type]
        content = interaction.followup.messages[0]["content"]
        assert "unexpected error" in content.lower()

    @pytest.mark.asyncio
    async def test_followup_failure_does_not_crash(self, caplog: pytest.LogCaptureFixture) -> None:
        """If followup.send() itself raises, the handler must log the
        error and not propagate the exception."""
        interaction = _FakeInteraction(deferred=True)

        # Make followup.send raise
        async def _raise(**kwargs: Any) -> None:
            raise RuntimeError("Discord API unavailable")

        interaction.followup.send = _raise  # type: ignore[assignment]

        # Must not raise
        await handle_app_command_error(interaction, _generic_error())  # type: ignore[arg-type]

        # Verify the error was logged
        assert any(
            "Could not send error message" in rec.message
            for rec in caplog.records
        )


# ===================================================================
# Tests for the COOLDOWN error branch
# ===================================================================


class TestCooldownErrorHandler:
    """Tests for the CommandOnCooldown branch."""

    @pytest.mark.asyncio
    async def test_deferred_sends_followup(self) -> None:
        """Cooldown message must use followup.send() when deferred."""
        interaction = _FakeInteraction(deferred=True)

        await handle_app_command_error(interaction, _cooldown_error(9.0))  # type: ignore[arg-type]

        assert len(interaction.followup.messages) == 1
        assert len(interaction.response.sent_messages) == 0

    @pytest.mark.asyncio
    async def test_not_deferred_sends_response(self) -> None:
        """Cooldown message must use response.send_message() when not deferred."""
        interaction = _FakeInteraction(deferred=False)

        await handle_app_command_error(interaction, _cooldown_error(9.0))  # type: ignore[arg-type]

        assert len(interaction.response.sent_messages) == 1
        assert len(interaction.followup.messages) == 0

    @pytest.mark.asyncio
    async def test_message_contains_retry_time(self) -> None:
        """The cooldown message must include the retry-after seconds
        (rounded up by 1) and preserve the ⏳ emoji from production."""
        interaction = _FakeInteraction(deferred=True)

        await handle_app_command_error(interaction, _cooldown_error(9.0))  # type: ignore[arg-type]

        content = interaction.followup.messages[0]["content"]
        # retry_after = int(9.0) + 1 = 10
        assert "10s" in content
        assert "⏳" in content

    @pytest.mark.asyncio
    async def test_cooldown_is_ephemeral(self) -> None:
        """Cooldown messages must always be ephemeral."""
        interaction = _FakeInteraction(deferred=True)
        await handle_app_command_error(interaction, _cooldown_error(5.0))  # type: ignore[arg-type]
        assert interaction.followup.messages[0]["ephemeral"] is True


# ===================================================================
# Tests for the CHECK FAILURE error branch
# ===================================================================


class TestCheckFailureErrorHandler:
    """Tests for the CheckFailure branch, including its inner retry."""

    @pytest.mark.asyncio
    async def test_deferred_sends_followup(self) -> None:
        """Permission denied message must use followup.send() when deferred."""
        interaction = _FakeInteraction(deferred=True)

        await handle_app_command_error(interaction, _check_failure())  # type: ignore[arg-type]

        assert len(interaction.followup.messages) == 1
        assert len(interaction.response.sent_messages) == 0

    @pytest.mark.asyncio
    async def test_not_deferred_sends_response(self) -> None:
        """Permission denied message must use response.send_message()
        when not deferred."""
        interaction = _FakeInteraction(deferred=False)

        await handle_app_command_error(interaction, _check_failure())  # type: ignore[arg-type]

        assert len(interaction.response.sent_messages) == 1
        assert len(interaction.followup.messages) == 0

    @pytest.mark.asyncio
    async def test_check_failure_is_ephemeral(self) -> None:
        """Permission denied messages must always be ephemeral."""
        interaction = _FakeInteraction(deferred=True)
        await handle_app_command_error(interaction, _check_failure())  # type: ignore[arg-type]
        assert interaction.followup.messages[0]["ephemeral"] is True

    @pytest.mark.asyncio
    async def test_retry_deferred_sends_followup(self) -> None:
        """If the primary CheckFailure handler throws AND the interaction
        was deferred, the retry must use followup.send()."""
        interaction = _FakeInteraction(deferred=True)

        # Patch format_slash_command_permission_denied to raise in bot.py
        with patch("ghdcbot.bot.format_slash_command_permission_denied", side_effect=RuntimeError("boom")):
            await handle_app_command_error(interaction, _check_failure(), config=MagicMock())  # type: ignore[arg-type]

        # The retry should have sent a simple fallback message via followup
        assert len(interaction.followup.messages) == 1
        content = interaction.followup.messages[0]["content"]
        assert "permission" in content.lower()

    @pytest.mark.asyncio
    async def test_retry_not_deferred_sends_response(self) -> None:
        """If the primary CheckFailure handler throws AND the interaction
        was NOT deferred, the retry must use response.send_message()."""
        interaction = _FakeInteraction(deferred=False)

        with patch("ghdcbot.bot.format_slash_command_permission_denied", side_effect=RuntimeError("boom")):
            await handle_app_command_error(interaction, _check_failure(), config=MagicMock())  # type: ignore[arg-type]

        # The retry should have sent via response.send_message
        assert len(interaction.response.sent_messages) == 1
        content = interaction.response.sent_messages[0]["content"]
        assert "permission" in content.lower()

    @pytest.mark.asyncio
    async def test_uses_command_name(self) -> None:
        """The handler must pass interaction.command.name to format function."""
        interaction = _FakeInteraction(deferred=False)
        interaction.command.name = "sync"

        mock_format = MagicMock(return_value="Denied: sync")
        with patch("ghdcbot.bot.format_slash_command_permission_denied", mock_format):
            await handle_app_command_error(interaction, _check_failure(), config=MagicMock())  # type: ignore[arg-type]

        mock_format.assert_called_once()
        assert mock_format.call_args[0][1] == "sync"
