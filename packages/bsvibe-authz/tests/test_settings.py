"""Settings (pydantic-settings) tests."""

from __future__ import annotations

import pytest


def test_settings_loads_from_env(reset_settings_env: pytest.MonkeyPatch) -> None:
    reset_settings_env.setenv("BSVIBE_AUTH_URL", "https://auth.bsvibe.dev")
    reset_settings_env.setenv("OPENFGA_API_URL", "http://openfga.local:8080")
    reset_settings_env.setenv("OPENFGA_STORE_ID", "01ABC")
    reset_settings_env.setenv("OPENFGA_AUTH_MODEL_ID", "01MODEL")
    reset_settings_env.setenv("OPENFGA_AUTH_TOKEN", "fga-token")
    reset_settings_env.setenv("SERVICE_TOKEN_SIGNING_SECRET", "secret-1")

    from bsvibe_authz.settings import Settings

    s = Settings()  # type: ignore[call-arg]
    assert s.bsvibe_auth_url == "https://auth.bsvibe.dev"
    assert s.openfga_api_url == "http://openfga.local:8080"
    assert s.openfga_store_id == "01ABC"
    assert s.openfga_auth_model_id == "01MODEL"
    assert s.openfga_auth_token == "fga-token"
    assert s.service_token_signing_secret == "secret-1"


def test_settings_defaults(reset_settings_env: pytest.MonkeyPatch) -> None:
    from bsvibe_authz.settings import Settings

    s = Settings(  # type: ignore[call-arg]
        bsvibe_auth_url="https://auth.bsvibe.dev",
        openfga_api_url="http://openfga.local:8080",
        openfga_store_id="01ABC",
        openfga_auth_model_id="01MODEL",
        service_token_signing_secret="x",
    )
    assert s.permission_cache_ttl_s == 30
    assert s.user_jwt_algorithm == "HS256"
    assert s.user_jwt_audience == "bsvibe"
    assert s.openfga_request_timeout_s == 3.0


def test_settings_get_settings_singleton(reset_settings_env: pytest.MonkeyPatch) -> None:
    reset_settings_env.setenv("BSVIBE_AUTH_URL", "https://auth.bsvibe.dev")
    reset_settings_env.setenv("OPENFGA_API_URL", "http://openfga.local:8080")
    reset_settings_env.setenv("OPENFGA_STORE_ID", "01ABC")
    reset_settings_env.setenv("OPENFGA_AUTH_MODEL_ID", "01MODEL")
    reset_settings_env.setenv("SERVICE_TOKEN_SIGNING_SECRET", "secret-1")

    from bsvibe_authz.settings import get_settings, reset_settings_cache

    reset_settings_cache()
    a = get_settings()
    b = get_settings()
    assert a is b
