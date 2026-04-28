"""Public alert wire types.

Every BSVibe product builds an :class:`Alert` and hands it to
:class:`bsvibe_alerts.AlertClient`. The dataclass shape is part of the
package's public contract — adding required fields or renaming
attributes is a breaking change for the four products.

The :class:`AlertSeverity` enum exposes a numeric ``rank`` so routing
rules can express "at-least" comparisons without re-deriving an order.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any


class AlertSeverity(str, enum.Enum):
    """Alert severity levels, ordered ``INFO < WARNING < CRITICAL``.

    Inherits from :class:`str` so the value is JSON-friendly out of the
    box (``severity.value == "info"``) — pydantic and structlog both
    serialise it without a custom encoder.
    """

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return _SEVERITY_RANK[self]

    @classmethod
    def from_string(cls, value: str) -> AlertSeverity:
        """Case-insensitive lookup. Raises :class:`ValueError` if unknown."""

        normalised = value.strip().lower()
        for member in cls:
            if member.value == normalised:
                return member
        raise ValueError(f"unknown alert severity: {value!r}")


_SEVERITY_RANK: dict[AlertSeverity, int] = {
    AlertSeverity.INFO: 0,
    AlertSeverity.WARNING: 1,
    AlertSeverity.CRITICAL: 2,
}


def _coerce_severity(value: AlertSeverity | str) -> AlertSeverity:
    if isinstance(value, AlertSeverity):
        return value
    return AlertSeverity.from_string(value)


@dataclass
class Alert:
    """A single alert payload.

    Attributes
    ----------
    event:
        Stable identifier (snake_case) — e.g. ``"rate_limit_exceeded"``.
        Used as the structlog event key.
    message:
        Human-readable summary intended for slack/telegram delivery.
    severity:
        :class:`AlertSeverity`. Strings are accepted and coerced.
    context:
        Optional structured fields (``tenant_id``, ``request_id``, ...)
        merged into the structlog event and rendered into telegram/slack
        bodies. Defaults to an empty dict — each instance owns its own
        copy (no mutable-default trap).
    service:
        Optional short identifier (``"bsgateway"`` etc.). When the
        :class:`AlertClient` is constructed from settings with
        ``service_name`` set, this is auto-populated.
    """

    event: str
    message: str
    severity: AlertSeverity = AlertSeverity.INFO
    context: dict[str, Any] = field(default_factory=dict)
    service: str | None = None

    def __post_init__(self) -> None:
        # Coerce string severities so producers can pass JSON-shaped data
        # without re-importing the enum on every call site.
        if not isinstance(self.severity, AlertSeverity):
            self.severity = _coerce_severity(self.severity)
        # Always own the dict — defensive copy in case a caller passes a
        # shared dict reference.
        if self.context is None:  # pragma: no cover - defensive
            self.context = {}
        else:
            self.context = dict(self.context)


__all__ = ["Alert", "AlertSeverity"]
