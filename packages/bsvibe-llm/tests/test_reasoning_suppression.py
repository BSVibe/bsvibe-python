"""Tests for provider-aware reasoning suppression.

Compile-time call sites (e.g. BSage IngestCompiler) want short, structured
JSON output and have no use for chain-of-thought. Reasoning models default
to emitting it anyway — Anthropic Opus 4.7 ships extended thinking on,
OpenAI o-series always reasons, Ollama reasoning models (glm-4.7-flash,
qwen3-thinking) emit hundreds of CoT tokens before the answer, and litellm
silently drops the ``think`` kwarg on the wire to ollama.

``LlmClient.complete(suppress_reasoning=True)`` is the single switch that
turns reasoning off correctly per provider.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bsvibe_llm.client import LlmClient
from bsvibe_llm.metadata import RunAuditMetadata
from bsvibe_llm.settings import LlmSettings


def _fake_response(text: str = "ok"):
    msg = MagicMock()
    msg.content = text
    msg.tool_calls = None
    choice = MagicMock()
    choice.message = msg
    choice.finish_reason = "stop"
    resp = MagicMock()
    resp.choices = [choice]
    resp.usage = MagicMock(prompt_tokens=1, completion_tokens=1, total_tokens=2)
    return resp


@pytest.fixture
def mock_acompletion():
    with patch("bsvibe_llm.client.litellm.acompletion", new_callable=AsyncMock) as mock:
        mock.return_value = _fake_response()
        yield mock


def _meta() -> RunAuditMetadata:
    return RunAuditMetadata(tenant_id="t-1", run_id="r-1")


def _client(model: str, *, bsgateway_url: str = "") -> LlmClient:
    return LlmClient(settings=LlmSettings(bsgateway_url=bsgateway_url, model=model))


class TestAnthropicSuppression:
    """Claude Opus 4.7 / Sonnet 4.6+ ship extended thinking ON by default.

    Suppression: pass ``thinking={"type": "disabled"}`` so the API returns
    a plain content block with no reasoning prefix.
    """

    async def test_opus_4_7_adds_thinking_disabled(self, mock_acompletion):
        client = _client("anthropic/claude-opus-4-7")
        await client.complete(
            messages=[{"role": "user", "content": "hi"}],
            metadata=_meta(),
            suppress_reasoning=True,
        )
        kwargs = mock_acompletion.call_args.kwargs
        assert kwargs.get("thinking") == {"type": "disabled"}

    async def test_sonnet_4_6_adds_thinking_disabled(self, mock_acompletion):
        client = _client("anthropic/claude-sonnet-4-6")
        await client.complete(
            messages=[{"role": "user", "content": "hi"}],
            metadata=_meta(),
            suppress_reasoning=True,
        )
        kwargs = mock_acompletion.call_args.kwargs
        assert kwargs.get("thinking") == {"type": "disabled"}

    async def test_sonnet_3_5_no_op(self, mock_acompletion):
        # Pre-extended-thinking model — suppression must not poison kwargs.
        client = _client("anthropic/claude-3-5-sonnet-20241022")
        await client.complete(
            messages=[{"role": "user", "content": "hi"}],
            metadata=_meta(),
            suppress_reasoning=True,
        )
        kwargs = mock_acompletion.call_args.kwargs
        assert "thinking" not in kwargs

    async def test_haiku_no_op(self, mock_acompletion):
        client = _client("anthropic/claude-haiku-4-5-20251001")
        await client.complete(
            messages=[{"role": "user", "content": "hi"}],
            metadata=_meta(),
            suppress_reasoning=True,
        )
        kwargs = mock_acompletion.call_args.kwargs
        assert "thinking" not in kwargs

    async def test_suppress_false_leaves_thinking_unset(self, mock_acompletion):
        # The default — caller wants reasoning, suppression off.
        client = _client("anthropic/claude-opus-4-7")
        await client.complete(
            messages=[{"role": "user", "content": "hi"}],
            metadata=_meta(),
        )
        kwargs = mock_acompletion.call_args.kwargs
        assert "thinking" not in kwargs


class TestOpenAISuppression:
    """OpenAI o-series and gpt-5-thinking always reason.

    Suppression: ``reasoning_effort="minimal"`` is the cheapest tier,
    drastically reduces reasoning token usage and latency.
    """

    async def test_o1_adds_reasoning_effort_minimal(self, mock_acompletion):
        client = _client("openai/o1-2024-12-17")
        await client.complete(
            messages=[{"role": "user", "content": "hi"}],
            metadata=_meta(),
            suppress_reasoning=True,
        )
        kwargs = mock_acompletion.call_args.kwargs
        assert kwargs.get("reasoning_effort") == "minimal"

    async def test_o3_adds_reasoning_effort_minimal(self, mock_acompletion):
        client = _client("openai/o3-mini")
        await client.complete(
            messages=[{"role": "user", "content": "hi"}],
            metadata=_meta(),
            suppress_reasoning=True,
        )
        kwargs = mock_acompletion.call_args.kwargs
        assert kwargs.get("reasoning_effort") == "minimal"

    async def test_gpt_5_thinking_adds_reasoning_effort(self, mock_acompletion):
        client = _client("openai/gpt-5-thinking")
        await client.complete(
            messages=[{"role": "user", "content": "hi"}],
            metadata=_meta(),
            suppress_reasoning=True,
        )
        kwargs = mock_acompletion.call_args.kwargs
        assert kwargs.get("reasoning_effort") == "minimal"

    async def test_gpt_4o_no_op(self, mock_acompletion):
        client = _client("openai/gpt-4o")
        await client.complete(
            messages=[{"role": "user", "content": "hi"}],
            metadata=_meta(),
            suppress_reasoning=True,
        )
        kwargs = mock_acompletion.call_args.kwargs
        assert "reasoning_effort" not in kwargs


class TestOllamaSuppression:
    """Ollama reasoning models (glm-4.7-flash, qwen3-thinking, deepseek-r1)
    need ``think: false`` in the request body. litellm DOES NOT forward
    this kwarg to ollama, so we must bypass litellm and POST to
    ``/api/chat`` directly via httpx.
    """

    async def test_reasoning_ollama_bypasses_litellm(self, mock_acompletion):
        # Probe says reasoning. Bypass litellm.acompletion entirely.
        client = _client("ollama/glm-4.7-flash", bsgateway_url="")

        with patch(
            "bsvibe_llm.client._ollama_chat_direct",
            new_callable=AsyncMock,
        ) as mock_direct:
            mock_direct.return_value = _fake_response()
            with patch(
                "bsvibe_llm.client._ollama_is_reasoning",
                return_value=True,
            ):
                await client.complete(
                    messages=[{"role": "user", "content": "hi"}],
                    metadata=_meta(),
                    suppress_reasoning=True,
                )

        # litellm path NOT taken; bypass took over.
        mock_acompletion.assert_not_called()
        mock_direct.assert_called_once()
        # And the bypass call carried think=False in the body kwargs.
        call_kwargs = mock_direct.call_args.kwargs
        body = call_kwargs.get("body") or {}
        assert body.get("think") is False

    async def test_non_reasoning_ollama_uses_litellm(self, mock_acompletion):
        # llama3 (no thinking capability). Stay on litellm path; no bypass.
        client = _client("ollama/llama3", bsgateway_url="")

        with patch(
            "bsvibe_llm.client._ollama_is_reasoning",
            return_value=False,
        ):
            await client.complete(
                messages=[{"role": "user", "content": "hi"}],
                metadata=_meta(),
                suppress_reasoning=True,
            )

        mock_acompletion.assert_called_once()

    async def test_ollama_suppress_false_uses_litellm(self, mock_acompletion):
        # Even reasoning ollama: if suppress_reasoning=False, we want the
        # CoT — go through litellm normally.
        client = _client("ollama/glm-4.7-flash", bsgateway_url="")

        await client.complete(
            messages=[{"role": "user", "content": "hi"}],
            metadata=_meta(),
        )

        mock_acompletion.assert_called_once()


class TestMlxAndVllmSuppression:
    """mlx-lm and vllm both expose OpenAI-compatible endpoints. We can't
    detect them from the model string alone (it's usually openai/<name>);
    we use ``api_base`` host hints instead. Worst case the server ignores
    the kwargs.
    """

    async def test_mlx_endpoint_via_api_base_adds_extra_body(self, mock_acompletion):
        # Direct mode so api_base flows from settings.
        settings = LlmSettings(
            bsgateway_url="",
            model="openai/qwen3-thinking",
        )
        client = LlmClient(settings=settings)

        # Caller sets api_base via extra to point at local mlx-lm.
        await client.complete(
            messages=[{"role": "user", "content": "hi"}],
            metadata=_meta(),
            suppress_reasoning=True,
            direct=True,
            extra={"api_base": "http://localhost:8080/v1"},
        )

        kwargs = mock_acompletion.call_args.kwargs
        extra_body = kwargs.get("extra_body") or {}
        assert extra_body.get("think") is False
        assert extra_body.get("reasoning_effort") == "minimal"


class TestSuppressionDefaultOff:
    """Backwards-compat: existing callers that never pass
    ``suppress_reasoning`` see no behavioural change."""

    async def test_default_does_not_inject_anything(self, mock_acompletion):
        client = _client("anthropic/claude-opus-4-7")
        await client.complete(
            messages=[{"role": "user", "content": "hi"}],
            metadata=_meta(),
        )
        kwargs = mock_acompletion.call_args.kwargs
        assert "thinking" not in kwargs
        assert "reasoning_effort" not in kwargs
        assert "extra_body" not in kwargs


class TestToolCallsFallback:
    """Bypass path doesn't support tool_calls. If caller passes tools AND
    suppress_reasoning AND the model is reasoning ollama, we must fall
    back to the litellm path with a warning rather than breaking the
    tool-calling contract."""

    async def test_tools_with_ollama_bypass_falls_back_to_litellm(self, mock_acompletion):
        client = _client("ollama/glm-4.7-flash", bsgateway_url="")

        with patch(
            "bsvibe_llm.client._ollama_is_reasoning",
            return_value=True,
        ):
            await client.complete(
                messages=[{"role": "user", "content": "hi"}],
                metadata=_meta(),
                suppress_reasoning=True,
                tools=[{"type": "function", "function": {"name": "f", "parameters": {}}}],
            )

        # Tool calling required → must use litellm even though it means
        # no think=False forwarding (ollama ignores). Warning is logged.
        mock_acompletion.assert_called_once()
