"""Tests for ``LlmClient``.

The client is a thin wrapper over ``litellm.acompletion``. Its
contract:

1. **Default routing is BSGateway** (Decision #11). When ``bsgateway_url``
   is set and ``direct=False`` (default), the call MUST set
   ``api_base=bsgateway_url`` and prefix the model accordingly.
2. **Direct vendor calls require explicit opt-in** via ``direct=True``.
3. **RunAuditMetadata is forwarded under ``metadata`` kwarg** in the
   exact BSGateway contract shape.
4. **Retry + fallback are wired** through the policy/chain primitives.

All ``litellm`` interactions are mocked — no real API calls.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bsvibe_llm.client import LlmClient
from bsvibe_llm.metadata import RunAuditMetadata
from bsvibe_llm.settings import LlmSettings


def _fake_response(text: str = "hi", prompt_tokens: int = 5, completion_tokens: int = 7):
    msg = MagicMock()
    msg.content = text
    msg.tool_calls = None
    choice = MagicMock()
    choice.message = msg
    choice.finish_reason = "stop"
    resp = MagicMock()
    resp.choices = [choice]
    resp.usage = MagicMock(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
    )
    return resp


@pytest.fixture
def mock_acompletion():
    with patch("bsvibe_llm.client.litellm.acompletion", new_callable=AsyncMock) as mock:
        mock.return_value = _fake_response()
        yield mock


class TestLlmClientRouting:
    async def test_default_route_is_bsgateway(self, mock_acompletion):
        settings = LlmSettings(
            bsgateway_url="http://gateway.local:9090",
            model="openai/gpt-4o-mini",
        )
        client = LlmClient(settings=settings)

        await client.complete(
            messages=[{"role": "user", "content": "hi"}],
            metadata=RunAuditMetadata(tenant_id="t-1", run_id="r-1"),
        )

        kwargs = mock_acompletion.call_args.kwargs
        assert kwargs["api_base"] == "http://gateway.local:9090"

    async def test_direct_opt_in_skips_bsgateway(self, mock_acompletion):
        settings = LlmSettings(
            bsgateway_url="http://gateway.local:9090",
            model="openai/gpt-4o-mini",
        )
        client = LlmClient(settings=settings)

        await client.complete(
            messages=[{"role": "user", "content": "hi"}],
            metadata=RunAuditMetadata(tenant_id="t-1", run_id="r-1"),
            direct=True,
        )

        kwargs = mock_acompletion.call_args.kwargs
        # Direct mode → api_base must be omitted (or None) so litellm
        # talks straight to the vendor SDK.
        assert kwargs.get("api_base") in (None, "")

    async def test_falls_back_to_direct_when_no_bsgateway_url(self, mock_acompletion):
        settings = LlmSettings(bsgateway_url="", model="openai/gpt-4o-mini")
        client = LlmClient(settings=settings)

        await client.complete(
            messages=[{"role": "user", "content": "hi"}],
            metadata=RunAuditMetadata(tenant_id="t-1", run_id="r-1"),
        )

        kwargs = mock_acompletion.call_args.kwargs
        assert kwargs.get("api_base") in (None, "")


class TestLlmClientMetadata:
    async def test_forwards_metadata_in_bsgateway_contract_shape(self, mock_acompletion):
        settings = LlmSettings(
            bsgateway_url="http://gateway.local",
            model="openai/gpt-4o-mini",
        )
        client = LlmClient(settings=settings)

        await client.complete(
            messages=[{"role": "user", "content": "hi"}],
            metadata=RunAuditMetadata(
                tenant_id="t-1",
                run_id="r-1",
                request_id="req-1",
                agent_name="composer",
                cost_estimate_cents=42,
            ),
        )

        kwargs = mock_acompletion.call_args.kwargs
        forwarded = kwargs["metadata"]
        # Hard-required for BSGateway parser.
        assert forwarded["tenant_id"] == "t-1"
        assert forwarded["run_id"] == "r-1"
        # Recommended fields preserved.
        assert forwarded["request_id"] == "req-1"
        assert forwarded["agent_name"] == "composer"
        assert forwarded["cost_estimate_cents"] == 42

    async def test_metadata_required(self, mock_acompletion):
        # No metadata → hard error. We never want anonymous LLM calls
        # bypassing audit (BSGateway only skips audit when run_id is
        # missing on its side, but we want callers to fail loud).
        settings = LlmSettings(bsgateway_url="http://gateway.local", model="openai/gpt-4o-mini")
        client = LlmClient(settings=settings)

        with pytest.raises((TypeError, ValueError)):
            await client.complete(
                messages=[{"role": "user", "content": "hi"}],
                metadata=None,  # type: ignore[arg-type]
            )


class TestLlmClientCallShape:
    async def test_passes_messages_and_model(self, mock_acompletion):
        settings = LlmSettings(bsgateway_url="http://gateway.local", model="openai/gpt-4o-mini")
        client = LlmClient(settings=settings)

        msgs = [{"role": "user", "content": "hi"}]
        await client.complete(
            messages=msgs,
            metadata=RunAuditMetadata(tenant_id="t-1", run_id="r-1"),
        )

        kwargs = mock_acompletion.call_args.kwargs
        assert kwargs["model"] == "openai/gpt-4o-mini"
        assert kwargs["messages"] == msgs

    async def test_explicit_model_overrides_default(self, mock_acompletion):
        settings = LlmSettings(bsgateway_url="http://gateway.local", model="openai/gpt-4o-mini")
        client = LlmClient(settings=settings)

        await client.complete(
            messages=[{"role": "user", "content": "hi"}],
            metadata=RunAuditMetadata(tenant_id="t-1", run_id="r-1"),
            model="anthropic/claude-3-5-sonnet",
        )

        kwargs = mock_acompletion.call_args.kwargs
        assert kwargs["model"] == "anthropic/claude-3-5-sonnet"


class TestLlmClientRetry:
    async def test_retries_on_transient_error(self):
        settings = LlmSettings(
            bsgateway_url="http://gateway.local",
            model="openai/gpt-4o-mini",
            retry_max_attempts=3,
            retry_base_delay_s=0.001,
        )

        calls = {"n": 0}

        async def fake(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] < 2:
                raise ConnectionError("transient")
            return _fake_response()

        with patch("bsvibe_llm.client.litellm.acompletion", side_effect=fake):
            client = LlmClient(settings=settings)
            await client.complete(
                messages=[{"role": "user", "content": "hi"}],
                metadata=RunAuditMetadata(tenant_id="t-1", run_id="r-1"),
            )
        assert calls["n"] == 2


class TestLlmClientFallback:
    async def test_fallback_chain_when_primary_fails(self):
        settings = LlmSettings(
            bsgateway_url="http://gateway.local",
            model="openai/gpt-4o",
            fallback_models=["anthropic/claude-3-5-sonnet"],
            retry_max_attempts=1,
            retry_base_delay_s=0.001,
        )

        used: list[str] = []

        async def fake(*args, **kwargs):
            model = kwargs.get("model")
            used.append(model)
            if model == "openai/gpt-4o":
                raise ConnectionError("primary down")
            return _fake_response()

        with patch("bsvibe_llm.client.litellm.acompletion", side_effect=fake):
            client = LlmClient(settings=settings)
            result = await client.complete(
                messages=[{"role": "user", "content": "hi"}],
                metadata=RunAuditMetadata(tenant_id="t-1", run_id="r-1"),
            )

        assert result.text == "hi"
        assert used == ["openai/gpt-4o", "anthropic/claude-3-5-sonnet"]


class TestLlmClientResponse:
    async def test_returns_completion_result_dataclass(self, mock_acompletion):
        mock_acompletion.return_value = _fake_response(
            text="answer",
            prompt_tokens=10,
            completion_tokens=20,
        )

        settings = LlmSettings(bsgateway_url="http://gateway.local", model="openai/gpt-4o-mini")
        client = LlmClient(settings=settings)

        result = await client.complete(
            messages=[{"role": "user", "content": "hi"}],
            metadata=RunAuditMetadata(tenant_id="t-1", run_id="r-1"),
        )

        assert result.text == "answer"
        assert result.model == "openai/gpt-4o-mini"
        assert result.prompt_tokens == 10
        assert result.completion_tokens == 20
        assert result.finish_reason == "stop"
