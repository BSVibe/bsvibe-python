"""Tests for :mod:`bsvibe_cli_base.keyring`.

The keyring module is the first lookup hop for resolving a profile's
bearer token. Two contracts matter:

1. ``set_token`` / ``get_token`` / ``delete_token`` are thin wrappers
   over the system keyring with a single ``service='bsvibe'`` namespace
   and ``username=profile_name`` slot.
2. **Failure of the keyring backend MUST NOT crash the CLI.** Users on
   headless boxes (CI runners, devcontainers without a secret service)
   regularly hit ``keyring.errors.KeyringError`` on import or first use.
   The CLI must degrade gracefully — a warning log and ``None`` from
   ``get_token`` so callers can fall back to env / profile.
"""

from __future__ import annotations

import sys
from typing import Any

import pytest
import structlog


@pytest.fixture(autouse=True)
def _reset_structlog_caplog() -> None:
    structlog.configure(
        processors=[structlog.testing.LogCapture()],
    )


def _set_keyring_stub(monkeypatch: pytest.MonkeyPatch, stub: Any) -> None:
    monkeypatch.setitem(sys.modules, "keyring", stub)


class _MemoryKeyring:
    """Minimal in-memory keyring substitute for happy-path tests."""

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def set_password(self, service: str, username: str, password: str) -> None:
        self.store[(service, username)] = password

    def get_password(self, service: str, username: str) -> str | None:
        return self.store.get((service, username))

    def delete_password(self, service: str, username: str) -> None:
        if (service, username) not in self.store:
            raise _PasswordDeleteError("not found")
        del self.store[(service, username)]


class _PasswordDeleteError(Exception):
    """Mimic keyring.errors.PasswordDeleteError without importing keyring."""


