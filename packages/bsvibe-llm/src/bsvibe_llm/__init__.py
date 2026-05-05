"""BSVibe LLM client — public API.

Decision #11 (Lockin): all four BSVibe products call LLMs **through
BSGateway** by default. Direct vendor calls are an explicit opt-in
(``direct=True``).

Stable imports:

.. code-block:: python

    from bsvibe_llm import (
        LlmClient,
        LlmSettings,
        RunAuditMetadata,
        CompletionResult,
        RetryPolicy,
        RetryError,
        CircuitBreaker,
        FallbackChain,
        FallbackExhaustedError,
    )
"""

from __future__ import annotations

from bsvibe_llm.client import CompletionResult, LlmClient
from bsvibe_llm.fallback import FallbackChain, FallbackExhaustedError
from bsvibe_llm.metadata import RunAuditMetadata
from bsvibe_llm.retry import CircuitBreaker, RetryError, RetryPolicy
from bsvibe_llm.settings import LlmSettings

__version__ = "0.2.0"

__all__ = [
    "LlmClient",
    "LlmSettings",
    "RunAuditMetadata",
    "CompletionResult",
    "RetryPolicy",
    "RetryError",
    "CircuitBreaker",
    "FallbackChain",
    "FallbackExhaustedError",
    "__version__",
]
