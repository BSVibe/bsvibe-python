"""Tests for bsvibe_alerts.settings.AlertSettings.

The settings class follows the BSupervisor §M18 ``Annotated[list[str],
NoDecode]`` pattern for any CSV env var so deployers can drop
``ALERT_INFO_CHANNELS=structlog,slack`` without JSON-encoding it.

Wire-compatible env contract:

* ``TELEGRAM_BOT_TOKEN`` / ``TELEGRAM_CHAT_ID`` — optional; if either is
  empty the telegram channel is treated as disabled.
* ``SLACK_WEBHOOK_URL`` — optional; empty means slack channel disabled.
* ``ALERT_INFO_CHANNELS`` / ``ALERT_WARNING_CHANNELS`` /
  ``ALERT_CRITICAL_CHANNELS`` — comma-separated channel names overriding
  the default routing table.
* ``ALERT_SERVICE_NAME`` — labels every emitted alert (matches the
  `service=` key already standardised in :mod:`bsvibe_core.logging`).
"""

from __future__ import annotations

import pytest

from bsvibe_alerts.settings import AlertSettings


class TestDefaults:
    def test_no_env_minimal(self) -> None:
        s = AlertSettings()
        assert s.telegram_bot_token == ""
        assert s.telegram_chat_id == ""
        assert s.slack_webhook_url == ""
        assert s.service_name is None

    def test_default_routing_rules(self) -> None:
        # info → structlog only, warning → +slack, critical → +telegram.
        s = AlertSettings()
        assert s.info_channels == ["structlog"]
        assert s.warning_channels == ["structlog", "slack"]
        assert s.critical_channels == ["structlog", "slack", "telegram"]


class TestEnvLoading:
    def test_telegram_credentials_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot-x")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
        s = AlertSettings()
        assert s.telegram_bot_token == "bot-x"
        assert s.telegram_chat_id == "12345"

    def test_slack_webhook_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/x")
        s = AlertSettings()
        assert s.slack_webhook_url == "https://hooks.slack.com/x"

    def test_service_name_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ALERT_SERVICE_NAME", "bsgateway")
        s = AlertSettings()
        assert s.service_name == "bsgateway"

    def test_csv_routing_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ALERT_INFO_CHANNELS", "structlog,slack")
        monkeypatch.setenv("ALERT_WARNING_CHANNELS", "telegram")
        monkeypatch.setenv("ALERT_CRITICAL_CHANNELS", "telegram,slack")
        s = AlertSettings()
        assert s.info_channels == ["structlog", "slack"]
        assert s.warning_channels == ["telegram"]
        assert s.critical_channels == ["telegram", "slack"]

    def test_empty_csv_falls_back_to_default(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("ALERT_INFO_CHANNELS", "")
        s = AlertSettings()
        assert s.info_channels == ["structlog"]


class TestEnabledFlags:
    def test_telegram_disabled_when_empty(self) -> None:
        s = AlertSettings()
        assert s.telegram_enabled is False

    def test_telegram_disabled_when_token_only(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot-x")
        s = AlertSettings()
        assert s.telegram_enabled is False  # chat_id missing

    def test_telegram_enabled_when_both_set(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot-x")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
        s = AlertSettings()
        assert s.telegram_enabled is True

    def test_slack_disabled_when_empty(self) -> None:
        s = AlertSettings()
        assert s.slack_enabled is False

    def test_slack_enabled_when_url_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/x")
        s = AlertSettings()
        assert s.slack_enabled is True


class TestExtendsBsvibeSettings:
    def test_is_subclass_of_bsvibe_settings(self) -> None:
        from bsvibe_core import BsvibeSettings

        assert issubclass(AlertSettings, BsvibeSettings)

    def test_unknown_env_vars_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BSVIBE_SOMETHING_ELSE", "x")
        AlertSettings()  # must not raise
