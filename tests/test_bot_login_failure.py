"""Tests for friendly bot startup errors."""

from __future__ import annotations

from unittest.mock import patch

import discord
import pytest

from ghdcbot.bot import main


def test_bot_main_invalid_discord_token_exits_with_friendly_message(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with patch("ghdcbot.bot.run_bot", side_effect=discord.LoginFailure()):
        with pytest.raises(SystemExit) as excinfo:
            main("config/config.yaml")

    assert excinfo.value.code == 1
    err = capsys.readouterr().err
    assert "Invalid DISCORD_TOKEN." in err
    assert "Please update DISCORD_TOKEN in your .env file" in err
    assert "Traceback" not in err
