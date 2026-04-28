"""Tests for AuditEmitter.

The emitter's contract:

1. ``emit(event, session)`` inserts a single ``audit_outbox`` row inside
   the caller's session — no commit, no network I/O.
2. ``trace_id`` is auto-pulled from structlog ``contextvars`` if the
   event itself didn't set one. Caller-provided trace_id wins.
3. The serialised payload is JSON-mode (UUIDs and datetimes are strings),
   so the relay can POST it verbatim.
"""

from __future__ import annotations

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from bsvibe_audit import AuditEmitter, AuditOutboxBase, AuditOutboxRecord
from bsvibe_audit.events import AuditActor
from bsvibe_audit.events.auth import UserCreated


async def _session_factory() -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(AuditOutboxBase.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)


async def test_emitter_inserts_outbox_row() -> None:
    factory = await _session_factory()
    emitter = AuditEmitter()
    actor = AuditActor(type="user", id="u-1")
    event = UserCreated(actor=actor, tenant_id="t-1", data={"email": "a@b.test"})

    async with factory() as session:
        await emitter.emit(event, session=session)
        await session.commit()

    async with factory() as session:
        rows = (await session.execute(select(AuditOutboxRecord))).scalars().all()
    assert len(rows) == 1
    assert rows[0].event_type == "auth.user.created"
    assert rows[0].delivered_at is None
    assert rows[0].retry_count == 0
    payload = rows[0].payload
    assert payload["event_type"] == "auth.user.created"
    assert payload["actor"]["id"] == "u-1"
    # UUIDs serialised as strings
    assert isinstance(payload["event_id"], str)


async def test_emitter_pulls_trace_id_from_structlog_contextvars() -> None:
    factory = await _session_factory()
    emitter = AuditEmitter()
    actor = AuditActor(type="user", id="u-1")
    structlog.contextvars.bind_contextvars(trace_id="trace-abc")
    event = UserCreated(actor=actor, tenant_id="t-1")

    async with factory() as session:
        await emitter.emit(event, session=session)
        await session.commit()

    async with factory() as session:
        row = (await session.execute(select(AuditOutboxRecord))).scalars().one()
    assert row.payload["trace_id"] == "trace-abc"


async def test_emitter_does_not_overwrite_explicit_trace_id() -> None:
    factory = await _session_factory()
    emitter = AuditEmitter()
    actor = AuditActor(type="user", id="u-1")
    structlog.contextvars.bind_contextvars(trace_id="ambient")
    event = UserCreated(actor=actor, tenant_id="t-1", trace_id="explicit")

    async with factory() as session:
        await emitter.emit(event, session=session)
        await session.commit()

    async with factory() as session:
        row = (await session.execute(select(AuditOutboxRecord))).scalars().one()
    assert row.payload["trace_id"] == "explicit"


async def test_emitter_does_not_commit_caller_session() -> None:
    """A failure after emit() must roll back the outbox row too."""
    factory = await _session_factory()
    emitter = AuditEmitter()
    actor = AuditActor(type="user", id="u-1")
    event = UserCreated(actor=actor, tenant_id="t-1")

    async with factory() as session:
        await emitter.emit(event, session=session)
        await session.rollback()

    async with factory() as session:
        rows = (await session.execute(select(AuditOutboxRecord))).scalars().all()
    assert rows == []
