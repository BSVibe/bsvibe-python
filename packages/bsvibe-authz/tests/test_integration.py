"""TASK-007 integration test — bootstrap token + require_scope through FastAPI."""

from __future__ import annotations

import hashlib

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

import bsvibe_authz
from bsvibe_authz import deps as deps_mod
from bsvibe_authz.settings import Settings


@pytest.fixture
def integration_settings(user_jwt_secret: str, service_signing_secret: str, issuer: str) -> Settings:
    bootstrap_token = "bsv_admin_integration_secret"
    digest = hashlib.sha256(bootstrap_token.encode()).hexdigest()
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
        bootstrap_token_hash=digest,
    )


def test_public_exports_are_present() -> None:
    """All TASK-007 required symbols are exported from the package root."""
    expected = (
        "IntrospectionClient",
        "IntrospectionCache",
        "verify_opaque_token",
        "verify_bootstrap_token",
        "require_scope",
        "get_introspection_client",
        "get_introspection_cache",
    )
    for name in expected:
        assert hasattr(bsvibe_authz, name), f"bsvibe_authz missing export: {name}"
        assert name in bsvibe_authz.__all__, f"{name} not in __all__"


def test_bootstrap_token_passes_protected_route(integration_settings: Settings) -> None:
    """Bootstrap token grants access through get_current_user + require_scope('test:read')."""
    deps_mod.reset_singletons()
    app = FastAPI()
    app.dependency_overrides[deps_mod.get_settings_dep] = lambda: integration_settings

    @app.get("/protected")
    async def protected(
        user: bsvibe_authz.CurrentUser,
        _scope: None = Depends(bsvibe_authz.require_scope("test:read")),
    ) -> dict:
        return {"sub": user.id, "scope": user.scope}

    with TestClient(app) as client:
        resp = client.get(
            "/protected",
            headers={"Authorization": "Bearer bsv_admin_integration_secret"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["sub"] == "bootstrap"
        assert body["scope"] == ["*"]


def test_bootstrap_token_wrong_value_blocked(integration_settings: Settings) -> None:
    deps_mod.reset_singletons()
    app = FastAPI()
    app.dependency_overrides[deps_mod.get_settings_dep] = lambda: integration_settings

    @app.get("/protected")
    async def protected(
        _scope: None = Depends(bsvibe_authz.require_scope("test:read")),
    ) -> dict:
        return {"ok": True}

    with TestClient(app) as client:
        resp = client.get("/protected", headers={"Authorization": "Bearer bsv_admin_wrong"})
        assert resp.status_code == 401
