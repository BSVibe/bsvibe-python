"""Smoke tests for the public ``bsvibe_llm`` import surface."""

from __future__ import annotations


def test_public_imports():
    from bsvibe_llm import (
        CircuitBreaker,
        CompletionResult,
        FallbackChain,
        FallbackExhaustedError,
        LlmClient,
        LlmSettings,
        RetryError,
        RetryPolicy,
        RunAuditMetadata,
        __version__,
    )

    assert __version__
    assert LlmClient is not None
    assert LlmSettings is not None
    assert RunAuditMetadata is not None
    assert RetryPolicy is not None
    assert RetryError is not None
    assert CircuitBreaker is not None
    assert FallbackChain is not None
    assert FallbackExhaustedError is not None
    assert CompletionResult is not None
