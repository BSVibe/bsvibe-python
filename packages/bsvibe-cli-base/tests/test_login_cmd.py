"""Tests for :mod:`bsvibe_cli_base.login_cmd` — loopback ``login``.

The ``login`` subapp is the CLI bootstrap for first-run authentication.
It runs the OAuth 2.0 authorization-code grant with PKCE over a
loopback redirect (RFC 7636 + RFC 8252), opens the user's browser at
``/oauth/authorize``, waits for the redirect on its loopback listener,
exchanges the code at ``/oauth/token``, then persists both tokens to
the OS keyring + the profile store.

Tests drive the underlying ``do_login`` async helper directly with a
real :class:`LoopbackFlowClient` whose transport is mocked via
``httpx.MockTransport``. The browser-open step is replaced by a synthetic
callback fired against the real loopback listener. The Typer wrapper has
its own smoke test that asserts the subapp registers the expected
options.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from bsvibe_cli_base.config import Profile
from bsvibe_cli_base.loopback_flow import LoopbackFlowClient, LoopbackFlowError
from bsvibe_cli_base.profile import ProfileStore


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _MemoryKeyring:
    """Minimal in-memory keyring substitute mirroring the test_keyring.py stub."""

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def set_password(self, service: str, username: str, password: str) -> None:
        self.store[(service, username)] = password

    def get_password(self, service: str, username: str) -> str | None:
        return self.store.get((service, username))

    def delete_password(self, service: str, username: str) -> None:
        self.store.pop((service, username), None)


@pytest.fixture
def keyring_stub(monkeypatch: pytest.MonkeyPatch) -> _MemoryKeyring:
    stub = _MemoryKeyring()
    monkeypatch.setitem(sys.modules, "keyring", stub)
    return stub


@pytest.fixture
def store(tmp_path: Path) -> ProfileStore:
    return ProfileStore(path=tmp_path / "config.yaml")


def _build_flow_client(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    base_url: str = "https://auth.test",
    client_id: str = "cli",
) -> LoopbackFlowClient:
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport, base_url=base_url)
    return LoopbackFlowClient(base_url, http=http, client_id=client_id)


def _approval_token_handler(
    captured: dict[str, Any] | None = None,
) -> Callable[[httpx.Request], httpx.Response]:
    """``/oauth/token`` handler that approves with the standard grant body."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/oauth/token"
        if captured is not None:
            captured["body"] = request.read().decode("utf-8")
        return httpx.Response(
            200,
            json={
                "access_token": "bsv_sk_access",
                "refresh_token": "bsv_rt_refresh",
                "expires_in": 3600,
                "token_type": "Bearer",
            },
        )

    return handler


def _make_browser_stub(
    *,
    code: str = "auth-code-1",
    state_override: str | None = None,
    error: str | None = None,
) -> tuple[Callable[[str], None], dict[str, Any]]:
    """Return a ``(open_browser, captured)`` pair that simulates approval.

    ``open_browser`` is the sync callable :class:`LoopbackFlowClient`
    invokes; it kicks an async task that GETs the loopback ``/callback``
    with the supplied code + state (or an ``error``).
    """
    import asyncio

    captured: dict[str, Any] = {}

    def open_browser(url: str) -> None:
        parts = urlsplit(url)
        params = parse_qs(parts.query)
        captured["url"] = url
        captured["params"] = {k: v[0] for k, v in params.items()}
        redirect_uri = params["redirect_uri"][0]
        state = state_override if state_override is not None else params["state"][0]
        rp = urlsplit(redirect_uri)
        host = rp.hostname or "127.0.0.1"
        port = rp.port or 0

        async def _drive() -> None:
            async with httpx.AsyncClient() as http:
                qs = [f"state={state}"]
                if error is not None:
                    qs.append(f"error={error}")
                else:
                    qs.append(f"code={code}")
                await http.get(f"http://{host}:{port}/callback?{'&'.join(qs)}")

        asyncio.get_event_loop().create_task(_drive())

    return open_browser, captured


def _no_browser() -> Callable[[str], None]:
    """``open_browser`` that does nothing — pairs with state-induced timeouts."""

    def _stub(_url: str) -> None:
        return None

    return _stub


# ---------------------------------------------------------------------------
# do_login — happy paths
# ---------------------------------------------------------------------------


