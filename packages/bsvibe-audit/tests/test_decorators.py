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


# ── Phase Audit Batch 3 — decorator extensions ──────────────────────


@dataclass
class _User:
    """Domain object that arrives via FastAPI ``Depends(get_current_user)``."""

    id: str
    email: str
    tenant_id: str


def _user_to_actor(user: _User) -> AuditActor:
    return AuditActor(type="user", id=user.id, email=user.email)


async def test_audit_emit_safe_false_propagates_emit_failure() -> None:
    """``safe=False`` (default) keeps the outbox-pattern atomicity contract."""
    factory = await _factory()
    emitter = AuditEmitter()
    boom = AsyncMock(side_effect=RuntimeError("audit store down"))
    emitter.emit = boom  # type: ignore[method-assign]

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
        return _Project(id="p-strict", name="strict")

    actor = AuditActor(type="user", id="u-1")
    async with factory() as session:
        with pytest.raises(RuntimeError, match="audit store down"):
            await create_project(actor=actor, tenant_id="t-1", session=session)
    boom.assert_awaited_once()


async def test_audit_emit_safe_true_swallows_emit_failure() -> None:
    """``safe=True`` mirrors the BSNexus ``safe_emit`` semantics."""
    factory = await _factory()
    emitter = AuditEmitter()
    boom = AsyncMock(side_effect=RuntimeError("audit store down"))
    emitter.emit = boom  # type: ignore[method-assign]

    @audit_emit(
        "nexus.project.created",
        emitter=emitter,
        resource_type="project",
        resource_id_attr="id",
        safe=True,
    )
    async def create_project(
        *,
        actor: AuditActor,
        tenant_id: str,
        session: AsyncSession,
    ) -> _Project:
        return _Project(id="p-safe", name="safe")

    actor = AuditActor(type="user", id="u-1")
    async with factory() as session:
        # No exception even though emitter raises — handler still
        # returns its result, exactly as ``safe_emit`` does.
        project = await create_project(actor=actor, tenant_id="t-1", session=session)

    assert project.id == "p-safe"
    boom.assert_awaited_once()


async def test_audit_emit_safe_true_does_not_emit_when_handler_raises() -> None:
    """Even with ``safe=True`` the handler exception propagates and no emit happens."""
    factory = await _factory()
    emitter = AuditEmitter()
    spy = AsyncMock(wraps=emitter.emit)
    emitter.emit = spy  # type: ignore[method-assign]

    @audit_emit(
        "nexus.project.created",
        emitter=emitter,
        resource_type="project",
        resource_id_attr="id",
        safe=True,
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
        with pytest.raises(RuntimeError, match="repo failed"):
            await create_project(actor=actor, tenant_id="t-1", session=session)
    spy.assert_not_awaited()


async def test_audit_emit_actor_factory_converts_domain_user() -> None:
    """``actor_factory`` turns a domain user kwarg into an :class:`AuditActor`."""
    factory = await _factory()
    emitter = AuditEmitter()
    spy = AsyncMock(wraps=emitter.emit)
    emitter.emit = spy  # type: ignore[method-assign]

    factory_calls: list[_User] = []

    def factory_fn(user: _User) -> AuditActor:
        factory_calls.append(user)
        return _user_to_actor(user)

    @audit_emit(
        "nexus.project.created",
        emitter=emitter,
        resource_type="project",
        resource_id_attr="id",
        actor_factory=factory_fn,
        actor_kwarg="user",
    )
    async def create_project(
        *,
        name: str,
        user: _User,
        tenant_id: str,
        session: AsyncSession,
    ) -> _Project:
        return _Project(id="p-fact", name=name)

    user = _User(id="u-7", email="founder@bsvibe.dev", tenant_id="t-9")
    async with factory() as session:
        await create_project(name="hi", user=user, tenant_id="t-9", session=session)
        await session.commit()

    spy.assert_awaited_once()
    assert factory_calls == [user]
    event = spy.await_args.args[0]
    assert event.actor.type == "user"
    assert event.actor.id == "u-7"
    assert event.actor.email == "founder@bsvibe.dev"
    assert event.tenant_id == "t-9"


async def test_audit_emit_actor_factory_must_return_audit_actor() -> None:
    """A misconfigured factory surfaces loudly even in ``safe=False`` mode."""
    factory = await _factory()
    emitter = AuditEmitter()

    def bad_factory(_user: _User) -> AuditActor:  # type: ignore[return-value]
        return "not-an-actor"  # type: ignore[return-value]

    @audit_emit(
        "nexus.project.created",
        emitter=emitter,
        actor_factory=bad_factory,
        actor_kwarg="user",
    )
    async def handler(*, user: _User, tenant_id: str, session: AsyncSession) -> _Project:
        return _Project(id="p", name="p")

    user = _User(id="u-1", email="a@b", tenant_id="t-1")
    async with factory() as session:
        with pytest.raises(TypeError, match="actor_factory"):
            await handler(user=user, tenant_id="t-1", session=session)


async def test_audit_emit_without_factory_still_requires_audit_actor() -> None:
    """Backward-compat: no factory → strict ``AuditActor`` kwarg check."""
    factory = await _factory()
    emitter = AuditEmitter()

    @audit_emit("nexus.project.created", emitter=emitter)
    async def handler(*, actor: Any, tenant_id: str, session: AsyncSession) -> _Project:
        return _Project(id="p", name="p")

    async with factory() as session:
        with pytest.raises(TypeError, match="AuditActor"):
            await handler(actor="not-an-actor", tenant_id="t-1", session=session)


async def test_audit_emit_outbox_protocol_accepts_async_session() -> None:
    """Sanity check that ``AsyncSession`` satisfies :class:`OutboxProtocol`."""
    from bsvibe_audit import OutboxProtocol

    factory = await _factory()
    async with factory() as session:
        assert isinstance(session, OutboxProtocol)


async def test_audit_emit_outbox_protocol_rejects_non_outbox_session() -> None:
    """With a factory in play, sessions must satisfy :class:`OutboxProtocol`."""
    emitter = AuditEmitter()

    @audit_emit(
        "nexus.project.created",
        emitter=emitter,
        actor_factory=_user_to_actor,
        actor_kwarg="user",
    )
    async def handler(*, user: _User, tenant_id: str, session: Any) -> _Project:
        return _Project(id="p", name="p")

    user = _User(id="u-1", email="a@b", tenant_id="t-1")
    with pytest.raises(TypeError, match="OutboxProtocol"):
        await handler(user=user, tenant_id="t-1", session=object())
