"""Tests for bsvibe_alerts.channels.structlog_channel.StructlogChannel.

The structlog channel is the **always-on** debug sink — it MUST never
raise (so client.publish() can rely on it as a fallback) and MUST emit a
key per alert field so log scrapers can pivot on ``severity`` / ``event``
/ ``service``.
"""

from __future__ import annotations

import io
import json

import pytest
from bsvibe_core import configure_logging

from bsvibe_alerts.channels.structlog_channel import StructlogChannel
from bsvibe_alerts.types import Alert, AlertSeverity


@pytest.fixture
def captured_stream() -> io.StringIO:
    stream = io.StringIO()
    configure_logging(level="info", json_output=True, stream=stream)
    return stream


def _last_log(stream: io.StringIO) -> dict:
    lines = [line for line in stream.getvalue().splitlines() if line.strip()]
    assert lines, "no log line captured"
    return json.loads(lines[-1])


class TestStructlogChannel:
    async def test_emits_event_severity_message(
        self,
        captured_stream: io.StringIO,
    ) -> None:
        channel = StructlogChannel()
        alert = Alert(
            event="task_failed",
            message="executor crashed",
            severity=AlertSeverity.CRITICAL,
            context={"task_id": "abc"},
            service="bsnexus",
        )
        await channel.send(alert)

        log = _last_log(captured_stream)
        assert log["event"] == "task_failed"
        assert log["alert_severity"] == "critical"
        assert log["alert_message"] == "executor crashed"
        assert log["task_id"] == "abc"
        assert log["service"] == "bsnexus"

    async def test_log_level_matches_severity(
        self,
        captured_stream: io.StringIO,
    ) -> None:
        channel = StructlogChannel()

        await channel.send(Alert(event="i", message="m", severity=AlertSeverity.INFO))
        assert _last_log(captured_stream)["level"] == "info"

        await channel.send(Alert(event="w", message="m", severity=AlertSeverity.WARNING))
        assert _last_log(captured_stream)["level"] == "warning"

        await channel.send(Alert(event="c", message="m", severity=AlertSeverity.CRITICAL))
        assert _last_log(captured_stream)["level"] == "critical"

    async def test_minimal_alert_does_not_crash(
        self,
        captured_stream: io.StringIO,
    ) -> None:
        channel = StructlogChannel()
        await channel.send(Alert(event="x", message="m"))
        log = _last_log(captured_stream)
        assert log["event"] == "x"
        assert log["alert_severity"] == "info"

    def test_channel_name(self) -> None:
        assert StructlogChannel().name == "structlog"
