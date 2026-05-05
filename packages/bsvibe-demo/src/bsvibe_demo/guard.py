"""Demo guard — prevents real LLM provider calls in demo mode.

When ``BSVIBE_DEMO_MODE=true``, the demo backend MUST NOT spend paid LLM
quota on demo traffic. The guard:

1. Forces LiteLLM ``mock_response`` so calls return canned text without
   hitting real providers.
2. Strips ``api_key`` from kwargs so providers reject the call even if
   mock_response is somehow bypassed.
3. In ``strict=True`` mode, raises ``DemoLLMBlockedError`` when caller tries to
   set a paid provider's ``api_base`` (defense in depth).

The guard is invoked from the LiteLLM ``async_pre_call_hook`` path
(see ``bsgateway/routing/hook.py``) before any provider request.
"""

from __future__ import annotations

import os

DEMO_MOCK_RESPONSE = (
    "[DEMO MODE] This is a mocked LLM response. "
    "Real LLM calls are disabled in the demo environment."
)

PAID_PROVIDER_HOSTS = (
    "api.openai.com",
    "api.anthropic.com",
    "generativelanguage.googleapis.com",
    "api.cohere.ai",
    "api.mistral.ai",
)


class DemoLLMBlockedError(Exception):
    """Raised when demo guard blocks a real LLM call (strict mode)."""


def is_demo_mode() -> bool:
    """Return True when BSVIBE_DEMO_MODE env var is set to a truthy value."""
    raw = os.environ.get("BSVIBE_DEMO_MODE", "").strip().lower()
    return raw in ("true", "1", "yes", "on")


def enforce_demo_llm_mock(kwargs: dict[str, object], *, strict: bool = False) -> None:
    """Mutate ``kwargs`` so any LLM call is forced through LiteLLM mock_response.

    No-op when not in demo mode. In demo mode:
    - Sets ``mock_response`` so LiteLLM returns canned text
    - Removes ``api_key`` so paid providers reject the call
    - In ``strict`` mode, raises ``DemoLLMBlockedError`` if ``api_base`` points at
      a known paid provider host
    """
    if not is_demo_mode():
        return

    if strict:
        api_base = str(kwargs.get("api_base", "") or "")
        for host in PAID_PROVIDER_HOSTS:
            if host in api_base:
                raise DemoLLMBlockedError(
                    f"Demo mode blocks real provider calls (api_base={api_base!r})"
                )

    kwargs["mock_response"] = DEMO_MOCK_RESPONSE
    kwargs.pop("api_key", None)
