"""Catalog test: every documented namespace exposes typed event classes.

The audit design (BSVibe_Audit_Design.md §2) lists the event surface
the four products and Auth must emit. Each typed class fixes
``event_type`` so producers cannot drift.
"""

from __future__ import annotations

import pytest

from bsvibe_audit.events import (
    EVENT_REGISTRY,
    AuditActor,
    AuditEventBase,
)
from bsvibe_audit.events import auth as auth_events
from bsvibe_audit.events import core as core_events
from bsvibe_audit.events import gateway as gateway_events
from bsvibe_audit.events import nexus as nexus_events
from bsvibe_audit.events import sage as sage_events
from bsvibe_audit.events import supervisor as supervisor_events


def _actor() -> AuditActor:
    return AuditActor(type="user", id="u-1")


@pytest.mark.parametrize(
    "cls,event_type",
    [
        (auth_events.UserCreated, "auth.user.created"),
        (auth_events.SessionStarted, "auth.session.started"),
        (auth_events.SessionFailed, "auth.session.failed"),
        (auth_events.TenantMemberAdded, "auth.tenant.member_added"),
        (auth_events.TenantMemberRoleChanged, "auth.tenant.member_role_changed"),
        (auth_events.TenantMemberRemoved, "auth.tenant.member_removed"),
        (auth_events.TenantSwitched, "auth.tenant.switched"),
        (auth_events.ServiceTokenIssued, "auth.service_token.issued"),
        (auth_events.AuthzRelationGranted, "auth.authz.relation.granted"),
        (auth_events.AuthzRelationRevoked, "auth.authz.relation.revoked"),
    ],
)
def test_auth_events_have_fixed_event_type(cls: type[AuditEventBase], event_type: str) -> None:
    event = cls(actor=_actor(), tenant_id="t-1")
    assert event.event_type == event_type
    assert event_type in EVENT_REGISTRY
    assert EVENT_REGISTRY[event_type] is cls


@pytest.mark.parametrize(
    "cls,event_type",
    [
        (nexus_events.ProjectCreated, "nexus.project.created"),
        (nexus_events.RunStarted, "nexus.run.started"),
        (nexus_events.RunCompleted, "nexus.run.completed"),
        (nexus_events.RunBlocked, "nexus.run.blocked"),
        (nexus_events.RequestCreated, "nexus.request.created"),
        (nexus_events.DeliverableCreated, "nexus.deliverable.created"),
        (nexus_events.DecisionCreated, "nexus.decision.created"),
        (nexus_events.DecisionResolved, "nexus.decision.resolved"),
    ],
)
def test_nexus_events_have_fixed_event_type(cls: type[AuditEventBase], event_type: str) -> None:
    event = cls(actor=_actor(), tenant_id="t-1")
    assert event.event_type == event_type
    assert EVENT_REGISTRY[event_type] is cls


@pytest.mark.parametrize(
    "cls,event_type",
    [
        (gateway_events.RouteConfigChanged, "gateway.route.config_changed"),
        (gateway_events.ApiKeyIssued, "gateway.api_key.issued"),
        (gateway_events.ApiKeyRevoked, "gateway.api_key.revoked"),
        (gateway_events.ClassifierCacheHit, "gateway.classifier.cache_hit"),
        (gateway_events.RateLimitViolated, "gateway.rate_limit.violated"),
    ],
)
def test_gateway_events_have_fixed_event_type(cls: type[AuditEventBase], event_type: str) -> None:
    event = cls(actor=_actor(), tenant_id="t-1")
    assert event.event_type == event_type
    assert EVENT_REGISTRY[event_type] is cls


@pytest.mark.parametrize(
    "cls,event_type",
    [
        (supervisor_events.RuleViolated, "supervisor.rule.violated"),
        (supervisor_events.BudgetExceeded, "supervisor.budget.exceeded"),
        (supervisor_events.AlertPublished, "supervisor.alert.published"),
        (supervisor_events.AnomalyDetected, "supervisor.anomaly.detected"),
    ],
)
def test_supervisor_events_have_fixed_event_type(cls: type[AuditEventBase], event_type: str) -> None:
    event = cls(actor=_actor(), tenant_id="t-1")
    assert event.event_type == event_type
    assert EVENT_REGISTRY[event_type] is cls


@pytest.mark.parametrize(
    "cls,event_type",
    [
        (sage_events.KnowledgeEntryCreated, "sage.knowledge.entry_created"),
        (sage_events.KnowledgeEntryUpdated, "sage.knowledge.entry_updated"),
        (sage_events.DecisionRecorded, "sage.decision.recorded"),
        (sage_events.VaultFileModified, "sage.vault.file_modified"),
    ],
)
def test_sage_events_have_fixed_event_type(cls: type[AuditEventBase], event_type: str) -> None:
    event = cls(actor=_actor(), tenant_id="t-1")
    assert event.event_type == event_type
    assert EVENT_REGISTRY[event_type] is cls


@pytest.mark.parametrize(
    "cls,event_type",
    [
        (core_events.AuditRead, "core.audit.read"),
        (core_events.AuditExport, "core.audit.export"),
    ],
)
def test_core_events_have_fixed_event_type(cls: type[AuditEventBase], event_type: str) -> None:
    event = cls(actor=_actor(), tenant_id="t-1")
    assert event.event_type == event_type
    assert EVENT_REGISTRY[event_type] is cls


def test_registry_covers_all_documented_namespaces() -> None:
    """Smoke check: registry has at least one event from every namespace."""
    namespaces = {key.split(".", 1)[0] for key in EVENT_REGISTRY}
    assert namespaces == {"auth", "nexus", "gateway", "supervisor", "sage", "core"}
