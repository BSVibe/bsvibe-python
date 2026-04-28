"""Tests for AuditClient — HTTP client to BSVibe-Auth /api/audit/events."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest

from bsvibe_audit import AuditClient
from bsvibe_audit.client import AuditDeliveryError, AuditDeliveryResult


def _payload() -> dict[str, object]:
    return {
        "event_id": str(uuid4()),
        "event_type": "test.x",
        "occurred_at": datetime.now(UTC).isoformat(),
        "actor": {"type": "user", "id": "u-1"},
        "tenant_id": "t-1",
        "trace_id": None,
        "resource": None,
        "data": {},
    }


async def test_client_posts_batch_with_service_token() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(202, json={"accepted": 2})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as inner:
        client = AuditClient(
            audit_url="https://auth.bsvibe.dev/api/audit/events",
            service_token="tok",
            http=inner,
        )
        result = await client.send([_payload(), _payload()])

    assert isinstance(result, AuditDeliveryResult)
    assert result.accepted is True
    assert len(captured) == 1
    req = captured[0]
    assert req.method == "POST"
    assert str(req.url) == "https://auth.bsvibe.dev/api/audit/events"
    assert req.headers["X-Service-Token"] == "tok"
    body = req.read()
    assert b'"events"' in body


async def test_client_treats_5xx_as_retryable() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream down")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as inner:
        client = AuditClient(
            audit_url="https://auth.bsvibe.dev/api/audit/events",
            service_token="tok",
            http=inner,
        )
        with pytest.raises(AuditDeliveryError) as ei:
            await client.send([_payload()])
    assert ei.value.retryable is True
    assert "503" in str(ei.value)


async def test_client_treats_400_as_non_retryable() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "bad event"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as inner:
        client = AuditClient(
            audit_url="https://auth.bsvibe.dev/api/audit/events",
            service_token="tok",
            http=inner,
        )
        with pytest.raises(AuditDeliveryError) as ei:
            await client.send([_payload()])
    assert ei.value.retryable is False


async def test_client_treats_network_error_as_retryable() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("dns down")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as inner:
        client = AuditClient(
            audit_url="https://auth.bsvibe.dev/api/audit/events",
            service_token="tok",
            http=inner,
        )
        with pytest.raises(AuditDeliveryError) as ei:
            await client.send([_payload()])
    assert ei.value.retryable is True


async def test_client_owns_http_when_built_from_settings() -> None:
    """from_settings should yield a self-contained client (close cleans up)."""
    client = AuditClient.from_settings(
        audit_url="https://auth.bsvibe.dev/api/audit/events",
        service_token="tok",
    )
    try:
        assert isinstance(client, AuditClient)
    finally:
        await client.aclose()
