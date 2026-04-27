"""``sage.*`` events emitted by BSage."""

from __future__ import annotations

from typing import ClassVar

from bsvibe_audit.events.base import AuditEventBase


class KnowledgeEntryCreated(AuditEventBase):
    DEFAULT_EVENT_TYPE: ClassVar[str] = "sage.knowledge.entry_created"


class KnowledgeEntryUpdated(AuditEventBase):
    DEFAULT_EVENT_TYPE: ClassVar[str] = "sage.knowledge.entry_updated"


class DecisionRecorded(AuditEventBase):
    DEFAULT_EVENT_TYPE: ClassVar[str] = "sage.decision.recorded"


class VaultFileModified(AuditEventBase):
    DEFAULT_EVENT_TYPE: ClassVar[str] = "sage.vault.file_modified"


__all__ = [
    "KnowledgeEntryCreated",
    "KnowledgeEntryUpdated",
    "DecisionRecorded",
    "VaultFileModified",
]
