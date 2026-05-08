"""FastAPI Depends integration tests."""

import hashlib
from collections.abc import Callable
from typing import Any

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from bsvibe_authz import deps as deps_mod
from bsvibe_authz.cache import IntrospectionCache
from bsvibe_authz.deps import (
    CurrentUser,
    ServiceKey,
    ServiceKeyAuth,
    require_permission,
    require_scope,
)
from bsvibe_authz.settings import Settings
from bsvibe_authz.types import IntrospectionResponse, User


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


# ---------------------------------------------------------------------------
# TASK-006: 3-way dispatch + require_scope
# ---------------------------------------------------------------------------
@pytest.fixture
def bootstrap_token() -> str:
    return "bsv_admin_supersecret"


@pytest.fixture
def bootstrap_settings(deps_settings: Settings, bootstrap_token: str) -> Settings:
    digest = hashlib.sha256(bootstrap_token.encode()).hexdigest()
    return deps_settings.model_copy(update={"bootstrap_token_hash": digest})


@pytest.fixture
def opaque_settings(deps_settings: Settings) -> Settings:
    return deps_settings.model_copy(
        update={
            "introspection_url": "https://auth.bsvibe.dev/oauth/introspect",
            "introspection_client_id": "bsage",
            "introspection_client_secret": "shh",
        },
    )


class _FakeIntrospectionClient:
    def __init__(self, response: IntrospectionResponse) -> None:
        self._response = response
        self.calls: list[str] = []

    async def introspect(self, token: str) -> IntrospectionResponse:
        self.calls.append(token)
        return self._response


def _build_dispatch_app(settings: Settings, fake_client: Any | None = None) -> FastAPI:
    deps_mod.reset_singletons()
    app = FastAPI()
    app.dependency_overrides[deps_mod.get_settings_dep] = lambda: settings
    if fake_client is not None:
        app.dependency_overrides[deps_mod.get_introspection_client] = lambda: fake_client
        app.dependency_overrides[deps_mod.get_introspection_cache] = lambda: IntrospectionCache(ttl_s=60)

    @app.get("/me")
    async def me(user: CurrentUser) -> dict:
        return {"id": user.id, "scope": user.scope, "is_service": user.is_service}

    return app


def test_dispatch_bootstrap_token(bootstrap_settings, bootstrap_token) -> None:
    app = _build_dispatch_app(bootstrap_settings)
    with TestClient(app) as client:
        resp = client.get("/me", headers=_bearer(bootstrap_token))
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == "bootstrap"
        assert body["scope"] == ["*"]
        assert body["is_service"] is True


def test_dispatch_bootstrap_token_wrong_value_returns_401(bootstrap_settings) -> None:
    app = _build_dispatch_app(bootstrap_settings)
    with TestClient(app) as client:
        resp = client.get("/me", headers=_bearer("bsv_admin_wrong"))
        assert resp.status_code == 401


def test_dispatch_opaque_token_active(opaque_settings) -> None:
    fake = _FakeIntrospectionClient(
        IntrospectionResponse(
            active=True,
            sub="user-123",
            tenant="t-1",
            scope=["gateway:models:read"],
        ),
    )
    app = _build_dispatch_app(opaque_settings, fake_client=fake)
    with TestClient(app) as client:
        resp = client.get("/me", headers=_bearer("bsv_sk_live_token"))
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == "user-123"
        assert body["scope"] == ["gateway:models:read"]


def test_dispatch_opaque_token_inactive_returns_401(opaque_settings) -> None:
    fake = _FakeIntrospectionClient(IntrospectionResponse(active=False))
    app = _build_dispatch_app(opaque_settings, fake_client=fake)
    with TestClient(app) as client:
        resp = client.get("/me", headers=_bearer("bsv_sk_dead_token"))
        assert resp.status_code == 401


def test_dispatch_opaque_token_falls_back_to_jwt_when_introspection_disabled(deps_settings, make_user_jwt) -> None:
    """No introspection_url configured -> bsv_sk_ tokens still go to JWT path (will fail)."""
    app = _build_dispatch_app(deps_settings)
    with TestClient(app) as client:
        resp = client.get("/me", headers=_bearer("bsv_sk_unknown"))
        assert resp.status_code == 401  # not a valid JWT


def test_dispatch_jwt_token(deps_settings, make_user_jwt) -> None:
    app = _build_dispatch_app(deps_settings)
    with TestClient(app) as client:
        token = make_user_jwt(sub="u-1")
        resp = client.get("/me", headers=_bearer(token))
        assert resp.status_code == 200
        assert resp.json()["id"] == "u-1"


# ---- PAT JWT introspection fallback ----------------------------------------
#
# Device-grant PATs are signed JWTs (HS256 + SERVICE_TOKEN_SIGNING_SECRET,
# not USER_JWT_SECRET) so they fail `verify_user_jwt`. The introspection
# endpoint accepts them by jti — the dispatcher falls back through
# introspection when user_jwt verification fails.


