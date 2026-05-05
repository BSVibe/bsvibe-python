# bsvibe-llm

Shared LLM client for the four BSVibe Python services. Wraps
`litellm.acompletion` with a **default route through BSGateway**
(Lockin Decision #11), the BSGateway / BSNexus run-audit metadata
contract, retry, and vendor fallback.

## Why this package exists

- **Decision #11** (BSVibe Execution Lockin §3): every BSVibe product
  routes LLM calls through BSGateway. The gateway owns BSupervisor
  `run.pre` / `run.post` audit and cost-aware routing. Direct vendor
  calls are an explicit per-call opt-in.
- **Supply-chain safety** (Shared Library Roadmap §D10): one place to
  pin LiteLLM, vendor SDKs, retry policy, fallback chain.
- **Wire-format pinning**: the run-audit metadata schema is shared
  between BSGateway PR #24 and BSNexus PR #38. This package is the
  **producer side** of that contract — drift here shows up as audit
  pipeline outages.

## Public API

```python
from bsvibe_llm import (
    LlmClient,             # async wrapper over litellm.acompletion
    LlmSettings,           # pydantic-settings: bsgateway_url, model, fallbacks
    RunAuditMetadata,      # BSGateway PR #24 metadata contract
    CompletionResult,      # normalised response (text + usage)
    RetryPolicy,           # exponential backoff
    RetryError,
    CircuitBreaker,        # per-vendor breaker
    FallbackChain,         # vendor fallback list
    FallbackExhaustedError,
)
```

## Quickstart

```python
from bsvibe_llm import LlmClient, LlmSettings, RunAuditMetadata

settings = LlmSettings(
    bsgateway_url="https://api-gateway.bsvibe.dev",
    model="openai/gpt-4o-mini",
    fallback_models=["anthropic/claude-3-5-sonnet"],
)
client = LlmClient(settings=settings)

result = await client.complete(
    messages=[{"role": "user", "content": "hello"}],
    metadata=RunAuditMetadata(
        tenant_id=str(tenant_id),
        run_id=str(execution_run.id),
        request_id=str(request.id),
        agent_name="composer",
    ),
)
print(result.text, result.prompt_tokens, result.completion_tokens)
```

## Routing — BSGateway by default

```python
# Default: api_base is set to settings.bsgateway_url (Decision #11).
await client.complete(messages=..., metadata=...)

# Opt-in: skip BSGateway and call the vendor SDK directly. Use this
# only inside the BSGateway hook itself (must not recurse).
await client.complete(messages=..., metadata=..., direct=True)
```

When `bsgateway_url` is empty the client also falls back to direct
vendor calls so dev environments without a gateway still work.

## Run-audit metadata contract

`RunAuditMetadata` is the producer-side mirror of BSGateway's
`RunMetadata.from_request_metadata` parser (`docs/BSNEXUS_METADATA_CONTRACT.md`).

| Field | Required | Source contract |
|---|---|---|
| `tenant_id` | yes | BSGateway hard-rejects when missing |
| `run_id` | yes (for audit) | BSGateway skips BSupervisor when missing |
| `request_id` | recommended | mirrors `Request.id` for tracing |
| `parent_run_id` | optional | hierarchical runs |
| `agent_name` | recommended | becomes `agent_id` on BSupervisor event |
| `cost_estimate_cents` | optional | surfaces in incident dashboards |
| `project_id` | recommended | scopes incidents per project |
| `composition_id` | optional | `CompositionSnapshot.id` |
| `extras` | optional | arbitrary keys, forwarded verbatim |

## Retry + fallback

```python
settings = LlmSettings(
    model="openai/gpt-4o",
    fallback_models=["anthropic/claude-3-5-sonnet"],
    retry_max_attempts=3,
    retry_base_delay_s=0.5,
)
```

- `RetryPolicy` retries only **transient** errors (connection, timeout,
  5xx surfaced as `litellm.exceptions.APIConnectionError` /
  `InternalServerError` / `ServiceUnavailableError` /
  `RateLimitError`). Programming errors propagate immediately.
- `FallbackChain` tries each model in order; the first success wins.
- `CircuitBreaker` is provided as a primitive so each product can wire
  its own per-vendor breaker on top.

## Reasoning suppression

Compile-time call sites that want short structured output and no
chain-of-thought set `suppress_reasoning=True`:

```python
result = await client.complete(
    messages=[{"role": "user", "content": "..."}],
    metadata=RunAuditMetadata(...),
    suppress_reasoning=True,
)
```

Per-provider behaviour (auto-detected from the model string / api_base):

| Provider | Effect |
|---|---|
| Anthropic Claude Opus 4.x / Sonnet 4.6+ | Adds `thinking={"type": "disabled"}` |
| OpenAI o1 / o3 / o4 / gpt-5-thinking | Adds `reasoning_effort="minimal"` |
| Ollama reasoning models (glm-4.7, qwen3-thinking, deepseek-r1) | **Bypasses litellm** (which silently drops `think`), POSTs `/api/chat` directly with `think: false`. Probes `/api/show` once per model + family-name fallback. Tool calls force fallback to litellm. |
| mlx-lm / vllm (OpenAI-compat self-hosted, detected via api_base) | Adds `extra_body={"think": false, "reasoning_effort": "minimal"}` |
| Non-reasoning models (gpt-4o, sonnet-3.5, haiku, llama3) | No-op |

`suppress_reasoning=False` (default) preserves existing behaviour for
all callers.

## Migration cheatsheet

| Today (per-product) | After Phase A |
|---|---|
| BSNexus `core/orchestrator_adapter.py` direct `litellm.acompletion` | `LlmClient.complete()` with `RunAuditMetadata` |
| BSGateway routing hook self-call | `LlmClient.complete(direct=True)` |
| BSage agent reasoning direct LiteLLM | `LlmClient.complete()` |
| Per-product retry/fallback bespoke | `LlmSettings.retry_*` + `fallback_models` |

## Install

```toml
# product pyproject.toml
[project]
dependencies = [
    "bsvibe-llm @ git+https://github.com/BSVibe/bsvibe-python.git@v0.2.0#subdirectory=packages/bsvibe-llm",
]
```

## Tests

```bash
uv run pytest packages/bsvibe-llm --cov=bsvibe_llm --cov-fail-under=80
uv run ruff check packages/bsvibe-llm/
uv run ruff format --check packages/bsvibe-llm/
```
