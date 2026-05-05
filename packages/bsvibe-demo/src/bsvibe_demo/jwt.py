"""Demo session JWT — separate issuer from prod auth.bsvibe.dev.

The demo backend issues its own HS256-signed JWTs using DEMO_JWT_SECRET.
Prod tokens (BSVibe Auth) are NOT accepted by the demo backend, and demo
tokens are NOT accepted by prod (different secrets, different verification
paths). This separation eliminates accidental cross-environment token use.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from uuid import UUID

import jwt

DEMO_JWT_ALG = "HS256"


class DemoJWTError(Exception):
    """Demo JWT verification failed (invalid signature, expired, missing claim)."""


@dataclass(frozen=True)
class DemoClaims:
    """Decoded claims from a demo JWT."""

    tenant_id: UUID
    is_demo: bool
    exp: int
    iat: int


def mint_demo_jwt(tenant_id: UUID, *, secret: str, ttl_seconds: int = 7200) -> str:
    """Mint a demo session JWT for ``tenant_id``.

    The token carries ``tenant_id``, ``is_demo=True``, and standard
    ``exp``/``iat``. ``ttl_seconds`` defaults to 2h (matches the GC window).
    """
    now = int(time.time())
    payload = {
        "tenant_id": str(tenant_id),
        "is_demo": True,
        "iat": now,
        "exp": now + ttl_seconds,
    }
    return jwt.encode(payload, secret, algorithm=DEMO_JWT_ALG)


def decode_demo_jwt(token: str, *, secret: str) -> DemoClaims:
    """Decode and verify a demo JWT.

    Raises ``DemoJWTError`` if signature is invalid, token is expired, or
    ``is_demo`` claim is absent (prevents prod tokens from being accepted
    even if their signature happens to verify under the demo secret).
    """
    try:
        decoded = jwt.decode(token, secret, algorithms=[DEMO_JWT_ALG])
    except jwt.PyJWTError as e:
        raise DemoJWTError(f"Demo JWT verification failed: {e}") from e

    if not decoded.get("is_demo"):
        raise DemoJWTError("Token is not a demo session token (missing is_demo)")

    try:
        tenant_id = UUID(decoded["tenant_id"])
    except (KeyError, ValueError) as e:
        raise DemoJWTError("Token missing or invalid tenant_id claim") from e

    return DemoClaims(
        tenant_id=tenant_id,
        is_demo=True,
        exp=int(decoded["exp"]),
        iat=int(decoded["iat"]),
    )
