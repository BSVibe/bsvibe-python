"""Tests for bsvibe_alerts.dispatch_client.CentralDispatchClient.

The client wraps ``POST /api/alerts/dispatch`` on BSVibe-Auth. External
HTTP calls are mocked — tests NEVER hit auth.bsvibe.dev.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from bsvibe_alerts.dispatch_client import (
    CentralDispatchClient,
    CentralDispatchError,
    DeliveryDescriptor,
    DispatchResult,
)


def _response(payload: dict[str, Any], status: int = 200) -> MagicMock:
    response = MagicMock(spec=httpx.Response)
    response.status_code = status
    response.json.return_value = payload
    response.text = "" if not payload else "{...}"
    return response


def _err_response(status: int, body: str = "boom") -> MagicMock:
    response = MagicMock(spec=httpx.Response)
    response.status_code = status

    def _raise() -> None:
        raise ValueError("not json")

    response.json.side_effect = _raise
    response.text = body
    return response


VALID_PAYLOAD = {
    "event_id": "11111111-1111-1111-1111-111111111111",
    "event_type": "auth.session.failed",
    "tenant_id": "00000000-0000-0000-0000-0000000000aa",
    "severity": "critical",
    "matched_rules": 1,
    "deliveries": [
        {
            "rule_id": "22222222-2222-2222-2222-222222222222",
            "name": "brute-force",
            "channel": "telegram",
            "severity": "warning",
            "config": {"chat_id": "-100"},
            "enabled": True,
        }
    ],
}


class TestConstruction:
    def test_rejects_empty_auth_url(self) -> None:
        with pytest.raises(ValueError):
            CentralDispatchClient(auth_url="", service_token="t")

    def test_rejects_empty_service_token(self) -> None:
        with pytest.raises(ValueError):
            CentralDispatchClient(auth_url="https://x", service_token="")

    def test_resolves_dispatch_url_from_base(self) -> None:
        c = CentralDispatchClient(auth_url="https://auth.bsvibe.dev", service_token="t")
        assert c.dispatch_url == "https://auth.bsvibe.dev/api/alerts/dispatch"

    def test_keeps_dispatch_url_when_already_resolved(self) -> None:
        c = CentralDispatchClient(
            auth_url="https://auth.bsvibe.dev/api/alerts/dispatch",
            service_token="t",
        )
        assert c.dispatch_url == "https://auth.bsvibe.dev/api/alerts/dispatch"

    def test_strips_trailing_slash(self) -> None:
        c = CentralDispatchClient(auth_url="https://auth.bsvibe.dev/", service_token="t")
        assert c.dispatch_url == "https://auth.bsvibe.dev/api/alerts/dispatch"


class TestDispatch:
    async def test_posts_dict_payload_and_returns_dispatch_result(self) -> None:
        post = AsyncMock(return_value=_response(VALID_PAYLOAD))
        with patch("httpx.AsyncClient.post", post):
            client = CentralDispatchClient(auth_url="https://auth.bsvibe.dev", service_token="svc-jwt")
            event = {
                "event_id": "11111111-1111-1111-1111-111111111111",
                "event_type": "auth.session.failed",
                "occurred_at": "2026-04-28T00:00:00Z",
                "actor": {"type": "user", "id": "user-1"},
                "tenant_id": "00000000-0000-0000-0000-0000000000aa",
                "data": {"severity": "critical"},
            }
            result = await client.dispatch(event)
            await client.aclose()

        assert isinstance(result, DispatchResult)
        assert result.matched_rules == 1
        assert len(result.deliveries) == 1
        assert isinstance(result.deliveries[0], DeliveryDescriptor)
        assert result.deliveries[0].channel == "telegram"
        assert result.deliveries[0].config == {"chat_id": "-100"}

        # Verify outbound shape: URL + Authorization header + body.
        assert post.call_count == 1
        call = post.call_args
        assert call.args[0] == "https://auth.bsvibe.dev/api/alerts/dispatch"
        assert call.kwargs["json"] == event
        assert call.kwargs["headers"]["Authorization"] == "Bearer svc-jwt"

    async def test_accepts_pydantic_model(self) -> None:
        from pydantic import BaseModel

        class FakeAuditEvent(BaseModel):
            event_id: str
            event_type: str
            occurred_at: str
            actor: dict[str, str]
            tenant_id: str
            data: dict[str, Any]

        post = AsyncMock(return_value=_response(VALID_PAYLOAD))
        with patch("httpx.AsyncClient.post", post):
            client = CentralDispatchClient(auth_url="https://auth.bsvibe.dev", service_token="svc-jwt")
            event = FakeAuditEvent(
                event_id="11111111-1111-1111-1111-111111111111",
                event_type="auth.session.failed",
                occurred_at="2026-04-28T00:00:00Z",
                actor={"type": "user", "id": "u-1"},
                tenant_id="00000000-0000-0000-0000-0000000000aa",
                data={"severity": "critical"},
            )
            result = await client.dispatch(event)
            await client.aclose()

        assert result.matched_rules == 1
        body = post.call_args.kwargs["json"]
        assert body["event_id"] == "11111111-1111-1111-1111-111111111111"

    async def test_rejects_unsupported_event_type(self) -> None:
        client = CentralDispatchClient(auth_url="https://auth.bsvibe.dev", service_token="svc-jwt")
        with pytest.raises(TypeError):
            await client.dispatch(object())  # type: ignore[arg-type]
        await client.aclose()

    async def test_raises_retryable_on_5xx(self) -> None:
        post = AsyncMock(return_value=_err_response(503))
        with patch("httpx.AsyncClient.post", post):
            client = CentralDispatchClient(auth_url="https://auth.bsvibe.dev", service_token="svc-jwt")
            with pytest.raises(CentralDispatchError) as exc_info:
                await client.dispatch({"event_id": "x"})
            await client.aclose()
        assert exc_info.value.retryable is True
        assert exc_info.value.status == 503

    async def test_raises_non_retryable_on_4xx(self) -> None:
        post = AsyncMock(return_value=_err_response(403))
        with patch("httpx.AsyncClient.post", post):
            client = CentralDispatchClient(auth_url="https://auth.bsvibe.dev", service_token="svc-jwt")
            with pytest.raises(CentralDispatchError) as exc_info:
                await client.dispatch({"event_id": "x"})
            await client.aclose()
        assert exc_info.value.retryable is False
        assert exc_info.value.status == 403

    async def test_raises_retryable_on_network_error(self) -> None:
        post = AsyncMock(side_effect=httpx.ConnectError("boom"))
        with patch("httpx.AsyncClient.post", post):
            client = CentralDispatchClient(auth_url="https://auth.bsvibe.dev", service_token="svc-jwt")
            with pytest.raises(CentralDispatchError) as exc_info:
                await client.dispatch({"event_id": "x"})
            await client.aclose()
        assert exc_info.value.retryable is True
