"""bsvibe-audit — outbox-pattern audit emission for the four BSVibe products.

Stable public surface:

.. code-block:: python

    from bsvibe_audit import (
        # Settings
        AuditSettings,
        # Wire types
        AuditEventBase, AuditActor, AuditResource,
        # In-transaction emitter
        AuditEmitter,
        # Outbox SQLAlchemy + helpers
        AuditOutboxBase, AuditOutboxRecord,
        OutboxStore, OutboxRelay,
        register_audit_outbox_with,
        # HTTP client
        AuditClient,
        # Decorator (Audit-3 PoC)
        audit_emit,
    )

Typical wiring inside a FastAPI service::

    from bsvibe_audit import AuditEmitter, AuditSettings, OutboxRelay
    from bsvibe_audit.events.nexus import ProjectCreated

    settings = AuditSettings()
    emitter = AuditEmitter()
    relay = OutboxRelay.from_settings(settings, session_factory=async_session)

    @app.on_event("startup")
    async def _start_audit_relay() -> None:
        await relay.start()

    @app.on_event("shutdown")
    async def _stop_audit_relay() -> None:
        await relay.stop()

    @app.post("/projects")
    async def create_project(
        body: CreateProjectRequest,
        user: CurrentUser = Depends(),
        session: AsyncSession = Depends(get_db),
    ) -> Project:
        project = await repo.create(session, body, tenant_id=user.tenant_id)
        await emitter.emit(
            ProjectCreated(
                actor=AuditActor(type="user", id=user.id, email=user.email),
                tenant_id=user.tenant_id,
                resource=AuditResource(type="project", id=project.id),
                data={"name": project.name},
            ),
            session=session,
        )
        return project
"""

from __future__ import annotations

from bsvibe_audit.client import AuditClient, AuditDeliveryError, AuditDeliveryResult
from bsvibe_audit.decorators import audit_emit
from bsvibe_audit.emitter import AuditEmitter
from bsvibe_audit.events import (
    EVENT_REGISTRY,
    AuditActor,
    AuditEventBase,
    AuditResource,
)
from bsvibe_audit.outbox import (
    AuditOutboxBase,
    AuditOutboxRecord,
    OutboxRelay,
    OutboxStore,
    register_audit_outbox_with,
)
from bsvibe_audit.settings import AuditSettings

__version__ = "0.1.0"

__all__ = [
    # Settings
    "AuditSettings",
    # Wire types
    "AuditEventBase",
    "AuditActor",
    "AuditResource",
    "EVENT_REGISTRY",
    # Emitter
    "AuditEmitter",
    # Outbox
    "AuditOutboxBase",
    "AuditOutboxRecord",
    "OutboxStore",
    "OutboxRelay",
    "register_audit_outbox_with",
    # HTTP client
    "AuditClient",
    "AuditDeliveryError",
    "AuditDeliveryResult",
    # Decorator
    "audit_emit",
    "__version__",
]
