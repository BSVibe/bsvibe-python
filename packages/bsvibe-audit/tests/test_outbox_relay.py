"""Tests for OutboxRelay — the background poller that ships outbox rows.

These tests deliberately drive the relay synchronously via ``run_once``
so we don't depend on real timing. The async loop ``start()``/``stop()``
contract is exercised separately to guarantee the task lifecycle is
clean (no zombie tasks after stop).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from bsvibe_audit import (
    AuditClient,
    AuditEmitter,
    AuditOutboxBase,
    AuditOutboxRecord,
    OutboxRelay,
    OutboxStore,
)
from bsvibe_audit.client import AuditDeliveryError
from bsvibe_audit.events import AuditActor
from bsvibe_audit.events.auth import UserCreated


async def _factory() -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(AuditOutboxBase.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)


async def _seed(factory: async_sessionmaker[AsyncSession], count: int) -> None:
    emitter = AuditEmitter()
    actor = AuditActor(type="user", id="u-1")
    async with factory() as session:
        for i in range(count):
            await emitter.emit(
                UserCreated(actor=actor, tenant_id="t-1", data={"email": f"u{i}@b.test"}),
                session=session,
            )
        await session.commit()


def _stub_client() -> AuditClient:
    """Make a real AuditClient instance whose .send is mocked."""
    client = AuditClient.__new__(AuditClient)
    client._owned_http = False
    client._http = None  # type: ignore[assignment]
    client.audit_url = "https://auth.bsvibe.dev/api/audit/events"
    client.service_token = "tok"
    client.send = AsyncMock()  # type: ignore[method-assign]
    client.aclose = AsyncMock()  # type: ignore[method-assign]
    return client


async def test_run_once_marks_rows_delivered_on_success() -> None:
    factory = await _factory()
    await _seed(factory, count=3)
    client = _stub_client()
    relay = OutboxRelay(
        session_factory=factory,
        client=client,
        store=OutboxStore(),
        batch_size=10,
        interval_s=0.01,
    )

    delivered = await relay.run_once()
    assert delivered == 3
    client.send.assert_awaited_once()  # type: ignore[attr-defined]
    sent_payloads: list[dict[str, Any]] = client.send.await_args.args[0]  # type: ignore[union-attr]
    assert len(sent_payloads) == 3

    async with factory() as session:
        rows = (await session.execute(select(AuditOutboxRecord))).scalars().all()
    assert all(r.delivered_at is not None for r in rows)


async def test_run_once_records_failure_on_retryable_error() -> None:
    factory = await _factory()
    await _seed(factory, count=2)
    client = _stub_client()
    client.send.side_effect = AuditDeliveryError("503 boom", retryable=True)  # type: ignore[attr-defined]
    relay = OutboxRelay(
        session_factory=factory,
        client=client,
        store=OutboxStore(),
        batch_size=10,
        interval_s=0.01,
    )

    delivered = await relay.run_once()
    assert delivered == 0

    async with factory() as session:
        rows = (await session.execute(select(AuditOutboxRecord))).scalars().all()
    for row in rows:
        assert row.delivered_at is None
        assert row.retry_count == 1
        assert row.last_error is not None
        assert "503 boom" in row.last_error
        assert row.next_attempt_at is not None
        # SQLite drops tzinfo; compare against naive utcnow plus a small slack.
        # The relay schedules backoff strictly in the future; the row must
        # therefore be at least "now minus a second" (clock skew) ahead.
        baseline = datetime.utcnow().replace(tzinfo=row.next_attempt_at.tzinfo)
        assert row.next_attempt_at > baseline - timedelta(seconds=1)


async def test_run_once_marks_dead_letter_on_non_retryable_error() -> None:
    factory = await _factory()
    await _seed(factory, count=1)
    client = _stub_client()
    client.send.side_effect = AuditDeliveryError("400 bad event", retryable=False)  # type: ignore[attr-defined]
    relay = OutboxRelay(
        session_factory=factory,
        client=client,
        store=OutboxStore(),
        batch_size=10,
        interval_s=0.01,
    )

    delivered = await relay.run_once()
    assert delivered == 0

    async with factory() as session:
        rows = (await session.execute(select(AuditOutboxRecord))).scalars().all()
    assert len(rows) == 1
    assert rows[0].dead_letter is True


async def test_run_once_with_no_undelivered_rows_returns_zero() -> None:
    factory = await _factory()
    client = _stub_client()
    relay = OutboxRelay(
        session_factory=factory,
        client=client,
        store=OutboxStore(),
        batch_size=10,
        interval_s=0.01,
    )
    delivered = await relay.run_once()
    assert delivered == 0
    client.send.assert_not_awaited()  # type: ignore[attr-defined]


async def test_relay_start_stop_cleans_up_task() -> None:
    factory = await _factory()
    client = _stub_client()
    relay = OutboxRelay(
        session_factory=factory,
        client=client,
        store=OutboxStore(),
        batch_size=10,
        interval_s=0.01,
    )
    await relay.start()
    assert relay.is_running()
    await asyncio.sleep(0.02)
    await relay.stop()
    assert not relay.is_running()


async def test_relay_disabled_when_settings_url_missing() -> None:
    """When auth_audit_url is empty, ``from_settings`` returns a no-op relay.

    Products always call ``OutboxRelay.from_settings`` at startup; in dev
    environments without a configured Audit endpoint, start()/stop() must
    be safe no-ops rather than raise.
    """
    from bsvibe_audit import AuditSettings

    settings = AuditSettings()  # auth_audit_url == ""
    factory = await _factory()
    relay = OutboxRelay.from_settings(settings, session_factory=factory)
    await relay.start()
    assert not relay.is_running()  # noop relay never starts
    await relay.stop()
