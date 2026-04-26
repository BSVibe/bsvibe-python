"""bsvibe-authz — shared authorization library for BSVibe Python services.

Public API surface (Phase 0 P0.4):

- ``CurrentUser`` / ``get_current_user`` — extract authenticated User from JWT
- ``require_permission`` — FastAPI dep factory that calls OpenFGA
- ``ServiceKeyAuth`` / ``ServiceKey`` — service-to-service JWT verification
- ``get_active_tenant_id`` — TenantScoped helper
- ``OpenFGAClient`` — async wrapper for OpenFGA HTTP API
- ``PermissionCache`` — 30s TTL decision cache
- ``Settings`` / ``get_settings`` — pydantic-settings configuration
- ``User`` / ``Permission`` / ``ServiceTokenPayload`` / ``TenantMembership``
- ``verify_user_jwt`` / ``verify_service_jwt`` / ``parse_user_token`` / ``AuthError``

Lock-in references
------------------
- Decision #15 (BaseServiceClient = Protocol): see ``deps.FGAClientProtocol``
- Decision #16 (service JWT audience-scoped + scope claim): see
  ``auth.verify_service_jwt`` and ``types.ServiceTokenPayload``
"""

from __future__ import annotations

from .auth import AuthError, parse_user_token, verify_service_jwt, verify_user_jwt
from .cache import PermissionCache
from .client import OpenFGAAuthError, OpenFGAClient, OpenFGAError
from .deps import (
    CurrentUser,
    FGAClientProtocol,
    ServiceKey,
    ServiceKeyAuth,
    get_active_tenant_id,
    get_current_user,
    get_openfga_client,
    get_permission_cache,
    get_settings_dep,
    require_permission,
    reset_singletons,
)
from .settings import Settings, get_settings, reset_settings_cache
from .types import (
    Permission,
    ServiceAudience,
    ServiceTokenPayload,
    TenantMembership,
    TenantPlan,
    TenantRole,
    TenantType,
    User,
)

__version__ = "0.1.0"

__all__ = [
    "AuthError",
    "CurrentUser",
    "FGAClientProtocol",
    "OpenFGAAuthError",
    "OpenFGAClient",
    "OpenFGAError",
    "Permission",
    "PermissionCache",
    "ServiceAudience",
    "ServiceKey",
    "ServiceKeyAuth",
    "ServiceTokenPayload",
    "Settings",
    "TenantMembership",
    "TenantPlan",
    "TenantRole",
    "TenantType",
    "User",
    "__version__",
    "get_active_tenant_id",
    "get_current_user",
    "get_openfga_client",
    "get_permission_cache",
    "get_settings",
    "get_settings_dep",
    "parse_user_token",
    "require_permission",
    "reset_settings_cache",
    "reset_singletons",
    "verify_service_jwt",
    "verify_user_jwt",
]
