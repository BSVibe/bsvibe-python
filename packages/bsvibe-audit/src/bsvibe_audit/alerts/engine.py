"""Match audit events against rules and publish alerts.

Two engines live here, one for each operational mode:

* :class:`AlertRuleEngine` — Phase A, hardcoded preset rules
  (:func:`bsvibe_audit.alerts.default_rules`) evaluated locally and
  published via :class:`bsvibe_alerts.AlertClient`. Zero dependency on
  BSVibe-Auth, ideal for early adopters and self-hosted setups.

* :class:`CentralAlertRuleEngine` — D18, runtime-tunable rules stored in
  BSVibe-Auth's ``alert_routes`` table. Each event is forwarded to
  :class:`bsvibe_alerts.CentralDispatchClient` which calls
  ``POST /api/alerts/dispatch``. Operators can flip ``enabled`` /
  thresholds without redeploys, and shared secrets (Slack / Telegram)
  live only in the auth service.

Both engines share the same wire contract: the OutboxRelay passes a list
of dict payloads after events have been delivered to the audit endpoint.
Failures inside the engine are logged via structlog but **never**
propagated — alert delivery problems must not break the OutboxRelay
loop.

Mode selection: producers may use either engine directly, or choose by
``BSVIBE_AUDIT_DISPATCH_MODE=hardcoded|central`` via
:func:`build_alert_engine`. Default is ``hardcoded`` for backward
compatibility with existing deployments.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, Literal

import structlog

from bsvibe_alerts import Alert

if TYPE_CHECKING:
    from bsvibe_alerts import AlertClient, CentralDispatchClient

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


class CentralAlertRuleEngine:
    """Forward every audit event to BSVibe-Auth ``/api/alerts/dispatch``.

    Unlike :class:`AlertRuleEngine`, the central engine owns no rule
    state — the ``alert_routes`` table on the auth service is the single
    source of truth. This keeps multi-product fan-out consistent
    (operator changes one row, every product picks it up immediately on
    the next dispatch).

    The engine is constructed with a :class:`bsvibe_alerts.CentralDispatchClient`
    instance. Callers normally instantiate the client once at process
    startup and reuse it; the engine does not own its lifecycle.

    Errors are isolated: dispatch failures (network / 5xx / 4xx) are
    logged via structlog and ``evaluate`` returns the count of *attempted*
    dispatches. The OutboxRelay will continue regardless.
    """

    def __init__(
        self,
        *,
        dispatch_client: CentralDispatchClient,
        service: str | None = None,
    ) -> None:
        self.dispatch_client = dispatch_client
        self.service = service
        self._logger = structlog.get_logger("bsvibe_audit.alerts.engine.central")

    async def evaluate(self, events: list[dict[str, Any]]) -> int:
        """Forward ``events`` to BSVibe-Auth dispatch and return matched count.

        Returns
        -------
        int
            Cumulative ``matched_rules`` reported by the dispatch
            endpoint across the batch — the number of alert routes that
            actually fired. Zero is returned for batches where the API
            fails for every event (failure is logged but not raised).
        """

        from bsvibe_alerts.dispatch_client import CentralDispatchError

        matched_total = 0
        for event in events:
            try:
                result = await self.dispatch_client.dispatch(event)
            except CentralDispatchError as exc:
                self._logger.warning(
                    "central_dispatch_failed",
                    audit_event_id=str(event.get("event_id")),
                    audit_event_type=event.get("event_type"),
                    retryable=exc.retryable,
                    status=exc.status,
                    error=repr(exc),
                )
                continue
            except Exception as exc:  # noqa: BLE001 — failure isolation contract
                self._logger.error(
                    "central_dispatch_unexpected_error",
                    audit_event_id=str(event.get("event_id")),
                    audit_event_type=event.get("event_type"),
                    error=repr(exc),
                )
                continue
            matched_total += result.matched_rules
        return matched_total


DispatchMode = Literal["hardcoded", "central"]
_ENV_VAR = "BSVIBE_AUDIT_DISPATCH_MODE"
_DEFAULT_MODE: DispatchMode = "hardcoded"


def resolve_dispatch_mode(
    explicit: DispatchMode | str | None = None,
) -> DispatchMode:
    """Pick the dispatch mode from explicit arg or env (``BSVIBE_AUDIT_DISPATCH_MODE``).

    Defaults to ``"hardcoded"`` for backward compatibility with the Phase
    A engine. Unknown values raise :class:`ValueError` so misconfiguration
    surfaces at startup, not at first audit event.
    """

    raw = explicit if explicit is not None else os.environ.get(_ENV_VAR)
    if raw is None or raw == "":
        return _DEFAULT_MODE
    value = raw.lower().strip()
    if value not in ("hardcoded", "central"):
        raise ValueError(f"{_ENV_VAR}={raw!r} is invalid; expected 'hardcoded' or 'central'")
    return value  # type: ignore[return-value]


__all__ = [
    "AlertRuleEngine",
    "CentralAlertRuleEngine",
    "DispatchMode",
    "resolve_dispatch_mode",
]
