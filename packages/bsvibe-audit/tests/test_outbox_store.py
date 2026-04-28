"""Tests for OutboxStore — read/mark-delivered helpers used by the relay."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from bsvibe_audit import (
    AuditEmitter,
    AuditOutboxBase,
    OutboxStore,
)
from bsvibe_audit.events import AuditActor
from bsvibe_audit.events.auth import UserCreated


async def _factory() -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(AuditOutboxBase.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)


async def _seed_events(factory: async_sessionmaker[AsyncSession], count: int) -> None:
    emitter = AuditEmitter()
    actor = AuditActor(type="user", id="u-1")
    async with factory() as session:
        for i in range(count):
            await emitter.emit(
                UserCreated(actor=actor, tenant_id="t-1", data={"email": f"u{i}@b.test"}),
                session=session,
            )
        await session.commit()


async def test_select_undelivered_respects_batch_size() -> None:
    factory = await _factory()
    await _seed_events(factory, count=10)
    store = OutboxStore()
    async with factory() as session:
        rows = await store.select_undelivered(session, batch_size=3)
    assert len(rows) == 3


async def test_select_undelivered_skips_delivered_rows() -> None:
    factory = await _factory()
    await _seed_events(factory, count=3)
    store = OutboxStore()
    async with factory() as session:
        rows = await store.select_undelivered(session, batch_size=10)
        await store.mark_delivered(session, [rows[0].id])
        await session.commit()
    async with factory() as session:
        rows = await store.select_undelivered(session, batch_size=10)
    assert len(rows) == 2


async def test_record_failure_increments_retry_count_and_records_error() -> None:
    factory = await _factory()
    await _seed_events(factory, count=1)
    store = OutboxStore()
    # Pin next_attempt_at to the past so the row remains selectable for
    # this test — without it, the backoff schedules ~1s into the future.
    immediate = datetime.now(UTC) - timedelta(seconds=1)
    async with factory() as session:
        rows = await store.select_undelivered(session, batch_size=1)
        await store.record_failure(session, rows[0].id, error="HTTP 500: boom", next_attempt_at=immediate)
        await session.commit()
    async with factory() as session:
        rows = await store.select_undelivered(session, batch_size=1)
    assert len(rows) == 1
    assert rows[0].retry_count == 1
    assert rows[0].last_error == "HTTP 500: boom"


async def test_select_undelivered_skips_rows_under_backoff() -> None:
    """Failed rows enter exponential backoff: not eligible until next_attempt_at <= now."""
    factory = await _factory()
    await _seed_events(factory, count=1)
    store = OutboxStore()
    far_future = datetime.now(UTC) + timedelta(hours=1)
    async with factory() as session:
        rows = await store.select_undelivered(session, batch_size=1)
        await store.record_failure(
            session,
            rows[0].id,
            error="boom",
            next_attempt_at=far_future,
        )
        await session.commit()
    async with factory() as session:
        rows = await store.select_undelivered(session, batch_size=10)
    assert rows == []


async def test_record_failure_marks_dead_letter_after_max_retries() -> None:
    factory = await _factory()
    await _seed_events(factory, count=1)
    store = OutboxStore()
    async with factory() as session:
        rows = await store.select_undelivered(session, batch_size=1)
        row_id = rows[0].id
        for _ in range(5):
            await store.record_failure(session, row_id, error="boom", max_retries=5)
        await session.commit()

    async with factory() as session:
        rows = await store.select_undelivered(session, batch_size=10)
        assert rows == []  # dead letter rows excluded
        dead = await store.select_dead_letter(session)
    assert len(dead) == 1
    assert dead[0].id == row_id
    assert dead[0].dead_letter is True
