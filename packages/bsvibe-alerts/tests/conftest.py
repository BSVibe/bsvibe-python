"""Shared fixtures for bsvibe-alerts tests.

Each test must start with a clean environment — pydantic-settings auto-loads
``BSVIBE_*`` and channel-specific env vars (``TELEGRAM_*`` / ``SLACK_*``)
which would otherwise leak between tests on developer shells.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

_PREFIXES = (
    "BSVIBE_",
    "ALERT_",
    "TELEGRAM_",
    "SLACK_",
)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for key in list(os.environ):
        if any(key.startswith(prefix) for prefix in _PREFIXES):
            monkeypatch.delenv(key, raising=False)
    yield
