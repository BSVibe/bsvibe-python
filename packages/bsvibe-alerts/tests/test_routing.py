"""Tests for bsvibe_alerts.routing.AlertRouter.

Routing is a pure function of (alert.severity, settings.routing_table).
Channels not registered in the client are silently skipped — products
should warn via structlog (always-on) and never crash production paths
because slack/telegram credentials are absent.
"""

from __future__ import annotations

import pytest

from bsvibe_alerts.routing import AlertRouter
from bsvibe_alerts.types import Alert, AlertSeverity


class TestDefaultRouting:
    def test_info_routes_to_structlog_only(self) -> None:
        router = AlertRouter.from_defaults()
        alert = Alert(event="x", message="m", severity=AlertSeverity.INFO)
        assert router.channels_for(alert) == ["structlog"]

    def test_warning_routes_to_structlog_and_slack(self) -> None:
        router = AlertRouter.from_defaults()
        alert = Alert(event="x", message="m", severity=AlertSeverity.WARNING)
        assert router.channels_for(alert) == ["structlog", "slack"]

    def test_critical_routes_to_all(self) -> None:
        router = AlertRouter.from_defaults()
        alert = Alert(event="x", message="m", severity=AlertSeverity.CRITICAL)
        assert router.channels_for(alert) == ["structlog", "slack", "telegram"]


class TestCustomRouting:
    def test_custom_table_overrides_defaults(self) -> None:
        router = AlertRouter(
            table={
                AlertSeverity.INFO: ["structlog"],
                AlertSeverity.WARNING: ["structlog"],
                AlertSeverity.CRITICAL: ["telegram"],
            },
        )
        info = Alert(event="x", message="m", severity=AlertSeverity.INFO)
        warn = Alert(event="x", message="m", severity=AlertSeverity.WARNING)
        crit = Alert(event="x", message="m", severity=AlertSeverity.CRITICAL)

        assert router.channels_for(info) == ["structlog"]
        assert router.channels_for(warn) == ["structlog"]
        assert router.channels_for(crit) == ["telegram"]

    def test_router_returns_independent_lists(self) -> None:
        # Mutating the returned list MUST NOT mutate the routing table.
        router = AlertRouter.from_defaults()
        alert = Alert(event="x", message="m", severity=AlertSeverity.INFO)
        first = router.channels_for(alert)
        first.append("telegram")
        second = router.channels_for(alert)
        assert second == ["structlog"]


class TestFromSettings:
    def test_routing_built_from_settings(self) -> None:
        from bsvibe_alerts.settings import AlertSettings

        settings = AlertSettings(
            info_channels=["structlog"],
            warning_channels=["telegram"],
            critical_channels=["telegram", "slack"],
        )
        router = AlertRouter.from_settings(settings)
        info = Alert(event="x", message="m", severity=AlertSeverity.INFO)
        warn = Alert(event="x", message="m", severity=AlertSeverity.WARNING)
        crit = Alert(event="x", message="m", severity=AlertSeverity.CRITICAL)

        assert router.channels_for(info) == ["structlog"]
        assert router.channels_for(warn) == ["telegram"]
        assert router.channels_for(crit) == ["telegram", "slack"]


class TestUnknownSeverity:
    def test_missing_table_entry_falls_back_to_structlog(self) -> None:
        # Defensive: if a deployer sets a custom table missing a severity,
        # we must still emit somewhere — never silently swallow alerts.
        router = AlertRouter(
            table={AlertSeverity.CRITICAL: ["telegram"]},
        )
        info = Alert(event="x", message="m", severity=AlertSeverity.INFO)
        assert router.channels_for(info) == ["structlog"]


class TestInvalidConstruction:
    def test_table_must_be_dict_of_severity_keys(self) -> None:
        with pytest.raises(TypeError):
            AlertRouter(table={"info": ["structlog"]})  # type: ignore[arg-type]