class TestDoLoginHappyPath:
    async def test_creates_new_profile_and_persists_tokens(
        self, keyring_stub: _MemoryKeyring, store: ProfileStore
    ) -> None:
        from bsvibe_cli_base.login_cmd import do_login

        printed: list[str] = []
        captured: dict[str, Any] = {}
        client = _build_flow_client(_approval_token_handler(captured))
        open_browser, _ = _make_browser_stub()
        try:
            await do_login(
                flow_client=client,
                profile_store=store,
                profile_name="prod",
                profile_url="https://api.prod.test",
                tenant_id="t-prod",
                scope="gateway:* sage:*",
                audience="gateway,sage",
                open_browser=open_browser,
                callback_timeout_s=2.0,
                print_fn=printed.append,
            )
        finally:
            await client.aclose()

        prof = store.get_profile("prod")
        assert prof.url == "https://api.prod.test"
        assert prof.tenant_id == "t-prod"
        assert prof.default is True
        assert prof.token_ref == "prod"
        assert prof.refresh_token_ref == "prod"

        assert keyring_stub.store[("bsvibe", "prod")] == "bsv_sk_access"
        assert keyring_stub.store[("bsvibe", "prod.refresh")] == "bsv_rt_refresh"

        # Token leg saw the same redirect_uri the auth-url leg carried — PKCE binding.
        body = parse_qs(captured["body"])
        assert body["grant_type"] == ["authorization_code"]
        assert body["client_id"] == ["cli"]
        # Output is reassuring but never shows the raw token.
        out = "\n".join(printed)
        assert "Saved PAT to keyring" in out
        assert "bsv_sk_access" not in out

    async def test_no_browser_mode_prints_authorize_url(
        self, keyring_stub: _MemoryKeyring, store: ProfileStore
    ) -> None:
        """``open_browser=False`` (CLI ``--no-browser``) MUST print the URL
        so a headless / CI driver can curl it themselves."""
        from bsvibe_cli_base.login_cmd import do_login

        captured: dict[str, Any] = {}
        client = _build_flow_client(_approval_token_handler(captured))

        # Capture the URL the announce path prints, then fire the callback
        # ourselves against the listener port encoded in it.
        import asyncio

        async def _drive_callback(url: str) -> None:
            parts = urlsplit(url)
            params = parse_qs(parts.query)
            rp = urlsplit(params["redirect_uri"][0])
            state = params["state"][0]
            async with httpx.AsyncClient() as http:
                await http.get(f"http://{rp.hostname}:{rp.port}/callback?code=c-headless&state={state}")

        announce_seen: list[str] = []

        def announce(msg: str) -> None:
            announce_seen.append(msg)
            for line in msg.splitlines():
                line = line.strip()
                if line.startswith("http"):
                    asyncio.get_event_loop().create_task(_drive_callback(line))
                    return

        try:
            await do_login(
                flow_client=client,
                profile_store=store,
                profile_name="ci",
                profile_url="https://api.test",
                tenant_id=None,
                open_browser=False,
                callback_timeout_s=2.0,
                print_fn=announce,
            )
        finally:
            await client.aclose()

        joined = "\n".join(announce_seen)
        assert "/oauth/authorize?" in joined, "authorize URL must be emitted in --no-browser mode"
        assert keyring_stub.store[("bsvibe", "ci")] == "bsv_sk_access"

    async def test_updates_existing_profile_tokens_only(
        self, keyring_stub: _MemoryKeyring, store: ProfileStore
    ) -> None:
        from bsvibe_cli_base.login_cmd import do_login

        store.add_profile(
            Profile(
                name="prod",
                url="https://api.prod.test",
                tenant_id="t-prod",
                default=True,
                token_ref="prod",
            )
        )

        client = _build_flow_client(_approval_token_handler())
        open_browser, _ = _make_browser_stub()
        try:
            await do_login(
                flow_client=client,
                profile_store=store,
                profile_name="prod",
                profile_url="https://api.prod.test",
                tenant_id=None,
                open_browser=open_browser,
                callback_timeout_s=2.0,
                print_fn=lambda _msg: None,
            )
        finally:
            await client.aclose()

        prof = store.get_profile("prod")
        assert prof.url == "https://api.prod.test"
        assert prof.tenant_id == "t-prod"  # untouched
        assert keyring_stub.store[("bsvibe", "prod")] == "bsv_sk_access"
        assert keyring_stub.store[("bsvibe", "prod.refresh")] == "bsv_rt_refresh"
        assert prof.token_ref == "prod"
        assert prof.refresh_token_ref == "prod"

    async def test_keyring_write_failure_aborts_with_actionable_message(
        self, store: ProfileStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Anti-regression from Phase 8 dogfood (2026-05-11) finding #5 —
        when the keyring backend refuses to store (macOS
        errSecInteractionNotAllowed, missing libsecret, etc.), login MUST
        surface the failure and print the raw token once so the operator
        isn't locked out. The previous behaviour printed
        ``Saved PAT to keyring`` regardless and the next CLI invocation
        401'd with no clue why."""
        from bsvibe_cli_base.login_cmd import do_login

        class _RefusingKeyring:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str]] = []

            def set_password(self, service: str, username: str, _pw: str) -> None:
                self.calls.append((service, username))
                raise RuntimeError("Can't store password on keychain: (-25308, ...)")

            def get_password(self, service: str, username: str) -> str | None:
                return None

            def delete_password(self, service: str, username: str) -> None:
                return None

        stub = _RefusingKeyring()
        monkeypatch.setitem(sys.modules, "keyring", stub)

        printed: list[str] = []
        client = _build_flow_client(_approval_token_handler())
        open_browser, _ = _make_browser_stub()
        try:
            with pytest.raises(LoopbackFlowError) as exc_info:
                await do_login(
                    flow_client=client,
                    profile_store=store,
                    profile_name="prod",
                    profile_url="https://api.prod.test",
                    tenant_id="t-prod",
                    open_browser=open_browser,
                    callback_timeout_s=2.0,
                    print_fn=printed.append,
                )
        finally:
            await client.aclose()

        out = "\n".join(printed)
        assert "Could not save the access token to the system keyring" in out
        assert "bsv_sk_access" in out, "raw access token must be surfaced"
        assert "bsv_rt_refresh" in out, "raw refresh token must be surfaced"
        assert "PYTHON_KEYRING_BACKEND" in out
        assert "BSVIBE_TOKEN" in out
        assert "Saved PAT to keyring" not in out

        from bsvibe_cli_base.profile import ProfileNotFoundError

        with pytest.raises(ProfileNotFoundError):
            store.get_profile("prod")

        assert "keyring refused to store the token" in str(exc_info.value)

    async def test_authorize_url_carries_audience_and_scope(
        self, keyring_stub: _MemoryKeyring, store: ProfileStore
    ) -> None:
        """Scope + audience must reach the auth server's authorize URL —
        not silently dropped."""
        from bsvibe_cli_base.login_cmd import do_login

        client = _build_flow_client(_approval_token_handler())
        open_browser, captured = _make_browser_stub()
        try:
            await do_login(
                flow_client=client,
                profile_store=store,
                profile_name="p",
                profile_url="https://api.test",
                tenant_id=None,
                scope="gateway:*",
                audience="gateway,sage,nexus,supervisor",
                open_browser=open_browser,
                callback_timeout_s=2.0,
                print_fn=lambda _msg: None,
            )
        finally:
            await client.aclose()

        assert captured["params"]["scope"] == "gateway:*"
        assert captured["params"]["audience"] == "gateway,sage,nexus,supervisor"
        assert captured["params"]["code_challenge_method"] == "S256"


