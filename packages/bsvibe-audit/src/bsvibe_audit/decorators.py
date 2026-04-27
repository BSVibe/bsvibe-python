"""``@audit_emit`` decorator (Audit-3 PoC, BSVibe Lockin Decision #14).

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

The PoC sample below covers four representative endpoints — see the
PR description for the auto-vs-manual ratio.
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession

from bsvibe_audit.emitter import AuditEmitter
from bsvibe_audit.events import EVENT_REGISTRY, AuditActor, AuditResource

R = TypeVar("R")


DataExtractor = Callable[[tuple[Any, ...], dict[str, Any], Any], dict[str, Any]]


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
    data_extractor: DataExtractor | None = None,
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
        session_kwarg: Keyword arg name carrying the
            :class:`AsyncSession` (default ``"session"``).
        actor_kwarg / tenant_kwarg: Keyword arg names carrying the
            :class:`AuditActor` and tenant id.
        data_extractor: Callable ``(args, kwargs, result) -> dict``.
            Result is forwarded into ``event.data``. Return None
            from the extractor to emit an empty data dict.

    Failure semantics:
        * If the wrapped call raises, no event is emitted.
        * If event emission fails, the underlying exception propagates;
          the caller's transaction will roll back along with it (this is
          the outbox-pattern's whole point).
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
            bound = _bind_kwargs(func, args, kwargs)
            actor = bound.get(actor_kwarg)
            tenant_id = bound.get(tenant_kwarg)
            session = bound.get(session_kwarg)

            if not isinstance(actor, AuditActor):
                raise TypeError(
                    f"audit_emit: expected '{actor_kwarg}' kwarg of type AuditActor, got {type(actor).__name__}"
                )
            if not isinstance(session, AsyncSession):
                raise TypeError(
                    f"audit_emit: expected '{session_kwarg}' kwarg of type AsyncSession, got {type(session).__name__}"
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
            return result

        return wrapper

    return decorator


__all__ = ["audit_emit", "DataExtractor"]
