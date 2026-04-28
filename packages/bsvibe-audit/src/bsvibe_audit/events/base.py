"""Wire-shape contracts for every audit event.

Every product-emitted event extends :class:`AuditEventBase` and pins
its own ``event_type`` literal. The base model carries the standard
fields documented in BSVibe_Audit_Design.md §2.2:

* ``event_id`` — UUID, generated client-side for idempotency.
* ``event_type`` — namespaced dotted identifier, e.g. ``auth.user.created``.
* ``occurred_at`` — UTC timestamp at emit time.
* ``actor`` — who triggered the event.
* ``tenant_id`` — current tenant context (nullable for some auth events).
* ``trace_id`` — correlation id; auto-pulled from structlog contextvars
  if not provided.
* ``resource`` — optional ``{type, id}`` reference.
* ``data`` — free-form payload extension per event_type.

Extra fields are forbidden so producer typos surface as validation
errors at emit time rather than silently disappearing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, ClassVar, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

ActorType = Literal["user", "service", "system"]


class AuditActor(BaseModel):
    """Who performed the action recorded by the event."""

    model_config = ConfigDict(extra="forbid", frozen=False)

    type: ActorType
    id: str
    email: str | None = None
    label: str | None = None


class AuditResource(BaseModel):
    """Reference to the resource the event is about (optional)."""

    model_config = ConfigDict(extra="forbid", frozen=False)

    type: str
    id: str


def _utcnow() -> datetime:
    return datetime.now(UTC)


class AuditEventBase(BaseModel):
    """Wire shape every audit event extends.

    Subclasses pin ``event_type`` via ``DEFAULT_EVENT_TYPE`` (registered
    automatically by :mod:`bsvibe_audit.events`). Producers should rely
    on the typed subclasses rather than instantiating this class directly.
    """

    model_config = ConfigDict(extra="forbid")

    DEFAULT_EVENT_TYPE: ClassVar[str | None] = None

    event_id: UUID = Field(default_factory=uuid4)
    event_type: str
    occurred_at: datetime = Field(default_factory=_utcnow)
    actor: AuditActor
    tenant_id: str | None = None
    trace_id: str | None = None
    resource: AuditResource | None = None
    data: dict[str, Any] = Field(default_factory=dict)

    def __init__(self, **values: Any) -> None:  # type: ignore[override]
        # If subclass pins DEFAULT_EVENT_TYPE and caller did not supply one,
        # fill it in. This keeps the wire shape consistent without forcing
        # every call site to re-declare the literal.
        if "event_type" not in values:
            cls_default = type(self).DEFAULT_EVENT_TYPE
            if cls_default is not None:
                values["event_type"] = cls_default
        super().__init__(**values)


__all__ = [
    "ActorType",
    "AuditActor",
    "AuditResource",
    "AuditEventBase",
]
