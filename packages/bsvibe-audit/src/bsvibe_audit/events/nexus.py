"""``nexus.*`` events emitted by BSNexus."""

from __future__ import annotations

from typing import ClassVar

from bsvibe_audit.events.base import AuditEventBase


class ProjectCreated(AuditEventBase):
    DEFAULT_EVENT_TYPE: ClassVar[str] = "nexus.project.created"


class RunStarted(AuditEventBase):
    DEFAULT_EVENT_TYPE: ClassVar[str] = "nexus.run.started"


class RunCompleted(AuditEventBase):
    DEFAULT_EVENT_TYPE: ClassVar[str] = "nexus.run.completed"


class RunBlocked(AuditEventBase):
    DEFAULT_EVENT_TYPE: ClassVar[str] = "nexus.run.blocked"


class RequestCreated(AuditEventBase):
    DEFAULT_EVENT_TYPE: ClassVar[str] = "nexus.request.created"


class DeliverableCreated(AuditEventBase):
    DEFAULT_EVENT_TYPE: ClassVar[str] = "nexus.deliverable.created"


class DecisionCreated(AuditEventBase):
    DEFAULT_EVENT_TYPE: ClassVar[str] = "nexus.decision.created"


class DecisionResolved(AuditEventBase):
    DEFAULT_EVENT_TYPE: ClassVar[str] = "nexus.decision.resolved"


__all__ = [
    "ProjectCreated",
    "RunStarted",
    "RunCompleted",
    "RunBlocked",
    "RequestCreated",
    "DeliverableCreated",
    "DecisionCreated",
    "DecisionResolved",
]
