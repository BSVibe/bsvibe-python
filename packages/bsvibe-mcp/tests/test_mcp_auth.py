"""Tests for :mod:`bsvibe_mcp.auth`.

Resolution order (matches the spec in TASK-006):

1. Per-call args (``token`` / ``tenant`` / ``url`` MCP tool params).
2. Profile from ``MCP_PROFILE`` env or ``ProfileStore`` default.
3. ``BSV_BOOTSTRAP_TOKEN`` env (admin escape).

Token values must NEVER appear in log records — verified by capturing
structlog events.

The file is named ``test_mcp_auth.py`` rather than ``test_auth.py`` to
avoid module-name collision with ``bsvibe-authz/tests/test_auth.py``
when the workspace pytest sweep collects both packages.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
import structlog
from bsvibe_cli_base.config import Profile

from bsvibe_mcp.auth import AuthContext, resolve_auth


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip any env vars the resolver inspects so tests are deterministic."""
    for var in ("MCP_PROFILE", "BSV_BOOTSTRAP_TOKEN", "BSVIBE_TOKEN"):
        monkeypatch.delenv(var, raising=False)


class _StubStore:
    """Minimal :class:`ProfileStore` stand-in.

    Holds an in-memory profile list plus an ``active`` pointer. The real
    :class:`bsvibe_cli_base.profile.ProfileStore` re-reads the YAML file
    on every call; we don't need that here — only the surface
    :func:`resolve_auth` calls.
    """

    def __init__(self, profiles: list[Profile], active: str | None = None) -> None:
        self._profiles = profiles
        self._active = active

    def get_profile(self, name: str) -> Profile:
        for profile in self._profiles:
            if profile.name == name:
                return profile
        from bsvibe_cli_base.profile import ProfileNotFoundError

        raise ProfileNotFoundError(f"Profile not found: {name}", context={"name": name})

    def get_active(self) -> Profile | None:
        if self._active is None:
            return None
        return self.get_profile(self._active)


class TestPerCallOverride:
    """Per-call args take precedence over every other source."""

    def test_token_and_tenant_and_url_all_provided(self) -> None:
        ctx = resolve_auth(
            token="explicit-token",
            tenant="explicit-tenant",
            url="https://explicit.example",
            store=_StubStore([]),
        )
        assert ctx.token == "explicit-token"
        assert ctx.tenant == "explicit-tenant"
        assert ctx.url == "https://explicit.example"
        assert ctx.source == "per_call"

    def test_per_call_token_wins_over_profile_token(self) -> None:
        store = _StubStore(
            [
                Profile(
                    name="dev", url="https://dev.example", tenant_id="t-dev", token_ref="profile-token", default=True
                )
            ],
            active="dev",
        )
        ctx = resolve_auth(token="explicit-token", store=store)
        assert ctx.token == "explicit-token"
        # Tenant + url still come from the profile since they weren't overridden.
        assert ctx.tenant == "t-dev"
        assert ctx.url == "https://dev.example"

    def test_per_call_tenant_wins_independently(self) -> None:
        store = _StubStore(
            [
                Profile(
                    name="dev", url="https://dev.example", tenant_id="t-dev", token_ref="profile-token", default=True
                )
            ],
            active="dev",
        )
        ctx = resolve_auth(tenant="t-override", store=store)
        assert ctx.tenant == "t-override"
        assert ctx.token == "profile-token"


