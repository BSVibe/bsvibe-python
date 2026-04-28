"""Audit emitter — turns a typed event into an outbox row.

The emitter is intentionally tiny: it serialises the Pydantic event,
auto-fills ``trace_id`` from structlog ``contextvars`` if absent, and
hands the row to :class:`OutboxStore`. **No commit, no network I/O.**
The caller's transaction owns the row.

This is the in-transaction contract that makes audit emit atomic with
the domain write — exactly the property BSVibe_Audit_Design.md §3.1
requires of the outbox pattern.
"""

from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from bsvibe_audit.events import AuditEventBase
from bsvibe_audit.outbox.store import OutboxStore


def _ambient_trace_id() -> str | None:
    """Return ``trace_id`` from structlog contextvars, or None."""

    bound = structlog.contextvars.get_contextvars()
    value = bound.get("trace_id")
    if isinstance(value, str) and value:
        return value
    return None


class AuditEmitter:
    """Emit one event into the caller's outbox table inside their session."""

    def __init__(self, *, store: OutboxStore | None = None) -> None:
        self._store = store or OutboxStore()
        self._logger = structlog.get_logger("bsvibe_audit.emitter")

    async def emit(
        self,
        event: AuditEventBase,
        *,
        session: AsyncSession,
    ) -> None:
        """Insert ``event`` into the outbox; do not commit.

        The caller's session/transaction is the unit of atomicity.
        Failures here propagate so the caller's transaction rolls back
        too — never silently drop events.
        """

        if event.trace_id is None:
            ambient = _ambient_trace_id()
            if ambient is not None:
                # Pydantic models are mutable by default; assign rather
                # than re-build to preserve subclass identity.
                event.trace_id = ambient

        payload: dict[str, Any] = event.model_dump(mode="json")
        await self._store.insert(
            session,
            event_id=str(event.event_id),
            event_type=event.event_type,
            occurred_at=event.occurred_at,
            payload=payload,
        )
        self._logger.debug(
            "audit_event_enqueued",
            event_type=event.event_type,
            tenant_id=event.tenant_id,
        )


__all__ = ["AuditEmitter"]