# ---------------------------------------------------------------------------
# do_login — error paths
# ---------------------------------------------------------------------------


class TestDoLoginErrorPaths:
    async def test_token_exchange_4xx_propagates_error(self, keyring_stub: _MemoryKeyring, store: ProfileStore) -> None:
        from bsvibe_cli_base.login_cmd import do_login

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, json={"error": "invalid_grant"})

        client = _build_flow_client(handler)
        open_browser, _ = _make_browser_stub()
        try:
            with pytest.raises(LoopbackFlowError) as exc_info:
                await do_login(
                    flow_client=client,
                    profile_store=store,
                    profile_name="p",
                    profile_url="https://api.test",
                    tenant_id=None,
                    open_browser=open_browser,
                    callback_timeout_s=2.0,
                    print_fn=lambda _msg: None,
                )
        finally:
            await client.aclose()

        assert "invalid_grant" in str(exc_info.value)
        assert keyring_stub.store == {}
        assert store.list_profiles() == []

    async def test_oauth_error_on_callback_propagates(self, keyring_stub: _MemoryKeyring, store: ProfileStore) -> None:
        from bsvibe_cli_base.login_cmd import do_login

        client = _build_flow_client(_approval_token_handler())
        open_browser, _ = _make_browser_stub(error="access_denied")
        try:
            with pytest.raises(LoopbackFlowError) as exc_info:
                await do_login(
                    flow_client=client,
                    profile_store=store,
                    profile_name="p",
                    profile_url="https://api.test",
                    tenant_id=None,
                    open_browser=open_browser,
                    callback_timeout_s=2.0,
                    print_fn=lambda _msg: None,
                )
        finally:
            await client.aclose()
        assert "access_denied" in str(exc_info.value)
        assert keyring_stub.store == {}

    async def test_state_mismatch_rejected(self, keyring_stub: _MemoryKeyring, store: ProfileStore) -> None:
        """CSRF defense: an attacker firing a callback with the wrong
        state must be rejected even if the token endpoint would have
        otherwise minted a grant."""
        from bsvibe_cli_base.login_cmd import do_login
        from bsvibe_cli_base.loopback_flow import LoopbackFlowStateMismatchError

        client = _build_flow_client(_approval_token_handler())
        open_browser, _ = _make_browser_stub(state_override="evil-state")
        try:
            with pytest.raises(LoopbackFlowStateMismatchError):
                await do_login(
                    flow_client=client,
                    profile_store=store,
                    profile_name="p",
                    profile_url="https://api.test",
                    tenant_id=None,
                    open_browser=open_browser,
                    callback_timeout_s=2.0,
                    print_fn=lambda _msg: None,
                )
        finally:
            await client.aclose()
        assert keyring_stub.store == {}


