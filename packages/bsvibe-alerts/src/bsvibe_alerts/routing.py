"""Severity → channel routing.

Routing is intentionally a pure data structure (a dict) plus a thin
lookup. The :class:`AlertClient` consumes the result list and dispatches.

Defensive policy: if a deployer ships a routing table missing a severity
entry, the lookup falls back to ``["structlog"]`` so alerts are *never*
silently swallowed. The structlog channel is always-on regardless of
credentials, so this fallback is guaranteed to fire.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from bsvibe_alerts.types import Alert, AlertSeverity

if TYPE_CHECKING:
    from bsvibe_alerts.settings import AlertSettings


_DEFAULT_TABLE: dict[AlertSeverity, list[str]] = {
    AlertSeverity.INFO: ["structlog"],
    AlertSeverity.WARNING: ["structlog", "slack"],
    AlertSeverity.CRITICAL: ["structlog", "slack", "telegram"],
}


_FALLBACK_CHANNELS: list[str] = ["structlog"]


class AlertRouter:
    """Map an :class:`Alert` to a list of channel names.

    The mapping is exposed through :meth:`channels_for` (defensive copy
    on every call so callers cannot mutate the underlying table).
    """

    def __init__(self, *, table: dict[AlertSeverity, list[str]]) -> None:
        for key in table:
            if not isinstance(key, AlertSeverity):
                raise TypeError(f"AlertRouter table keys must be AlertSeverity, got {type(key).__name__}")
        # Store our own copies so external mutation is harmless.
        self._table: dict[AlertSeverity, list[str]] = {k: list(v) for k, v in table.items()}

    @classmethod
    def from_defaults(cls) -> AlertRouter:
        return cls(table={k: list(v) for k, v in _DEFAULT_TABLE.items()})

    @classmethod
    def from_settings(cls, settings: AlertSettings) -> AlertRouter:
        return cls(
            table={
                AlertSeverity.INFO: list(settings.info_channels),
                AlertSeverity.WARNING: list(settings.warning_channels),
                AlertSeverity.CRITICAL: list(settings.critical_channels),
            }
        )

    def channels_for(self, alert: Alert) -> list[str]:
        """Return a fresh list of channel names for ``alert.severity``."""

        channels = self._table.get(alert.severity)
        if channels is None:
            return list(_FALLBACK_CHANNELS)
        return list(channels)


__all__ = ["AlertRouter"]
