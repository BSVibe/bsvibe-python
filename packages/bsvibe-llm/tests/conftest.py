"""Test environment isolation for ``bsvibe-llm`` tests.

Strips any LLM-related env vars so ``LlmSettings()`` defaults are
deterministic regardless of the host shell.
"""

from __future__ import annotations

import os

import pytest

_SCRUB = (
    "BSGATEWAY_URL",
    "MODEL",
    "FALLBACK_MODELS",
    "RETRY_MAX_ATTEMPTS",
    "RETRY_BASE_DELAY_S",
    "ROUTE_DEFAULT",
)


@pytest.fixture(autouse=True)
def _scrub_llm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in _SCRUB:
        if key in os.environ:
            monkeypatch.delenv(key, raising=False)
