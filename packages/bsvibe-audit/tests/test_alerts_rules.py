"""Tests for the audit-driven alert rule engine.

The engine watches every audit event that flows through the relay and
fires :class:`bsvibe_alerts.Alert` messages whenever a rule's condition
matches. The presets implement the four operational rules from the
audit design:

* brute force (``auth.session.failed`` 5x in 1 minute)
* budget exceeded (``supervisor.budget.exceeded``)
* rate-limit pressure (``gateway.rate_limit.violated`` 10x in 5 minutes)
* anomaly (``supervisor.anomaly.detected``)
* run blocked (``nexus.run.blocked`` 3x in 10 minutes)

Tests cover each rule's matching contract plus the engine's fan-out
to a mocked :class:`AlertClient`. We use a controllable ``clock``
callable so threshold tests are deterministic.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest

from bsvibe_audit.alerts import (
    AlertRuleEngine,
    AuditAlertRule,
    default_rules,
)
from bsvibe_alerts import AlertSeverity


def _event(event_type: str, *, occurred_at: datetime | None = None, tenant_id: str = "t-1") -> dict[str, Any]:
    return {
        "event_id": f"e-{event_type}-{occurred_at}",
        "event_type": event_type,
        "occurred_at": (occurred_at or datetime.now(UTC)).isoformat(),
        "actor": {"type": "user", "id": "u-1"},
        "tenant_id": tenant_id,
        "data": {},
    }


# ---------------------------------------------------------------------------
# AuditAlertRule
# ---------------------------------------------------------------------------


def test_rule_simple_pattern_match() -> None:
    rule = AuditAlertRule(
        name="budget-exceeded",
        event_type_pattern="supervisor.budget.exceeded",
        severity=AlertSeverity.WARNING,
        message_template="Budget exceeded for tenant {tenant_id}",
    )
    assert rule.matches(_event("supervisor.budget.exceeded")) is True
    assert rule.matches(_event("nexus.run.blocked")) is False


def test_rule_wildcard_pattern() -> None:
    rule = AuditAlertRule(
        name="auth-failures",
        event_type_pattern="auth.*",
        severity=AlertSeverity.WARNING,
        message_template="Auth event {event_type}",
    )
    assert rule.matches(_event("auth.session.failed")) is True
    assert rule.matches(_event("auth.user.created")) is True
    assert rule.matches(_event("supervisor.budget.exceeded")) is False


def test_rule_message_template_renders_event_fields() -> None:
    rule = AuditAlertRule(
        name="brute-force",
        event_type_pattern="auth.session.failed",
        severity=AlertSeverity.CRITICAL,
        message_template="Failed login burst for tenant {tenant_id}",
    )
    rendered = rule.render(_event("auth.session.failed", tenant_id="acme-1"))
    assert "acme-1" in rendered


# ---------------------------------------------------------------------------
# Threshold rule (count-within-window)
# ---------------------------------------------------------------------------


def test_threshold_rule_does_not_fire_below_count() -> None:
    now = datetime(2026, 4, 25, 12, 0, 0, tzinfo=UTC)

    def clock() -> datetime:
        return now

    rule = AuditAlertRule(
        name="brute-force",
        event_type_pattern="auth.session.failed",
        severity=AlertSeverity.CRITICAL,
        message_template="brute force",
        threshold_count=5,
        threshold_window_s=60,
        clock=clock,
    )
    for _ in range(4):
        assert rule.should_fire(_event("auth.session.failed")) is False


def test_threshold_rule_fires_when_count_reached() -> None:
    now = datetime(2026, 4, 25, 12, 0, 0, tzinfo=UTC)

    def clock() -> datetime:
        return now

    rule = AuditAlertRule(
        name="brute-force",
        event_type_pattern="auth.session.failed",
        severity=AlertSeverity.CRITICAL,
        message_template="brute force",
        threshold_count=5,
        threshold_window_s=60,
        clock=clock,
    )
    fired = [rule.should_fire(_event("auth.session.failed")) for _ in range(5)]
    assert fired[:4] == [False] * 4
    assert fired[4] is True


def test_threshold_rule_resets_after_window() -> None:
    times = [datetime(2026, 4, 25, 12, 0, 0, tzinfo=UTC)]

    def clock() -> datetime:
        return times[0]

    rule = AuditAlertRule(
        name="rate-limit",
        event_type_pattern="gateway.rate_limit.violated",
        severity=AlertSeverity.WARNING,
        message_template="rate limit pressure",
        threshold_count=3,
        threshold_window_s=300,  # 5 minutes
        clock=clock,
    )
    for _ in range(2):
        rule.should_fire(_event("gateway.rate_limit.violated"))
    times[0] = times[0] + timedelta(seconds=600)  # past the window
    # Only one event is now in the window — should not fire.
    assert rule.should_fire(_event("gateway.rate_limit.violated")) is False


def test_threshold_rule_keys_window_per_tenant() -> None:
    now = datetime(2026, 4, 25, 12, 0, 0, tzinfo=UTC)
    rule = AuditAlertRule(
        name="brute-force",
        event_type_pattern="auth.session.failed",
        severity=AlertSeverity.CRITICAL,
        message_template="brute force",
        threshold_count=3,
        threshold_window_s=60,
        clock=lambda: now,
        threshold_key=("tenant_id",),
    )
    # Tenant A: 3 attempts -> fires.
    for _ in range(2):
        rule.should_fire(_event("auth.session.failed", tenant_id="A"))
    assert rule.should_fire(_event("auth.session.failed", tenant_id="A")) is True
    # Tenant B is independent and should not fire on the first event.
    assert rule.should_fire(_event("auth.session.failed", tenant_id="B")) is False


# ---------------------------------------------------------------------------
# AlertRuleEngine
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_engine_publishes_alert_when_rule_matches() -> None:
    rule = AuditAlertRule(
        name="budget-exceeded",
        event_type_pattern="supervisor.budget.exceeded",
        severity=AlertSeverity.WARNING,
        message_template="Budget exceeded for tenant {tenant_id}",
    )
    alert_client = AsyncMock()
    alert_client.publish = AsyncMock()

    engine = AlertRuleEngine(rules=[rule], alert_client=alert_client)
    await engine.evaluate([_event("supervisor.budget.exceeded", tenant_id="acme")])

    alert_client.publish.assert_awaited_once()
    published = alert_client.publish.await_args.args[0]
    assert published.event == "audit.budget-exceeded"
    assert published.severity == AlertSeverity.WARNING
    assert "acme" in published.message


@pytest.mark.asyncio
async def test_engine_skips_unmatched_events() -> None:
    rule = AuditAlertRule(
        name="budget-exceeded",
        event_type_pattern="supervisor.budget.exceeded",
        severity=AlertSeverity.WARNING,
        message_template="Budget exceeded",
    )
    alert_client = AsyncMock()
    alert_client.publish = AsyncMock()

    engine = AlertRuleEngine(rules=[rule], alert_client=alert_client)
    await engine.evaluate([_event("auth.user.created")])

    alert_client.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_engine_threshold_rule_fires_only_after_count() -> None:
    now = datetime(2026, 4, 25, 12, 0, 0, tzinfo=UTC)

    rule = AuditAlertRule(
        name="brute-force",
        event_type_pattern="auth.session.failed",
        severity=AlertSeverity.CRITICAL,
        message_template="brute force",
        threshold_count=3,
        threshold_window_s=60,
        clock=lambda: now,
    )
    alert_client = AsyncMock()
    alert_client.publish = AsyncMock()

    engine = AlertRuleEngine(rules=[rule], alert_client=alert_client)
    batch = [_event("auth.session.failed") for _ in range(3)]
    await engine.evaluate(batch)
    # Exactly one alert published when the third event hits the threshold.
    assert alert_client.publish.await_count == 1


@pytest.mark.asyncio
async def test_engine_failure_does_not_propagate() -> None:
    """A broken alert client must not crash audit relay flow."""

    rule = AuditAlertRule(
        name="anomaly",
        event_type_pattern="supervisor.anomaly.detected",
        severity=AlertSeverity.CRITICAL,
        message_template="anomaly",
    )
    alert_client = AsyncMock()
    alert_client.publish = AsyncMock(side_effect=RuntimeError("slack down"))

    engine = AlertRuleEngine(rules=[rule], alert_client=alert_client)
    # Should not raise.
    await engine.evaluate([_event("supervisor.anomaly.detected")])


@pytest.mark.asyncio
async def test_engine_evaluates_multiple_rules_per_event() -> None:
    rule_a = AuditAlertRule(
        name="catch-all-supervisor",
        event_type_pattern="supervisor.*",
        severity=AlertSeverity.INFO,
        message_template="supervisor event",
    )
    rule_b = AuditAlertRule(
        name="anomaly-detail",
        event_type_pattern="supervisor.anomaly.detected",
        severity=AlertSeverity.CRITICAL,
        message_template="anomaly detail",
    )
    alert_client = AsyncMock()
    alert_client.publish = AsyncMock()

    engine = AlertRuleEngine(rules=[rule_a, rule_b], alert_client=alert_client)
    await engine.evaluate([_event("supervisor.anomaly.detected")])
    assert alert_client.publish.await_count == 2


# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------


def test_default_rules_includes_required_presets() -> None:
    rules = default_rules()
    names = {rule.name for rule in rules}
    assert "audit.brute-force" in names
    assert "audit.budget-exceeded" in names
    assert "audit.rate-limit-pressure" in names
    assert "audit.anomaly-detected" in names
    assert "audit.run-blocked" in names


def test_default_brute_force_threshold_matches_design() -> None:
    rules = {rule.name: rule for rule in default_rules()}
    brute = rules["audit.brute-force"]
    assert brute.event_type_pattern == "auth.session.failed"
    assert brute.threshold_count == 5
    assert brute.threshold_window_s == 60
    assert brute.severity == AlertSeverity.CRITICAL


def test_default_rate_limit_threshold_matches_design() -> None:
    rules = {rule.name: rule for rule in default_rules()}
    rl = rules["audit.rate-limit-pressure"]
    assert rl.event_type_pattern == "gateway.rate_limit.violated"
    assert rl.threshold_count == 10
    assert rl.threshold_window_s == 300
    assert rl.severity == AlertSeverity.WARNING


def test_default_run_blocked_threshold_matches_design() -> None:
    rules = {rule.name: rule for rule in default_rules()}
    nb = rules["audit.run-blocked"]
    assert nb.event_type_pattern == "nexus.run.blocked"
    assert nb.threshold_count == 3
    assert nb.threshold_window_s == 600
    assert nb.severity == AlertSeverity.WARNING
