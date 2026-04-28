"""Shared fixtures for bsvibe-audit tests.

Each test starts with a clean environment so audit/auth-flavoured env
vars set on the developer shell can never leak into pydantic-settings
construction.

A second fixture provides a clean structlog ``contextvars`` view per
test — :class:`AuditEmitter` reads ``trace_id`` from contextvars, and
without resetting we would see contamination between tests.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
import structlog


_PREFIXES = (
    "BSVIBE_",
    "AUDIT_",
)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for key in list(os.environ):
        if any(key.startswith(prefix) for prefix in _PREFIXES):
            monkeypatch.delenv(key, raising=False)
    yield


@pytest.fixture(autouse=True)
def _isolate_contextvars() -> Iterator[None]:
    structlog.contextvars.clear_contextvars()
    yield
    structlog.contextvars.clear_contextvars()
