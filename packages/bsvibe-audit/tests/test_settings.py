"""Tests for AuditSettings — env contract for the audit relay + emitter."""

from __future__ import annotations

import pytest

from bsvibe_audit import AuditSettings


def test_audit_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = AuditSettings()
    # Ship-safe defaults — disabled by default until BSVIBE_AUTH_AUDIT_URL is set.
    assert settings.auth_audit_url == ""
    assert settings.auth_service_token == ""
    assert settings.outbox_table_name == "audit_outbox"
    assert settings.batch_size == 50
    assert settings.relay_interval_s == pytest.approx(5.0)
    assert settings.max_retries == 5
    assert settings.relay_enabled is False
    assert settings.service_name == ""


def test_audit_settings_picks_up_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BSVIBE_AUTH_AUDIT_URL", "https://auth.bsvibe.dev/api/audit/events")
    monkeypatch.setenv("BSVIBE_AUTH_AUDIT_SERVICE_TOKEN", "tok")
    monkeypatch.setenv("AUDIT_OUTBOX_TABLE_NAME", "custom_outbox")
    monkeypatch.setenv("AUDIT_BATCH_SIZE", "100")
    monkeypatch.setenv("AUDIT_RELAY_INTERVAL_S", "1.5")
    monkeypatch.setenv("AUDIT_MAX_RETRIES", "10")
    monkeypatch.setenv("AUDIT_SERVICE_NAME", "bsnexus")
    settings = AuditSettings()
    assert settings.auth_audit_url.endswith("/api/audit/events")
    assert settings.auth_service_token == "tok"
    assert settings.outbox_table_name == "custom_outbox"
    assert settings.batch_size == 100
    assert settings.relay_interval_s == pytest.approx(1.5)
    assert settings.max_retries == 10
    assert settings.service_name == "bsnexus"
    # Once the URL is configured, relay is enabled by default
    assert settings.relay_enabled is True


def test_audit_settings_explicit_disable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BSVIBE_AUTH_AUDIT_URL", "https://auth.bsvibe.dev/api/audit/events")
    monkeypatch.setenv("AUDIT_RELAY_ENABLED", "false")
    settings = AuditSettings()
    assert settings.relay_enabled is False


def test_audit_settings_extra_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inherits BsvibeSettings.extra='ignore' — a stray env var must not crash."""
    monkeypatch.setenv("AUDIT_UNKNOWN_KNOB", "noise")
    monkeypatch.setenv("BSVIBE_OTHER_PRODUCT_VAR", "x")
    AuditSettings()
