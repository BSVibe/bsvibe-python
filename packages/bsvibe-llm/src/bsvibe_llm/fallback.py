"""Vendor fallback chain.

Tries each model in order. Falls back to the next on transient errors
only — programming errors (``ValueError``, ``TypeError``) propagate
immediately because retrying with a different vendor will not fix bad
input.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TypeVar

from bsvibe_llm.retry import RetryError, is_transient

T = TypeVar("T")


@dataclass
class FallbackExhaustedError(Exception):
    """Raised when every model in a :class:`FallbackChain` fails.

    Carries the list of (model, exception) pairs so callers can log
    each vendor's failure mode.
    """

    failures: list[tuple[str, BaseException]] = field(default_factory=list)

    def __str__(self) -> str:  # pragma: no cover - debug repr
        if not self.failures:
            return "fallback chain is empty"
        parts = [f"{model}: {exc!r}" for model, exc in self.failures]
        return "all fallback models failed: " + "; ".join(parts)


@dataclass
class FallbackChain:
    """Ordered model list with first-success-wins semantics."""

    models: list[str]

    async def call(self, fn: Callable[[str], Awaitable[T]]) -> T:
        """Try each model in order, falling back on transient errors."""
        failures: list[tuple[str, BaseException]] = []
        for model in self.models:
            try:
                return await fn(model)
            except RetryError as exc:
                # RetryPolicy already validated transience and exhausted
                # its budget for this model — fall through to the next.
                failures.append((model, exc))
            except BaseException as exc:  # noqa: BLE001 — caller decides retryability
                if not is_transient(exc):
                    raise
                failures.append((model, exc))
        raise FallbackExhaustedError(failures=failures)


__all__ = ["FallbackChain", "FallbackExhaustedError"]
