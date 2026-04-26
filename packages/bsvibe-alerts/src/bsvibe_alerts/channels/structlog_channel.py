"""Structlog alert channel — always-on debug sink.

The channel emits one structured log line per alert. The log level is
mapped from :class:`AlertSeverity` so existing log scrapers (loki,
datadog, ...) can apply per-level alerting policies without parsing the
``alert_severity`` key.

Module is named ``structlog_channel`` (not ``structlog``) so it does
not shadow the upstream :mod:`structlog` package on import.
"""

from __future__ import annotations

import structlog

from bsvibe_alerts.types import Alert, AlertSeverity

_LEVEL_FOR_SEVERITY = {
    AlertSeverity.INFO: "info",
    AlertSeverity.WARNING: "warning",
    AlertSeverity.CRITICAL: "critical",
}


class StructlogChannel:
    """Emit alerts as structlog events.

    The emitted record carries:

    * ``event`` = ``alert.event`` (top-level structlog event key).
    * ``alert_severity`` = ``alert.severity.value``.
    * ``alert_message`` = ``alert.message``.
    * Each ``alert.context`` key is merged into the event dict.
    * ``service`` is set when ``alert.service`` is not None — matches
      the convention enforced by :func:`bsvibe_core.configure_logging`.
    """

    name: str = "structlog"

    def __init__(self, logger: structlog.stdlib.BoundLogger | None = None) -> None:
        self._logger = logger or structlog.get_logger("bsvibe_alerts")

    async def send(self, alert: Alert) -> None:
        method = _LEVEL_FOR_SEVERITY[alert.severity]
        emit = getattr(self._logger, method)
        kwargs: dict[str, object] = {
            "alert_severity": alert.severity.value,
            "alert_message": alert.message,
        }
        # Per-alert context keys are merged top-level so log scrapers can
        # pivot on tenant_id / request_id without reaching into a nested
        # dict.
        for key, value in alert.context.items():
            kwargs[key] = value
        if alert.service is not None:
            kwargs["service"] = alert.service
        emit(alert.event, **kwargs)


__all__ = ["StructlogChannel"]
