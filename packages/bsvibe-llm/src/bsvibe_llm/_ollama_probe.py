"""Ollama capability probe + direct ``/api/chat`` bypass.

Why this exists: ``litellm.acompletion(model="ollama/glm-4.7-flash", ...)``
silently drops the ``think`` kwarg on the wire (visible only in
``extra_kwargs`` debug logs). For reasoning models that emit hundreds of
CoT tokens by default, this is fatal — generation that would take 50s
takes 600s+ and times out.

Detection:

1. **Model-family heuristic** — substrings like ``"glm"``, ``"thinking"``,
   ``"r1"`` (deepseek-r1) catch most reasoning Ollama models without a
   network round-trip. Used as the cheap fallback.
2. **``/api/show`` probe** — the authoritative source. Returns the
   model's declared capabilities; we look for a ``thinking`` flag or a
   model template that mentions reasoning. Cached per model name.

Bypass: when the probe says reasoning, we POST to ``/api/chat`` with
``think: false`` and translate the response shape into the litellm
``ModelResponse`` form so callers see no difference.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import structlog
from litellm.types.utils import Choices, Message, ModelResponse, Usage

logger = structlog.get_logger(__name__)


_DEFAULT_OLLAMA_BASE = "http://localhost:11434"
_PROBE_TIMEOUT_S = 5.0
_REASONING_FAMILY_HINTS = (
    "glm",
    "thinking",
    "deepseek-r1",
    "qwen3-thinking",
    "qwq",
)


def _bare_model_name(model: str) -> str:
    """``"ollama/glm-4.7-flash"`` -> ``"glm-4.7-flash"``."""
    if "/" in model:
        return model.split("/", 1)[1]
    return model


def _family_hint_says_reasoning(bare_model: str) -> bool:
    lowered = bare_model.lower()
    return any(hint in lowered for hint in _REASONING_FAMILY_HINTS)


# Cache: {(model, base): bool}. Probe is per-process; a restart reprobes.
_PROBE_CACHE: dict[tuple[str, str], bool] = {}


async def is_reasoning_model(model: str, *, api_base: str = "") -> bool:
    """Return True iff the ollama model is reasoning-capable.

    Probes ``/api/show`` once per (model, base) and caches the result. On
    probe failure, falls back to the family-name heuristic.
    """
    base = api_base or _DEFAULT_OLLAMA_BASE
    key = (model, base)
    if key in _PROBE_CACHE:
        return _PROBE_CACHE[key]

    bare = _bare_model_name(model)
    try:
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT_S) as client:
            resp = await client.post(
                f"{base.rstrip('/')}/api/show",
                json={"name": bare},
            )
            resp.raise_for_status()
            data = resp.json()
    except (httpx.HTTPError, asyncio.TimeoutError) as exc:
        logger.debug("ollama_probe_failed", model=model, error=str(exc))
        result = _family_hint_says_reasoning(bare)
        _PROBE_CACHE[key] = result
        return result

    capabilities = data.get("capabilities") or []
    if "thinking" in capabilities or "reasoning" in capabilities:
        _PROBE_CACHE[key] = True
        return True

    template = (data.get("template") or "") + (data.get("modelfile") or "")
    if "<thinking>" in template.lower() or "<think>" in template.lower():
        _PROBE_CACHE[key] = True
        return True

    # Fallback: family hint when /api/show didn't expose capabilities.
    result = _family_hint_says_reasoning(bare)
    _PROBE_CACHE[key] = result
    return result


def reset_cache() -> None:
    """Test-only helper; clears the probe memoisation."""
    _PROBE_CACHE.clear()


async def chat_direct(
    *,
    model: str,
    messages: list[dict[str, Any]],
    api_base: str = "",
    body: dict[str, Any] | None = None,
    timeout_s: float = 120.0,
) -> ModelResponse:
    """POST ``/api/chat`` directly, bypassing litellm.

    Returns a litellm ``ModelResponse``-shaped object so the caller's
    normalisation path is unchanged.
    """
    base = api_base or _DEFAULT_OLLAMA_BASE
    bare = _bare_model_name(model)

    payload: dict[str, Any] = {
        "model": bare,
        "messages": messages,
        "stream": False,
        "think": False,
    }
    if body:
        payload.update(body)

    async with httpx.AsyncClient(timeout=timeout_s) as client:
        resp = await client.post(f"{base.rstrip('/')}/api/chat", json=payload)
        resp.raise_for_status()
        data = resp.json()

    return _normalise_ollama_response(data, model=model)


def _normalise_ollama_response(data: dict[str, Any], *, model: str) -> ModelResponse:
    """Map an ollama ``/api/chat`` response onto litellm's ``ModelResponse``.

    Ollama returns: ``{"message": {"role": "assistant", "content": "..."},
    "done": True, "prompt_eval_count": N, "eval_count": M, ...}``.
    """
    message_data = data.get("message") or {}
    content = message_data.get("content", "")
    finish_reason = "stop" if data.get("done") else "length"

    msg = Message(content=content, role="assistant")
    choice = Choices(finish_reason=finish_reason, index=0, message=msg)
    usage = Usage(
        prompt_tokens=int(data.get("prompt_eval_count") or 0),
        completion_tokens=int(data.get("eval_count") or 0),
        total_tokens=int(data.get("prompt_eval_count") or 0) + int(data.get("eval_count") or 0),
    )

    response = ModelResponse(
        id=data.get("id", ""),
        choices=[choice],
        created=int(data.get("created_at_unix") or 0),
        model=model,
        object="chat.completion",
        usage=usage,
    )
    return response


__all__ = ["is_reasoning_model", "chat_direct", "reset_cache"]