def test_dispatch_pat_jwt_falls_back_to_introspection(opaque_settings) -> None:
    """JWT not signed with user_jwt_secret → introspection picks it up."""
    fake = _FakeIntrospectionClient(
        IntrospectionResponse(
            active=True,
            sub="user-pat",
            tenant="t-1",
            scope=["gateway:models:read"],
        ),
    )
    app = _build_dispatch_app(opaque_settings, fake_client=fake)
    # JWT-shaped token signed with the wrong secret — verify_user_jwt rejects,
    # introspection accepts.
    import jwt as _jwt

    bogus_pat = _jwt.encode(
        {"sub": "user-pat", "exp": 9_999_999_999, "token_type": "pat"},
        "different-signing-secret",
        algorithm="HS256",
    )
    with TestClient(app) as client:
        resp = client.get("/me", headers=_bearer(bogus_pat))
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == "user-pat"
        assert fake.calls == [bogus_pat]


def test_dispatch_pat_jwt_inactive_returns_401(opaque_settings) -> None:
    fake = _FakeIntrospectionClient(IntrospectionResponse(active=False))
    app = _build_dispatch_app(opaque_settings, fake_client=fake)
    import jwt as _jwt

    bogus_pat = _jwt.encode(
        {"sub": "x", "exp": 9_999_999_999},
        "different-signing-secret",
        algorithm="HS256",
    )
    with TestClient(app) as client:
        resp = client.get("/me", headers=_bearer(bogus_pat))
        assert resp.status_code == 401


def test_dispatch_pat_jwt_no_introspection_client_returns_401(deps_settings) -> None:
    """JWT fails user_jwt verification; introspection unconfigured → 401."""
    app = _build_dispatch_app(deps_settings)
    import jwt as _jwt

    bogus_pat = _jwt.encode(
        {"sub": "x", "exp": 9_999_999_999},
        "different-signing-secret",
        algorithm="HS256",
    )
    with TestClient(app) as client:
        resp = client.get("/me", headers=_bearer(bogus_pat))
        assert resp.status_code == 401


def test_dispatch_non_jwt_garbage_does_not_call_introspection(opaque_settings) -> None:
    """Random non-JWT strings should not waste an introspection call."""
    fake = _FakeIntrospectionClient(IntrospectionResponse(active=False))
    app = _build_dispatch_app(opaque_settings, fake_client=fake)
    with TestClient(app) as client:
        resp = client.get("/me", headers=_bearer("not-a-jwt"))
        assert resp.status_code == 401
        assert fake.calls == []


# ---- require_scope ---------------------------------------------------------


def _scope_app(user: User, required: str) -> FastAPI:
    deps_mod.reset_singletons()
    app = FastAPI()
    app.dependency_overrides[deps_mod.get_current_user] = lambda: user

    @app.get("/scoped")
    async def scoped(_dep: None = Depends(require_scope(required))) -> dict:
        return {"ok": True}

    return app


def test_require_scope_exact_match() -> None:
    user = User(id="u-1", scope=["gateway:models:read"])
    with TestClient(_scope_app(user, "gateway:models:read")) as client:
        resp = client.get("/scoped")
        assert resp.status_code == 200


def test_require_scope_star_grants_all() -> None:
    user = User(id="bootstrap", scope=["*"], is_service=True)
    with TestClient(_scope_app(user, "anything:goes")) as client:
        resp = client.get("/scoped")
        assert resp.status_code == 200


def test_require_scope_prefix_wildcard() -> None:
    user = User(id="u-1", scope=["gateway:*"])
    with TestClient(_scope_app(user, "gateway:models:write")) as client:
        resp = client.get("/scoped")
        assert resp.status_code == 200


def test_require_scope_403_when_missing() -> None:
    user = User(id="u-1", scope=["gateway:models:read"])
    with TestClient(_scope_app(user, "gateway:models:write")) as client:
        resp = client.get("/scoped")
        assert resp.status_code == 403


def test_require_scope_empty_scope_403() -> None:
    user = User(id="u-1", scope=[])
    with TestClient(_scope_app(user, "anything")) as client:
        resp = client.get("/scoped")
        assert resp.status_code == 403


# ---- lazy singleton sanity -------------------------------------------------


def test_get_introspection_cache_singleton(opaque_settings) -> None:
    deps_mod.reset_singletons()
    a = deps_mod.get_introspection_cache(opaque_settings)
    b = deps_mod.get_introspection_cache(opaque_settings)
    assert a is b


def test_get_introspection_client_singleton(opaque_settings) -> None:
    deps_mod.reset_singletons()
    a = deps_mod.get_introspection_client(opaque_settings)
    b = deps_mod.get_introspection_client(opaque_settings)
    assert a is b


def test_get_introspection_client_returns_none_when_disabled(deps_settings) -> None:
    deps_mod.reset_singletons()
    assert deps_mod.get_introspection_client(deps_settings) is None
