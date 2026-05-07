"""Tests for :mod:`bsvibe_cli_base.http`.

``CliHttpClient`` extends :class:`bsvibe_core.http.HttpClientBase` with
the one piece of behaviour every BSVibe CLI subcommand needs: when the
control plane returns 401, the client transparently exchanges the
profile's refresh token for a fresh access token via
``POST /oauth/token`` (refresh_token grant) and replays the original
request exactly once. If the refresh fails, a typed
:class:`CliHttpAuthError` is raised so the CLI surfaces a friendly
"please run `<cli> login` again" message instead of a stack trace.

Coverage targets:

  * Happy path — initial 200 returned untouched.
  * 401 + refresh succeeds → second call returns 200.
  * 401 + refresh succeeds → second call still 401 → final 401 surfaced.
  * 401 with no refresh_token → returned as-is (caller decides UX).
  * 401 + refresh endpoint fails → :class:`CliHttpAuthError` raised.
  * ``on_token_refreshed`` callback fires with the new grant so the
    CLI can persist the rotated refresh_token.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from bsvibe_cli_base.device_flow import DeviceTokenGrant
from bsvibe_cli_base.http import CliHttpAuthError, CliHttpClient


def _client(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    token: str | None = "at-old",
    refresh_token: str | None = "rt-old",
    on_token_refreshed: Callable[[DeviceTokenGrant], None] | None = None,
) -> CliHttpClient:
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport, base_url="https://api.test")
    return CliHttpClient(
        "https://api.test",
        http=http,
        token=token,
        refresh_token=refresh_token,
        on_token_refreshed=on_token_refreshed,
    )


class TestPassThrough:
    async def test_2xx_returned_untouched(self) -> None:
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["auth"] = request.headers.get("authorization")
            return httpx.Response(200, json={"ok": True})

        client = _client(handler)
        try:
            resp = await client.get("/items")
            assert resp.status_code == 200
            assert captured["auth"] == "Bearer at-old"
        finally:
            await client.aclose()

    async def test_401_without_refresh_token_returned_as_is(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": "expired"})

        client = _client(handler, refresh_token=None)
        try:
            resp = await client.get("/items")
            assert resp.status_code == 401
        finally:
            await client.aclose()


class TestRefreshFlow:
    async def test_401_then_refresh_then_replay_succeeds(self) -> None:
        seen: list[tuple[str, str | None]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            auth = request.headers.get("authorization")
            seen.append((path, auth))
            if path == "/oauth/token":
                return httpx.Response(
                    200,
                    json={
                        "access_token": "at-new",
                        "refresh_token": "rt-new",
                        "token_type": "Bearer",
                        "expires_in": 3600,
                    },
                )
            if path == "/items" and auth == "Bearer at-old":
                return httpx.Response(401, json={"error": "expired"})
            if path == "/items" and auth == "Bearer at-new":
                return httpx.Response(200, json={"ok": True})
            return httpx.Response(500)

        granted: list[DeviceTokenGrant] = []
        client = _client(handler, on_token_refreshed=lambda g: granted.append(g))
        try:
            resp = await client.get("/items")
            assert resp.status_code == 200
            assert resp.json() == {"ok": True}
        finally:
            await client.aclose()

        # Sequence: original 401, refresh call, replayed request.
        assert [s[0] for s in seen] == ["/items", "/oauth/token", "/items"]
        assert seen[0][1] == "Bearer at-old"
        assert seen[2][1] == "Bearer at-new"
        assert granted and granted[0].access_token == "at-new"
        assert granted[0].refresh_token == "rt-new"

    async def test_401_after_refresh_returns_final_401(self) -> None:
        polls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/oauth/token":
                return httpx.Response(
                    200,
                    json={"access_token": "at-new", "refresh_token": "rt-new"},
                )
            polls["n"] += 1
            return httpx.Response(401, json={"error": "still-bad"})

        client = _client(handler)
        try:
            resp = await client.get("/items")
            assert resp.status_code == 401
        finally:
            await client.aclose()
        # Two attempts at /items (original + replay).
        assert polls["n"] == 2

    async def test_refresh_endpoint_4xx_raises_auth_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/oauth/token":
                return httpx.Response(400, json={"error": "invalid_grant"})
            return httpx.Response(401, json={"error": "expired"})

        client = _client(handler)
        try:
            with pytest.raises(CliHttpAuthError) as exc_info:
                await client.get("/items")
        finally:
            await client.aclose()
        assert "invalid_grant" in str(exc_info.value)

    async def test_refresh_only_attempted_once_per_call(self) -> None:
        # Even if the replay returns 401 again, we must NOT loop on refresh.
        attempts = {"items": 0, "token": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/oauth/token":
                attempts["token"] += 1
                return httpx.Response(
                    200,
                    json={"access_token": "at-new", "refresh_token": "rt-new"},
                )
            attempts["items"] += 1
            return httpx.Response(401, json={"error": "expired"})

        client = _client(handler)
        try:
            await client.get("/items")
        finally:
            await client.aclose()
        assert attempts["token"] == 1
        assert attempts["items"] == 2

    async def test_token_state_updated_after_refresh(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/oauth/token":
                return httpx.Response(
                    200,
                    json={"access_token": "at-new", "refresh_token": "rt-new"},
                )
            return (
                httpx.Response(200, json={"ok": True})
                if request.headers.get("authorization") == "Bearer at-new"
                else httpx.Response(401)
            )

        client = _client(handler)
        try:
            await client.get("/items")
            # Subsequent call uses the refreshed token directly (no second 401).
            resp = await client.get("/items")
            assert resp.status_code == 200
            assert client.token == "at-new"
            assert client.refresh_token == "rt-new"
        finally:
            await client.aclose()
