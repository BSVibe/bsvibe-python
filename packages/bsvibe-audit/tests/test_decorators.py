"""Tests for the @audit_emit decorator (Audit-3 PoC).

The decorator's contract:

* Wrap an async function.
* On success, build an :class:`AuditEventBase` (or registered subclass) and
  emit through the supplied :class:`AuditEmitter`.
* Read ``actor`` and ``tenant_id`` from the wrapped function's keyword args
  (any callable that exposes ``actor: AuditActor`` and ``tenant_id``).
* Optionally read a ``resource_id_attr`` from the return value.
* If the call raises, no event is emitted (failure-silent).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from bsvibe_audit import (
    AuditEmitter,
    AuditOutboxBase,
    audit_emit,
)
from bsvibe_audit.events import AuditActor


async def _factory() -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(AuditOutboxBase.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)


@dataclass
class _Project:
    id: str
    name: str


async def test_audit_emit_decorator_emits_on_success() -> None:
    factory = await _factory()
    emitter = AuditEmitter()
    spy = AsyncMock(wraps=emitter.emit)
    emitter.emit = spy  # type: ignore[method-assign]

    @audit_emit(
        "nexus.project.created",
        emitter=emitter,
        resource_type="project",
        resource_id_attr="id",
    )
    async def create_project(
        *,
        name: str,
        actor: AuditActor,
        tenant_id: str,
        session: AsyncSession,
    ) -> _Project:
        return _Project(id="p-1", name=name)

    actor = AuditActor(type="user", id="u-1")
    async with factory() as session:
        project = await create_project(name="hi", actor=actor, tenant_id="t-1", session=session)
        await session.commit()

    assert project.id == "p-1"
    spy.assert_awaited_once()
    event = spy.await_args.args[0]
    assert event.event_type == "nexus.project.created"
    assert event.actor.id == "u-1"
    assert event.tenant_id == "t-1"
    assert event.resource is not None
    assert event.resource.type == "project"
    assert event.resource.id == "p-1"


async def test_audit_emit_decorator_skips_on_exception() -> None:
    factory = await _factory()
    emitter = AuditEmitter()
    spy = AsyncMock(wraps=emitter.emit)
    emitter.emit = spy  # type: ignore[method-assign]

    @audit_emit(
        "nexus.project.created",
        emitter=emitter,
        resource_type="project",
        resource_id_attr="id",
    )
    async def create_project(
        *,
        actor: AuditActor,
        tenant_id: str,
        session: AsyncSession,
    ) -> _Project:
        raise RuntimeError("repo failed")

    actor = AuditActor(type="user", id="u-1")
    async with factory() as session:
        with pytest.raises(RuntimeError):
            await create_project(actor=actor, tenant_id="t-1", session=session)

    spy.assert_not_awaited()


async def test_audit_emit_decorator_supports_data_extractor() -> None:
    factory = await _factory()
    emitter = AuditEmitter()
    spy = AsyncMock(wraps=emitter.emit)
    emitter.emit = spy  # type: ignore[method-assign]

    def extract(args: tuple[Any, ...], kwargs: dict[str, Any], result: Any) -> dict[str, Any]:
        return {"name": result.name, "explicit": True}

    @audit_emit(
        "nexus.project.created",
        emitter=emitter,
        resource_type="project",
        resource_id_attr="id",
        data_extractor=extract,
    )
    async def create_project(
        *,
        name: str,
        actor: AuditActor,
        tenant_id: str,
        session: AsyncSession,
    ) -> _Project:
        return _Project(id="p-2", name=name)

    actor = AuditActor(type="user", id="u-1")
    async with factory() as session:
        await create_project(name="proj-x", actor=actor, tenant_id="t-1", session=session)
        await session.commit()

    event = spy.await_args.args[0]
    assert event.data == {"name": "proj-x", "explicit": True}


async def test_audit_emit_decorator_unknown_event_type_raises() -> None:
    """Unknown event_type at decoration time fails fast (CI catches typos)."""
    emitter = AuditEmitter()
    with pytest.raises(KeyError):

        @audit_emit("totally.bogus.event", emitter=emitter)
        async def bad() -> None:
            return None
