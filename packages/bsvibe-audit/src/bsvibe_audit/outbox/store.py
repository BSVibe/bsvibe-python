"""Outbox persistence helpers (used by emitter + relay).

The store is a thin wrapper around the SQLAlchemy model so the emitter
and relay don't need to know SQL. Every method takes an
:class:`AsyncSession` and never commits — the caller decides
transactional boundaries.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bsvibe_audit.outbox.schema import AuditOutboxRecord


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _backoff_delta(retry_count: int) -> timedelta:
    """Exponential backoff capped at one minute (per Audit Design §3.2)."""

    seconds = min(60.0, 2.0 ** max(0, retry_count - 1))
    return timedelta(seconds=seconds)


class OutboxStore:
    """CRUD façade for the audit outbox."""

    async def insert(
        self,
        session: AsyncSession,
        *,
        event_id: str,
        event_type: str,
        occurred_at: datetime,
        payload: dict[str, Any],
    ) -> AuditOutboxRecord:
        """Add one row. The caller commits."""

        record = AuditOutboxRecord(
            event_id=event_id,
            event_type=event_type,
            occurred_at=occurred_at,
            payload=payload,
        )
        session.add(record)
        await session.flush()
        return record

    async def select_undelivered(
        self,
        session: AsyncSession,
        *,
        batch_size: int,
        now: datetime | None = None,
    ) -> Sequence[AuditOutboxRecord]:
        """Return up to ``batch_size`` rows ready for delivery.

        Eligibility: not yet delivered, not dead-lettered, and either
        no backoff or backoff has elapsed.
        """

        cutoff = now or _utcnow()
        stmt = (
            select(AuditOutboxRecord)
            .where(
                AuditOutboxRecord.delivered_at.is_(None),
                AuditOutboxRecord.dead_letter.is_(False),
            )
            .where((AuditOutboxRecord.next_attempt_at.is_(None)) | (AuditOutboxRecord.next_attempt_at <= cutoff))
            .order_by(AuditOutboxRecord.id.asc())
            .limit(batch_size)
        )
        result = await session.execute(stmt)
        return result.scalars().all()

    async def select_dead_letter(
        self,
        session: AsyncSession,
        *,
        limit: int = 100,
    ) -> Sequence[AuditOutboxRecord]:
        """Operator helper: list dead-lettered rows for triage."""

        stmt = (
            select(AuditOutboxRecord)
            .where(AuditOutboxRecord.dead_letter.is_(True))
            .order_by(AuditOutboxRecord.id.asc())
            .limit(limit)
        )
        result = await session.execute(stmt)
        return result.scalars().all()

    async def mark_delivered(
        self,
        session: AsyncSession,
        ids: Sequence[int],
        *,
        now: datetime | None = None,
    ) -> None:
        if not ids:
            return
        stmt = (
            update(AuditOutboxRecord)
            .where(AuditOutboxRecord.id.in_(list(ids)))
            .values(delivered_at=now or _utcnow(), last_error=None)
        )
        await session.execute(stmt)

    async def record_failure(
        self,
        session: AsyncSession,
        record_id: int,
        *,
        error: str,
        max_retries: int = 5,
        next_attempt_at: datetime | None = None,
        now: datetime | None = None,
    ) -> None:
        """Increment retry_count, schedule backoff, optionally dead-letter.

        ``next_attempt_at=None`` triggers automatic exponential backoff
        based on the post-increment retry count. Pass an explicit value
        when the relay wants to override (tests, calibration).
        """

        cutoff = now or _utcnow()
        record = await session.get(AuditOutboxRecord, record_id)
        if record is None:
            return
        record.retry_count += 1
        record.last_error = error
        if next_attempt_at is not None:
            record.next_attempt_at = next_attempt_at
        else:
            record.next_attempt_at = cutoff + _backoff_delta(record.retry_count)
        if record.retry_count >= max_retries:
            record.dead_letter = True
        await session.flush()

    async def mark_dead_letter(
        self,
        session: AsyncSession,
        record_id: int,
        *,
        error: str,
    ) -> None:
        """Permanent failure (e.g. 4xx response): no retry."""

        record = await session.get(AuditOutboxRecord, record_id)
        if record is None:
            return
        record.dead_letter = True
        record.last_error = error
        record.retry_count += 1
        await session.flush()


__all__ = ["OutboxStore"]
