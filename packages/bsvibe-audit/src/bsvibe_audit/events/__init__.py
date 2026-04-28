"""Audit event catalog.

Re-exports the base wire types plus a frozen ``EVENT_REGISTRY`` mapping
each ``event_type`` to its Pydantic class. Lookup is the contract used
by :func:`bsvibe_audit.audit_emit` and CI lints to validate that every
emit call references a known event type.
"""

from __future__ import annotations

from bsvibe_audit.events import auth, core, gateway, nexus, sage, supervisor
from bsvibe_audit.events.base import (
    ActorType,
    AuditActor,
    AuditEventBase,
    AuditResource,
)


def _build_registry() -> dict[str, type[AuditEventBase]]:
    registry: dict[str, type[AuditEventBase]] = {}
    for module in (auth, nexus, gateway, supervisor, sage, core):
        for name in module.__all__:
            cls = getattr(module, name)
            if not isinstance(cls, type) or not issubclass(cls, AuditEventBase):
                continue
            event_type = cls.DEFAULT_EVENT_TYPE
            if event_type is None:
                continue
            if event_type in registry and registry[event_type] is not cls:
                raise RuntimeError(
                    f"duplicate event_type registration: {event_type!r} maps to "
                    f"{registry[event_type].__name__} and {cls.__name__}"
                )
            registry[event_type] = cls
    return registry


EVENT_REGISTRY: dict[str, type[AuditEventBase]] = _build_registry()


__all__ = [
    "ActorType",
    "AuditActor",
    "AuditResource",
    "AuditEventBase",
    "EVENT_REGISTRY",
    "auth",
    "nexus",
    "gateway",
    "supervisor",
    "sage",
    "core",
]
