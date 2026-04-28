"""Tests for ``LlmSettings``.

Pins:
- ``bsgateway_url`` is the **default routing target** (Decision #11).
- ``model`` default and a fallback chain are configurable via env.
- Settings inherit from ``BsvibeSettings`` so wire format matches the
  shared CSV-list pattern.
"""

from __future__ import annotations

import pytest
from bsvibe_core import BsvibeSettings

from bsvibe_llm.settings import LlmSettings


class TestLlmSettingsDefaults:
    def test_inherits_from_bsvibe_settings(self):
        # Architectural lock: every BSVibe service settings object derives
        # from ``BsvibeSettings`` so case-insensitivity / extra="ignore"
        # are uniform.
        assert issubclass(LlmSettings, BsvibeSettings)

    def test_default_routing_via_bsgateway(self):
        s = LlmSettings()
        # Decision #11: BSGateway is the default routing target. The URL
        # may be empty until a deployer configures it, but the field must
        # exist with the standard ``bsgateway_url`` env name.
        assert hasattr(s, "bsgateway_url")
        assert s.route_default == "bsgateway"

    def test_default_model_field_present(self):
        s = LlmSettings()
        assert hasattr(s, "model")
        # We do NOT pin a vendor here; deployers set this. But the field
        # must be a string default (empty allowed).
        assert isinstance(s.model, str)

    def test_fallback_chain_default_empty_list(self):
        s = LlmSettings()
        assert s.fallback_models == []

    def test_retry_defaults(self):
        s = LlmSettings()
        assert s.retry_max_attempts >= 1
        assert s.retry_base_delay_s > 0


class TestLlmSettingsEnv:
    def test_csv_fallback_models_from_env(self, monkeypatch):
        # CSV-list pattern from bsvibe-core. Validates the shared wire
        # format applies cleanly in this package too.
        monkeypatch.setenv("FALLBACK_MODELS", "openai/gpt-4o,anthropic/claude-3-5-sonnet")
        s = LlmSettings()
        assert s.fallback_models == [
            "openai/gpt-4o",
            "anthropic/claude-3-5-sonnet",
        ]

    def test_bsgateway_url_from_env(self, monkeypatch):
        monkeypatch.setenv("BSGATEWAY_URL", "http://gateway.local:9090")
        s = LlmSettings()
        assert s.bsgateway_url == "http://gateway.local:9090"

    def test_model_from_env(self, monkeypatch):
        monkeypatch.setenv("MODEL", "openai/gpt-4o-mini")
        s = LlmSettings()
        assert s.model == "openai/gpt-4o-mini"

    def test_route_default_must_be_bsgateway_or_direct(self):
        with pytest.raises(Exception):
            LlmSettings(route_default="invalid")  # type: ignore[arg-type]

    def test_route_default_direct_allowed(self):
        s = LlmSettings(route_default="direct")
        assert s.route_default == "direct"
