"""Wire test: OutboxRelay forwards delivered events to AlertRuleEngine.

The integration contract is small but important: when ``run_once``
successfully ships a batch, it should pass the same payloads to the
configured :class:`AlertRuleEngine`. Failed deliveries do *not* fire
alerts — we want operators to react to events that actually exist in
the audit store, not to phantom ones.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from bsvibe_audit import (
    AuditClient,
    AuditEmitter,
    AuditOutboxBase,
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


def _stub_client() -> AuditClient:
    client = AuditClient.__new__(AuditClient)
    client._owned_http = False
    client._http = None  # type: ignore[assignment]
    client.audit_url = "https://auth.test"
    client.service_token = "tok"
    client.send = AsyncMock()  # type: ignore[method-assign]
    client.aclose = AsyncMock()  # type: ignore[method-assign]
    return client


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


async def test_run_once_forwards_delivered_events_to_alert_engine() -> None:
    factory = await _factory()
    await _seed(factory, count=2)
    client = _stub_client()
    engine = AsyncMock()
    engine.evaluate = AsyncMock()

    relay = OutboxRelay(
        session_factory=factory,
        client=client,
        store=OutboxStore(),
        batch_size=10,
        interval_s=0.01,
        alert_engine=engine,
    )

    delivered = await relay.run_once()
    assert delivered == 2
    engine.evaluate.assert_awaited_once()
    forwarded: list[dict[str, Any]] = engine.evaluate.await_args.args[0]
    assert len(forwarded) == 2
    assert all("event_type" in payload for payload in forwarded)


async def test_run_once_skips_alert_engine_on_delivery_failure() -> None:
    factory = await _factory()
    await _seed(factory, count=1)
    client = _stub_client()
    client.send.side_effect = AuditDeliveryError("503 boom", retryable=True)  # type: ignore[attr-defined]
    engine = AsyncMock()
    engine.evaluate = AsyncMock()

    relay = OutboxRelay(
        session_factory=factory,
        client=client,
        store=OutboxStore(),
        batch_size=10,
        interval_s=0.01,
        alert_engine=engine,
    )
    delivered = await relay.run_once()
    assert delivered == 0
    engine.evaluate.assert_not_awaited()


async def test_run_once_isolates_alert_engine_failures() -> None:
    """A broken alert engine must never break the audit relay's success path."""

    factory = await _factory()
    await _seed(factory, count=1)
    client = _stub_client()
    engine = AsyncMock()
    engine.evaluate = AsyncMock(side_effect=RuntimeError("alerts down"))

    relay = OutboxRelay(
        session_factory=factory,
        client=client,
        store=OutboxStore(),
        batch_size=10,
        interval_s=0.01,
        alert_engine=engine,
    )
    delivered = await relay.run_once()
    # Delivery still counts as successful — alerting is best-effort.
    assert delivered == 1
