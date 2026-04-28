"""``@audit_emit`` decorator (Audit-3, BSVibe Lockin Decision #14).

The decorator wraps an async function and automatically emits a typed
audit event after the function returns. It pulls the standard fields —
``actor`` / ``tenant_id`` — from the wrapped function's keyword
arguments and (optionally) the resource id from the return value.

Adoption guidance — what auto-resolves vs. what needs adapters:

* **Auto-OK** (4-product hot paths sampled): create/update endpoints
  whose handler signatures look like ``async def handler(*, body,
  actor, tenant_id, session)`` and whose return value carries an ``id``
  attribute. Examples: ``nexus.project.created``,
  ``sage.knowledge.entry_created``, ``sage.vault.file_modified``,
  ``gateway.api_key.issued``.
* **Adapter-needed**: when before/after diff or "why" metadata matters
  (``nexus.run.completed`` carries a status; ``supervisor.budget.exceeded``
  carries a quota id). For these, pass ``data_extractor=`` and the
  decorator forwards the dict into ``event.data``.

Adoption pivot (Phase Audit Batch 3) — three knobs unblocking
product-side adoption:

* ``safe=True`` mirrors the BSNexus ``safe_emit`` semantics — emit
  failures are logged via structlog and swallowed so a serialization
  bug or a session-state quirk cannot 500 the user-facing endpoint.
  Default ``safe=False`` keeps the strict outbox-pattern propagation
  (caller's transaction rolls back along with the audit row) so the
  Audit-1 wire-format guarantee is preserved.
* ``actor_factory`` + ``actor_kwarg`` lets the handler keep its native
  user/principal kwarg (``user: User = Depends(get_current_user)``)
  instead of being forced to receive a pre-built :class:`AuditActor`.
  The factory is called with ``kwargs[actor_kwarg]`` and must return
  an :class:`AuditActor`. Without a factory the decorator falls back
  to the original PoC behaviour and asserts the kwarg is already an
  :class:`AuditActor`.
* ``OutboxProtocol`` (typing.Protocol) abstracts over the persistence
  target. ``AsyncSession`` is the canonical implementation today; the
  protocol allows BSage's aiosqlite path or a future test double to
  swap in without changing call sites. ``isinstance`` checks are
  retained against ``AsyncSession`` only when no factory is in use,
  so existing call sites keep their type guard.
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Awaitable, Callable
from typing import Any, Protocol, TypeVar, runtime_checkable

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from bsvibe_audit.emitter import AuditEmitter
from bsvibe_audit.events import EVENT_REGISTRY, AuditActor, AuditResource

R = TypeVar("R")


DataExtractor = Callable[[tuple[Any, ...], dict[str, Any], Any], dict[str, Any]]
ActorFactory = Callable[[Any], AuditActor]


@runtime_checkable
class OutboxProtocol(Protocol):
    """Structural type for an outbox-capable session.

    Today's only concrete implementation is
    :class:`sqlalchemy.ext.asyncio.AsyncSession`; the Protocol exists so
    BSage (aiosqlite) and tests can supply a duck-typed double without
    inheriting from SQLAlchemy. ``OutboxStore`` only needs ``add`` +
    ``flush`` from the session, both of which are present on every
    ``AsyncSession`` and are the documented contract for an "in-flight
    transaction the caller commits later" outbox row.
    """

    def add(self, instance: Any) -> None: ...

    async def flush(self) -> None: ...


_logger = structlog.get_logger("bsvibe_audit.decorator")


def _bind_kwargs(
    func: Callable[..., Awaitable[R]],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    """Best-effort merge of positional + keyword args into a kwargs dict."""

    try:
        sig = inspect.signature(func)
        bound = sig.bind_partial(*args, **kwargs)
        return dict(bound.arguments)
    except TypeError:
        return dict(kwargs)


def audit_emit(
    event_type: str,
    *,
    emitter: AuditEmitter,
    resource_type: str | None = None,
    resource_id_attr: str | None = None,
    session_kwarg: str = "session",
    actor_kwarg: str = "actor",
    tenant_kwarg: str = "tenant_id",
    actor_factory: ActorFactory | None = None,
    data_extractor: DataExtractor | None = None,
    safe: bool = False,
) -> Callable[[Callable[..., Awaitable[R]]], Callable[..., Awaitable[R]]]:
    """Decorate an async function to emit an audit event after it returns.

    Args:
        event_type: A registered event type from
            :data:`bsvibe_audit.events.EVENT_REGISTRY`. Unknown values
            raise :class:`KeyError` immediately so typos surface at
            import time.
        emitter: The :class:`AuditEmitter` to use for the in-transaction
            insert.
        resource_type: ``resource.type`` for the emitted event. When
            absent, no resource block is attached.
        resource_id_attr: Attribute name on the wrapped function's
            return value used as ``resource.id``. ``None`` skips
            extraction.
        session_kwarg: Keyword arg name carrying the outbox session
            (default ``"session"``). Implementations must satisfy
            :class:`OutboxProtocol`; today every BSVibe service uses
            :class:`AsyncSession`.
        actor_kwarg / tenant_kwarg: Keyword arg names carrying the
            actor and tenant id. ``actor_factory`` controls whether the
            actor kwarg arrives pre-built as :class:`AuditActor` or as
            a domain object (``User`` etc.) that needs conversion.
        actor_factory: Optional ``Callable[[Any], AuditActor]`` invoked
            with ``kwargs[actor_kwarg]`` to produce the audit actor.
            When ``None`` the decorator requires ``kwargs[actor_kwarg]``
            to already be an :class:`AuditActor` (the original PoC
            contract).
        data_extractor: Callable ``(args, kwargs, result) -> dict``.
            Result is forwarded into ``event.data``. Return ``None``
            from the extractor to emit an empty data dict.
        safe: When True, exceptions raised by the audit emit path are
            logged via structlog and swallowed so the wrapped function
            still returns its result. Use this on user-facing handlers
            where audit must degrade to "log + carry on" rather than
            500. Defaults to False to preserve the outbox-pattern
            atomicity guarantee (caller's transaction rolls back if the
            audit row cannot be written).

    Failure semantics:
        * If the wrapped call raises, no event is emitted regardless of
          ``safe``.
        * If event emission fails and ``safe=False`` (default), the
          underlying exception propagates; the caller's transaction
          will roll back along with it (this is the outbox-pattern's
          whole point).
        * If event emission fails and ``safe=True``, the failure is
          logged at ``warning`` and the wrapped result is returned to
          the caller — matches the BSNexus ``safe_emit`` semantics.
    """

    if event_type not in EVENT_REGISTRY:
        raise KeyError(
            f"audit_emit: unknown event_type {event_type!r}. Register a Pydantic class via bsvibe_audit.events first."
        )

    event_cls = EVENT_REGISTRY[event_type]

    def decorator(func: Callable[..., Awaitable[R]]) -> Callable[..., Awaitable[R]]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> R:
            result = await func(*args, **kwargs)
            try:
                bound = _bind_kwargs(func, args, kwargs)
                raw_actor = bound.get(actor_kwarg)
                tenant_id = bound.get(tenant_kwarg)
                session = bound.get(session_kwarg)

                if actor_factory is not None:
                    actor = actor_factory(raw_actor)
                    if not isinstance(actor, AuditActor):
                        raise TypeError(f"audit_emit: actor_factory must return AuditActor, got {type(actor).__name__}")
                else:
                    if not isinstance(raw_actor, AuditActor):
                        raise TypeError(
                            f"audit_emit: expected '{actor_kwarg}' kwarg of type AuditActor, "
                            f"got {type(raw_actor).__name__} (set actor_factory= to convert "
                            f"a domain object to AuditActor)"
                        )
                    actor = raw_actor

                # Without an explicit factory we keep the strict
                # AsyncSession guard so existing PoC call sites still
                # surface mis-wired sessions loudly. Factory call sites
                # are typically FastAPI routes where the session arrives
                # via Depends() and is structurally compatible — duck
                # typing against ``OutboxProtocol`` is enough.
                if actor_factory is None:
                    if not isinstance(session, AsyncSession):
                        raise TypeError(
                            f"audit_emit: expected '{session_kwarg}' kwarg of type AsyncSession, "
                            f"got {type(session).__name__}"
                        )
                else:
                    if session is None or not isinstance(session, OutboxProtocol):
                        raise TypeError(
                            f"audit_emit: expected '{session_kwarg}' kwarg satisfying OutboxProtocol "
                            f"(add + async flush), got {type(session).__name__}"
                        )

                resource: AuditResource | None = None
                if resource_type is not None and resource_id_attr is not None:
                    resource_id = getattr(result, resource_id_attr, None)
                    if resource_id is not None:
                        resource = AuditResource(type=resource_type, id=str(resource_id))

                data: dict[str, Any] = {}
                if data_extractor is not None:
                    extracted = data_extractor(args, kwargs, result)
                    if extracted is not None:
                        data = dict(extracted)

                event = event_cls(
                    actor=actor,
                    tenant_id=tenant_id if isinstance(tenant_id, (str, type(None))) else str(tenant_id),
                    resource=resource,
                    data=data,
                )
                await emitter.emit(event, session=session)
            except Exception:  # noqa: BLE001
                if not safe:
                    raise
                _logger.warning(
                    "audit_emit_failed",
                    event_type=event_type,
                    func=getattr(func, "__qualname__", repr(func)),
                    exc_info=True,
                )
            return result

        return wrapper

    return decorator


__all__ = [
    "ActorFactory",
    "DataExtractor",
    "OutboxProtocol",
    "audit_emit",
]
