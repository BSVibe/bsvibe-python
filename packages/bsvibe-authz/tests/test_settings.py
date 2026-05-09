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


def test_settings_introspection_and_bootstrap_defaults(
    reset_settings_env: pytest.MonkeyPatch,
) -> None:
    from bsvibe_authz.settings import Settings

    s = Settings(  # type: ignore[call-arg]
        bsvibe_auth_url="https://auth.bsvibe.dev",
        openfga_api_url="http://openfga.local:8080",
        openfga_store_id="01ABC",
        openfga_auth_model_id="01MODEL",
        service_token_signing_secret="x",
    )
    assert s.introspection_url == ""
    assert s.introspection_client_id == ""
    assert s.introspection_client_secret == ""
    assert s.bootstrap_token_hash == ""


def test_settings_introspection_and_bootstrap_from_env(
    reset_settings_env: pytest.MonkeyPatch,
) -> None:
    reset_settings_env.setenv("BSVIBE_AUTH_URL", "https://auth.bsvibe.dev")
    reset_settings_env.setenv("OPENFGA_API_URL", "http://openfga.local:8080")
    reset_settings_env.setenv("OPENFGA_STORE_ID", "01ABC")
    reset_settings_env.setenv("OPENFGA_AUTH_MODEL_ID", "01MODEL")
    reset_settings_env.setenv("SERVICE_TOKEN_SIGNING_SECRET", "secret-1")
    reset_settings_env.setenv("INTROSPECTION_URL", "https://auth.bsvibe.dev/oauth/introspect")
    reset_settings_env.setenv("INTROSPECTION_CLIENT_ID", "bsnexus")
    reset_settings_env.setenv("INTROSPECTION_CLIENT_SECRET", "intro-secret")
    reset_settings_env.setenv("BOOTSTRAP_TOKEN_HASH", "a" * 64)

    from bsvibe_authz.settings import Settings

    s = Settings()  # type: ignore[call-arg]
    assert s.introspection_url == "https://auth.bsvibe.dev/oauth/introspect"
    assert s.introspection_client_id == "bsnexus"
    assert s.introspection_client_secret == "intro-secret"
    assert s.bootstrap_token_hash == "a" * 64


def test_settings_introspection_and_bootstrap_from_bsv_prefixed_env(
    reset_settings_env: pytest.MonkeyPatch,
) -> None:
    """`BSV_*` aliases match canonical product-doc env names."""
    reset_settings_env.setenv("BSVIBE_AUTH_URL", "https://auth.bsvibe.dev")
    reset_settings_env.setenv("OPENFGA_API_URL", "http://openfga.local:8080")
    reset_settings_env.setenv("OPENFGA_STORE_ID", "01ABC")
    reset_settings_env.setenv("OPENFGA_AUTH_MODEL_ID", "01MODEL")
    reset_settings_env.setenv("SERVICE_TOKEN_SIGNING_SECRET", "secret-1")
    reset_settings_env.setenv("BSV_INTROSPECTION_URL", "https://auth.bsvibe.dev/api/tokens/introspect")
    reset_settings_env.setenv("BSV_INTROSPECTION_CLIENT_ID", "bsage")
    reset_settings_env.setenv("BSV_INTROSPECTION_CLIENT_SECRET", "intro-secret")
    reset_settings_env.setenv("BSV_BOOTSTRAP_TOKEN_HASH", "b" * 64)

    from bsvibe_authz.settings import Settings

    s = Settings()  # type: ignore[call-arg]
    assert s.introspection_url == "https://auth.bsvibe.dev/api/tokens/introspect"
    assert s.introspection_client_id == "bsage"
    assert s.introspection_client_secret == "intro-secret"
    assert s.bootstrap_token_hash == "b" * 64


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
