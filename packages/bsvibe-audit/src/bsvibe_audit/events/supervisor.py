"""``supervisor.*`` events emitted by BSupervisor."""

from __future__ import annotations

from typing import ClassVar

from bsvibe_audit.events.base import AuditEventBase


class RuleViolated(AuditEventBase):
    DEFAULT_EVENT_TYPE: ClassVar[str] = "supervisor.rule.violated"


class BudgetExceeded(AuditEventBase):
    DEFAULT_EVENT_TYPE: ClassVar[str] = "supervisor.budget.exceeded"


class AlertPublished(AuditEventBase):
    DEFAULT_EVENT_TYPE: ClassVar[str] = "supervisor.alert.published"


class AnomalyDetected(AuditEventBase):
    DEFAULT_EVENT_TYPE: ClassVar[str] = "supervisor.anomaly.detected"


__all__ = [
    "RuleViolated",
    "BudgetExceeded",
    "AlertPublished",
    "AnomalyDetected",
]
