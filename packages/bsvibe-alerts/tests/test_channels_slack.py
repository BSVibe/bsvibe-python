"""Tests for bsvibe_alerts.channels.slack.SlackChannel.

External HTTP calls are mocked. Slack incoming webhooks accept a
``{"text": "..."}`` body or full Block Kit; we send the simple text shape
because all four products today only need a one-line summary.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from bsvibe_alerts.channels.slack import SlackChannel
from bsvibe_alerts.types import Alert, AlertSeverity


def _ok_response() -> MagicMock:
    response = MagicMock(spec=httpx.Response)
    response.status_code = 200
    response.raise_for_status.return_value = None
    return response


class TestPayload:
    async def test_posts_to_webhook_url(self) -> None:
        post = AsyncMock(return_value=_ok_response())
        url = "https://hooks.slack.com/services/AAA/BBB/CCC"
        with patch("httpx.AsyncClient.post", post):
            channel = SlackChannel(webhook_url=url)
            await channel.send(
                Alert(
                    event="rate_limit_exceeded",
                    message="quota hit",
                    severity=AlertSeverity.WARNING,
                    context={"tenant_id": "t-1"},
                    service="bsgateway",
                )
            )

        assert post.call_count == 1
        assert post.call_args.args[0] == url
        body = post.call_args.kwargs["json"]
        assert "rate_limit_exceeded" in body["text"]
        assert "quota hit" in body["text"]
        assert "warning" in body["text"].lower()
        assert "bsgateway" in body["text"]

    async def test_includes_context(self) -> None:
        post = AsyncMock(return_value=_ok_response())
        with patch("httpx.AsyncClient.post", post):
            channel = SlackChannel(webhook_url="https://hooks.slack.com/x")
            await channel.send(
                Alert(
                    event="x",
                    message="m",
                    context={"k1": "v1", "k2": "v2"},
                )
            )

        text = post.call_args.kwargs["json"]["text"]
        assert "k1" in text and "v1" in text


class TestErrorHandling:
    async def test_http_error_raises(self) -> None:
        bad = MagicMock(spec=httpx.Response)
        bad.status_code = 500
        bad.raise_for_status.side_effect = httpx.HTTPStatusError("boom", request=MagicMock(), response=bad)
        post = AsyncMock(return_value=bad)
        with patch("httpx.AsyncClient.post", post):
            channel = SlackChannel(webhook_url="https://hooks.slack.com/x")
            with pytest.raises(httpx.HTTPStatusError):
                await channel.send(Alert(event="x", message="m"))


class TestChannelMetadata:
    def test_name(self) -> None:
        assert SlackChannel(webhook_url="https://hooks.slack.com/x").name == "slack"

    def test_requires_webhook_url(self) -> None:
        with pytest.raises(ValueError):
            SlackChannel(webhook_url="")
