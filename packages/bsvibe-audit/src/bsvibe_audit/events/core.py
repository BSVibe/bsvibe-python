"""``core.*`` events emitted by BSVibe-Auth's audit query API itself.

These events are the "audit of the audit" — every read/export of audit
data is itself audited (per BSVibe_Audit_Design.md §10 D-A5).
"""

from __future__ import annotations

from typing import ClassVar

from bsvibe_audit.events.base import AuditEventBase


class AuditRead(AuditEventBase):
    DEFAULT_EVENT_TYPE: ClassVar[str] = "core.audit.read"


class AuditExport(AuditEventBase):
    DEFAULT_EVENT_TYPE: ClassVar[str] = "core.audit.export"


__all__ = ["AuditRead", "AuditExport"]
