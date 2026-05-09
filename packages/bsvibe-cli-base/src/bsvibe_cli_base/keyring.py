"""Token storage backed by the system keyring (libsecret / Keychain / WCM).

This module is a thin, fail-soft adapter over the third-party ``keyring``
package. Two design constraints:

* All stored secrets share a single service namespace ``"bsvibe"`` with
  the profile name as the username slot. One profile, one secret.
* The CLI must come up even when no keyring backend is installed —
  common in CI runners and bare devcontainers — so every call swallows
  ``keyring`` exceptions, emits a structlog warning, and returns
  ``None`` (read) or no-op (write).

``resolve_token`` is the single entry point used by the cli factory:
keyring → ``$BSVIBE_TOKEN`` env → :attr:`Profile.token_ref` raw.
"""

from __future__ import annotations

import importlib
import os
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import structlog

from bsvibe_cli_base.config import Profile

if TYPE_CHECKING:  # pragma: no cover - typing only
    from bsvibe_cli_base.device_flow import DeviceTokenGrant

logger = structlog.get_logger(__name__)

SERVICE = "bsvibe"
ENV_VAR = "BSVIBE_TOKEN"
_REFRESH_SUFFIX = ".refresh"


def _backend() -> Any | None:
    """Lazy import so tests can swap ``sys.modules['keyring']``."""
    try:
        return importlib.import_module("keyring")
    except Exception as exc:  # pragma: no cover - import failure is environment-specific
        logger.warning("keyring_import_failed", error=str(exc))
        return None


def set_token(profile_name: str, token: str) -> None:
    """Persist ``token`` under ``(SERVICE, profile_name)``.

    Backend errors are logged and dropped — the CLI never aborts because
    libsecret is missing.
    """
    backend = _backend()
    if backend is None:
        return
    try:
        backend.set_password(SERVICE, profile_name, token)
        logger.debug("keyring_token_set", profile=profile_name)
    except Exception as exc:
        logger.warning("keyring_set_failed", profile=profile_name, error=str(exc))


def get_token(profile_name: str) -> str | None:
    """Return the stored token for ``profile_name`` or ``None`` if absent
    or the backend is unavailable.
    """
    backend = _backend()
    if backend is None:
        return None
    try:
        return backend.get_password(SERVICE, profile_name)
    except Exception as exc:
        logger.warning("keyring_get_failed", profile=profile_name, error=str(exc))
        return None


def delete_token(profile_name: str) -> None:
    """Remove the stored token. Idempotent — missing entries and backend
    failures are silently absorbed.
    """
    backend = _backend()
    if backend is None:
        return
    try:
        backend.delete_password(SERVICE, profile_name)
        logger.debug("keyring_token_deleted", profile=profile_name)
    except Exception as exc:
        # Includes PasswordDeleteError when entry is missing — both swallowed.
        logger.debug("keyring_delete_skipped", profile=profile_name, error=str(exc))


def resolve_token(profile: Profile) -> str | None:
    """Resolve the bearer token for ``profile``.

    Lookup order:
      1. Keyring entry ``(bsvibe, profile.name)``.
      2. ``$BSVIBE_TOKEN`` environment variable.
      3. ``profile.token_ref`` returned verbatim (raw fallback).

    Returns ``None`` when nothing is configured. The caller decides
    whether absence is fatal (most subcommands need a token; ``login``
    obviously does not).
    """
    if (token := get_token(profile.name)) is not None:
        return token
    if (token := os.environ.get(ENV_VAR)) is not None and token != "":
        return token
    return profile.token_ref


def set_refresh_token(profile_name: str, token: str) -> None:
    """Persist a refresh token under ``(SERVICE, "{profile}.refresh")``.

    Same fail-soft contract as :func:`set_token` — a missing keyring
    backend or libsecret error is logged and dropped, never raised, so
    CLI startup survives headless hosts.
    """
    backend = _backend()
    if backend is None:
        return
    try:
        backend.set_password(SERVICE, _refresh_username(profile_name), token)
        logger.debug("keyring_refresh_set", profile=profile_name)
    except Exception as exc:
        logger.warning("keyring_refresh_set_failed", profile=profile_name, error=str(exc))


def get_refresh_token(profile_name: str) -> str | None:
    """Return the stored refresh token or ``None`` if absent / backend down."""
    backend = _backend()
    if backend is None:
        return None
    try:
        return backend.get_password(SERVICE, _refresh_username(profile_name))
    except Exception as exc:
        logger.warning("keyring_refresh_get_failed", profile=profile_name, error=str(exc))
        return None


def delete_refresh_token(profile_name: str) -> None:
    """Idempotent delete of the refresh-token slot."""
    backend = _backend()
    if backend is None:
        return
    try:
        backend.delete_password(SERVICE, _refresh_username(profile_name))
        logger.debug("keyring_refresh_deleted", profile=profile_name)
    except Exception as exc:
        logger.debug("keyring_refresh_delete_skipped", profile=profile_name, error=str(exc))


def make_persist_callback(profile_name: str) -> Callable[["DeviceTokenGrant"], None]:
    """Return the ``on_token_refreshed`` hook for :class:`CliHttpClient`.

    After a 401-then-refresh rotation the client invokes this callback
    with the new :class:`DeviceTokenGrant`; we mirror both halves into
    keyring so the next process invocation picks them up. If the grant
    omits a new refresh token (server-side rotation is optional per
    RFC 6749 §6) the existing refresh slot is left alone.
    """

    def _persist(grant: "DeviceTokenGrant") -> None:
        set_token(profile_name, grant.access_token)
        if grant.refresh_token:
            set_refresh_token(profile_name, grant.refresh_token)

    return _persist


def _refresh_username(profile_name: str) -> str:
    return f"{profile_name}{_REFRESH_SUFFIX}"


__all__ = [
    "SERVICE",
    "ENV_VAR",
    "set_token",
    "get_token",
    "delete_token",
    "resolve_token",
    "set_refresh_token",
    "get_refresh_token",
    "delete_refresh_token",
    "make_persist_callback",
]
