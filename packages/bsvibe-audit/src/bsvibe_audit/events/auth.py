"""``auth.*`` events emitted by BSVibe-Auth.

Per BSVibe_Audit_Design.md §2.1. Each subclass pins its
``event_type`` so producers cannot drift.
"""

from __future__ import annotations

from typing import ClassVar

from bsvibe_audit.events.base import AuditEventBase


class UserCreated(AuditEventBase):
    DEFAULT_EVENT_TYPE: ClassVar[str] = "auth.user.created"


class SessionStarted(AuditEventBase):
    DEFAULT_EVENT_TYPE: ClassVar[str] = "auth.session.started"


class SessionFailed(AuditEventBase):
    DEFAULT_EVENT_TYPE: ClassVar[str] = "auth.session.failed"


class TenantMemberAdded(AuditEventBase):
    DEFAULT_EVENT_TYPE: ClassVar[str] = "auth.tenant.member_added"


class TenantMemberRoleChanged(AuditEventBase):
    DEFAULT_EVENT_TYPE: ClassVar[str] = "auth.tenant.member_role_changed"


class TenantMemberRemoved(AuditEventBase):
    DEFAULT_EVENT_TYPE: ClassVar[str] = "auth.tenant.member_removed"


class TenantSwitched(AuditEventBase):
    DEFAULT_EVENT_TYPE: ClassVar[str] = "auth.tenant.switched"


class ServiceTokenIssued(AuditEventBase):
    DEFAULT_EVENT_TYPE: ClassVar[str] = "auth.service_token.issued"


class AuthzRelationGranted(AuditEventBase):
    DEFAULT_EVENT_TYPE: ClassVar[str] = "auth.authz.relation.granted"


class AuthzRelationRevoked(AuditEventBase):
    DEFAULT_EVENT_TYPE: ClassVar[str] = "auth.authz.relation.revoked"


__all__ = [
    "UserCreated",
    "SessionStarted",
    "SessionFailed",
    "TenantMemberAdded",
    "TenantMemberRoleChanged",
    "TenantMemberRemoved",
    "TenantSwitched",
    "ServiceTokenIssued",
    "AuthzRelationGranted",
    "AuthzRelationRevoked",
]
