"""Tests for bsvibe_alerts.types.

Pins the wire format of :class:`Alert` — every product builds an
``Alert`` and hands it to :class:`AlertClient`, so the dataclass shape is
a public contract and breaking changes ripple through 4 products.
"""

from __future__ import annotations

import pytest

from bsvibe_alerts.types import Alert, AlertSeverity


class TestAlertSeverity:
    def test_known_severities(self) -> None:
        assert AlertSeverity.INFO.value == "info"
        assert AlertSeverity.WARNING.value == "warning"
        assert AlertSeverity.CRITICAL.value == "critical"

    def test_severity_ordering(self) -> None:
        # INFO < WARNING < CRITICAL — used by routing rules to compute
        # "at least this severity" matchers.
        assert AlertSeverity.INFO.rank < AlertSeverity.WARNING.rank
        assert AlertSeverity.WARNING.rank < AlertSeverity.CRITICAL.rank

    def test_from_string(self) -> None:
        assert AlertSeverity.from_string("INFO") is AlertSeverity.INFO
        assert AlertSeverity.from_string("warning") is AlertSeverity.WARNING
        assert AlertSeverity.from_string("Critical") is AlertSeverity.CRITICAL

    def test_from_string_unknown_raises(self) -> None:
        with pytest.raises(ValueError):
            AlertSeverity.from_string("loud")


class TestAlert:
    def test_minimal_construction(self) -> None:
        alert = Alert(event="rate_limit_exceeded", message="quota hit")
        assert alert.event == "rate_limit_exceeded"
        assert alert.message == "quota hit"
        assert alert.severity is AlertSeverity.INFO  # default
        assert alert.context == {}
        assert alert.service is None

    def test_full_construction(self) -> None:
        alert = Alert(
            event="task_failed",
            message="executor crashed",
            severity=AlertSeverity.CRITICAL,
            context={"task_id": "abc", "tenant_id": "t-1"},
            service="bsnexus",
        )
        assert alert.severity is AlertSeverity.CRITICAL
        assert alert.context["task_id"] == "abc"
        assert alert.service == "bsnexus"

    def test_severity_accepts_string(self) -> None:
        # Producers (pydantic models, JSON payloads) will pass strings;
        # the dataclass coerces them so callers do not have to.
        alert = Alert(event="x", message="m", severity="warning")
        assert alert.severity is AlertSeverity.WARNING

    def test_context_defaults_independently(self) -> None:
        # Mutable-default trap: each Alert must own its dict.
        a = Alert(event="x", message="m")
        b = Alert(event="y", message="n")
        a.context["key"] = "value"
        assert "key" not in b.context
