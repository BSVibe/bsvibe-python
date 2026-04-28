"""Shared fixtures for bsvibe-sqlalchemy tests.

Each test runs against a clean environment so settings auto-loading
from the dev shell does not leak ``DATABASE_URL`` etc. into the test.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

_PREFIXES = ("BSVIBE_", "DATABASE_", "DB_", "TEST_")


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for key in list(os.environ):
        if any(key.startswith(prefix) for prefix in _PREFIXES):
            monkeypatch.delenv(key, raising=False)
    yield
