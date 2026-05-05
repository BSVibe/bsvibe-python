"""Tests for the ollama probe + direct ``/api/chat`` bypass."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from bsvibe_llm._ollama_probe import (
    _bare_model_name,
    _family_hint_says_reasoning,
    chat_direct,
    is_reasoning_model,
    reset_cache,
)


@pytest.fixture(autouse=True)
def _reset_probe_cache():
    reset_cache()
    yield
    reset_cache()


def _httpx_response(json_data: dict, status: int = 200) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value=json_data)
    return resp


class TestBareModelName:
    def test_strips_provider_prefix(self):
        assert _bare_model_name("ollama/glm-4.7-flash") == "glm-4.7-flash"

    def test_no_prefix_passthrough(self):
        assert _bare_model_name("llama3") == "llama3"


class TestFamilyHints:
    @pytest.mark.parametrize(
        "model",
        ["glm-4.7-flash", "qwen3-thinking", "deepseek-r1:7b", "qwq-32b"],
    )
    def test_known_reasoning_families(self, model):
        assert _family_hint_says_reasoning(model) is True

    @pytest.mark.parametrize("model", ["llama3", "mistral", "phi-3", "gemma2"])
    def test_non_reasoning_families(self, model):
        assert _family_hint_says_reasoning(model) is False


class TestProbe:
    async def test_capabilities_thinking_returns_true(self):
        with patch("bsvibe_llm._ollama_probe.httpx.AsyncClient") as mock_class:
            mock_client = AsyncMock()
            mock_class.return_value.__aenter__.return_value = mock_client
            mock_client.post = AsyncMock(return_value=_httpx_response({"capabilities": ["thinking", "completion"]}))

            assert await is_reasoning_model("ollama/some-model") is True

    async def test_template_with_think_tag_returns_true(self):
        with patch("bsvibe_llm._ollama_probe.httpx.AsyncClient") as mock_class:
            mock_client = AsyncMock()
            mock_class.return_value.__aenter__.return_value = mock_client
            mock_client.post = AsyncMock(
                return_value=_httpx_response({"template": "{{ if .Thinking }}<think>{{ .Thinking }}</think>{{ end }}"})
            )

            assert await is_reasoning_model("ollama/some-model") is True

    async def test_no_capability_no_template_falls_back_to_family(self):
        with patch("bsvibe_llm._ollama_probe.httpx.AsyncClient") as mock_class:
            mock_client = AsyncMock()
            mock_class.return_value.__aenter__.return_value = mock_client
            mock_client.post = AsyncMock(return_value=_httpx_response({}))

            # Family hint catches this even though /api/show didn't.
            assert await is_reasoning_model("ollama/glm-4.7-flash") is True
            # And rejects this.
            reset_cache()
            assert await is_reasoning_model("ollama/llama3") is False

    async def test_probe_failure_falls_back_to_family(self):
        with patch("bsvibe_llm._ollama_probe.httpx.AsyncClient") as mock_class:
            mock_client = AsyncMock()
            mock_class.return_value.__aenter__.return_value = mock_client
            mock_client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))

            assert await is_reasoning_model("ollama/glm-4.7-flash") is True
            reset_cache()
            assert await is_reasoning_model("ollama/llama3") is False

    async def test_result_is_cached(self):
        with patch("bsvibe_llm._ollama_probe.httpx.AsyncClient") as mock_class:
            mock_client = AsyncMock()
            mock_class.return_value.__aenter__.return_value = mock_client
            mock_client.post = AsyncMock(return_value=_httpx_response({"capabilities": ["thinking"]}))

            await is_reasoning_model("ollama/some-model")
            await is_reasoning_model("ollama/some-model")
            await is_reasoning_model("ollama/some-model")

            mock_client.post.assert_called_once()


class TestChatDirect:
    async def test_posts_to_api_chat_with_think_false(self):
        with patch("bsvibe_llm._ollama_probe.httpx.AsyncClient") as mock_class:
            mock_client = AsyncMock()
            mock_class.return_value.__aenter__.return_value = mock_client
            mock_client.post = AsyncMock(
                return_value=_httpx_response(
                    {
                        "message": {"role": "assistant", "content": "ok"},
                        "done": True,
                        "prompt_eval_count": 10,
                        "eval_count": 3,
                    }
                )
            )

            result = await chat_direct(
                model="ollama/glm-4.7-flash",
                messages=[{"role": "user", "content": "hi"}],
            )

        # Verify call shape.
        call_kwargs = mock_client.post.call_args.kwargs
        body = call_kwargs["json"]
        assert body["model"] == "glm-4.7-flash"
        assert body["think"] is False
        assert body["stream"] is False
        # Response normalised onto litellm shape.
        assert result.choices[0].message.content == "ok"
        assert result.choices[0].finish_reason == "stop"
        assert result.usage.prompt_tokens == 10
        assert result.usage.completion_tokens == 3
        assert result.usage.total_tokens == 13

    async def test_uses_default_base_when_none_given(self):
        with patch("bsvibe_llm._ollama_probe.httpx.AsyncClient") as mock_class:
            mock_client = AsyncMock()
            mock_class.return_value.__aenter__.return_value = mock_client
            mock_client.post = AsyncMock(return_value=_httpx_response({"message": {"content": ""}, "done": True}))

            await chat_direct(
                model="ollama/llama3",
                messages=[{"role": "user", "content": "hi"}],
            )

            url = mock_client.post.call_args.args[0]
            assert "localhost:11434" in url
            assert url.endswith("/api/chat")