# ---------------------------------------------------------------------------
# Typer subapp smoke
# ---------------------------------------------------------------------------


class TestLoginTyperApp:
    def test_login_app_exposes_loopback_options(self) -> None:
        """The exported ``login_app`` should accept the loopback option set."""
        import typer
        from typer.testing import CliRunner

        from bsvibe_cli_base.login_cmd import login_app

        assert isinstance(login_app, typer.Typer)
        runner = CliRunner()
        result = runner.invoke(login_app, ["--help"])
        assert result.exit_code == 0
        for needle in ("--auth-url", "--client-id", "--scope", "--no-browser"):
            assert needle in result.output, f"missing {needle} in login --help"

    def test_login_failure_cleanup_runs_in_same_asyncio_loop(
        self,
        tmp_path: Path,
    ) -> None:
        """When ``do_login`` raises, ``flow_client.aclose()`` MUST run inside
        the same ``asyncio.run`` invocation as ``do_login`` — not from a
        ``finally:`` block that spins up a second ``asyncio.run``.

        Anti-regression from Phase 8 dogfood 2026-05-11 — a second
        ``asyncio.run`` invocation creates a fresh event loop, but
        httpx's connection pool kept a reference to the FIRST loop.
        Calling aclose on the new loop tries to schedule callbacks on
        the dead loop and crashes with ``RuntimeError: Event loop is
        closed``.
        """
        import asyncio
        from unittest.mock import patch

        from typer.testing import CliRunner

        import bsvibe_cli_base.login_cmd as login_cmd

        aclose_calls: list[int] = []

        class _FailingFlow:
            def __init__(self, *_args: Any, **_kwargs: Any) -> None:
                pass

            async def run_login_flow(self, *_args: Any, **_kwargs: Any) -> Any:
                raise LoopbackFlowError("token exchange failed: 404 ...")

            async def aclose(self) -> None:
                aclose_calls.append(id(asyncio.get_running_loop()))

        real_asyncio_run = asyncio.run
        run_invocations: list[int] = []

        def _counting_run(coro: Any, *args: Any, **kwargs: Any) -> Any:
            run_invocations.append(1)
            return real_asyncio_run(coro, *args, **kwargs)

        runner = CliRunner()
        config_path = tmp_path / "config.yaml"
        with (
            patch.object(login_cmd, "LoopbackFlowClient", _FailingFlow),
            patch.object(
                login_cmd,
                "ProfileStore",
                lambda: ProfileStore(path=config_path),
            ),
            patch.object(login_cmd.asyncio, "run", _counting_run),
        ):
            result = runner.invoke(
                login_cmd.login_app,
                [
                    "--auth-url",
                    "https://auth.example.test",
                    "--client-id",
                    "cli",
                    "--scope",
                    "gateway:*",
                    "--audience",
                    "gateway",
                    "--profile-name",
                    "ci",
                    "--profile-url",
                    "https://gateway.example.test",
                    "--no-browser",
                ],
            )

        assert result.exit_code == 1, (result.output, result.exception)
        assert "Login failed" in result.output
        assert "Event loop is closed" not in result.output, result.output
        assert len(aclose_calls) == 1, aclose_calls
        assert len(run_invocations) == 1, (
            f"Expected exactly one asyncio.run from login_cmd "
            f"(do_login + aclose share the same loop); saw {len(run_invocations)}."
        )
