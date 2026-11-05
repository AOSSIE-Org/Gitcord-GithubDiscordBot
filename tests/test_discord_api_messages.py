"""Direct HTTP-path tests for DiscordApiAdapter.create_message / edit_message."""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from ghdcbot.adapters.discord.api import DiscordApiAdapter


def _adapter() -> tuple[DiscordApiAdapter, MagicMock]:
    adapter = DiscordApiAdapter(token="test-token", guild_id="guild-1")
    mock_client = MagicMock()
    adapter._client = mock_client
    return adapter, mock_client


def test_create_message_empty_content_noop() -> None:
    adapter, mock_client = _adapter()
    assert adapter.create_message("chan-1", "") == ""
    mock_client.request.assert_not_called()


def test_create_message_success_returns_message_id() -> None:
    adapter, mock_client = _adapter()
    mock_client.request.return_value = httpx.Response(
        200, json={"id": "msg-42"}, request=httpx.Request("POST", "https://discord.com")
    )

    message_id = adapter.create_message("chan-1", "hello")

    assert message_id == "msg-42"
    mock_client.request.assert_called_once()
    method, path = mock_client.request.call_args.args[:2]
    assert method == "POST"
    assert path == "/channels/chan-1/messages"
    payload = mock_client.request.call_args.kwargs["json"]
    assert payload["content"] == "hello"
    assert payload["flags"] == 4  # SUPPRESS_EMBEDS for plain text


def test_create_message_with_embeds_skips_suppress_flag() -> None:
    adapter, mock_client = _adapter()
    mock_client.request.return_value = httpx.Response(
        201, json={"id": "msg-9"}, request=httpx.Request("POST", "https://discord.com")
    )
    embeds = [{"title": "Merged", "color": 0x8250DF}]

    message_id = adapter.create_message("chan-1", "", embeds=embeds)

    assert message_id == "msg-9"
    payload = mock_client.request.call_args.kwargs["json"]
    assert payload["embeds"] == embeds
    assert "flags" not in payload
    assert "content" not in payload


def test_create_message_truncates_at_2000_chars() -> None:
    adapter, mock_client = _adapter()
    mock_client.request.return_value = httpx.Response(
        200, json={"id": "msg-1"}, request=httpx.Request("POST", "https://discord.com")
    )
    long_text = "a" * 2500

    adapter.create_message("chan-1", long_text)

    payload = mock_client.request.call_args.kwargs["json"]
    assert len(payload["content"]) == 2000


def test_create_message_http_error_returns_none() -> None:
    adapter, mock_client = _adapter()
    mock_client.request.side_effect = httpx.ConnectError("boom")

    assert adapter.create_message("chan-1", "hello") is None


@pytest.mark.parametrize("status_code", [400, 403, 500])
def test_create_message_non_success_status_returns_none(status_code: int) -> None:
    adapter, mock_client = _adapter()
    mock_client.request.return_value = httpx.Response(
        status_code, request=httpx.Request("POST", "https://discord.com")
    )

    assert adapter.create_message("chan-1", "hello") is None


def test_create_message_malformed_json_returns_none() -> None:
    adapter, mock_client = _adapter()
    mock_client.request.return_value = httpx.Response(
        200, text="not-json", request=httpx.Request("POST", "https://discord.com")
    )

    assert adapter.create_message("chan-1", "hello") is None


def test_create_message_missing_id_returns_none() -> None:
    adapter, mock_client = _adapter()
    mock_client.request.return_value = httpx.Response(
        200, json={"content": "ok"}, request=httpx.Request("POST", "https://discord.com")
    )

    assert adapter.create_message("chan-1", "hello") is None


def test_send_message_delegates_to_create_message() -> None:
    adapter, mock_client = _adapter()
    mock_client.request.return_value = httpx.Response(
        200, json={"id": "msg-1"}, request=httpx.Request("POST", "https://discord.com")
    )

    assert adapter.send_message("chan-1", "hi") is True
    mock_client.request.assert_called_once()


def test_edit_message_success() -> None:
    adapter, mock_client = _adapter()
    mock_client.request.return_value = httpx.Response(
        200, json={"id": "msg-9"}, request=httpx.Request("PATCH", "https://discord.com")
    )

    assert adapter.edit_message("chan-1", "msg-9", "updated") is True
    method, path = mock_client.request.call_args.args[:2]
    assert method == "PATCH"
    assert path == "/channels/chan-1/messages/msg-9"
    payload = mock_client.request.call_args.kwargs["json"]
    assert payload["content"] == "updated"
    assert payload["flags"] == 4
    assert payload["allowed_mentions"] == {"parse": ["users"]}


