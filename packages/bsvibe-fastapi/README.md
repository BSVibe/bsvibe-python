# bsvibe-fastapi

Shared FastAPI helpers for the four BSVibe products (BSGateway, BSNexus,
BSupervisor, BSage). Phase A — base package only: middleware, CORS,
health router, settings. DB-pool concerns (e.g.
`pool_pre_ping=True`) live in the sibling package `bsvibe-sqlalchemy`.

## Public API

```python
from bsvibe_fastapi import (
    FastApiSettings,
    add_cors_middleware,
    make_health_router,
    RequestIdMiddleware,
)
```

## Usage

```python
from fastapi import FastAPI
from bsvibe_core import configure_logging
from bsvibe_fastapi import (
    FastApiSettings,
    RequestIdMiddleware,
    add_cors_middleware,
    make_health_router,
)


class Settings(FastApiSettings):
    """Product-specific fields go here."""

    database_url: str = ""


settings = Settings()
configure_logging(level="info", service_name="bsage")

app = FastAPI(title="bsage")

# Order matters: RequestIdMiddleware must wrap the rest so every log
# line emitted inside the handler carries `request_id=<value>`.
app.add_middleware(RequestIdMiddleware)
add_cors_middleware(app, settings)

async def deps() -> dict[str, str]:
    return {"database": "ok", "redis": "ok"}

app.include_router(make_health_router(deps_callable=deps))
```

## Settings — CORS env-var contract (BSupervisor PR #13 §M18)

`cors_allowed_origins`, `cors_allow_methods`, and `cors_allow_headers`
are all `Annotated[list[str], NoDecode]` fields with a
`field_validator(mode="before")` that runs `bsvibe_core.parse_csv_list`.

This means the legacy
`os.environ.get("CORS_ALLOWED_ORIGINS", "...").split(",")` shape every
product uses today migrates with **zero** deployer changes:

```bash
CORS_ALLOWED_ORIGINS=http://a.test,http://b.test,http://c.test
CORS_ALLOW_METHODS=GET,POST
CORS_ALLOW_HEADERS=Authorization,Content-Type
CORS_ALLOW_CREDENTIALS=true
```

Empty / unset env falls back to `http://localhost:3500` so dev bootstrap
does not crash without a `.env` file.

## Health endpoints

| Path           | Status        | Description                                            |
|----------------|---------------|--------------------------------------------------------|
| `/health`      | 200 always    | Liveness probe. No dependency checks.                  |
| `/health/deps` | 200 / 503     | Readiness — 200 when every dep returns `"ok"`, else 503. |

`make_health_router(deps_callable=...)` accepts a sync OR async
callable. When `deps_callable` is `None`, `/health/deps` returns 200
with an empty map (trivially healthy) so probes never 404.

## Request id middleware

`RequestIdMiddleware` reads the incoming `x-request-id` header, falls
back to a fresh UUID4 hex when missing or empty, stores the id on
`request.state.request_id`, echoes it on the response header, and binds
it into `structlog.contextvars` for the duration of the request — so
every log line emitted inside the handler carries `request_id=<value>`
without manual `logger.bind(...)` calls.

## Out of scope

* DB pool / SQLAlchemy session factory (`pool_pre_ping=True` lives in
  `bsvibe-sqlalchemy`).
* Authentication / authorization (`bsvibe-authz`).
* Audit emission (`bsvibe-audit`).
* LiteLLM wrapper (`bsvibe-llm`).
