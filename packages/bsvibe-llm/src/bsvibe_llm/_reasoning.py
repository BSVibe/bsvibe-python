"""Provider-aware reasoning-suppression dispatch.

Compile-time call sites (BSage IngestCompiler, BSNexus orchestrator
templated steps) want short structured output and no chain-of-thought.
Reasoning-capable providers each need a different switch:

* Anthropic Claude Opus 4.x / Sonnet 4.6+ ship extended thinking on by
  default — disable with ``thinking={"type": "disabled"}``.
* OpenAI o-series and gpt-5-thinking always reason — minimise with
  ``reasoning_effort="minimal"``.
* Ollama reasoning models (glm-4.7, qwen3-thinking, deepseek-r1) need
  ``think: false`` in the request body. **litellm drops this kwarg on
  the wire**, so we must bypass litellm and POST ``/api/chat`` directly.
* mlx-lm / vllm expose OpenAI-compatible endpoints; we can't tell them
  apart from "real" OpenAI by model string. We attach both
  ``think: false`` and ``reasoning_effort`` via ``extra_body`` — servers
  that don't recognise either ignore it.

Detection here uses model-string prefixes / families. The ollama probe
(``_ollama_probe``) supplies a runtime answer for whether a specific
ollama model is reasoning; we accept it as input rather than calling it
from inside this module so unit tests can stay sync and fast.
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class SuppressionStrategy(str, Enum):
    """How a single ``complete()`` call must suppress reasoning.

    The client picks the strategy from ``decide_strategy`` and dispatches:
    NONE / ANTHROPIC / OPENAI / EXTRA_BODY -> mutate kwargs and stay on
    the litellm path. OLLAMA_BYPASS -> skip litellm, POST directly.
    """

    NONE = "none"
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    OLLAMA_BYPASS = "ollama_bypass"
    EXTRA_BODY = "extra_body"


# Model string prefixes / families that ship reasoning by default.
_ANTHROPIC_REASONING_FAMILIES = (
    "claude-opus-4-",
    "claude-sonnet-4-6",
    "claude-sonnet-4-7",
    "claude-opus-4-7",
)
_OPENAI_REASONING_PREFIXES = (
    "openai/o1",
    "openai/o3",
    "openai/o4",
)
_OPENAI_REASONING_SUBSTRINGS = ("gpt-5-thinking",)
_OLLAMA_PREFIXES = ("ollama/", "ollama_chat/")


def _strip_provider(model: str) -> str:
    """``"anthropic/claude-opus-4-7"`` -> ``"claude-opus-4-7"``."""
    if "/" in model:
        return model.split("/", 1)[1]
    return model


def is_anthropic_reasoning(model: str) -> bool:
    """``True`` for Anthropic models that emit extended thinking by default."""
    bare = _strip_provider(model)
    return any(bare.startswith(prefix) for prefix in _ANTHROPIC_REASONING_FAMILIES)


def is_openai_reasoning(model: str) -> bool:
    """``True`` for OpenAI o-series and gpt-5-thinking."""
    if any(model.startswith(prefix) for prefix in _OPENAI_REASONING_PREFIXES):
        return True
    return any(token in model for token in _OPENAI_REASONING_SUBSTRINGS)


def is_ollama_model(model: str) -> bool:
    return any(model.startswith(prefix) for prefix in _OLLAMA_PREFIXES)


def is_mlx_or_vllm_endpoint(api_base: str) -> bool:
    """Heuristic: api_base is an OpenAI-compat self-hosted server.

    Real OpenAI / Anthropic SaaS endpoints aren't ``/v1`` on localhost or
    private hosts. We cast a wide net here because the suppression payload
    (``extra_body``) is a no-op when the server doesn't recognise the
    keys.
    """
    if not api_base:
        return False
    lowered = api_base.lower()
    return (
        "localhost" in lowered
        or "127.0.0.1" in lowered
        or ":8080" in lowered
        or ":11434" in lowered  # ollama default
        or "/v1" in lowered
    )


def decide_strategy(
    model: str,
    *,
    api_base: str = "",
    has_tools: bool = False,
    ollama_is_reasoning: bool = False,
) -> SuppressionStrategy:
    """Select the suppression strategy for one call.

    Args:
        model: LiteLLM model string (e.g. ``"anthropic/claude-opus-4-7"``).
        api_base: Optional explicit API base URL. Used for the OpenAI-compat
            self-hosted heuristic only.
        has_tools: When True and the model would need ollama bypass, we
            fall back to litellm (the bypass path doesn't implement tool
            calling) — caller logs a warning.
        ollama_is_reasoning: Output of the ollama probe. Caller is
            responsible for passing a fresh value; we don't probe here.
    """
    if is_anthropic_reasoning(model):
        return SuppressionStrategy.ANTHROPIC
    if is_openai_reasoning(model):
        return SuppressionStrategy.OPENAI
    if is_ollama_model(model):
        if ollama_is_reasoning and not has_tools:
            return SuppressionStrategy.OLLAMA_BYPASS
        # Non-reasoning ollama, or reasoning + tools: stay on litellm.
        return SuppressionStrategy.NONE
    if is_mlx_or_vllm_endpoint(api_base):
        return SuppressionStrategy.EXTRA_BODY
    return SuppressionStrategy.NONE


def apply_to_kwargs(strategy: SuppressionStrategy, kwargs: dict[str, Any]) -> dict[str, Any]:
    """Mutate ``kwargs`` in place per strategy and return it.

    Idempotent — callers shouldn't pre-set the relevant fields, but if
    they do we don't clobber explicit values (the caller knows better).
    """
    if strategy is SuppressionStrategy.ANTHROPIC:
        kwargs.setdefault("thinking", {"type": "disabled"})
    elif strategy is SuppressionStrategy.OPENAI:
        kwargs.setdefault("reasoning_effort", "minimal")
    elif strategy is SuppressionStrategy.EXTRA_BODY:
        existing = kwargs.get("extra_body") or {}
        existing.setdefault("think", False)
        existing.setdefault("reasoning_effort", "minimal")
        kwargs["extra_body"] = existing
    return kwargs


__all__ = [
    "SuppressionStrategy",
    "decide_strategy",
    "apply_to_kwargs",
    "is_anthropic_reasoning",
    "is_openai_reasoning",
    "is_ollama_model",
    "is_mlx_or_vllm_endpoint",
]
