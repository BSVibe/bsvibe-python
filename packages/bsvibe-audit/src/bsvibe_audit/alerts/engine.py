"""Match audit events against rules and publish alerts.

The engine is the bridge between :mod:`bsvibe_audit` and
:mod:`bsvibe_alerts`. It is intentionally tiny:

* It owns a list of :class:`AuditAlertRule` instances and an
  :class:`bsvibe_alerts.AlertClient`.
* :meth:`evaluate` walks a batch of audit events, asks every rule
  whether it should fire, and publishes a :class:`bsvibe_alerts.Alert`
  for each rule that does. Failures inside ``alert_client.publish`` are
  logged via structlog but **never** propagated — alert delivery
  problems must not break the OutboxRelay loop.

Wire-up: the OutboxRelay calls ``await engine.evaluate(payloads)`` in
its ``run_once`` happy path *after* events have been delivered to the
audit endpoint. This guarantees the operator's Telegram alert always
reflects an event that already lives in the audit store.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from bsvibe_alerts import Alert

if TYPE_CHECKING:
    from bsvibe_alerts import AlertClient

    from bsvibe_audit.alerts.rules import AuditAlertRule


class AlertRuleEngine:
    """Evaluate audit events against a list of :class:`AuditAlertRule`."""

    def __init__(
        self,
        *,
        rules: list[AuditAlertRule],
        alert_client: AlertClient,
        service: str | None = None,
    ) -> None:
        self.rules = list(rules)
        self.alert_client = alert_client
        self.service = service
        self._logger = structlog.get_logger("bsvibe_audit.alerts.engine")

    async def evaluate(self, events: list[dict[str, Any]]) -> int:
        """Run every rule against every event and publish matches.

        Returns
        -------
        int
            Number of alerts actually published (useful in tests + for
            ``audit_alerts_published`` metrics later).
        """

        published = 0
        for event in events:
            for rule in self.rules:
                if not rule.should_fire(event):
                    continue
                alert = Alert(
                    event=f"audit.{rule.name}",
                    message=rule.render(event),
                    severity=rule.severity,
                    context={
                        "audit_event_id": event.get("event_id"),
                        "audit_event_type": event.get("event_type"),
                        "tenant_id": event.get("tenant_id"),
                        "actor_id": (event.get("actor") or {}).get("id"),
                    },
                    service=self.service,
                )
                try:
                    await self.alert_client.publish(alert)
                    published += 1
                except Exception as exc:  # noqa: BLE001 — failure isolation contract
                    self._logger.error(
                        "audit_alert_publish_failed",
                        rule=rule.name,
                        audit_event_id=event.get("event_id"),
                        error=repr(exc),
                    )
        return published


__all__ = ["AlertRuleEngine"]
