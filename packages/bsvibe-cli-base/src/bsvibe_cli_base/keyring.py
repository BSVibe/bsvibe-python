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
from typing import Any

import structlog

from bsvibe_cli_base.config import Profile

logger = structlog.get_logger(__name__)

SERVICE = "bsvibe"
ENV_VAR = "BSVIBE_TOKEN"


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


__all__ = [
    "SERVICE",
    "ENV_VAR",
    "set_token",
    "get_token",
    "delete_token",
    "resolve_token",
]
