"""Tests for :mod:`bsvibe_cli_base.device_flow`.

The device flow client is the bootstrap path for first-run authentication
against ``auth.bsvibe.dev``. It implements the BSVibe convention:

  1. ``POST /oauth/device/code`` returns ``{device_code, user_code,
     verification_uri, expires_in, interval}``.
  2. The CLI prints the user code + URL to the terminal so the human
     authenticates in a browser.
  3. ``POST /oauth/device/token`` is polled every ``interval`` seconds;
     payload encodes ``status``:

       * ``pending``    → keep polling.
       * ``slow_down``  → keep polling, server is asking for backoff.
       * ``approved`` / ``granted`` → response carries ``access_token``
         + ``refresh_token``; flow is done.
       * anything else  → fail fast (``access_denied``, ``expired_token``,
         transport errors, etc.).

  4. If the wall-clock exceeds ``expires_in``, raise
     :class:`DeviceFlowTimeoutError`.

All transport is mocked via ``httpx.MockTransport`` so the suite never
touches the network. ``asyncio.sleep`` is replaced with a counting stub
so polling tests run instantly.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import pytest

from bsvibe_cli_base.device_flow import (
    DeviceCode,
    DeviceFlowClient,
    DeviceFlowError,
    DeviceFlowTimeoutError,
    DeviceTokenGrant,
)


def _build_client(handler: Callable[[httpx.Request], httpx.Response]) -> DeviceFlowClient:
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport, base_url="https://auth.test")
    return DeviceFlowClient("https://auth.test", http=http, client_id="cli")


def _sleep_recorder() -> tuple[Callable[[float], Awaitable[None]], list[float]]:
    delays: list[float] = []

    async def sleep(seconds: float) -> None:
        delays.append(seconds)

    return sleep, delays


class TestRequestCode:
    async def test_returns_device_code(self) -> None:
        captured: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["body"] = dict(request.url.params) or request.content
            return httpx.Response(
                200,
                json={
                    "device_code": "dev-abc",
                    "user_code": "WXYZ-1234",
                    "verification_uri": "https://auth.test/device",
                    "expires_in": 600,
                    "interval": 5,
                },
            )

        client = _build_client(handler)
        try:
            code = await client.request_code(scope="openid profile")
        finally:
            await client.aclose()

        assert isinstance(code, DeviceCode)
        assert code.device_code == "dev-abc"
        assert code.user_code == "WXYZ-1234"
        assert code.verification_uri == "https://auth.test/device"
        assert code.expires_in == 600
        assert code.interval == 5
        assert captured["url"].endswith("/oauth/device/code")

    async def test_4xx_raises_device_flow_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, json={"error": "invalid_client"})

        client = _build_client(handler)
        try:
            with pytest.raises(DeviceFlowError) as exc_info:
                await client.request_code()
        finally:
            await client.aclose()
        assert "invalid_client" in str(exc_info.value)


class TestPollToken:
    async def test_pending_then_approved(self) -> None:
        polls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            polls["n"] += 1
            if polls["n"] < 3:
                return httpx.Response(200, json={"status": "pending"})
            return httpx.Response(
                200,
                json={
                    "status": "approved",
                    "access_token": "at-1",
                    "refresh_token": "rt-1",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                },
            )

        client = _build_client(handler)
        sleep, delays = _sleep_recorder()
        code = DeviceCode(
            device_code="dev",
            user_code="U",
            verification_uri="https://x",
            expires_in=600,
            interval=5,
        )
        try:
            grant = await client.poll_token(code, sleep=sleep)
        finally:
            await client.aclose()

        assert isinstance(grant, DeviceTokenGrant)
        assert grant.access_token == "at-1"
        assert grant.refresh_token == "rt-1"
        assert polls["n"] == 3
        assert delays == [5, 5]

    async def test_slow_down_increases_interval(self) -> None:
        polls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            polls["n"] += 1
            if polls["n"] == 1:
                return httpx.Response(200, json={"status": "slow_down"})
            return httpx.Response(
                200,
                json={
                    "status": "granted",
                    "access_token": "at-2",
                    "refresh_token": "rt-2",
                },
            )

        client = _build_client(handler)
        sleep, delays = _sleep_recorder()
        code = DeviceCode(
            device_code="dev",
            user_code="U",
            verification_uri="https://x",
            expires_in=600,
            interval=5,
        )
        try:
            grant = await client.poll_token(code, sleep=sleep)
        finally:
            await client.aclose()

        assert grant.access_token == "at-2"
        # After slow_down the interval was bumped above the initial 5s
        # before the next poll's sleep.
        assert len(delays) == 1
        assert delays[0] >= 10

    async def test_access_denied_raises(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"status": "access_denied"})

        client = _build_client(handler)
        sleep, _delays = _sleep_recorder()
        code = DeviceCode(
            device_code="d",
            user_code="U",
            verification_uri="https://x",
            expires_in=600,
            interval=1,
        )
        try:
            with pytest.raises(DeviceFlowError) as exc_info:
                await client.poll_token(code, sleep=sleep)
        finally:
            await client.aclose()
        assert "access_denied" in str(exc_info.value)

    async def test_polling_times_out_when_expires_in_elapses(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"status": "pending"})

        client = _build_client(handler)
        elapsed = {"t": 0.0}

        async def sleep(seconds: float) -> None:
            elapsed["t"] += seconds

        # Fake monotonic that returns the accumulated sleep total.
        client._monotonic = lambda: elapsed["t"]  # type: ignore[attr-defined]

        code = DeviceCode(
            device_code="d",
            user_code="U",
            verification_uri="https://x",
            expires_in=10,
            interval=3,
        )
        try:
            with pytest.raises(DeviceFlowTimeoutError):
                await client.poll_token(code, sleep=sleep)
        finally:
            await client.aclose()

    async def test_token_endpoint_4xx_raises(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, json={"error": "expired_token"})

        client = _build_client(handler)
        sleep, _delays = _sleep_recorder()
        code = DeviceCode(
            device_code="d",
            user_code="U",
            verification_uri="https://x",
            expires_in=600,
            interval=1,
        )
        try:
            with pytest.raises(DeviceFlowError) as exc_info:
                await client.poll_token(code, sleep=sleep)
        finally:
            await client.aclose()
        assert "expired_token" in str(exc_info.value)
