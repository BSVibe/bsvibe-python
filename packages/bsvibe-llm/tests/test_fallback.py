"""Tests for vendor fallback chain."""

from __future__ import annotations

import pytest

from bsvibe_llm.fallback import FallbackChain, FallbackExhaustedError


class TestFallbackChain:
    async def test_returns_first_success(self):
        chain = FallbackChain(["model-a", "model-b", "model-c"])
        attempts: list[str] = []

        async def caller(model: str) -> str:
            attempts.append(model)
            return f"ok:{model}"

        result = await chain.call(caller)
        assert result == "ok:model-a"
        assert attempts == ["model-a"]

    async def test_falls_back_on_failure(self):
        chain = FallbackChain(["model-a", "model-b", "model-c"])
        attempts: list[str] = []

        async def caller(model: str) -> str:
            attempts.append(model)
            if model == "model-a":
                raise ConnectionError("a is down")
            return f"ok:{model}"

        result = await chain.call(caller)
        assert result == "ok:model-b"
        assert attempts == ["model-a", "model-b"]

    async def test_raises_when_all_fail(self):
        chain = FallbackChain(["model-a", "model-b"])

        async def caller(model: str) -> str:
            raise ConnectionError(f"{model} down")

        with pytest.raises(FallbackExhaustedError) as exc_info:
            await chain.call(caller)
        # Exhaustion error must capture the per-model failure list for
        # debugging.
        assert len(exc_info.value.failures) == 2

    async def test_does_not_fallback_on_unretryable_error(self):
        chain = FallbackChain(["model-a", "model-b"])
        attempts: list[str] = []

        async def caller(model: str) -> str:
            attempts.append(model)
            raise ValueError("bad input")

        with pytest.raises(ValueError):
            await chain.call(caller)
        # Only the first model was tried — value errors are programming
        # errors, not vendor outages.
        assert attempts == ["model-a"]

    async def test_empty_chain_raises(self):
        chain = FallbackChain([])

        async def caller(model: str) -> str:
            return "unreachable"

        with pytest.raises(FallbackExhaustedError):
            await chain.call(caller)
