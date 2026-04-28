# bsvibe-audit

BSVibe ecosystem-wide audit logging library.

## What it is

`bsvibe-audit` provides the building blocks every BSVibe product needs to emit
audit events into the central BSVibe-Auth audit store, while preserving
**transactional atomicity** with the product's own domain writes.

The package is the implementation of [BSVibe_Audit_Design.md](../../docs/BSVibe_Audit_Design.md)
§6 — `bsvibe-audit` Python package.

## Public surface

```python
from bsvibe_audit import (
    # Core types
    AuditEventBase,
    AuditActor,
    AuditResource,
    # Emitter (in-transaction outbox insert)
    AuditEmitter,
    # Outbox table + relay worker
    AuditOutboxRecord,
    AuditOutboxBase,
    OutboxStore,
    OutboxRelay,
    # HTTP client to BSVibe-Auth /api/audit/events
    AuditClient,
    # Decorator (Audit-3 PoC)
    audit_emit,
    # Settings
    AuditSettings,
)
```

## Architecture

```
┌────────────────────────────────────────────────┐
│  Product code (FastAPI route)                   │
│  await audit.emit(<TypedEvent>)                 │
└────────────────────────────────────────────────┘
                  │ same DB transaction
                  ▼
┌────────────────────────────────────────────────┐
│  audit_outbox row inserted (commit-bound)       │
└────────────────────────────────────────────────┘
                  │ background polling (default 5s)
                  ▼
┌────────────────────────────────────────────────┐
│  OutboxRelay batches undelivered rows           │
│  POST https://auth.bsvibe.dev/api/audit/events  │
│  → mark delivered or increment retry_count      │
└────────────────────────────────────────────────┘
```

## Wire contract

Each event is a Pydantic model with a stable schema:

| Namespace | Examples |
|-----------|----------|
| `auth.*` | `auth.user.created`, `auth.session.started`, `auth.tenant.member_added` |
| `nexus.*` | `nexus.project.created`, `nexus.run.completed`, `nexus.deliverable.created` |
| `gateway.*` | `gateway.route.config_changed`, `gateway.api_key.issued` |
| `supervisor.*` | `supervisor.rule.violated`, `supervisor.budget.exceeded` |
| `sage.*` | `sage.knowledge.entry_created`, `sage.vault.file_modified` |
| `core.*` | `core.audit.read`, `core.audit.export` |

All events extend `AuditEventBase`:

```python
class AuditEventBase(BaseModel):
    event_id: UUID
    event_type: str           # e.g. "auth.user.created"
    occurred_at: datetime
    actor: AuditActor
    tenant_id: TenantId | None
    trace_id: str | None      # auto-pulled from structlog contextvars
    resource: AuditResource | None
    data: dict[str, Any]
```

## TDD

`tests/` mocks every external dependency:

* `httpx` — `respx`/`AsyncMock` for `AuditClient`.
* `sqlalchemy` — `aiosqlite` for in-process roundtrips, `AsyncMock` for failure paths.
* `structlog` — fresh `contextvars` per test.

All tests are async (`asyncio_mode = "auto"`). Coverage gate: **80%**.

## Usage skeleton

```python
from bsvibe_audit import AuditEmitter, AuditSettings, OutboxRelay
from bsvibe_audit.events.auth import UserCreated

settings = AuditSettings()
emitter = AuditEmitter(session_factory=async_session)
relay = OutboxRelay.from_settings(settings, session_factory=async_session)

# In a FastAPI route:
async with async_session() as session:
    user = await repo.create(session, ...)
    await emitter.emit(
        UserCreated(actor=actor, tenant_id=tid, data={"user_id": str(user.id)}),
        session=session,
    )
    await session.commit()  # outbox row + domain row commit atomically

# At app startup:
await relay.start()
# At app shutdown:
await relay.stop()
```

## Audit-3 PoC: `@audit_emit` decorator

```python
from bsvibe_audit import audit_emit

@audit_emit("nexus.project.created", resource_type="project")
async def create_project(req: CreateProjectRequest, ...) -> Project:
    return await repo.create(...)
```

The decorator inspects the wrapped function's args/return for `actor`,
`tenant_id`, and a `resource_id` field, builds a generic `DomainEvent`, and
calls `AuditEmitter` automatically. See `decorators.py` for which patterns
auto-resolve and which require explicit `event_data=...` adapters.
