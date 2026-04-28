"""Production alert-rule presets.

These rules implement the operational alerts called out in
``BSVibe_Audit_Design.md`` §11. They are deliberately conservative —
the goal is for an on-call operator to react when each fires, not to
chase noisy false positives.

The five presets:

* ``audit.brute-force`` — ``auth.session.failed`` 5 times within 60s
  (per tenant). Severity ``critical`` because brute-force matches any
  consumer-facing tenant immediately needing investigation.
* ``audit.budget-exceeded`` — ``supervisor.budget.exceeded`` (every
  occurrence). Severity ``warning`` — operator should triage but it's
  not a security incident.
* ``audit.rate-limit-pressure`` — ``gateway.rate_limit.violated`` 10
  times within 5 minutes (per tenant). DDoS or noisy integration.
* ``audit.anomaly-detected`` — ``supervisor.anomaly.detected`` (every
  occurrence). Severity ``critical``.
* ``audit.run-blocked`` — ``nexus.run.blocked`` 3 times within 10
  minutes (per tenant). Indicates a system / config problem.

Operators can compose their own rules by importing
:class:`AuditAlertRule` directly; ``default_rules()`` is just a sensible
starting set.
"""

from __future__ import annotations

from bsvibe_alerts import AlertSeverity

from bsvibe_audit.alerts.rules import AuditAlertRule


def default_rules() -> list[AuditAlertRule]:
    """Return a fresh list of preset rules.

    A fresh instance is returned on every call because each rule keeps
    sliding-window state — sharing across tests / processes would mix
    counts.
    """

    return [
        AuditAlertRule(
            name="audit.brute-force",
            event_type_pattern="auth.session.failed",
            severity=AlertSeverity.CRITICAL,
            message_template=("Possible brute-force: 5 failed sessions within 60s for tenant {tenant_id}"),
            threshold_count=5,
            threshold_window_s=60.0,
            threshold_key=("tenant_id",),
        ),
        AuditAlertRule(
            name="audit.budget-exceeded",
            event_type_pattern="supervisor.budget.exceeded",
            severity=AlertSeverity.WARNING,
            message_template="Budget exceeded for tenant {tenant_id} (actor={actor_id})",
        ),
        AuditAlertRule(
            name="audit.rate-limit-pressure",
            event_type_pattern="gateway.rate_limit.violated",
            severity=AlertSeverity.WARNING,
            message_template=("Rate-limit pressure: 10 violations within 5m for tenant {tenant_id}"),
            threshold_count=10,
            threshold_window_s=300.0,
            threshold_key=("tenant_id",),
        ),
        AuditAlertRule(
            name="audit.anomaly-detected",
            event_type_pattern="supervisor.anomaly.detected",
            severity=AlertSeverity.CRITICAL,
            message_template="Anomaly detected for tenant {tenant_id} (actor={actor_id})",
        ),
        AuditAlertRule(
            name="audit.run-blocked",
            event_type_pattern="nexus.run.blocked",
            severity=AlertSeverity.WARNING,
            message_template=("Run blocked 3x within 10m for tenant {tenant_id} — investigate config"),
            threshold_count=3,
            threshold_window_s=600.0,
            threshold_key=("tenant_id",),
        ),
    ]


__all__ = ["default_rules"]
