"""Tests for bsvibe_alerts.client.AlertClient.

The client orchestrates routing → channel dispatch. Contract:

* :meth:`AlertClient.publish` MUST dispatch to every channel in the
  routing rule for ``alert.severity``.
* A channel that raises MUST NOT prevent other channels from running.
  This is the production-safety guarantee — slack outage cannot silence
  telegram.
* :meth:`AlertClient.emit` is a convenience wrapper that builds an
  :class:`Alert` and calls ``publish``.
* Channel names referenced in the routing table but absent from the
  registered channel set are silently skipped (logged via structlog
  fallback only — never an exception).
"""

from __future__ import annotations

from typing import Any

import pytest

from bsvibe_alerts.client import AlertClient
from bsvibe_alerts.routing import AlertRouter
from bsvibe_alerts.settings import AlertSettings
from bsvibe_alerts.types import Alert, AlertSeverity


class _StubChannel:
    """Minimal AlertChannel implementation for routing tests."""

    def __init__(self, name: str, *, raises: BaseException | None = None) -> None:
        self.name = name
        self.calls: list[Alert] = []
        self._raises = raises

    async def send(self, alert: Alert) -> None:
        self.calls.append(alert)
        if self._raises is not None:
            raise self._raises


def _client_with(channels: list[Any], router: AlertRouter | None = None) -> AlertClient:
    return AlertClient(
        channels=channels,
        router=router or AlertRouter.from_defaults(),
    )


class TestPublishRouting:
    async def test_info_dispatches_to_structlog_only(self) -> None:
        structlog_ch = _StubChannel("structlog")
        slack_ch = _StubChannel("slack")
        telegram_ch = _StubChannel("telegram")
        client = _client_with([structlog_ch, slack_ch, telegram_ch])

        await client.publish(Alert(event="x", message="m", severity=AlertSeverity.INFO))

        assert len(structlog_ch.calls) == 1
        assert len(slack_ch.calls) == 0
        assert len(telegram_ch.calls) == 0

    async def test_warning_dispatches_to_structlog_and_slack(self) -> None:
        structlog_ch = _StubChannel("structlog")
        slack_ch = _StubChannel("slack")
        telegram_ch = _StubChannel("telegram")
        client = _client_with([structlog_ch, slack_ch, telegram_ch])

        await client.publish(Alert(event="x", message="m", severity=AlertSeverity.WARNING))

        assert len(structlog_ch.calls) == 1
        assert len(slack_ch.calls) == 1
        assert len(telegram_ch.calls) == 0

    async def test_critical_dispatches_to_all(self) -> None:
        structlog_ch = _StubChannel("structlog")
        slack_ch = _StubChannel("slack")
        telegram_ch = _StubChannel("telegram")
        client = _client_with([structlog_ch, slack_ch, telegram_ch])

        await client.publish(Alert(event="x", message="m", severity=AlertSeverity.CRITICAL))

        assert len(structlog_ch.calls) == 1
        assert len(slack_ch.calls) == 1
        assert len(telegram_ch.calls) == 1


class TestEmitConvenience:
    async def test_emit_builds_alert_and_publishes(self) -> None:
        structlog_ch = _StubChannel("structlog")
        client = _client_with([structlog_ch])

        await client.emit(
            event="task_failed",
            message="executor crashed",
            severity=AlertSeverity.INFO,
            context={"task_id": "abc"},
        )

        assert len(structlog_ch.calls) == 1
        sent = structlog_ch.calls[0]
        assert sent.event == "task_failed"
        assert sent.message == "executor crashed"
        assert sent.severity is AlertSeverity.INFO
        assert sent.context == {"task_id": "abc"}

    async def test_emit_accepts_string_severity(self) -> None:
        structlog_ch = _StubChannel("structlog")
        client = _client_with([structlog_ch])
        await client.emit(event="x", message="m", severity="critical")
        assert structlog_ch.calls[0].severity is AlertSeverity.CRITICAL

    async def test_emit_attaches_service_from_settings(self) -> None:
        # When AlertClient is built from settings with service_name set,
        # emit() should default the alert's service to that name so each
        # product does not have to repeat it on every call site.
        settings = AlertSettings(service_name="bsgateway")
        structlog_ch = _StubChannel("structlog")
        client = AlertClient(
            channels=[structlog_ch],
            router=AlertRouter.from_settings(settings),
            service_name=settings.service_name,
        )
        await client.emit(event="x", message="m")
        assert structlog_ch.calls[0].service == "bsgateway"


class TestFailureIsolation:
    async def test_one_channel_failure_does_not_block_others(self) -> None:
        structlog_ch = _StubChannel("structlog")
        slack_ch = _StubChannel("slack", raises=RuntimeError("slack down"))
        telegram_ch = _StubChannel("telegram")
        client = _client_with([structlog_ch, slack_ch, telegram_ch])

        # Critical -> all three. slack throws but telegram + structlog still see it.
        await client.publish(Alert(event="x", message="m", severity=AlertSeverity.CRITICAL))

        assert len(structlog_ch.calls) == 1
        assert len(slack_ch.calls) == 1  # call attempted
        assert len(telegram_ch.calls) == 1

    async def test_publish_returns_dispatch_result(self) -> None:
        structlog_ch = _StubChannel("structlog")
        slack_ch = _StubChannel("slack", raises=RuntimeError("slack down"))
        telegram_ch = _StubChannel("telegram")
        client = _client_with([structlog_ch, slack_ch, telegram_ch])

        result = await client.publish(Alert(event="x", message="m", severity=AlertSeverity.CRITICAL))

        # Returned shape: dict[channel_name -> True | error_repr]
        assert result["structlog"] is True
        assert result["telegram"] is True
        assert result["slack"] is not True  # error captured


class TestUnknownChannelInRoute:
    async def test_missing_channel_skipped_silently(self) -> None:
        # Routing rule references "telegram" but no telegram channel registered.
        structlog_ch = _StubChannel("structlog")
        client = _client_with([structlog_ch])  # no slack/telegram

        # Should NOT raise — production cannot crash because slack/telegram
        # creds are absent.
        await client.publish(Alert(event="x", message="m", severity=AlertSeverity.CRITICAL))

        assert len(structlog_ch.calls) == 1


class TestFromSettingsFactory:
    async def test_build_client_from_settings_no_external(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # No telegram/slack creds → only structlog channel registered.
        settings = AlertSettings()
        client = AlertClient.from_settings(settings)
        # AlertClient.from_settings always registers structlog (always-on).
        names = [ch.name for ch in client.channels]
        assert "structlog" in names
        assert "telegram" not in names
        assert "slack" not in names

    async def test_build_client_from_settings_with_telegram(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
        settings = AlertSettings()
        client = AlertClient.from_settings(settings)
        names = [ch.name for ch in client.channels]
        assert "telegram" in names

    async def test_build_client_from_settings_with_slack(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/x")
        settings = AlertSettings()
        client = AlertClient.from_settings(settings)
        names = [ch.name for ch in client.channels]
        assert "slack" in names

    async def test_emit_uses_routing_from_settings(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Override critical to ONLY structlog.
        monkeypatch.setenv("ALERT_CRITICAL_CHANNELS", "structlog")
        settings = AlertSettings()
        # Use stub instead of real telegram/slack so we can assert.
        slack_stub = _StubChannel("slack")
        client = AlertClient(
            channels=[_StubChannel("structlog"), slack_stub],
            router=AlertRouter.from_settings(settings),
        )
        await client.emit(event="x", message="m", severity=AlertSeverity.CRITICAL)
        # slack must NOT be called because routing override excludes it.
        assert len(slack_stub.calls) == 0