class _RaisingKeyring:
    """Keyring stub whose every call raises — simulates headless host."""

    class _Errors:
        class KeyringError(Exception):
            pass

    errors = _Errors

    def set_password(self, *a: Any, **kw: Any) -> None:
        raise self.errors.KeyringError("no backend available")

    def get_password(self, *a: Any, **kw: Any) -> None:
        raise self.errors.KeyringError("no backend available")

    def delete_password(self, *a: Any, **kw: Any) -> None:
        raise self.errors.KeyringError("no backend available")


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_set_get_delete(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stub = _MemoryKeyring()
        _set_keyring_stub(monkeypatch, stub)
        from bsvibe_cli_base import keyring as kr

        kr.set_token("dev", "abc.def.ghi")
        assert kr.get_token("dev") == "abc.def.ghi"
        assert ("bsvibe", "dev") in stub.store

        kr.delete_token("dev")
        assert kr.get_token("dev") is None

    def test_get_token_missing_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_keyring_stub(monkeypatch, _MemoryKeyring())
        from bsvibe_cli_base import keyring as kr

        assert kr.get_token("never-set") is None

    def test_delete_idempotent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """delete_token on a missing slot must NOT raise."""
        _set_keyring_stub(monkeypatch, _MemoryKeyring())
        from bsvibe_cli_base import keyring as kr

        # No prior set; should be a no-op.
        kr.delete_token("ghost")


# ---------------------------------------------------------------------------
# Backend failure → graceful fallback
# ---------------------------------------------------------------------------


class TestBackendFailure:
    def test_get_token_returns_none_on_backend_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_keyring_stub(monkeypatch, _RaisingKeyring())
        from bsvibe_cli_base import keyring as kr

        assert kr.get_token("dev") is None

    def test_set_token_swallows_backend_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """set_token must not crash CLI startup; warn + drop."""
        _set_keyring_stub(monkeypatch, _RaisingKeyring())
        from bsvibe_cli_base import keyring as kr

        # Should not raise.
        kr.set_token("dev", "secret")

    def test_delete_token_swallows_backend_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_keyring_stub(monkeypatch, _RaisingKeyring())
        from bsvibe_cli_base import keyring as kr

        kr.delete_token("dev")


# ---------------------------------------------------------------------------
# resolve_token: keyring → env → profile.token_ref (raw)
# ---------------------------------------------------------------------------


class TestResolveToken:
    def test_keyring_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stub = _MemoryKeyring()
        stub.store[("bsvibe", "dev")] = "from-keyring"
        _set_keyring_stub(monkeypatch, stub)
        monkeypatch.setenv("BSVIBE_TOKEN", "from-env")
        from bsvibe_cli_base import keyring as kr
        from bsvibe_cli_base.config import Profile

        p = Profile(name="dev", url="https://api.dev", token_ref="raw-fallback")
        assert kr.resolve_token(p) == "from-keyring"

    def test_env_wins_when_keyring_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_keyring_stub(monkeypatch, _MemoryKeyring())
        monkeypatch.setenv("BSVIBE_TOKEN", "from-env")
        from bsvibe_cli_base import keyring as kr
        from bsvibe_cli_base.config import Profile

        p = Profile(name="dev", url="https://api.dev", token_ref="raw-fallback")
        assert kr.resolve_token(p) == "from-env"

    def test_profile_token_ref_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_keyring_stub(monkeypatch, _MemoryKeyring())
        monkeypatch.delenv("BSVIBE_TOKEN", raising=False)
        from bsvibe_cli_base import keyring as kr
        from bsvibe_cli_base.config import Profile

        p = Profile(name="dev", url="https://api.dev", token_ref="raw-fallback")
        assert kr.resolve_token(p) == "raw-fallback"

    def test_returns_none_when_nothing_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_keyring_stub(monkeypatch, _MemoryKeyring())
        monkeypatch.delenv("BSVIBE_TOKEN", raising=False)
        from bsvibe_cli_base import keyring as kr
        from bsvibe_cli_base.config import Profile

        p = Profile(name="dev", url="https://api.dev")
        assert kr.resolve_token(p) is None

    def test_env_used_when_keyring_backend_down(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_keyring_stub(monkeypatch, _RaisingKeyring())
        monkeypatch.setenv("BSVIBE_TOKEN", "from-env")
        from bsvibe_cli_base import keyring as kr
        from bsvibe_cli_base.config import Profile

        p = Profile(name="dev", url="https://api.dev")
        assert kr.resolve_token(p) == "from-env"


# ---------------------------------------------------------------------------
# Refresh token slot — separate username-suffixed entry
# ---------------------------------------------------------------------------


class TestRefreshTokenSlot:
    """Refresh tokens live alongside access tokens but in a separate slot.

    Username convention: ``f"{profile_name}.refresh"`` under the same
    ``"bsvibe"`` service. This keeps the access-token API untouched
    (callers that only know about ``get_token`` keep working) while
    ``CliHttpClient.on_token_refreshed`` can rotate the refresh half
    independently.
    """

    def test_set_get_delete_roundtrip(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stub = _MemoryKeyring()
        _set_keyring_stub(monkeypatch, stub)
        from bsvibe_cli_base import keyring as kr

        kr.set_refresh_token("dev", "rt-1")
        assert kr.get_refresh_token("dev") == "rt-1"
        assert ("bsvibe", "dev.refresh") in stub.store

        kr.delete_refresh_token("dev")
        assert kr.get_refresh_token("dev") is None

    def test_get_missing_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_keyring_stub(monkeypatch, _MemoryKeyring())
        from bsvibe_cli_base import keyring as kr

        assert kr.get_refresh_token("never-set") is None

    def test_delete_idempotent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_keyring_stub(monkeypatch, _MemoryKeyring())
        from bsvibe_cli_base import keyring as kr

        # Should not raise even though no entry exists.
        kr.delete_refresh_token("ghost")

    def test_set_refresh_token_swallows_backend_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_keyring_stub(monkeypatch, _RaisingKeyring())
        from bsvibe_cli_base import keyring as kr

        # Must not raise — fail-soft like set_token.
        kr.set_refresh_token("dev", "rt-1")

    def test_get_refresh_token_returns_none_on_backend_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_keyring_stub(monkeypatch, _RaisingKeyring())
        from bsvibe_cli_base import keyring as kr

        assert kr.get_refresh_token("dev") is None

    def test_access_and_refresh_slots_are_independent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stub = _MemoryKeyring()
        _set_keyring_stub(monkeypatch, stub)
        from bsvibe_cli_base import keyring as kr

        kr.set_token("dev", "access-1")
        kr.set_refresh_token("dev", "refresh-1")
        assert kr.get_token("dev") == "access-1"
        assert kr.get_refresh_token("dev") == "refresh-1"

        kr.delete_token("dev")
        assert kr.get_token("dev") is None
        assert kr.get_refresh_token("dev") == "refresh-1"


# ---------------------------------------------------------------------------
# make_persist_callback — refresh-rotation hook for CliHttpClient
# ---------------------------------------------------------------------------


class TestMakePersistCallback:
    """``make_persist_callback`` returns the keyring-write hook for the
    CliHttpClient ``on_token_refreshed`` slot. After a 401-then-refresh
    rotation, both slots in keyring stay in sync without subcommand code
    having to know how the storage works.
    """

    def test_callback_writes_access_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stub = _MemoryKeyring()
        _set_keyring_stub(monkeypatch, stub)
        from bsvibe_cli_base import keyring as kr
        from bsvibe_cli_base.loopback_flow import TokenGrant

        cb = kr.make_persist_callback("prod")
        cb(TokenGrant(access_token="new-access", refresh_token="new-refresh"))

        assert stub.store[("bsvibe", "prod")] == "new-access"
        assert stub.store[("bsvibe", "prod.refresh")] == "new-refresh"

    def test_callback_no_refresh_in_grant_keeps_existing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If the refresh response omits a new refresh_token, the prior
        one stays put — server-side rotation is optional per RFC 6749 §6.
        """
        stub = _MemoryKeyring()
        stub.store[("bsvibe", "prod.refresh")] = "old-refresh"
        _set_keyring_stub(monkeypatch, stub)
        from bsvibe_cli_base import keyring as kr
        from bsvibe_cli_base.loopback_flow import TokenGrant

        cb = kr.make_persist_callback("prod")
        cb(TokenGrant(access_token="new-access"))

        assert stub.store[("bsvibe", "prod")] == "new-access"
        assert stub.store[("bsvibe", "prod.refresh")] == "old-refresh"
