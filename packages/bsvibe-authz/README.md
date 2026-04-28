# bsvibe-authz

Shared authorization library for BSVibe Python services (BSGateway, BSNexus,
BSupervisor, BSage). Implements Phase 0 P0.4 of the
[BSVibe Auth Design](../../../../Docs/BSVibe_Auth_Design.md) §6.3.

## What it does

- Verifies **user session JWTs** issued by BSVibe-Auth (`auth.bsvibe.dev`)
- Verifies **service JWTs** issued by BSVibe-Auth's `POST /api/service-tokens/issue`
  (audience-scoped + scope claim — Lockin §3 decision #16)
- Wraps OpenFGA's HTTP API for `check`, `list-objects`, `write` (httpx async, 3s timeout)
- Caches permission decisions for 30s in-process (asyncio-safe)
- Plugs into FastAPI via `Depends`:
  - `CurrentUser` — injects authenticated `User`
  - `require_permission(...)` — 403 on OpenFGA deny
  - `ServiceKeyAuth(audience=...)` — service-only endpoints
  - `get_active_tenant_id` — TenantScoped helper
- Owns the canonical [`bsvibe.fga`](schema/bsvibe.fga) authorization model

## Install

`bsvibe-authz` is a uv workspace member of `bsvibe-python`. From a consumer project:

```toml
# pyproject.toml
[project]
dependencies = [
    "bsvibe-authz",
]

[tool.uv.sources]
bsvibe-authz = { git = "https://github.com/BSVibe/bsvibe-python", subdirectory = "packages/bsvibe-authz" }
```

Or, when working inside the `bsvibe-python` workspace:

```toml
[tool.uv.sources]
bsvibe-authz = { workspace = true }
```

## Configure

Settings are loaded from environment variables via `pydantic-settings`:

| Env var                          | Required | Default       | Notes                                          |
|----------------------------------|----------|---------------|------------------------------------------------|
| `BSVIBE_AUTH_URL`                | yes      | —             | e.g. `https://auth.bsvibe.dev`                 |
| `OPENFGA_API_URL`                | yes      | —             | e.g. `http://openfga.local:8080`               |
| `OPENFGA_STORE_ID`               | yes      | —             |                                                |
| `OPENFGA_AUTH_MODEL_ID`          | yes      | —             | from `fga model write` output                   |
| `OPENFGA_AUTH_TOKEN`             | no       | —             | Bearer token for OpenFGA Admin API             |
| `OPENFGA_REQUEST_TIMEOUT_S`      | no       | `3.0`         |                                                |
| `SERVICE_TOKEN_SIGNING_SECRET`   | yes      | —             | shared with BSVibe-Auth (HS256, Phase 0)       |
| `USER_JWT_SECRET`                | conditional | —          | required when `USER_JWT_ALGORITHM=HS256`       |
| `USER_JWT_PUBLIC_KEY`            | conditional | —          | required for `RS256/ES256/EdDSA` (P0.4-후속)   |
| `USER_JWT_ALGORITHM`             | no       | `HS256`       | `HS256 \| RS256 \| ES256 \| EdDSA`             |
| `USER_JWT_AUDIENCE`              | no       | `bsvibe`      |                                                |
| `USER_JWT_ISSUER`                | no       | —             | enforce iss claim if set                       |
| `PERMISSION_CACHE_TTL_S`         | no       | `30`          |                                                |

## FastAPI usage

```python
from fastapi import Depends, FastAPI
from bsvibe_authz import (
    CurrentUser,
    ServiceKey,
    ServiceKeyAuth,
    get_active_tenant_id,
    require_permission,
)

app = FastAPI()


@app.get("/projects/{project_id}")
async def get_project(
    project_id: str,
    user: CurrentUser,
    _allowed: None = Depends(
        require_permission(
            "nexus.project.read",
            resource_type="project",
            resource_id_param="project_id",
        ),
    ),
):
    ...


@app.get("/me")
async def me(user: CurrentUser):
    return {"id": user.id, "email": user.email}


@app.get("/internal/cost-rollup")
async def cost_rollup(svc: ServiceKey = Depends(ServiceKeyAuth(audience="bsupervisor"))):
    return {"caller": svc.sub, "scopes": svc.scopes}


@app.get("/tenant/projects")
async def list_projects(tenant_id: str = Depends(get_active_tenant_id)):
    ...
```

`CurrentUser` is `Annotated[User, Depends(get_current_user)]` — use it as a type
annotation directly. Resolve `resource_id_param` from path params; omit
`resource_type`/`resource_id_param` for tenant-wide checks
(`tenant:<active_tenant_id>`).

## Service-to-service calls

A producer calls `BSVibe-Auth /api/service-tokens/issue`, then includes the
returned token in the `Authorization` header:

```python
import httpx

async with httpx.AsyncClient() as cli:
    resp = await cli.post(
        f"{settings.bsvibe_auth_url}/api/service-tokens/issue",
        json={
            "audience": "bsage",
            "scope": ["bsage.read"],
            "subject": "service:bsnexus",
            "tenantId": tenant_id,
        },
        headers={"Authorization": f"Bearer {bootstrap_token}"},
    )
    token = resp.json()["access_token"]
    # call bsage:
    await cli.get(
        "https://api-sage.bsvibe.dev/api/...",
        headers={"Authorization": f"Bearer {token}"},
    )
```

The receiver protects the route with `ServiceKeyAuth(audience="bsage")`. The
verifier enforces:

- `aud == "bsage"`
- every `scope` entry is prefixed with `bsage.`
- `token_type == "service"`
- signature, `exp`, `iat` valid

This matches the contract in
[`BSVibe-Auth/phase0/auth-app/api/_lib/service-token.ts`](../../../../Works/BSVibe-Auth/phase0/auth-app/api/_lib/service-token.ts).

## OpenFGA schema

The canonical model is [`schema/bsvibe.fga`](schema/bsvibe.fga). Apply it with:

```bash
fga model write --store-id $OPENFGA_STORE_ID --file schema/bsvibe.fga
```

The `_infra/openfga/bsvibe.fga` copy was a P0.3 bootstrap seed; from P0.4
onwards changes happen in this package and a follow-up PR resyncs the seed.

## Dev guide

```bash
# from bsvibe-python repo root
uv sync --dev
uv run ruff check .
uv run ruff format --check .
uv run pytest packages/bsvibe-authz --cov=bsvibe_authz --cov-fail-under=80
```

External calls (OpenFGA, Supabase) are mocked in tests via `respx` and locally
generated HS256 tokens. There are no network dependencies.

## Roadmap

- **Phase 0.4-후속**: swap user JWT verification to JWKS (RS256/ES256/EdDSA),
  swap service JWT signing secret to Ed25519. The `Settings` already carries
  `user_jwt_public_key` to make the change mechanical.
- **Phase 0.5**: 4 products (BSupervisor → BSage → BSGateway → BSNexus) adopt
  this package, replacing per-product auth middleware.
