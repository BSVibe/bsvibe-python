"""Slack incoming-webhook alert channel.

Wire format (locked by :mod:`tests.test_channels_slack`):

* URL: configured webhook (``https://hooks.slack.com/services/...``).
* JSON body: ``{"text": "<one-line summary>"}`` — the simple text shape.
  Block Kit is intentionally not used; all four products today only
  surface single-line alert summaries.
"""

from __future__ import annotations

import httpx

from bsvibe_alerts.types import Alert, AlertSeverity

_DEFAULT_TIMEOUT_S = 10.0
_SEVERITY_ICON = {
    AlertSeverity.INFO: ":information_source:",
    AlertSeverity.WARNING: ":warning:",
    AlertSeverity.CRITICAL: ":rotating_light:",
}


def _format_text(alert: Alert) -> str:
    icon = _SEVERITY_ICON[alert.severity]
    parts = [
        f"{icon} *{alert.severity.value.upper()}* `{alert.event}`",
        alert.message,
    ]
    if alert.service:
        parts.append(f"(service: {alert.service})")
    if alert.context:
        ctx = ", ".join(f"{k}={v}" for k, v in alert.context.items())
        parts.append(f"context: {ctx}")
    return " — ".join(parts)


class SlackChannel:
    """Send alerts to a Slack incoming webhook."""

    name: str = "slack"

    def __init__(
        self,
        *,
        webhook_url: str,
        timeout: float = _DEFAULT_TIMEOUT_S,
    ) -> None:
        if not webhook_url:
            raise ValueError("SlackChannel requires a non-empty webhook_url")
        self._url = webhook_url
        self._timeout = timeout

    async def send(self, alert: Alert) -> None:
        body = {"text": _format_text(alert)}
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(self._url, json=body)
            response.raise_for_status()


__all__ = ["SlackChannel"]
