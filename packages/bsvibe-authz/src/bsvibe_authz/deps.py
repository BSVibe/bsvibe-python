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

from .auth import AuthError, parse_user_token, verify_service_jwt, verify_user_jwt
from .cache import PermissionCache
from .client import OpenFGAClient
from .settings import Settings, get_settings
from .types import ServiceAudience, ServiceTokenPayload, User

_bearer_scheme = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------------------
# Lockin §3 decision #15 — BaseServiceClient is a Protocol (structural typing).
# Same shape applies here so callers can swap in fakes without inheritance.
# ---------------------------------------------------------------------------
class FGAClientProtocol(Protocol):
    async def check(self, user: str, relation: str, object_: str) -> bool: ...
    async def list_objects(self, user: str, relation: str, type_: str) -> list[str]: ...


# ---------------------------------------------------------------------------
# Injection points
# ---------------------------------------------------------------------------
def get_settings_dep() -> Settings:
    """Override-friendly Settings provider."""
    return get_settings()


_fga_client_singleton: OpenFGAClient | None = None
_cache_singleton: PermissionCache | None = None


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


def reset_singletons() -> None:
    """Used by tests — reset the process-wide client/cache."""
    global _fga_client_singleton, _cache_singleton
    _fga_client_singleton = None
    _cache_singleton = None


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
async def get_current_user(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings_dep),
) -> User:
    token = _extract_bearer(authorization)
    try:
        payload = verify_user_jwt(token, settings)
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
) -> Callable[..., Awaitable[None]]:
    """Build a dependency that calls OpenFGA `check(user, relation, object)`.

    Mapping
    -------
    The OpenFGA tuple key is built as:
        user      = "user:<user.id>"  (or "service:..." if is_service)
        relation  = the *action* portion of `permission` (e.g. "read")
        object    = "<resource_type>:<resource_id>"
                    where ``resource_id`` is taken from ``request.path_params``
                    using ``resource_id_param``. If neither is set, the
                    `permission` is treated as a tenant-wide check
                    (object = "tenant:<active_tenant_id>").

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

    async def _dep(
        request: Request,
        user: User = Depends(get_current_user),
        cache: PermissionCache = Depends(get_permission_cache),
        fga: FGAClientProtocol = Depends(get_openfga_client),
    ) -> None:
        principal = user.id if user.is_service else f"user:{user.id}"

        # Resolve object identifier.
        if resource_type and resource_id_param:
            resource_id = request.path_params.get(resource_id_param)
            if not resource_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"missing path param {resource_id_param!r}",
                )
            object_ = f"{resource_type}:{resource_id}"
        else:
            if not user.active_tenant_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="no active tenant in session",
                )
            object_ = f"tenant:{user.active_tenant_id}"

        cached = await cache.get(principal, action, object_)
        if cached is not None:
            allowed = cached
        else:
            allowed = await fga.check(principal, action, object_)
            await cache.set(principal, action, object_, allowed)

        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"permission denied: {permission}",
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
