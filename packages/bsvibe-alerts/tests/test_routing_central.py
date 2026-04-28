"""Tests for bsvibe_alerts.routing.CentralAlertRouter.

The router calls BSVibe-Auth ``POST /api/alerts/dispatch`` to resolve
which channels should fire for an alert. External HTTP is mocked.

Failure isolation is the key contract: when the auth-app is unreachable
or returns a non-2xx, the router falls back to the local
:class:`AlertRouter` (or its baked-in defaults) so producers never crash.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from bsvibe_alerts.routing import AlertRouter, CentralAlertRouter
from bsvibe_alerts.types import Alert, AlertSeverity


def _response(payload: dict[str, Any], status: int = 200) -> MagicMock:
    response = MagicMock(spec=httpx.Response)
    response.status_code = status
    response.json.return_value = payload
    response.text = "{...}"
    return response


def _err_response(status: int) -> MagicMock:
    response = MagicMock(spec=httpx.Response)
    response.status_code = status

    def _raise() -> None:
        raise ValueError("not json")

    response.json.side_effect = _raise
    response.text = "boom"
    return response


CRITICAL_ALERT = Alert(
    event="task_failed",
    message="executor crashed",
    severity=AlertSeverity.CRITICAL,
    context={"tenant_id": "00000000-0000-0000-0000-0000000000aa"},
    service="bsnexus",
)


class TestConstruction:
    def test_rejects_empty_url(self) -> None:
        with pytest.raises(ValueError):
            CentralAlertRouter(auth_url="", service_token="t")

    def test_rejects_empty_token(self) -> None:
        with pytest.raises(ValueError):
            CentralAlertRouter(auth_url="https://x", service_token="")


class TestAsyncResolution:
    async def test_returns_channel_names_from_dispatch(self) -> None:
        payload = {
            "event_id": "x",
            "event_type": "alerts.task_failed",
            "tenant_id": "00000000-0000-0000-0000-0000000000aa",
            "severity": "critical",
            "matched_rules": 2,
            "deliveries": [
                {
                    "rule_id": "r1",
                    "name": "n1",
                    "channel": "telegram",
                    "severity": "warning",
                    "config": {},
                    "enabled": True,
                },
                {
                    "rule_id": "r2",
                    "name": "n2",
                    "channel": "structlog",
                    "severity": "info",
                    "config": {},
                    "enabled": True,
                },
            ],
        }
        post = AsyncMock(return_value=_response(payload))
        with patch("httpx.AsyncClient.post", post):
            router = CentralAlertRouter(auth_url="https://auth.bsvibe.dev", service_token="svc-jwt")
            names = await router.channels_for_async(CRITICAL_ALERT)
            await router.aclose()
        assert names == ["telegram", "structlog"]

    async def test_dedupes_channel_names(self) -> None:
        payload = {
            "event_id": "x",
            "event_type": "alerts.task_failed",
            "tenant_id": "t",
            "severity": "critical",
            "matched_rules": 2,
            "deliveries": [
                {
                    "rule_id": "r1",
                    "name": "n1",
                    "channel": "slack",
                    "severity": "warning",
                    "config": {},
                    "enabled": True,
                },
                {
                    "rule_id": "r2",
                    "name": "n2",
                    "channel": "slack",
                    "severity": "warning",
                    "config": {},
                    "enabled": True,
                },
            ],
        }
        post = AsyncMock(return_value=_response(payload))
        with patch("httpx.AsyncClient.post", post):
            router = CentralAlertRouter(auth_url="https://auth.bsvibe.dev", service_token="svc-jwt")
            names = await router.channels_for_async(CRITICAL_ALERT)
            await router.aclose()
        assert names == ["slack"]

    async def test_zero_matches_falls_back_to_local(self) -> None:
        payload = {
            "event_id": "x",
            "event_type": "alerts.task_failed",
            "tenant_id": "t",
            "severity": "critical",
            "matched_rules": 0,
            "deliveries": [],
        }
        post = AsyncMock(return_value=_response(payload))
        with patch("httpx.AsyncClient.post", post):
            router = CentralAlertRouter(
                auth_url="https://auth.bsvibe.dev",
                service_token="svc-jwt",
                fallback=AlertRouter.from_defaults(),
            )
            names = await router.channels_for_async(CRITICAL_ALERT)
            await router.aclose()
        # Default critical → ["structlog", "slack", "telegram"]
        assert names == ["structlog", "slack", "telegram"]

    async def test_5xx_falls_back_to_local(self) -> None:
        post = AsyncMock(return_value=_err_response(503))
        with patch("httpx.AsyncClient.post", post):
            router = CentralAlertRouter(
                auth_url="https://auth.bsvibe.dev",
                service_token="svc-jwt",
            )
            names = await router.channels_for_async(CRITICAL_ALERT)
            await router.aclose()
        assert "structlog" in names

    async def test_network_error_falls_back_to_local(self) -> None:
        post = AsyncMock(side_effect=httpx.ConnectError("boom"))
        with patch("httpx.AsyncClient.post", post):
            router = CentralAlertRouter(
                auth_url="https://auth.bsvibe.dev",
                service_token="svc-jwt",
            )
            names = await router.channels_for_async(CRITICAL_ALERT)
            await router.aclose()
        assert "structlog" in names

    async def test_uses_provided_http_client(self) -> None:
        # Ensure the router does NOT close an http client it does not own.
        post = AsyncMock(
            return_value=_response(
                {
                    "event_id": "x",
                    "event_type": "alerts.task_failed",
                    "tenant_id": "t",
                    "severity": "critical",
                    "matched_rules": 1,
                    "deliveries": [
                        {
                            "rule_id": "r1",
                            "name": "n1",
                            "channel": "telegram",
                            "severity": "info",
                            "config": {},
                            "enabled": True,
                        }
                    ],
                }
            )
        )
        http = MagicMock(spec=httpx.AsyncClient)
        http.post = post
        http.aclose = AsyncMock()
        router = CentralAlertRouter(
            auth_url="https://auth.bsvibe.dev",
            service_token="svc-jwt",
            http=http,
        )
        names = await router.channels_for_async(CRITICAL_ALERT)
        await router.aclose()
        assert names == ["telegram"]
        # Should NOT close caller-provided client.
        http.aclose.assert_not_called()


class TestEnvelopeShape:
    async def test_dispatch_payload_uses_auditeventbase_shape(self) -> None:
        captured: dict[str, Any] = {}

        async def _capture(self: Any, url: str, *, json: dict[str, Any], headers: dict[str, str]) -> MagicMock:
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return _response(
                {
                    "event_id": "x",
                    "event_type": "alerts.task_failed",
                    "tenant_id": "t",
                    "severity": "critical",
                    "matched_rules": 1,
                    "deliveries": [
                        {
                            "rule_id": "r1",
                            "name": "n1",
                            "channel": "structlog",
                            "severity": "info",
                            "config": {},
                            "enabled": True,
                        }
                    ],
                }
            )

        with patch("httpx.AsyncClient.post", new=_capture):
            router = CentralAlertRouter(
                auth_url="https://auth.bsvibe.dev",
                service_token="svc-jwt",
            )
            await router.channels_for_async(CRITICAL_ALERT)
            await router.aclose()

        env = captured["json"]
        # AuditEventBase wire fields.
        assert "event_id" in env
        assert env["event_type"].startswith("alerts.") or "." in env["event_type"]
        assert env["actor"]["type"] == "system"
        assert env["tenant_id"] == "00000000-0000-0000-0000-0000000000aa"
        assert env["data"]["severity"] == "critical"
        assert env["data"]["message"] == "executor crashed"
        # Authorization bearer.
        assert captured["headers"]["Authorization"] == "Bearer svc-jwt"


class TestSyncBridge:
    def test_sync_call_outside_loop_runs_dispatch(self) -> None:
        # When invoked outside an event loop, channels_for() must execute
        # the async resolver via asyncio.run.
        payload = {
            "event_id": "x",
            "event_type": "alerts.task_failed",
            "tenant_id": "t",
            "severity": "critical",
            "matched_rules": 1,
            "deliveries": [
                {
                    "rule_id": "r1",
                    "name": "n1",
                    "channel": "telegram",
                    "severity": "info",
                    "config": {},
                    "enabled": True,
                }
            ],
        }
        post = AsyncMock(return_value=_response(payload))
        with patch("httpx.AsyncClient.post", post):
            router = CentralAlertRouter(
                auth_url="https://auth.bsvibe.dev",
                service_token="svc-jwt",
            )
            names = router.channels_for(CRITICAL_ALERT)
        assert names == ["telegram"]

    async def test_sync_call_inside_loop_uses_fallback(self) -> None:
        # When called from inside an active loop, channels_for() bails to
        # the fallback router instead of blocking.
        post = AsyncMock()  # never invoked
        with patch("httpx.AsyncClient.post", post):
            router = CentralAlertRouter(
                auth_url="https://auth.bsvibe.dev",
                service_token="svc-jwt",
            )
            names = router.channels_for(CRITICAL_ALERT)
            await router.aclose()
        # critical → default fallback table
        assert "structlog" in names
        post.assert_not_called()
