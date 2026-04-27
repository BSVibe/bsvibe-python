# E2E checklist — `bsvibe-llm` Phase A

Phase A scope: package implementation only. Real vendor calls and
4-product migrations are out of scope (see lockin §A3.4 — Decision #11).

## Public API smoke (Python)

- [x] `from bsvibe_llm import LlmClient, LlmSettings, RunAuditMetadata, CompletionResult` succeeds (covered by `tests/test_public_api.py`)
- [x] `from bsvibe_llm import RetryPolicy, RetryError, CircuitBreaker, FallbackChain, FallbackExhaustedError` succeeds (same)
- [x] `bsvibe_llm.__version__` is set (== `0.1.0`)

## Decision #11 — BSGateway as the default route

- [x] When `bsgateway_url` is set and `direct=False` (default), `LlmClient.complete()` sets `api_base=bsgateway_url` on the litellm call (`tests/test_client.py::test_default_route_is_bsgateway`)
- [x] `direct=True` skips BSGateway and lets LiteLLM talk to the vendor (`test_direct_opt_in_skips_bsgateway`)
- [x] When `bsgateway_url` is empty the call falls back to direct vendor (dev-mode safety) (`test_falls_back_to_direct_when_no_bsgateway_url`)
- [x] `route_default="direct"` switches default behaviour at the settings level (`test_route_default_direct_allowed`)

## Metadata contract — BSGateway PR #24 + BSNexus PR #38

- [x] Required keys (`tenant_id`, `run_id`) raise on empty input (`test_missing_*_raises`)
- [x] Optional keys default to None and are dropped from the wire payload (`test_minimal_required_fields_only`)
- [x] All 8 named keys + `extras` survive the round-trip with the BSGateway parser shape (`test_full_payload_matches_contract_keys`, `test_round_trip_via_bsgateway_from_request_metadata_shape`)
- [x] `from_metadata({...})` mirrors BSGateway's `from_request_metadata` precondition (returns None when required keys missing)
- [x] `cost_estimate_cents` is type-checked (`test_cost_estimate_cents_must_be_int`) and coerced from strings on parse (`test_from_metadata_coerces_cost_estimate`)
- [x] Named fields beat extras on collision (`test_extras_keys_do_not_collide_with_named_fields`)

## Retry policy

- [x] Returns immediately on success (`test_returns_value_when_first_call_succeeds`)
- [x] Retries transient infra errors (`test_retries_on_transient_failure`)
- [x] Surfaces `RetryError` after exhaustion with `__cause__` set (`test_raises_retry_error_after_max_attempts`)
- [x] Does NOT retry programming errors (`ValueError`) (`test_does_not_retry_unretryable_error`)
- [x] Exponential backoff verified (0.1, 0.2, 0.4 …) (`test_exponential_backoff_grows`)

## Circuit breaker

- [x] Starts closed, opens after threshold consecutive failures (`test_starts_closed`, `test_opens_after_threshold`)
- [x] Success resets the counter (`test_success_resets_failures`)
- [x] Re-closes after recovery window (`test_recovers_after_window`)

## Fallback chain

- [x] First success wins, no further models tried (`test_returns_first_success`)
- [x] Falls back on transient errors (`test_falls_back_on_failure`)
- [x] All models exhausted → `FallbackExhaustedError` with per-model failure list (`test_raises_when_all_fail`)
- [x] Programming errors propagate without trying the next model (`test_does_not_fallback_on_unretryable_error`)

## Coverage gates

- [x] `uv run pytest packages/bsvibe-llm --cov=bsvibe_llm --cov-fail-under=80` passes (current: 94.94%)
- [x] `uv run ruff check packages/bsvibe-llm/` clean
- [x] `uv run ruff format --check packages/bsvibe-llm/` clean
- [x] Workspace-wide `uv run pytest` still green (98 tests pass)

## Out of scope (deferred to follow-up phases)

- [ ] 4-product migrations (BSNexus, BSGateway routing hook, BSage agent, BSupervisor) — Phase A continues with each product separately
- [ ] Real vendor smoke tests (will live in product test suites with credentials, not in this shared package)
- [ ] Streaming responses + tool-calling loop helpers — extracted from BSNexus `orchestrator_adapter.py` in a follow-up so the v0.1 API stays minimal
- [ ] Cost-tracking / `cost_per_token` integration — BSGateway now owns cost on the `run.post` event; we'll add a producer-side estimator only if a product needs it
