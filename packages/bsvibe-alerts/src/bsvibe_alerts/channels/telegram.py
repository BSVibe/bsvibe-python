"""Telegram bot API alert channel.

Wire format (locked by :mod:`tests.test_channels_telegram`):

* URL: ``https://api.telegram.org/bot<token>/sendMessage``.
* JSON body: ``{"chat_id": ..., "text": ..., "parse_mode": "Markdown"}``.

The channel does NOT reuse a long-lived :class:`httpx.AsyncClient` — each
``send`` opens its own short-lived client. This keeps the channel safe
to instantiate at module import time without managing background
connection pools (matches the pattern used by ``daily-search.mjs`` in
the OpenClaw cron stack).
"""

from __future__ import annotations

import httpx

from bsvibe_alerts.types import Alert, AlertSeverity

_API_BASE = "https://api.telegram.org/bot{token}/sendMessage"
_DEFAULT_TIMEOUT_S = 10.0
_SEVERITY_ICON = {
    AlertSeverity.INFO: "ℹ️",
    AlertSeverity.WARNING: "⚠️",
    AlertSeverity.CRITICAL: "🚨",
}


def _format_text(alert: Alert) -> str:
    icon = _SEVERITY_ICON[alert.severity]
    lines = [
        f"{icon} *{alert.severity.value.upper()}* — `{alert.event}`",
        alert.message,
    ]
    if alert.service:
        lines.append(f"_service: {alert.service}_")
    if alert.context:
        ctx_lines = [f"- {k}: {v}" for k, v in alert.context.items()]
        lines.append("\n".join(ctx_lines))
    return "\n".join(lines)


class TelegramChannel:
    """Send alerts to a Telegram chat via the Bot API."""

    name: str = "telegram"

    def __init__(
        self,
        *,
        bot_token: str,
        chat_id: str,
        timeout: float = _DEFAULT_TIMEOUT_S,
    ) -> None:
        if not bot_token:
            raise ValueError("TelegramChannel requires a non-empty bot_token")
        if not chat_id:
            raise ValueError("TelegramChannel requires a non-empty chat_id")
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._timeout = timeout

    async def send(self, alert: Alert) -> None:
        url = _API_BASE.format(token=self._bot_token)
        body = {
            "chat_id": self._chat_id,
            "text": _format_text(alert),
            "parse_mode": "Markdown",
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(url, json=body)
            response.raise_for_status()


__all__ = ["TelegramChannel"]
