"""FastAPI Depends integration tests."""

from collections.abc import Callable
from typing import Any

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from bsvibe_authz import deps as deps_mod
from bsvibe_authz.deps import CurrentUser, ServiceKey, ServiceKeyAuth, require_permission
from bsvibe_authz.settings import Settings


@pytest.fixture
def deps_settings(user_jwt_secret: str, service_signing_secret: str, issuer: str):
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


def _build_app(
    settings: Settings,
    *,
    fga_check: Callable[..., bool] | None = None,
) -> FastAPI:
    # Reset process-wide singletons (cache, OpenFGA client) so tests are
    # hermetic — otherwise a `True` decision cached by an earlier test bleeds
    # into a later "denied" test.
    deps_mod.reset_singletons()
    app = FastAPI()

    class FakeFGA:
        async def check(self, user: str, relation: str, object_: str, **_: Any) -> bool:
            if fga_check is None:
                return True
            return fga_check(user, relation, object_)

        async def list_objects(self, *args: Any, **kwargs: Any) -> list[str]:
            return []

    fake_fga = FakeFGA()

    app.dependency_overrides[deps_mod.get_settings_dep] = lambda: settings
    app.dependency_overrides[deps_mod.get_openfga_client] = lambda: fake_fga

    @app.get("/me")
    async def me(user: CurrentUser) -> dict:
        return {"id": user.id, "email": user.email, "active_tenant_id": user.active_tenant_id}

    @app.get("/projects/{project_id}")
    async def project(
        project_id: str,
        user: CurrentUser,
        _allowed: None = Depends(
            require_permission(
                "nexus.project.read",
                resource_type="project",
                resource_id_param="project_id",
            ),
        ),
    ) -> dict:
        return {"id": project_id, "user": user.id}

    @app.get("/internal/data")
    async def internal_data(
        svc: ServiceKey = Depends(ServiceKeyAuth(audience="bsage")),
    ) -> dict:
        return {"sub": svc.sub, "scope": svc.scope}

    return app


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_current_user_returns_user_from_valid_jwt(deps_settings, make_user_jwt) -> None:
    app = _build_app(deps_settings)
    with TestClient(app) as client:
        token = make_user_jwt(sub="u-1", email="alice@bsvibe.dev", active_tenant_id="t-1")
        resp = client.get("/me", headers=_bearer(token))
        assert resp.status_code == 200
        assert resp.json() == {
            "id": "u-1",
            "email": "alice@bsvibe.dev",
            "active_tenant_id": "t-1",
        }


def test_current_user_401_without_authorization(deps_settings) -> None:
    app = _build_app(deps_settings)
    with TestClient(app) as client:
        resp = client.get("/me")
        assert resp.status_code == 401


def test_current_user_401_invalid_token(deps_settings) -> None:
    app = _build_app(deps_settings)
    with TestClient(app) as client:
        resp = client.get("/me", headers=_bearer("not-a-jwt"))
        assert resp.status_code == 401


def test_require_permission_allows_when_fga_allows(deps_settings, make_user_jwt) -> None:
    app = _build_app(deps_settings, fga_check=lambda u, r, o: True)
    with TestClient(app) as client:
        token = make_user_jwt(sub="u-1")
        resp = client.get("/projects/p1", headers=_bearer(token))
        assert resp.status_code == 200


def test_require_permission_403_when_fga_denies(deps_settings, make_user_jwt) -> None:
    app = _build_app(deps_settings, fga_check=lambda u, r, o: False)
    with TestClient(app) as client:
        token = make_user_jwt(sub="u-1")
        resp = client.get("/projects/p1", headers=_bearer(token))
        assert resp.status_code == 403


def test_service_key_auth_accepts_valid_service_token(deps_settings, make_service_jwt) -> None:
    app = _build_app(deps_settings)
    with TestClient(app) as client:
        token = make_service_jwt(
            sub="service:bsnexus",
            aud="bsage",
            scope="bsage.read bsage.write",
            tenant_id="t-1",
        )
        resp = client.get("/internal/data", headers=_bearer(token))
        assert resp.status_code == 200
        body = resp.json()
        assert body["sub"] == "service:bsnexus"
        assert body["scope"] == "bsage.read bsage.write"


def test_service_key_auth_rejects_user_jwt(deps_settings, make_user_jwt) -> None:
    app = _build_app(deps_settings)
    with TestClient(app) as client:
        token = make_user_jwt()
        resp = client.get("/internal/data", headers=_bearer(token))
        assert resp.status_code == 401


def test_service_key_auth_rejects_wrong_audience(deps_settings, make_service_jwt) -> None:
    app = _build_app(deps_settings)
    with TestClient(app) as client:
        token = make_service_jwt(aud="bsgateway")
        resp = client.get("/internal/data", headers=_bearer(token))
        assert resp.status_code == 401


def test_tenant_scoped_extracts_active_tenant(deps_settings, make_user_jwt) -> None:
    """TenantScoped dep mixin auto-injects active tenant id."""
    from bsvibe_authz import deps as deps_mod

    app = _build_app(deps_settings)

    @app.get("/tenant-id")
    async def tenant_route(
        tenant_id: str = Depends(deps_mod.get_active_tenant_id),
    ) -> dict:
        return {"tenant_id": tenant_id}

    with TestClient(app) as client:
        token = make_user_jwt(active_tenant_id="t-99")
        resp = client.get("/tenant-id", headers=_bearer(token))
        assert resp.status_code == 200
        assert resp.json() == {"tenant_id": "t-99"}


def test_tenant_scoped_403_when_no_active_tenant(deps_settings, user_jwt_secret, issuer) -> None:
    import jwt

    from bsvibe_authz import deps as deps_mod

    app = _build_app(deps_settings)

    @app.get("/tenant-id")
    async def tenant_route(
        tenant_id: str = Depends(deps_mod.get_active_tenant_id),
    ) -> dict:
        return {"tenant_id": tenant_id}

    import time

    now = int(time.time())
    token = jwt.encode(
        {
            "iss": issuer,
            "sub": "u-1",
            "email": "x@y.z",
            "aud": "bsvibe",
            "iat": now,
            "exp": now + 60,
        },
        user_jwt_secret,
        algorithm="HS256",
    )
    with TestClient(app) as client:
        resp = client.get("/tenant-id", headers=_bearer(token))
        assert resp.status_code == 403
