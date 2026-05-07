"""Pydantic models exposed to package consumers.

`ServiceTokenPayload` matches the BSVibe-Auth PR #3 contract verbatim
(see ``BSVibe-Auth/phase0/auth-app/api/_lib/service-token.ts:51-62``).
Drift here would break service-to-service auth across all 4 products.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ServiceAudience = Literal["bsage", "bsgateway", "bsupervisor", "bsnexus"]
SERVICE_AUDIENCES: frozenset[str] = frozenset(("bsage", "bsgateway", "bsupervisor", "bsnexus"))
TenantRole = Literal["owner", "admin", "member", "viewer"]
TenantPlan = Literal["free", "pro", "team", "enterprise"]
TenantType = Literal["personal", "org"]

PERMISSION_PATTERN = re.compile(r"^[a-z][a-z0-9-]*\.[a-z][a-z0-9-]*\.[a-z][a-z0-9-]*$")
SCOPE_PATTERN = re.compile(r"^[a-z][a-z0-9-]*\.[a-z][a-z0-9-]*$")


@dataclass(frozen=True, slots=True)
class Permission:
    """`<product>.<resource>.<action>` permission identifier."""

    product: str
    resource: str
    action: str

    def __str__(self) -> str:
        return f"{self.product}.{self.resource}.{self.action}"

    @classmethod
    def parse(cls, value: str) -> "Permission":
        if not PERMISSION_PATTERN.match(value):
            raise ValueError(
                f"invalid permission identifier: {value!r} (expected '<product>.<resource>.<action>')",
            )
        product, resource, action = value.split(".")
        return cls(product=product, resource=resource, action=action)


class TenantMembership(BaseModel):
    """One row of the user's tenant memberships, denormalised onto User."""

    model_config = ConfigDict(extra="ignore")

    id: str
    role: TenantRole
    plan: TenantPlan = "free"
    type: TenantType = "personal"
    name: str | None = None


class User(BaseModel):
    """Authenticated principal — either a human or a service identity."""

    model_config = ConfigDict(extra="ignore")

    id: str
    email: str | None = None
    active_tenant_id: str | None = None
    tenants: list[TenantMembership] = Field(default_factory=list)
    is_service: bool = False
    # Demo-mode session: token is signed by demo_jwt_secret (not the
    # prod user_jwt_secret), permissions short-circuit to allow.
    is_demo: bool = False
    scope: list[str] = Field(default_factory=list)

    def role_in(self, tenant_id: str) -> TenantRole | None:
        for t in self.tenants:
            if t.id == tenant_id:
                return t.role
        return None

    def has_tenant(self, tenant_id: str) -> bool:
        return any(t.id == tenant_id for t in self.tenants)


class IntrospectionResponse(BaseModel):
    """RFC 7662 Token Introspection response.

    An inactive token MAY consist solely of ``{"active": false}`` per §2.2,
    so all other fields are optional.
    """

    model_config = ConfigDict(extra="ignore")

    active: bool
    sub: str | None = None
    tenant: str | None = None
    aud: list[str] | None = None
    scope: list[str] | None = None
    exp: int | None = None
    client_id: str | None = None
    username: str | None = None


class ServiceTokenPayload(BaseModel):
    """Decoded service JWT payload.

    Mirrors `ServiceTokenPayload` from BSVibe-Auth PR #3:
    iss, sub, aud (one of bsage/bsgateway/bsupervisor/bsnexus),
    scope (space-delimited string), iat, exp, token_type="service",
    optional tenant_id.
    """

    model_config = ConfigDict(extra="forbid")

    iss: str
    sub: str
    aud: ServiceAudience
    scope: str
    iat: int
    exp: int
    token_type: Literal["service"]
    tenant_id: str | None = None

    @field_validator("scope")
    @classmethod
    def _validate_scope_format(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("scope must not be empty")
        for s in value.split():
            if not SCOPE_PATTERN.match(s):
                raise ValueError(f"invalid scope identifier: {s!r}")
        return value

    @property
    def scopes(self) -> list[str]:
        return self.scope.split()

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes
