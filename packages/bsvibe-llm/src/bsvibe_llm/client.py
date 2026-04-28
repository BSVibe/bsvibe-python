"""``LlmClient`` — LiteLLM adapter routed through BSGateway by default.

Decision #11 (Lockin §3): every BSVibe product calls LLMs **through
BSGateway**. The gateway owns the BSupervisor ``run.pre`` / ``run.post``
audit hop and the cost-aware routing decisions. Direct vendor calls are
an explicit per-call opt-in (``direct=True``) — useful for the routing
hook itself, which must not recurse.

Wire format: callers pass a :class:`RunAuditMetadata` instance; we
flatten it into the ``metadata`` kwarg accepted by
``litellm.acompletion`` (which BSGateway parses on its
``async_pre_call_hook``). The dict shape mirrors
``docs/BSNEXUS_METADATA_CONTRACT.md`` exactly.

The client wraps every vendor call in a :class:`RetryPolicy` and a
:class:`FallbackChain` so transient errors and a single sick provider
don't surface as user errors.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import litellm
import structlog

from bsvibe_llm.fallback import FallbackChain
from bsvibe_llm.metadata import RunAuditMetadata
from bsvibe_llm.retry import RetryPolicy
from bsvibe_llm.settings import LlmSettings

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class CompletionResult:
    """Normalised response shape returned by :meth:`LlmClient.complete`."""

    text: str
    model: str
    finish_reason: str
    prompt_tokens: int
    completion_tokens: int
    raw: Any = None  # litellm response object for advanced consumers


class LlmClient:
    """Thin async wrapper over ``litellm.acompletion``.

    All vendor traffic flows through this class so cost tracking and
    audit-metadata plumbing live in one place.
    """

    def __init__(
        self,
        settings: LlmSettings | None = None,
        *,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        self.settings = settings or LlmSettings()
        self._retry = retry_policy or RetryPolicy(
            max_attempts=self.settings.retry_max_attempts,
            base_delay_s=self.settings.retry_base_delay_s,
        )

    async def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        metadata: RunAuditMetadata,
        model: str | None = None,
        direct: bool = False,
        timeout_s: float | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        tools: list[dict[str, Any]] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> CompletionResult:
        """Run one completion through LiteLLM.

        Args:
            messages: Chat-completion messages list.
            metadata: Run-audit metadata. Required (Decision #11 — no
                anonymous traffic).
            model: Override the default model.
            direct: Skip BSGateway and call the vendor directly.
            timeout_s: Per-attempt timeout (passed straight to litellm).
            max_tokens / temperature / tools / extra: forwarded to
                ``litellm.acompletion``.
        """
        if metadata is None:
            raise ValueError("LlmClient.complete() requires a RunAuditMetadata instance")
        if not isinstance(metadata, RunAuditMetadata):
            raise TypeError(f"metadata must be RunAuditMetadata, got {type(metadata).__name__}")

        primary_model = model or self.settings.model
        chain_models = (
            [primary_model, *self.settings.fallback_models] if primary_model else list(self.settings.fallback_models)
        )
        chain_models = [m for m in chain_models if m]
        if not chain_models:
            raise ValueError("LlmClient.complete(): no model resolved (settings.model empty and no override)")

        chain = FallbackChain(chain_models)

        async def call_for_model(resolved_model: str) -> CompletionResult:
            async def attempt() -> CompletionResult:
                kwargs = self._build_kwargs(
                    model=resolved_model,
                    messages=messages,
                    metadata=metadata,
                    direct=direct,
                    timeout_s=timeout_s,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    tools=tools,
                    extra=extra,
                )
                logger.debug(
                    "llm_call_start",
                    model=resolved_model,
                    direct=direct,
                    tenant_id=metadata.tenant_id,
                    run_id=metadata.run_id,
                )
                response = await litellm.acompletion(**kwargs)
                return _normalise(response, model=resolved_model)

            return await self._retry.call(attempt)

        return await chain.call(call_for_model)

    # ──────────────────────────── helpers ────────────────────────────

    def _build_kwargs(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        metadata: RunAuditMetadata,
        direct: bool,
        timeout_s: float | None,
        max_tokens: int | None,
        temperature: float | None,
        tools: list[dict[str, Any]] | None,
        extra: dict[str, Any] | None,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": list(messages),
            "metadata": metadata.to_metadata(),
        }
        api_base = self._resolve_api_base(direct=direct)
        if api_base:
            kwargs["api_base"] = api_base
        if timeout_s is not None:
            kwargs["timeout"] = timeout_s
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if temperature is not None:
            kwargs["temperature"] = temperature
        if tools:
            kwargs["tools"] = tools
        if extra:
            kwargs.update(extra)
        return kwargs

    def _resolve_api_base(self, *, direct: bool) -> str:
        """Return the effective ``api_base`` for the current call."""
        if direct:
            return ""
        if self.settings.route_default != "bsgateway":
            return ""
        return self.settings.bsgateway_url or ""


def _normalise(response: Any, *, model: str) -> CompletionResult:
    """Map a litellm response object onto our ``CompletionResult``."""
    text = ""
    finish_reason = "stop"
    prompt_tokens = 0
    completion_tokens = 0

    choices = getattr(response, "choices", None) or []
    if choices:
        choice = choices[0]
        message = getattr(choice, "message", None)
        if message is not None:
            content = getattr(message, "content", None)
            if content is None and isinstance(message, dict):
                content = message.get("content")
            text = content or ""
        fr = getattr(choice, "finish_reason", None)
        if fr:
            finish_reason = str(fr)

    usage = getattr(response, "usage", None)
    if usage is not None:
        prompt_tokens = _safe_int(getattr(usage, "prompt_tokens", None))
        completion_tokens = _safe_int(getattr(usage, "completion_tokens", None))

    return CompletionResult(
        text=text,
        model=model,
        finish_reason=finish_reason,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        raw=response,
    )


def _safe_int(value: Any) -> int:
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


# Type alias kept for forward compatibility with adapter-style callers.
LlmCallFn = Callable[[dict[str, Any]], Awaitable[Any]]


__all__ = ["LlmClient", "CompletionResult"]
