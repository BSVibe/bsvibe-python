"""FastAPI dependency helpers.

Public surface (re-exported from ``bsvibe_authz``):
- ``CurrentUser`` — Annotated[User, Depends(get_current_user)]
- ``require_permission(...)`` — dep factory enforcing OpenFGA check (403 on deny)
- ``ServiceKeyAuth(audience=...)`` — service-JWT only verifier (no user auth)
- ``ServiceKey`` — return type for ServiceKeyAuth (the verified payload)
- ``get_active_tenant_id`` — extract & require ``active_tenant_id`` from session
- ``get_openfga_client`` / ``get_settings_dep`` — overridable injection points
- ``get_permission_cache`` — shared cache singleton

The OpenFGA client is **constructed once per process** and reused across
requests via dependency caching, matching the cache-30s-TTL design (no
per-request overhead beyond the OpenFGA HTTP call).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated, Protocol

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.security.utils import get_authorization_scheme_param

import jwt as _jwt

from .auth import (
    AuthError,
    parse_user_token,
    verify_service_jwt,
    verify_user_jwt,
    verify_via_introspection,
)
from .cache import IntrospectionCache, PermissionCache
from .client import OpenFGAClient, OpenFGAError
from .introspection import IntrospectionClient
from .settings import Settings, get_settings
from .types import ServiceAudience, ServiceTokenPayload, User


def _looks_like_jwt(token: str) -> bool:
    """Cheap structural check: three base64url segments separated by dots.

    Only used to gate the introspection fallback so a stray garbage string
    doesn't trigger a network round-trip to the auth server.
    """
    parts = token.split(".")
    return len(parts) == 3 and all(p for p in parts)


_bearer_scheme = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------------------
# Lockin §3 decision #15 — BaseServiceClient is a Protocol (structural typing).
# Same shape applies here so callers can swap in fakes without inheritance.
# ---------------------------------------------------------------------------
class FGAClientProtocol(Protocol):
    async def check(self, user: str, relation: str, object_: str) -> bool: ...
    async def list_objects(self, user: str, relation: str, type_: str) -> list[str]: ...
    async def write_tuple(self, user: str, relation: str, object_: str) -> None: ...


# ---------------------------------------------------------------------------
# Injection points
# ---------------------------------------------------------------------------
def get_settings_dep() -> Settings:
    """Override-friendly Settings provider."""
    return get_settings()


_fga_client_singleton: OpenFGAClient | None = None
_cache_singleton: PermissionCache | None = None
_introspection_client_singleton: IntrospectionClient | None = None
_introspection_cache_singleton: IntrospectionCache | None = None


def get_openfga_client(settings: Settings = Depends(get_settings_dep)) -> FGAClientProtocol:
    """Process-wide OpenFGA client (lazy init)."""
    global _fga_client_singleton
    if _fga_client_singleton is None:
        _fga_client_singleton = OpenFGAClient(settings)
    return _fga_client_singleton  # type: ignore[return-value]


def get_permission_cache(
    settings: Settings = Depends(get_settings_dep),
) -> PermissionCache:
    """Process-wide permission cache (lazy init)."""
    global _cache_singleton
    if _cache_singleton is None:
        _cache_singleton = PermissionCache(ttl_s=settings.permission_cache_ttl_s)
    return _cache_singleton


def get_introspection_client(
    settings: Settings = Depends(get_settings_dep),
) -> IntrospectionClient | None:
    """Process-wide RFC 7662 introspection client (lazy init).

    Returns ``None`` when ``introspection_url`` is unconfigured so callers can
    fall through to the JWT path.
    """
    global _introspection_client_singleton
    if _introspection_client_singleton is None:
        if not settings.introspection_url:
            return None
        _introspection_client_singleton = IntrospectionClient(
            introspection_url=settings.introspection_url,
            client_id=settings.introspection_client_id,
            client_secret=settings.introspection_client_secret,
        )
    return _introspection_client_singleton


def get_introspection_cache(
    settings: Settings = Depends(get_settings_dep),
) -> IntrospectionCache:
    """Process-wide introspection-response cache (lazy init)."""
    global _introspection_cache_singleton
    if _introspection_cache_singleton is None:
        _introspection_cache_singleton = IntrospectionCache(
            ttl_s=settings.permission_cache_ttl_s,
        )
    return _introspection_cache_singleton


def reset_singletons() -> None:
    """Used by tests — reset the process-wide client/cache."""
    global _fga_client_singleton, _cache_singleton
    global _introspection_client_singleton, _introspection_cache_singleton
    _fga_client_singleton = None
    _cache_singleton = None
    _introspection_client_singleton = None
    _introspection_cache_singleton = None


# ---------------------------------------------------------------------------
# Bearer token extraction
# ---------------------------------------------------------------------------
def _extract_bearer(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing Authorization header",
        )
    scheme, token = get_authorization_scheme_param(authorization)
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid Authorization scheme",
        )
    return token


# ---------------------------------------------------------------------------
# CurrentUser
# ---------------------------------------------------------------------------
def _try_demo_user(token: str, settings: Settings) -> User | None:
    """If ``token`` carries ``is_demo: true`` and a ``demo_jwt_secret`` is
    configured, verify against it and return a synthetic demo User.
    Returns None when the token is not a demo token, or demo mode is not
    enabled. Lets the prod auth path keep handling everything else.
    """
    if not settings.demo_jwt_secret:
        return None
    # Peek at the unverified payload to check the is_demo flag — avoids a
    # spurious signature check on every prod request.
    try:
        peek = _jwt.decode(token, options={"verify_signature": False})
    except _jwt.PyJWTError:
        return None
    if peek.get("is_demo") is not True:
        return None
    try:
        payload = _jwt.decode(
            token,
            settings.demo_jwt_secret,
            algorithms=["HS256"],
            options={"require": ["exp", "iat"]},
        )
    except _jwt.PyJWTError:
        return None
    tenant_id = payload.get("tenant_id")
    if not tenant_id:
        return None
    return User(
        id=f"demo-{tenant_id}",
        email="demo@bsvibe.dev",
        active_tenant_id=str(tenant_id),
        tenants=[],
        is_service=False,
        is_demo=True,
    )


async def get_current_user(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings_dep),
    introspection_client: IntrospectionClient | None = Depends(get_introspection_client),
    introspection_cache: IntrospectionCache = Depends(get_introspection_cache),
) -> User:
    token = _extract_bearer(authorization)
    # Demo bypass — accept tokens issued by the demo backend's JWT secret
    # (separate from prod user_jwt_secret) so demo deployments do not need
    # a real OpenFGA model + synthetic user graph.
    demo_user = _try_demo_user(token, settings)
    if demo_user is not None:
        return demo_user
    try:
        try:
            payload = verify_user_jwt(token, settings)
        except AuthError:
            # PAT JWTs from the device-authorization grant are signed with
            # SERVICE_TOKEN_SIGNING_SECRET (not USER_JWT_SECRET), so they fail
            # `verify_user_jwt`. The `/oauth/introspect` endpoint accepts
            # them by jti — fall through when introspection is configured.
            # (The legacy ``bsv_sk_*`` opaque token dispatch was removed in
            # Tier 2 of the 2026-05 auth cleanup; introspection now serves
            # only the PAT JWT path.)
            if introspection_client is not None and _looks_like_jwt(token):
                return await verify_via_introspection(token, introspection_client, introspection_cache)
            raise
    except AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc
    return parse_user_token(payload)


CurrentUser = Annotated[User, Depends(get_current_user)]


# ---------------------------------------------------------------------------
# TenantScoped
# ---------------------------------------------------------------------------
def get_active_tenant_id(user: CurrentUser) -> str:
    if not user.active_tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="no active tenant in session",
        )
    return user.active_tenant_id


# ---------------------------------------------------------------------------
# require_permission
# ---------------------------------------------------------------------------
def require_permission(
    permission: str,
    *,
    resource_type: str | None = None,
    resource_id_param: str | None = None,
    principal_dep: Callable[..., Awaitable[User]] | None = None,
) -> Callable[..., Awaitable[None]]:
    """Build a dependency that calls OpenFGA `check(user, relation, object)`.

    Mapping
    -------
    The OpenFGA tuple key is built as:
        user      = "user:<user.id>"  (or "service:..." if is_service)
        relation  = tenant-scoped check  -> "<product>_<resource>_<action>"
                    (e.g. "bsgateway_routing_read") so each resource×action
                    is a distinct relation on the tenant type — per-resource
                    granularity, Tier 5.
                    instance-scoped check -> the bare *action* ("read"),
                    matching the plain read/write/delete relations on the
                    resource-instance types.
        object    = "<resource_type>:<resource_id>"
                    where ``resource_id`` is taken from ``request.path_params``
                    using ``resource_id_param``. If neither is set, the
                    `permission` is treated as a tenant-wide check
                    (object = "tenant:<active_tenant_id>").

    Permissive mode
    ---------------
    When ``settings.openfga_api_url`` is empty, OpenFGA is not deployed —
    the dep returns immediately (authenticated callers pass, the OpenFGA
    check is skipped). Production sets the env var and the same code path
    enforces ``check``.

    ``principal_dep``
    -----------------
    By default the principal is resolved via :func:`get_current_user`. Pass
    a custom ``principal_dep`` (e.g. ``combined_principal("bsage")``) for
    routes that must also accept a service JWT.

    Raises 403 on deny.
    """
    # Validate permission identifier eagerly so misconfigured routes fail at
    # import-time, not request-time.
    parts = permission.split(".")
    if len(parts) != 3:
        raise ValueError(
            f"require_permission: invalid permission {permission!r} (expected '<product>.<resource>.<action>')",
        )
    action = parts[2]
    # Tier 5: tenant-scoped relation encodes the full triple so per-resource
    # permissions are distinct OpenFGA relations on the tenant type.
    tenant_relation = "_".join(parts)
    resolve_principal = principal_dep or get_current_user

    async def _dep(
        request: Request,
        user: User = Depends(resolve_principal),
        settings: Settings = Depends(get_settings_dep),
        cache: PermissionCache = Depends(get_permission_cache),
        fga: FGAClientProtocol = Depends(get_openfga_client),
    ) -> None:
        # Demo sessions bypass OpenFGA — the demo backend has no user
        # graph and every demo principal is scoped to a single ephemeral
        # tenant whose data is, by design, public sandbox data.
        if user.is_demo:
            return
        # Permissive mode — OpenFGA not deployed. Authenticated caller passes;
        # the route's own tenant filtering is the effective gate until tuples
        # exist. See module docstring / Auth handoff 2026-05-15.
        if not settings.openfga_api_url:
            return
        principal = user.id if user.is_service else f"user:{user.id}"

        # Resolve object identifier + relation.
        if resource_type and resource_id_param:
            resource_id = request.path_params.get(resource_id_param)
            if not resource_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"missing path param {resource_id_param!r}",
                )
            object_ = f"{resource_type}:{resource_id}"
            relation = action
        else:
            if not user.active_tenant_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="no active tenant in session",
                )
            object_ = f"tenant:{user.active_tenant_id}"
            relation = tenant_relation

        # Lazy auto-provision: ensure the caller's role tuple exists on
        # the tenant before the check. This bridges the missing-platform-
        # tenant-provisioning gap (BSVibe writes no runtime tuples today —
        # the only authoritative source for "user X has role R on tenant
        # T" is ``app_metadata`` in the wrapped session JWT). Without this,
        # every ``require_permission`` route 403s the moment OpenFGA is
        # enabled in a deployment.
        #
        # No-op for service principals (their scope tokens are explicit)
        # and for users without a role claim (e.g. transitional state).
        # OpenFGA returns HTTP 400 when the tuple already exists — that's
        # the steady-state path; we swallow it as success.
        if (
            not user.is_service
            and user.app_metadata
            and user.active_tenant_id
        ):
            tuple_role = user.app_metadata.get("role")
            if isinstance(tuple_role, str) and tuple_role:
                try:
                    await fga.write_tuple(
                        principal,
                        tuple_role,
                        f"tenant:{user.active_tenant_id}",
                    )
                except OpenFGAError:
                    pass

        cached = await cache.get(principal, relation, object_)
        if cached is not None:
            allowed = cached
        else:
            allowed = await fga.check(principal, relation, object_)
            await cache.set(principal, relation, object_, allowed)

        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"permission denied: {permission}",
            )

    return _dep


# ---------------------------------------------------------------------------
# require_admin — role-gated guard
# ---------------------------------------------------------------------------
def require_admin(
    *,
    principal_dep: Callable[..., Awaitable[User]] | None = None,
) -> Callable[..., Awaitable[None]]:
    """Build a dependency that asserts the caller is a tenant admin.

    Checks ``app_metadata.role`` (Supabase claim, lifted onto ``User`` by
    :func:`parse_user_token`) — ``owner`` or ``admin`` pass, anything else
    403s. Unlike :func:`require_permission`, this is a *real* enforced check
    in production today (the role claim rides in the JWT — no OpenFGA
    dependency), so it is the right gate for mutations / admin config.

    Demo and service principals pass: demo sessions are sandboxed, and a
    verified service JWT scoped to the product audience is an already-
    authorized internal caller (auth-app constrains it via the OAuth
    client's ``allowed_audiences`` / ``allowed_scopes``).

    Pass a custom ``principal_dep`` (e.g. ``combined_principal("bsage")``)
    for routes that also accept a service JWT.
    """
    resolve_principal = principal_dep or get_current_user

    async def _dep(user: User = Depends(resolve_principal)) -> None:
        if user.is_demo or user.is_service:
            return
        role = (user.app_metadata or {}).get("role")
        if role not in ("owner", "admin"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="admin role required",
            )

    return _dep


# ---------------------------------------------------------------------------
# Service-to-service auth
# ---------------------------------------------------------------------------
class ServiceKey(ServiceTokenPayload):
    """Verified service-JWT payload (alias for clarity in route signatures)."""


class ServiceKeyAuth:
    """Dep that *only* accepts service JWTs scoped to ``audience``.

    Use for internal endpoints that must never be reachable by a user session
    cookie / SPA token.
    """

    def __init__(self, audience: ServiceAudience) -> None:
        self.audience = audience

    async def __call__(
        self,
        creds: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
        settings: Settings = Depends(get_settings_dep),
    ) -> ServiceKey:
        if creds is None or creds.scheme.lower() != "bearer" or not creds.credentials:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="missing service token",
            )
        try:
            payload = verify_service_jwt(creds.credentials, settings, self.audience)
        except AuthError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=str(exc),
            ) from exc
        return ServiceKey(**payload.model_dump())


def combined_principal(
    service_audience: ServiceAudience,
) -> Callable[..., Awaitable[User]]:
    """Build a dependency that resolves a principal from a service JWT
    (``aud == service_audience``) **or** falls through to the standard
    user / PAT / opaque dispatch (:func:`get_current_user`).

    For routes that must accept both an end-user session and an internal
    service caller on the *same* path — e.g. BSage knowledge/vault routes
    that BSNexus reads service-to-service. This is the standard library
    primitive; products should not hand-roll their own combined resolver.

    Resolution order:
      1. ``aud=<service_audience>`` service JWT → ``User(is_service=True)``.
      2. anything else → :func:`get_current_user` (demo / opaque / user JWT
         / PAT-JWT-introspection).
    """

    async def _dep(
        request: Request,
        creds: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
        settings: Settings = Depends(get_settings_dep),
        introspection_client: IntrospectionClient | None = Depends(get_introspection_client),
        introspection_cache: IntrospectionCache = Depends(get_introspection_cache),
    ) -> User:
        # Try the service-JWT path first. A token that is not a service JWT
        # (wrong signing secret / wrong aud) raises AuthError — fall through
        # to the user dispatch rather than 401, so user sessions still work.
        if (
            creds is not None
            and creds.scheme.lower() == "bearer"
            and creds.credentials
            and settings.service_token_signing_secret
        ):
            try:
                payload = verify_service_jwt(creds.credentials, settings, service_audience)
            except AuthError:
                pass
            else:
                return User(
                    id=payload.sub,
                    email=None,
                    active_tenant_id=payload.tenant_id,
                    tenants=[],
                    is_service=True,
                )
        return await get_current_user(
            authorization=request.headers.get("Authorization"),
            settings=settings,
            introspection_client=introspection_client,
            introspection_cache=introspection_cache,
        )

    return _dep


# ---------------------------------------------------------------------------
# require_scope — opaque-token scope guard
# ---------------------------------------------------------------------------
def _scope_grants(user_scopes: list[str], required: str) -> bool:
    """Check whether ``user_scopes`` grant ``required``.

    Rules:
    - exact match.
    - prefix wildcard: ``"gateway:*"`` grants ``"gateway:models:write"``.
    """
    for granted in user_scopes:
        if granted == required:
            return True
        if granted.endswith(":*") and required.startswith(granted[:-1]):
            return True
    return False


def require_scope(required: str) -> Callable[..., Awaitable[None]]:
    """Build a dependency that asserts ``required`` is in the user's scope.

    Raises 403 on miss. Designed for opaque-token flows where the OpenFGA
    model is bypassed; for tuple-based checks use ``require_permission``.
    """
    if not required:
        raise ValueError("require_scope: required scope must not be empty")

    async def _dep(user: User = Depends(get_current_user)) -> None:
        if not _scope_grants(user.scope, required):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"missing required scope: {required}",
            )

    return _dep
