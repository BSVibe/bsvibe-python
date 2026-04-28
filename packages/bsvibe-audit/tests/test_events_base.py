"""Tests for the AuditEventBase contract — the wire shape every product emits."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from bsvibe_audit.events import AuditActor, AuditEventBase, AuditResource


def test_event_base_minimal_construction() -> None:
    actor = AuditActor(type="user", id="u-1", email="a@b.test")
    event = AuditEventBase(
        event_type="test.minimal",
        actor=actor,
        tenant_id="t-1",
    )
    assert isinstance(event.event_id, UUID)
    assert event.event_type == "test.minimal"
    assert event.actor.id == "u-1"
    assert event.tenant_id == "t-1"
    assert event.occurred_at.tzinfo is not None
    assert event.data == {}
    assert event.trace_id is None
    assert event.resource is None


def test_event_base_explicit_event_id_and_occurred_at() -> None:
    eid = uuid4()
    now = datetime(2026, 4, 26, 12, 0, 0, tzinfo=UTC)
    actor = AuditActor(type="service", id="svc-1")
    event = AuditEventBase(
        event_id=eid,
        event_type="test.explicit",
        occurred_at=now,
        actor=actor,
        tenant_id=None,
    )
    assert event.event_id == eid
    assert event.occurred_at == now


def test_event_base_actor_required() -> None:
    with pytest.raises(ValidationError):
        AuditEventBase(  # type: ignore[call-arg]
            event_type="test.missing_actor",
            tenant_id="t-1",
        )


def test_actor_type_must_be_known() -> None:
    with pytest.raises(ValidationError):
        AuditActor(type="alien", id="x")  # type: ignore[arg-type]


def test_resource_optional_round_trip() -> None:
    actor = AuditActor(type="system", id="sys")
    resource = AuditResource(type="project", id="p-1")
    event = AuditEventBase(
        event_type="test.with_resource",
        actor=actor,
        tenant_id="t-1",
        resource=resource,
    )
    assert event.resource is not None
    assert event.resource.type == "project"
    assert event.resource.id == "p-1"


def test_event_base_serialisable_to_jsonable_dict() -> None:
    actor = AuditActor(type="user", id="u-1")
    event = AuditEventBase(
        event_type="test.serialise",
        actor=actor,
        tenant_id="t-1",
        data={"key": "value", "nested": {"x": 1}},
    )
    payload = event.model_dump(mode="json")
    # Required wire fields
    for key in ("event_id", "event_type", "occurred_at", "actor", "tenant_id", "data"):
        assert key in payload
    # event_id is a string under mode="json"
    assert isinstance(payload["event_id"], str)
    UUID(payload["event_id"])  # parses


def test_event_base_extra_fields_forbidden() -> None:
    """The wire shape is closed — typos (`tenantId` vs `tenant_id`) must fail."""
    actor = AuditActor(type="user", id="u-1")
    with pytest.raises(ValidationError):
        AuditEventBase(  # type: ignore[call-arg]
            event_type="test.extra",
            actor=actor,
            tenant_id="t-1",
            unexpected_typo="oops",
        )
