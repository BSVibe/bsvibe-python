"""Retry policy + circuit breaker primitives.

These are intentionally tiny and dependency-free. Each product chooses
its own ``RetryPolicy`` parameters via ``LlmSettings``; the breaker is
the per-process safety net that prevents flooding a sick vendor.

Retryable errors: transport-level (``httpx.RequestError`` /
``ConnectionError`` / ``TimeoutError``) and 5xx surfaced as
``litellm.exceptions.APIConnectionError`` /
``InternalServerError`` / ``ServiceUnavailableError``. We catch broadly
on type name to avoid pinning to a specific litellm minor.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TypeVar

T = TypeVar("T")

# Exception types we treat as transient. Anything else is a programming
# error and propagated unchanged.
_TRANSIENT_TYPE_NAMES: frozenset[str] = frozenset(
    {
        "ConnectionError",
        "TimeoutError",
        "APIConnectionError",
        "InternalServerError",
        "ServiceUnavailableError",
        "RateLimitError",
        "BadGatewayError",
        "ConnectError",
        "ReadTimeout",
        "WriteTimeout",
        "PoolTimeout",
        "TimeoutException",
    }
)


def is_transient(exc: BaseException) -> bool:
    """True when ``exc`` looks like a transient infra failure."""
    if isinstance(exc, ConnectionError | TimeoutError | asyncio.TimeoutError):
        return True
    return type(exc).__name__ in _TRANSIENT_TYPE_NAMES


class RetryError(Exception):
    """Raised when a :class:`RetryPolicy` exhausts all attempts."""


@dataclass
class RetryPolicy:
    """Bounded exponential backoff retry policy.

    Parameters:
        max_attempts: Total attempts including the first. ``1`` disables
            retries.
        base_delay_s: Initial sleep before retry #2.
        max_delay_s: Cap on per-step backoff (default: 30 s).
        jitter: When ``True`` (default), adds 0-25% random jitter to
            each sleep to avoid thundering herd.
    """

    max_attempts: int = 3
    base_delay_s: float = 0.5
    max_delay_s: float = 30.0
    jitter: bool = True

    async def call(self, fn: Callable[[], Awaitable[T]]) -> T:
        """Run ``fn`` up to ``max_attempts`` times with backoff."""
        last_exc: BaseException | None = None
        for attempt in range(self.max_attempts):
            try:
                return await fn()
            except BaseException as exc:  # noqa: BLE001 — caller decides retryability
                if not is_transient(exc):
                    raise
                last_exc = exc
                if attempt + 1 >= self.max_attempts:
                    break
                delay = self.base_delay_s * (2**attempt)
                delay = min(delay, self.max_delay_s)
                if self.jitter:
                    delay *= 1.0 + random.random() * 0.25
                await asyncio.sleep(delay)
        err = RetryError(f"exceeded {self.max_attempts} attempts")
        if last_exc is not None:
            err.__cause__ = last_exc
        raise err


@dataclass
class CircuitBreaker:
    """Per-vendor circuit breaker.

    The breaker counts consecutive failures (any success resets) and
    flips ``is_open`` to True once ``failure_threshold`` is hit. After
    ``recovery_seconds`` the breaker closes optimistically — the next
    call will get a chance, and a single failure re-opens it.
    """

    failure_threshold: int = 5
    recovery_seconds: float = 30.0
    clock: Callable[[], float] = field(default_factory=lambda: time.monotonic)

    _failures: int = field(default=0, init=False)
    _opened_at: float | None = field(default=None, init=False)

    @property
    def is_open(self) -> bool:
        if self._opened_at is None:
            return False
        if self.clock() - self._opened_at >= self.recovery_seconds:
            return False
        return True

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self.failure_threshold:
            self._opened_at = self.clock()

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None


__all__ = ["RetryPolicy", "RetryError", "CircuitBreaker", "is_transient"]
