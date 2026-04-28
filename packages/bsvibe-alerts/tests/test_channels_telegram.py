"""Tests for bsvibe_alerts.channels.telegram.TelegramChannel.

External HTTP calls are mocked — tests never hit api.telegram.org.

Wire format (locked):

* URL: ``https://api.telegram.org/bot<token>/sendMessage``
* JSON body: ``{"chat_id": ..., "text": ..., "parse_mode": "Markdown"}``
* Channel name: ``"telegram"``
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from bsvibe_alerts.channels.telegram import TelegramChannel
from bsvibe_alerts.types import Alert, AlertSeverity


def _ok_response() -> MagicMock:
    response = MagicMock(spec=httpx.Response)
    response.status_code = 200
    response.raise_for_status.return_value = None
    return response


class TestPayload:
    async def test_calls_telegram_api_with_correct_payload(self) -> None:
        post = AsyncMock(return_value=_ok_response())
        with patch("httpx.AsyncClient.post", post):
            channel = TelegramChannel(bot_token="abc-token", chat_id="42")
            alert = Alert(
                event="task_failed",
                message="executor crashed",
                severity=AlertSeverity.CRITICAL,
                context={"task_id": "t-1"},
                service="bsnexus",
            )
            await channel.send(alert)

        assert post.call_count == 1
        call = post.call_args
        assert call.args[0] == "https://api.telegram.org/botabc-token/sendMessage"
        body = call.kwargs["json"]
        assert body["chat_id"] == "42"
        assert "executor crashed" in body["text"]
        assert "task_failed" in body["text"]
        assert "critical" in body["text"].lower()
        assert body["parse_mode"] == "Markdown"

    async def test_message_includes_context_keys(self) -> None:
        post = AsyncMock(return_value=_ok_response())
        with patch("httpx.AsyncClient.post", post):
            channel = TelegramChannel(bot_token="t", chat_id="1")
            alert = Alert(
                event="x",
                message="m",
                context={"tenant_id": "T-9", "request_id": "r-1"},
            )
            await channel.send(alert)

        text = post.call_args.kwargs["json"]["text"]
        assert "tenant_id" in text
        assert "T-9" in text


class TestErrorHandling:
    async def test_http_error_raises(self) -> None:
        bad = MagicMock(spec=httpx.Response)
        bad.status_code = 500
        bad.raise_for_status.side_effect = httpx.HTTPStatusError("boom", request=MagicMock(), response=bad)
        post = AsyncMock(return_value=bad)
        with patch("httpx.AsyncClient.post", post):
            channel = TelegramChannel(bot_token="t", chat_id="1")
            with pytest.raises(httpx.HTTPStatusError):
                await channel.send(Alert(event="x", message="m"))

    async def test_network_error_raises(self) -> None:
        post = AsyncMock(side_effect=httpx.ConnectError("net down"))
        with patch("httpx.AsyncClient.post", post):
            channel = TelegramChannel(bot_token="t", chat_id="1")
            with pytest.raises(httpx.ConnectError):
                await channel.send(Alert(event="x", message="m"))


class TestChannelMetadata:
    def test_name(self) -> None:
        assert TelegramChannel(bot_token="t", chat_id="1").name == "telegram"

    def test_requires_token_and_chat_id(self) -> None:
        with pytest.raises(ValueError):
            TelegramChannel(bot_token="", chat_id="1")
        with pytest.raises(ValueError):
            TelegramChannel(bot_token="t", chat_id="")
