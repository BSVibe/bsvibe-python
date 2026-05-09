"""Tests for :mod:`bsvibe_cli_base.login_cmd` — device-flow ``login``.

The ``login`` subapp is the CLI bootstrap for first-run authentication.
It runs the OAuth 2.0 Device Authorization Grant against an auth server,
prints the user_code + verification URL while the human approves in a
browser, and on approval persists both tokens to the OS keyring + the
profile store.

Tests exercise the underlying ``do_login`` async helper directly so the
device-flow client and keyring backend can be swapped for an in-memory
stub. The Typer wrapper is covered separately by a smoke test that
asserts the subapp registers the expected options.
"""

from __future__ import annotations

import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from bsvibe_cli_base.config import Profile
from bsvibe_cli_base.device_flow import DeviceFlowClient, DeviceFlowError
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


def _build_flow_client(handler: Callable[[httpx.Request], httpx.Response]) -> DeviceFlowClient:
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport, base_url="https://auth.test")
    return DeviceFlowClient("https://auth.test", http=http, client_id="cli")


def _approval_handler() -> Callable[[httpx.Request], httpx.Response]:
    """Handler that approves on first poll — covers the happy path."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth/device/code"):
            return httpx.Response(
                200,
                json={
                    "device_code": "dev-1",
                    "user_code": "WXYZ-1234",
                    "verification_uri": "https://auth.test/auth/device",
                    "expires_in": 600,
                    "interval": 5,
                },
            )
        # token endpoint
        return httpx.Response(
            200,
            json={
                "status": "approved",
                "access_token": "bsv_sk_access",
                "refresh_token": "bsv_rt_refresh",
                "expires_in": 3600,
                "token_type": "Bearer",
            },
        )

    return handler


def _instant_sleep() -> Callable[[float], Awaitable[None]]:
    async def sleep(_: float) -> None:
        return None

    return sleep


# ---------------------------------------------------------------------------
# do_login — happy paths
# ---------------------------------------------------------------------------


class TestDoLoginHappyPath:
    async def test_creates_new_profile_and_persists_tokens(
        self, keyring_stub: _MemoryKeyring, store: ProfileStore
    ) -> None:
        from bsvibe_cli_base.login_cmd import do_login

        printed: list[str] = []
        client = _build_flow_client(_approval_handler())
        try:
            await do_login(
                flow_client=client,
                profile_store=store,
                profile_name="prod",
                profile_url="https://api.prod.test",
                tenant_id="t-prod",
                scope="gateway:* sage:*",
                audience="gateway,sage",
                sleep=_instant_sleep(),
                print_fn=printed.append,
            )
        finally:
            await client.aclose()

        # Profile created and active
        prof = store.get_profile("prod")
        assert prof.url == "https://api.prod.test"
        assert prof.tenant_id == "t-prod"
        assert prof.default is True
        assert prof.token_ref == "prod"
        assert prof.refresh_token_ref == "prod"

        # Tokens in keyring under the standard service namespace
        assert keyring_stub.store[("bsvibe", "prod")] == "bsv_sk_access"
        assert keyring_stub.store[("bsvibe", "prod.refresh")] == "bsv_rt_refresh"

        # User-facing output included the user code + verification URL
        out = "\n".join(printed)
        assert "WXYZ-1234" in out
        assert "https://auth.test/auth/device" in out

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

        client = _build_flow_client(_approval_handler())
        try:
            await do_login(
                flow_client=client,
                profile_store=store,
                profile_name="prod",
                profile_url="https://api.prod.test",
                tenant_id=None,
                sleep=_instant_sleep(),
                print_fn=lambda _msg: None,
            )
        finally:
            await client.aclose()

        prof = store.get_profile("prod")
        assert prof.url == "https://api.prod.test"
        assert prof.tenant_id == "t-prod"  # untouched
        assert keyring_stub.store[("bsvibe", "prod")] == "bsv_sk_access"
        assert keyring_stub.store[("bsvibe", "prod.refresh")] == "bsv_rt_refresh"

    async def test_passes_audience_to_request_code(self, keyring_stub: _MemoryKeyring, store: ProfileStore) -> None:
        """Audience must reach the auth server — not silently dropped."""
        from bsvibe_cli_base.login_cmd import do_login

        captured: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/oauth/device/code"):
                captured["body"] = request.read()
                return httpx.Response(
                    200,
                    json={
                        "device_code": "d",
                        "user_code": "U",
                        "verification_uri": "https://auth.test/auth/device",
                        "expires_in": 600,
                        "interval": 5,
                    },
                )
            return httpx.Response(
                200,
                json={"status": "approved", "access_token": "a", "refresh_token": "r"},
            )

        client = _build_flow_client(handler)
        try:
            await do_login(
                flow_client=client,
                profile_store=store,
                profile_name="p",
                profile_url="https://api.test",
                tenant_id=None,
                scope="gateway:*",
                audience="gateway,sage,nexus,supervisor",
                sleep=_instant_sleep(),
                print_fn=lambda _msg: None,
            )
        finally:
            await client.aclose()

        body = captured["body"].decode("utf-8")
        assert "gateway,sage,nexus,supervisor" in body


# ---------------------------------------------------------------------------
# do_login — error paths
# ---------------------------------------------------------------------------


class TestDoLoginErrorPaths:
    async def test_request_code_4xx_propagates_device_flow_error(
        self, keyring_stub: _MemoryKeyring, store: ProfileStore
    ) -> None:
        from bsvibe_cli_base.login_cmd import do_login

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, json={"error": "invalid_client"})

        client = _build_flow_client(handler)
        try:
            with pytest.raises(DeviceFlowError):
                await do_login(
                    flow_client=client,
                    profile_store=store,
                    profile_name="p",
                    profile_url="https://api.test",
                    tenant_id=None,
                    sleep=_instant_sleep(),
                    print_fn=lambda _msg: None,
                )
        finally:
            await client.aclose()

        # Nothing persisted on failure
        assert keyring_stub.store == {}
        assert store.list_profiles() == []

    async def test_access_denied_propagates_device_flow_error(
        self, keyring_stub: _MemoryKeyring, store: ProfileStore
    ) -> None:
        from bsvibe_cli_base.login_cmd import do_login

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/oauth/device/code"):
                return httpx.Response(
                    200,
                    json={
                        "device_code": "d",
                        "user_code": "U",
                        "verification_uri": "https://auth.test/auth/device",
                        "expires_in": 600,
                        "interval": 1,
                    },
                )
            return httpx.Response(200, json={"status": "access_denied"})

        client = _build_flow_client(handler)
        try:
            with pytest.raises(DeviceFlowError) as exc_info:
                await do_login(
                    flow_client=client,
                    profile_store=store,
                    profile_name="p",
                    profile_url="https://api.test",
                    tenant_id=None,
                    sleep=_instant_sleep(),
                    print_fn=lambda _msg: None,
                )
        finally:
            await client.aclose()
        assert "access_denied" in str(exc_info.value)
        assert keyring_stub.store == {}


# ---------------------------------------------------------------------------
# Typer subapp smoke
# ---------------------------------------------------------------------------


class TestLoginTyperApp:
    def test_login_app_exposes_login_command(self) -> None:
        """The exported ``login_app`` should have a runnable callback."""
        import typer
        from typer.testing import CliRunner

        from bsvibe_cli_base.login_cmd import login_app

        assert isinstance(login_app, typer.Typer)
        runner = CliRunner()
        result = runner.invoke(login_app, ["--help"])
        assert result.exit_code == 0
        # Help should mention the device-flow surface.
        for needle in ("--auth-url", "--client-id", "--scope"):
            assert needle in result.output, f"missing {needle} in login --help"
