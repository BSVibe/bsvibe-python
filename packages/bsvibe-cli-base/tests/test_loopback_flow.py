"""Tests for :mod:`bsvibe_cli_base.loopback_flow`.

The loopback flow is the BSVibe CLI's authentication path against
``auth.bsvibe.dev``. It implements:

* RFC 7636 (Proof Key for Code Exchange — PKCE) with S256 only.
* RFC 8252 §7.3 (native-app loopback redirect) — bind ``127.0.0.1:0`` so
  the OS picks a free port, build a ``redirect_uri`` pinned to that
  port, open the browser at ``/oauth/authorize``, wait for the redirect
  back to ``/callback?code=…&state=…``, then exchange the code at
  ``/oauth/token``.

Tests bind real ephemeral listeners (port 0 → kernel picks) because the
sockets layer is the part most likely to drift between platforms.
Token-exchange traffic is mocked via ``httpx.MockTransport`` so the
suite never hits the network.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import re
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from bsvibe_cli_base.loopback_flow import (
    LoopbackFlowClient,
    LoopbackFlowError,
    LoopbackFlowStateMismatchError,
    LoopbackFlowTimeoutError,
    TokenGrant,
    bind_loopback_listener,
    build_authorize_url,
    generate_pkce,
)


# ---------------------------------------------------------------------------
# PKCE primitives — RFC 7636 §4
# ---------------------------------------------------------------------------


class TestGeneratePkce:
    def test_verifier_is_43_to_128_chars_unreserved(self) -> None:
        verifier, _challenge = generate_pkce()
        assert 43 <= len(verifier) <= 128
        # RFC 7636 §4.1: ALPHA / DIGIT / '-' / '.' / '_' / '~'
        assert re.fullmatch(r"[A-Za-z0-9._~-]+", verifier), verifier

    def test_challenge_is_s256_of_verifier(self) -> None:
        verifier, challenge = generate_pkce()
        expected = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
        )
        assert challenge == expected

    def test_each_call_returns_a_fresh_verifier(self) -> None:
        seen = {generate_pkce()[0] for _ in range(8)}
        assert len(seen) == 8


# ---------------------------------------------------------------------------
# Authorize URL builder
# ---------------------------------------------------------------------------


class TestBuildAuthorizeUrl:
    def test_emits_required_oauth_params(self) -> None:
        url = build_authorize_url(
            "https://auth.test",
            client_id="cli",
            redirect_uri="http://127.0.0.1:54321/callback",
            code_challenge="challenge-abc",
            state="state-xyz",
            scope="gateway:* sage:*",
            audience="gateway,sage",
        )
        parts = urlsplit(url)
        assert parts.scheme == "https"
        assert parts.netloc == "auth.test"
        assert parts.path == "/oauth/authorize"
        params = parse_qs(parts.query)
        assert params["response_type"] == ["code"]
        assert params["client_id"] == ["cli"]
        assert params["redirect_uri"] == ["http://127.0.0.1:54321/callback"]
        assert params["code_challenge"] == ["challenge-abc"]
        assert params["code_challenge_method"] == ["S256"]
        assert params["state"] == ["state-xyz"]
        assert params["scope"] == ["gateway:* sage:*"]
        assert params["audience"] == ["gateway,sage"]

    def test_omits_scope_and_audience_when_none(self) -> None:
        url = build_authorize_url(
            "https://auth.test",
            client_id="cli",
            redirect_uri="http://127.0.0.1:1/callback",
            code_challenge="c",
            state="s",
        )
        params = parse_qs(urlsplit(url).query)
        assert "scope" not in params
        assert "audience" not in params

    def test_handles_trailing_slash_in_auth_url(self) -> None:
        url = build_authorize_url(
            "https://auth.test/",
            client_id="cli",
            redirect_uri="http://127.0.0.1:1/callback",
            code_challenge="c",
            state="s",
        )
        assert "/oauth/authorize?" in url
        # No double slashes after scheme.
        assert "://auth.test//" not in url


# ---------------------------------------------------------------------------
# Loopback listener — real bind on 127.0.0.1:0
# ---------------------------------------------------------------------------


async def _fire_callback(
    host: str, port: int, *, code: str | None, state: str | None, error: str | None = None
) -> None:
    """Drive a synthetic browser redirect against the loopback listener."""
    params: list[str] = []
    if code is not None:
        params.append(f"code={code}")
    if state is not None:
        params.append(f"state={state}")
    if error is not None:
        params.append(f"error={error}")
    query = "&".join(params)
    async with httpx.AsyncClient() as http:
        await http.get(f"http://{host}:{port}/callback?{query}")


class TestLoopbackListener:
    async def test_binds_ephemeral_port_on_loopback(self) -> None:
        listener = await bind_loopback_listener()
        try:
            assert listener.host == "127.0.0.1"
            assert listener.port > 0
            assert listener.redirect_uri == f"http://127.0.0.1:{listener.port}/callback"
        finally:
            await listener.close()

    async def test_returns_code_when_state_matches(self) -> None:
        listener = await bind_loopback_listener()
        try:
            asyncio.create_task(_fire_callback(listener.host, listener.port, code="auth-code-1", state="s1"))
            code = await listener.wait_for_callback("s1", timeout_s=2.0)
        finally:
            await listener.close()
        assert code == "auth-code-1"

    async def test_state_mismatch_raises(self) -> None:
        listener = await bind_loopback_listener()
        try:
            asyncio.create_task(_fire_callback(listener.host, listener.port, code="c", state="wrong"))
            with pytest.raises(LoopbackFlowStateMismatchError):
                await listener.wait_for_callback("expected", timeout_s=2.0)
        finally:
            await listener.close()

    async def test_timeout_raises_when_no_callback_arrives(self) -> None:
        listener = await bind_loopback_listener()
        try:
            with pytest.raises(LoopbackFlowTimeoutError):
                await listener.wait_for_callback("s", timeout_s=0.2)
        finally:
            await listener.close()

    async def test_oauth_error_query_raises(self) -> None:
        listener = await bind_loopback_listener()
        try:
            asyncio.create_task(
                _fire_callback(
                    listener.host,
                    listener.port,
                    code=None,
                    state="s",
                    error="access_denied",
                )
            )
            with pytest.raises(LoopbackFlowError) as exc_info:
                await listener.wait_for_callback("s", timeout_s=2.0)
        finally:
            await listener.close()
        assert "access_denied" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Token exchange — mocked transport
# ---------------------------------------------------------------------------


def _build_token_client(
    handler: Any,
) -> LoopbackFlowClient:
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport, base_url="https://auth.test")
    return LoopbackFlowClient("https://auth.test", http=http, client_id="cli")


class TestExchangeCode:
    async def test_posts_authorization_code_grant_form_encoded(self) -> None:
        captured: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["content_type"] = request.headers.get("content-type", "")
            captured["body"] = request.read().decode("utf-8")
            return httpx.Response(
                200,
                json={
                    "access_token": "at-1",
                    "refresh_token": "rt-1",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                },
            )

        client = _build_token_client(handler)
        try:
            grant = await client.exchange_code(
                code="auth-code",
                code_verifier="ver-1",
                redirect_uri="http://127.0.0.1:55555/callback",
            )
        finally:
            await client.aclose()

        assert isinstance(grant, TokenGrant)
        assert grant.access_token == "at-1"
        assert grant.refresh_token == "rt-1"
        assert grant.expires_in == 3600
        assert grant.token_type == "Bearer"
        assert captured["url"].endswith("/oauth/token")
        # RFC 6749 §4.1.3 requires application/x-www-form-urlencoded.
        assert captured["content_type"].startswith("application/x-www-form-urlencoded")
        body = parse_qs(captured["body"])
        assert body["grant_type"] == ["authorization_code"]
        assert body["client_id"] == ["cli"]
        assert body["code"] == ["auth-code"]
        assert body["code_verifier"] == ["ver-1"]
        assert body["redirect_uri"] == ["http://127.0.0.1:55555/callback"]

    async def test_4xx_raises_loopback_flow_error(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, json={"error": "invalid_grant"})

        client = _build_token_client(handler)
        try:
            with pytest.raises(LoopbackFlowError) as exc_info:
                await client.exchange_code(
                    code="c",
                    code_verifier="v",
                    redirect_uri="http://127.0.0.1:1/callback",
                )
        finally:
            await client.aclose()
        assert "invalid_grant" in str(exc_info.value)

    async def test_missing_access_token_raises(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"refresh_token": "r"})

        client = _build_token_client(handler)
        try:
            with pytest.raises(LoopbackFlowError):
                await client.exchange_code(
                    code="c",
                    code_verifier="v",
                    redirect_uri="http://127.0.0.1:1/callback",
                )
        finally:
            await client.aclose()


# ---------------------------------------------------------------------------
# Full flow — listener + browser stub + token exchange round trip
# ---------------------------------------------------------------------------


class TestRunLoginFlow:
    async def test_full_round_trip_returns_token_grant(self) -> None:
        captured_authorize: dict[str, Any] = {}
        captured_token: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/oauth/token"
            captured_token["body"] = parse_qs(request.read().decode("utf-8"))
            return httpx.Response(
                200,
                json={
                    "access_token": "at-roundtrip",
                    "refresh_token": "rt-roundtrip",
                    "expires_in": 1800,
                },
            )

        client = _build_token_client(handler)

        async def _browser_open(url: str) -> None:
            """Simulate the user approving in their browser."""
            parts = urlsplit(url)
            params = parse_qs(parts.query)
            captured_authorize["params"] = {k: v[0] for k, v in params.items()}
            redirect_uri = params["redirect_uri"][0]
            state = params["state"][0]
            redirect_parts = urlsplit(redirect_uri)
            await _fire_callback(
                redirect_parts.hostname or "127.0.0.1",
                redirect_parts.port or 0,
                code="auth-code-roundtrip",
                state=state,
            )

        def _open_sync(url: str) -> None:
            # webbrowser.open is sync — we kick the async fire-and-forget here.
            asyncio.get_event_loop().create_task(_browser_open(url))

        try:
            grant = await client.run_login_flow(
                scope="gateway:*",
                audience="gateway",
                open_browser=_open_sync,
                callback_timeout_s=2.0,
            )
        finally:
            await client.aclose()

        assert grant.access_token == "at-roundtrip"
        assert grant.refresh_token == "rt-roundtrip"

        # Authorize URL had every required PKCE/redirect param.
        ap = captured_authorize["params"]
        assert ap["response_type"] == "code"
        assert ap["code_challenge_method"] == "S256"
        assert ap["client_id"] == "cli"
        assert ap["scope"] == "gateway:*"
        assert ap["audience"] == "gateway"
        assert ap["redirect_uri"].startswith("http://127.0.0.1:")
        assert ap["redirect_uri"].endswith("/callback")

        # Token exchange echoed the SAME verifier whose challenge appeared
        # in the authorize URL (the PKCE binding).
        token_body = captured_token["body"]
        sent_verifier = token_body["code_verifier"][0]
        sent_redirect = token_body["redirect_uri"][0]
        # Same redirect_uri on both legs — RFC 8252 §8.10.
        assert sent_redirect == ap["redirect_uri"]
        # Verifier hashes to the challenge embedded in the authorize URL.
        recomputed = (
            base64.urlsafe_b64encode(hashlib.sha256(sent_verifier.encode("ascii")).digest())
            .rstrip(b"=")
            .decode("ascii")
        )
        assert recomputed == ap["code_challenge"]
