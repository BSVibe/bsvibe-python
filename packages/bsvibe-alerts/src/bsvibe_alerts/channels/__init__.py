"""Concrete alert channels and the :class:`AlertChannel` protocol.

Every channel exposes:

* ``name`` — string used in routing tables (``"structlog"``, ``"slack"``,
  ``"telegram"``).
* ``async send(alert)`` — performs the side effect (log emit / HTTP
  POST). Implementations MAY raise; the :class:`AlertClient` isolates
  failures so other channels still fire.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from bsvibe_alerts.channels.slack import SlackChannel
from bsvibe_alerts.channels.structlog_channel import StructlogChannel
from bsvibe_alerts.channels.telegram import TelegramChannel
from bsvibe_alerts.types import Alert


@runtime_checkable
class AlertChannel(Protocol):
    """Structural protocol every concrete channel implements."""

    name: str

    async def send(self, alert: Alert) -> None: ...


__all__ = [
    "AlertChannel",
    "StructlogChannel",
    "SlackChannel",
    "TelegramChannel",
]
