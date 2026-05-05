# bsvibe-demo

Shared helpers for the BSVibe public interactive-demo stack.

Each BSVibe product (BSGateway, BSNexus, BSupervisor, BSage) runs an
isolated demo deployment at `demo-{product}.bsvibe.dev`. The demo
backend issues per-visitor ephemeral tenants and reaps them via cron
after 2 hours of inactivity.

## What this package provides

- `mint_demo_jwt` / `decode_demo_jwt` — HS256 JWTs with the `is_demo`
  claim. Separate signing key per product (`DEMO_JWT_SECRET`),
  isolated from prod auth.bsvibe.dev tokens.
- `is_demo_mode` / `enforce_demo_llm_mock` — block real LLM provider
  calls when `BSVIBE_DEMO_MODE=true`. Forces LiteLLM `mock_response`
  and strips `api_key` defensively.
- `find_expired_tenants` / `demo_gc` — cascade-delete demo tenants
  whose `settings->>'last_active_at'` is older than the configured
  TTL. Filters strictly on `settings->>'is_demo' = 'true'` so prod
  tenants are never touched.

## What this package does NOT provide

- Per-tenant seed data (each product has its own dashboard surfaces)
- HTTP endpoints (each product wires its own `/api/v1/demo/session`
  route — share via copy-paste, not framework lock-in)
- Auth dependency (each product has its own `AuthContext` shape)

## Usage

```python
from bsvibe_demo import (
    mint_demo_jwt, decode_demo_jwt, DemoJWTError,
    is_demo_mode, enforce_demo_llm_mock,
    demo_gc, find_expired_tenants,
)

# In your demo router
token = mint_demo_jwt(tenant_id, secret=settings.demo_jwt_secret)

# In your LLM hook
enforce_demo_llm_mock(litellm_kwargs)

# In your hourly cron
deleted = await demo_gc(pool, ttl_seconds=7200)
```

See per-product `demo/seed.py` and `demo/router.py` for product-side wiring.
