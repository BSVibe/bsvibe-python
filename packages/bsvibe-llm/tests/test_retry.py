"""Tests for retry + circuit breaker policy."""

from __future__ import annotations

import asyncio

import pytest

from bsvibe_llm.retry import CircuitBreaker, RetryPolicy, RetryError


class TestRetryPolicy:
    async def test_returns_value_when_first_call_succeeds(self):
        policy = RetryPolicy(max_attempts=3, base_delay_s=0.001)

        async def call() -> str:
            return "ok"

        result = await policy.call(call)
        assert result == "ok"

    async def test_retries_on_transient_failure(self):
        policy = RetryPolicy(max_attempts=3, base_delay_s=0.001)
        attempts = {"n": 0}

        async def call() -> str:
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise ConnectionError("transient")
            return "ok"

        result = await policy.call(call)
        assert result == "ok"
        assert attempts["n"] == 3

    async def test_raises_retry_error_after_max_attempts(self):
        policy = RetryPolicy(max_attempts=2, base_delay_s=0.001)
        attempts = {"n": 0}

        async def call() -> str:
            attempts["n"] += 1
            raise ConnectionError("always fails")

        with pytest.raises(RetryError) as exc_info:
            await policy.call(call)
        assert attempts["n"] == 2
        # The original exception must be reachable for diagnostics.
        assert exc_info.value.__cause__ is not None

    async def test_does_not_retry_unretryable_error(self):
        # ``ValueError`` is a programming error, not a transient infra
        # failure → no retries.
        policy = RetryPolicy(max_attempts=3, base_delay_s=0.001)
        attempts = {"n": 0}

        async def call() -> str:
            attempts["n"] += 1
            raise ValueError("bad input")

        with pytest.raises(ValueError):
            await policy.call(call)
        assert attempts["n"] == 1

    async def test_exponential_backoff_grows(self, monkeypatch):
        sleeps: list[float] = []

        async def fake_sleep(delay: float) -> None:
            sleeps.append(delay)

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)

        policy = RetryPolicy(max_attempts=4, base_delay_s=0.1, jitter=False)
        attempts = {"n": 0}

        async def call() -> str:
            attempts["n"] += 1
            if attempts["n"] < 4:
                raise ConnectionError("transient")
            return "ok"

        await policy.call(call)
        # 3 sleeps between 4 attempts. Exponential: 0.1, 0.2, 0.4.
        assert len(sleeps) == 3
        assert sleeps[0] == pytest.approx(0.1)
        assert sleeps[1] == pytest.approx(0.2)
        assert sleeps[2] == pytest.approx(0.4)


class TestCircuitBreaker:
    def test_starts_closed(self):
        cb = CircuitBreaker(failure_threshold=3, recovery_seconds=10.0)
        assert cb.is_open is False

    def test_opens_after_threshold(self):
        cb = CircuitBreaker(failure_threshold=2, recovery_seconds=10.0)
        cb.record_failure()
        assert cb.is_open is False
        cb.record_failure()
        assert cb.is_open is True

    def test_success_resets_failures(self):
        cb = CircuitBreaker(failure_threshold=2, recovery_seconds=10.0)
        cb.record_failure()
        cb.record_success()
        cb.record_failure()
        # Two failures total but interleaved → still closed.
        assert cb.is_open is False

    def test_recovers_after_window(self, monkeypatch):
        clock = {"t": 1000.0}

        def now() -> float:
            return clock["t"]

        cb = CircuitBreaker(failure_threshold=1, recovery_seconds=5.0, clock=now)
        cb.record_failure()
        assert cb.is_open is True
        clock["t"] = 1006.0
        # After recovery window elapses, is_open returns False (half-open).
        assert cb.is_open is False
