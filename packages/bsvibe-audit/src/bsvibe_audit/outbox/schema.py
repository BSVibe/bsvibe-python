"""SQLAlchemy model for the ``audit_outbox`` table.

Every BSVibe service ships its own copy of this table inside its own
database (the outbox pattern requires the table live in the same DB
as the domain rows so they share a transaction). Each service's
Alembic migrations include this table by importing
:func:`audit_outbox_table` and rendering the matching DDL.

The schema mirrors BSVibe_Audit_Design.md §3.1 with three additions
needed for production-quality retry semantics:

* ``retry_count`` — increments on every relay failure.
* ``last_error`` — last error text for operator triage.
* ``next_attempt_at`` — earliest time the row may be retried (drives
  exponential backoff). NULL means "eligible immediately".
* ``dead_letter`` — true once ``retry_count`` reaches ``max_retries`` or
  a non-retryable error is recorded. Excluded from
  :meth:`OutboxStore.select_undelivered`.

The model is decoupled from any particular ``MetaData`` instance:
``AuditOutboxBase`` is a fresh declarative base owned by this package,
so consumers can either add it to their own ``Base.metadata`` (via the
helper) or migrate it standalone.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class AuditOutboxBase(DeclarativeBase):
    """Standalone declarative base for the outbox table.

    Consumers integrate by either:

    1. Calling :func:`register_audit_outbox_with` on their own ``Base``
       so a single Alembic migration covers both, or
    2. Running ``AuditOutboxBase.metadata.create_all`` against the same
       database (used in tests).
    """


class AuditOutboxRecord(AuditOutboxBase):
    """One audit event waiting to be relayed to BSVibe-Auth."""

    __tablename__ = "audit_outbox"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dead_letter: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        Index(
            "ix_audit_outbox_undelivered",
            "delivered_at",
            "next_attempt_at",
        ),
    )


def register_audit_outbox_with(metadata: Any) -> None:
    """Re-attach ``AuditOutboxRecord``'s table to a foreign ``MetaData``.

    Pass your service's declarative-base ``Base.metadata``; we copy the
    audit_outbox :class:`Table` into it so a single Alembic
    ``target_metadata`` covers both. This must be called *before* the
    first Alembic autogenerate run.
    """
    table = AuditOutboxRecord.__table__
    if table.name in metadata.tables:
        # already registered by a previous call — no-op.
        return
    table.tometadata(metadata)


__all__ = [
    "AuditOutboxBase",
    "AuditOutboxRecord",
    "register_audit_outbox_with",
]
