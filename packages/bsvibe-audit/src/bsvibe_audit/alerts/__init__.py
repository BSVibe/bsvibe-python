"""Audit-driven alert rule engine.

This subpackage builds on :mod:`bsvibe_alerts` rather than reimplementing
delivery: a rule matches an audit event payload, the engine instantiates
a :class:`bsvibe_alerts.Alert`, and :class:`bsvibe_alerts.AlertClient`
ships it to the configured Telegram / Slack / structlog channels.

Public surface:

* :class:`AuditAlertRule` — declarative rule (pattern + threshold +
  severity + message template).
* :class:`AlertRuleEngine` — evaluator that takes a batch of audit
  payloads and fans out matching alerts.
* :func:`default_rules` — production presets (brute force, budget,
  rate-limit, anomaly, run blocked) wired up with the thresholds from
  BSVibe_Audit_Design.md §11.
"""

from __future__ import annotations

from bsvibe_audit.alerts.engine import (
    AlertRuleEngine,
    CentralAlertRuleEngine,
    DispatchMode,
    resolve_dispatch_mode,
)
from bsvibe_audit.alerts.presets import default_rules
from bsvibe_audit.alerts.rules import AuditAlertRule

__all__ = [
    "AlertRuleEngine",
    "AuditAlertRule",
    "CentralAlertRuleEngine",
    "DispatchMode",
    "default_rules",
    "resolve_dispatch_mode",
]