def test_edit_message_with_embeds_clears_suppress_flag() -> None:
    adapter, mock_client = _adapter()
    mock_client.request.return_value = httpx.Response(
        200, json={"id": "msg-9"}, request=httpx.Request("PATCH", "https://discord.com")
    )
    embeds = [{"title": "Merged", "color": 0x8250DF}]

    assert adapter.edit_message("chan-1", "msg-9", "", embeds=embeds) is True
    payload = mock_client.request.call_args.kwargs["json"]
    assert payload["content"] == ""
    assert payload["embeds"] == embeds
    assert payload["flags"] == 0
    assert payload["allowed_mentions"] == {"parse": ["users"]}


def test_edit_message_truncates_at_2000_chars() -> None:
    adapter, mock_client = _adapter()
    mock_client.request.return_value = httpx.Response(
        200, json={"id": "msg-9"}, request=httpx.Request("PATCH", "https://discord.com")
    )

    assert adapter.edit_message("chan-1", "msg-9", "b" * 2500) is True
    payload = mock_client.request.call_args.kwargs["json"]
    assert len(payload["content"]) == 2000


def test_edit_message_missing_ids_returns_false() -> None:
    adapter, mock_client = _adapter()
    assert adapter.edit_message("", "msg-9", "x") is False
    assert adapter.edit_message("chan-1", "", "x") is False
    mock_client.request.assert_not_called()


def test_edit_message_http_error_returns_false() -> None:
    adapter, mock_client = _adapter()
    mock_client.request.side_effect = httpx.TimeoutException("timeout")

    assert adapter.edit_message("chan-1", "msg-9", "x") is False


@pytest.mark.parametrize("status_code", [400, 404, 500])
def test_edit_message_non_success_status_returns_false(status_code: int) -> None:
    adapter, mock_client = _adapter()
    mock_client.request.return_value = httpx.Response(
        status_code, request=httpx.Request("PATCH", "https://discord.com")
    )

    assert adapter.edit_message("chan-1", "msg-9", "x") is False


def test_create_dm_message_returns_channel_and_message_ids() -> None:
    adapter, mock_client = _adapter()
    mock_client.request.side_effect = [
        httpx.Response(
            200,
            json={"id": "dm-chan-1"},
            request=httpx.Request("POST", "https://discord.com"),
        ),
        httpx.Response(
            200,
            json={"id": "msg-77"},
            request=httpx.Request("POST", "https://discord.com"),
        ),
    ]
    embeds = [{"title": "🟣 Merged", "description": "PR #7"}]

    result = adapter.create_dm_message("user-1", "", embeds=embeds)

    assert result == ("dm-chan-1", "msg-77")
    payload = mock_client.request.call_args.kwargs["json"]
    assert payload["embeds"] == embeds
    assert "flags" not in payload


def test_create_dm_message_empty_content_and_embeds_is_noop() -> None:
    adapter, mock_client = _adapter()

    assert adapter.create_dm_message("user-1", "") is None
    mock_client.request.assert_not_called()


def test_create_dm_message_channel_creation_failure_returns_none() -> None:
    adapter, mock_client = _adapter()
    mock_client.request.return_value = httpx.Response(
        403, request=httpx.Request("POST", "https://discord.com")
    )

    assert adapter.create_dm_message("user-1", "hello") is None


def test_delete_message_returns_true_on_success_and_404() -> None:
    adapter, mock_client = _adapter()
    mock_client.request.return_value = httpx.Response(
        204, request=httpx.Request("DELETE", "https://discord.com")
    )

    assert adapter.delete_message("dm-chan-1", "msg-77") is True
    method, path = mock_client.request.call_args.args[:2]
    assert (method, path) == ("DELETE", "/channels/dm-chan-1/messages/msg-77")

    # A message the user already deleted is treated as cleaned up.
    mock_client.request.return_value = httpx.Response(
        404, request=httpx.Request("DELETE", "https://discord.com")
    )
    assert adapter.delete_message("dm-chan-1", "msg-77") is True
