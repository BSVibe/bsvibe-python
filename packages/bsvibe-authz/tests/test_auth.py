"""JWT verification tests — user JWT + service JWT."""

from __future__ import annotations

import time

import jwt
import pytest


@pytest.fixture
def auth_settings(user_jwt_secret: str, service_signing_secret: str, issuer: str):
    from bsvibe_authz.settings import Settings

    return Settings(  # type: ignore[call-arg]
        bsvibe_auth_url=issuer,
        openfga_api_url="http://openfga.local:8080",
        openfga_store_id="store-1",
        openfga_auth_model_id="model-1",
        service_token_signing_secret=service_signing_secret,
        user_jwt_secret=user_jwt_secret,
        user_jwt_algorithm="HS256",
        user_jwt_audience="bsvibe",
        user_jwt_issuer=issuer,
    )


async def test_verify_user_jwt_returns_payload(auth_settings, make_user_jwt) -> None:
    from bsvibe_authz.auth import verify_user_jwt

    token = make_user_jwt(sub="u-1", email="alice@bsvibe.dev")
    payload = verify_user_jwt(token, auth_settings)
    assert payload["sub"] == "u-1"
    assert payload["email"] == "alice@bsvibe.dev"


async def test_verify_user_jwt_expired_token(auth_settings, make_user_jwt) -> None:
    from bsvibe_authz.auth import AuthError, verify_user_jwt

    token = make_user_jwt(exp_offset=-10)
    with pytest.raises(AuthError):
        verify_user_jwt(token, auth_settings)


async def test_verify_user_jwt_wrong_audience(auth_settings, user_jwt_secret, issuer, now) -> None:
    from bsvibe_authz.auth import AuthError, verify_user_jwt

    token = jwt.encode(
        {
            "iss": issuer,
            "sub": "u-1",
            "email": "x@y.z",
            "aud": "wrong-aud",
            "iat": now,
            "exp": now + 60,
        },
        user_jwt_secret,
        algorithm="HS256",
    )
    with pytest.raises(AuthError):
        verify_user_jwt(token, auth_settings)


async def test_verify_user_jwt_wrong_signature(auth_settings, issuer, now) -> None:
    from bsvibe_authz.auth import AuthError, verify_user_jwt

    token = jwt.encode(
        {
            "iss": issuer,
            "sub": "u-1",
            "email": "x@y.z",
            "aud": "bsvibe",
            "iat": now,
            "exp": now + 60,
        },
        "different-secret-but-still-32-bytes-long-x",
        algorithm="HS256",
    )
    with pytest.raises(AuthError):
        verify_user_jwt(token, auth_settings)


async def test_verify_service_jwt_passes_for_valid_payload(auth_settings, make_service_jwt) -> None:
    from bsvibe_authz.auth import verify_service_jwt

    token = make_service_jwt(
        sub="service:bsnexus",
        aud="bsage",
        scope="bsage.read bsage.write",
        tenant_id="t-1",
    )
    payload = verify_service_jwt(token, auth_settings, expected_audience="bsage")
    assert payload.aud == "bsage"
    assert payload.sub == "service:bsnexus"
    assert payload.has_scope("bsage.read")
    assert payload.tenant_id == "t-1"
    assert payload.token_type == "service"


async def test_verify_service_jwt_uses_service_token_issuer(
    user_jwt_secret: str,
    service_signing_secret: str,
    now: int,
) -> None:
    from bsvibe_authz.auth import verify_service_jwt
    from bsvibe_authz.settings import Settings

    settings = Settings(  # type: ignore[call-arg]
        bsvibe_auth_url="http://auth-app:5179",
        openfga_api_url="http://openfga.local:8080",
        openfga_store_id="store-1",
        openfga_auth_model_id="model-1",
        service_token_signing_secret=service_signing_secret,
        service_token_issuer="http://auth-app:5179",
        user_jwt_secret=user_jwt_secret,
        user_jwt_algorithm="HS256",
        user_jwt_audience="bsvibe",
        user_jwt_issuer="http://localhost:54321/auth/v1",
    )
    token = jwt.encode(
        {
            "iss": "http://auth-app:5179",
            "sub": "user:u-1",
            "aud": "bsupervisor",
            "scope": "bsupervisor.events",
            "iat": now,
            "exp": now + 60,
            "token_type": "service",
            "tenant_id": "t-1",
        },
        service_signing_secret,
        algorithm="HS256",
    )

    payload = verify_service_jwt(token, settings, expected_audience="bsupervisor")

    assert payload.aud == "bsupervisor"
    assert payload.has_scope("bsupervisor.events")


async def test_verify_service_jwt_rejects_wrong_audience(auth_settings, make_service_jwt) -> None:
    from bsvibe_authz.auth import AuthError, verify_service_jwt

    token = make_service_jwt(aud="bsage")
    with pytest.raises(AuthError):
        verify_service_jwt(token, auth_settings, expected_audience="bsgateway")


async def test_verify_service_jwt_rejects_wrong_token_type(auth_settings, service_signing_secret, issuer) -> None:
    from bsvibe_authz.auth import AuthError, verify_service_jwt

    now = int(time.time())
    token = jwt.encode(
        {
            "iss": issuer,
            "sub": "service:x",
            "aud": "bsage",
            "scope": "bsage.read",
            "iat": now,
            "exp": now + 60,
            "token_type": "user",  # wrong
        },
        service_signing_secret,
        algorithm="HS256",
    )
    with pytest.raises(AuthError):
        verify_service_jwt(token, auth_settings, expected_audience="bsage")


async def test_verify_service_jwt_rejects_expired(auth_settings, make_service_jwt) -> None:
    from bsvibe_authz.auth import AuthError, verify_service_jwt

    token = make_service_jwt(exp_offset=-10)
    with pytest.raises(AuthError):
        verify_service_jwt(token, auth_settings, expected_audience="bsage")


async def test_verify_service_jwt_rejects_scope_audience_mismatch(
    auth_settings, service_signing_secret, issuer
) -> None:
    from bsvibe_authz.auth import AuthError, verify_service_jwt

    now = int(time.time())
    token = jwt.encode(
        {
            "iss": issuer,
            "sub": "service:x",
            "aud": "bsage",
            "scope": "bsgateway.read",  # scope doesn't match aud
            "iat": now,
            "exp": now + 60,
            "token_type": "service",
        },
        service_signing_secret,
        algorithm="HS256",
    )
    with pytest.raises(AuthError):
        verify_service_jwt(token, auth_settings, expected_audience="bsage")


async def test_parse_user_token_returns_user(auth_settings, make_user_jwt) -> None:
    from bsvibe_authz.auth import parse_user_token, verify_user_jwt

    token = make_user_jwt(sub="u-1", email="alice@bsvibe.dev", active_tenant_id="t-1")
    payload = verify_user_jwt(token, auth_settings)
    user = parse_user_token(payload)
    assert user.id == "u-1"
    assert user.email == "alice@bsvibe.dev"
    assert user.active_tenant_id == "t-1"
    assert user.is_service is False