class TestProfileResolution:
    """Profile resolved by name then by default."""

    def test_explicit_profile_name(self) -> None:
        store = _StubStore(
            [
                Profile(name="dev", url="https://dev.example", tenant_id="t-dev", token_ref="dev-token"),
                Profile(
                    name="prod", url="https://prod.example", tenant_id="t-prod", token_ref="prod-token", default=True
                ),
            ],
            active="prod",
        )
        ctx = resolve_auth(profile_name="dev", store=store)
        assert ctx.token == "dev-token"
        assert ctx.tenant == "t-dev"
        assert ctx.url == "https://dev.example"
        assert ctx.source == "profile:dev"

    def test_mcp_profile_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MCP_PROFILE", "dev")
        store = _StubStore(
            [
                Profile(name="dev", url="https://dev.example", tenant_id="t-dev", token_ref="dev-token"),
                Profile(
                    name="prod", url="https://prod.example", tenant_id="t-prod", token_ref="prod-token", default=True
                ),
            ],
            active="prod",
        )
        ctx = resolve_auth(store=store)
        assert ctx.token == "dev-token"
        assert ctx.source == "profile:dev"

    def test_default_profile_when_no_name_given(self) -> None:
        store = _StubStore(
            [
                Profile(
                    name="prod", url="https://prod.example", tenant_id="t-prod", token_ref="prod-token", default=True
                )
            ],
            active="prod",
        )
        ctx = resolve_auth(store=store)
        assert ctx.token == "prod-token"
        assert ctx.tenant == "t-prod"
        assert ctx.url == "https://prod.example"
        assert ctx.source == "profile:prod"

    def test_profile_token_resolved_via_keyring(self) -> None:
        """The keyring layer is consulted before falling back to ``token_ref``."""
        store = _StubStore(
            [Profile(name="dev", url="https://dev.example", tenant_id="t-dev", token_ref="raw-fallback", default=True)],
            active="dev",
        )
        with patch("bsvibe_mcp.auth.resolve_token", return_value="keyring-token"):
            ctx = resolve_auth(store=store)
        assert ctx.token == "keyring-token"

    def test_unknown_profile_name_raises(self) -> None:
        from bsvibe_cli_base.profile import ProfileNotFoundError

        with pytest.raises(ProfileNotFoundError):
            resolve_auth(profile_name="missing", store=_StubStore([]))


class TestBootstrapFallback:
    """``BSV_BOOTSTRAP_TOKEN`` is the last-resort admin escape."""

    def test_bootstrap_used_when_no_profile(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BSV_BOOTSTRAP_TOKEN", "boot-token")
        ctx = resolve_auth(store=_StubStore([]))
        assert ctx.token == "boot-token"
        assert ctx.source == "bootstrap"

    def test_per_call_token_beats_bootstrap(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BSV_BOOTSTRAP_TOKEN", "boot-token")
        ctx = resolve_auth(token="explicit", store=_StubStore([]))
        assert ctx.token == "explicit"
        assert ctx.source == "per_call"

    def test_profile_token_beats_bootstrap(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BSV_BOOTSTRAP_TOKEN", "boot-token")
        store = _StubStore(
            [Profile(name="dev", url="https://dev.example", token_ref="profile-token", default=True)],
            active="dev",
        )
        ctx = resolve_auth(store=store)
        assert ctx.token == "profile-token"
        assert ctx.source == "profile:dev"


class TestNoSourceConfigured:
    """When nothing is available the resolver returns an empty context."""

    def test_returns_empty_context(self) -> None:
        ctx = resolve_auth(store=_StubStore([]))
        assert ctx == AuthContext(token=None, tenant=None, url=None, source="none")


class TestRedaction:
    """Token values must not appear in any structlog event."""

    def test_token_value_never_logged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        events: list[dict[str, object]] = []

        def _capture(_logger: object, _method: str, event_dict: dict[str, object]) -> dict[str, object]:
            events.append(dict(event_dict))
            return event_dict

        structlog.configure(
            processors=[_capture, structlog.processors.KeyValueRenderer()],
            wrapper_class=structlog.BoundLogger,
            cache_logger_on_first_use=False,
        )

        secret = "super-secret-token-value"
        store = _StubStore(
            [Profile(name="dev", url="https://dev.example", tenant_id="t", token_ref=secret, default=True)],
            active="dev",
        )
        resolve_auth(store=store)

        rendered = " ".join(str(value) for event in events for value in event.values())
        assert secret not in rendered
        # At least one event recorded — otherwise the assertion above is vacuous.
        assert events, "expected resolve_auth to emit at least one structlog event"

    def test_per_call_token_not_logged(self) -> None:
        events: list[dict[str, object]] = []

        def _capture(_logger: object, _method: str, event_dict: dict[str, object]) -> dict[str, object]:
            events.append(dict(event_dict))
            return event_dict

        structlog.configure(
            processors=[_capture, structlog.processors.KeyValueRenderer()],
            wrapper_class=structlog.BoundLogger,
            cache_logger_on_first_use=False,
        )

        secret = "another-secret-value"
        resolve_auth(token=secret, store=_StubStore([]))

        rendered = " ".join(str(value) for event in events for value in event.values())
        assert secret not in rendered
