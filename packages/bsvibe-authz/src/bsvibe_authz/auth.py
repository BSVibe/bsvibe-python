"""JWT verification — user session JWTs and service-to-service JWTs.

Two distinct flows
------------------
1. **User JWT** (`verify_user_jwt`): tokens issued by BSVibe-Auth's session
   endpoint. Phase 0 dev uses HS256 with a shared secret; Phase 0.4-후속
   will swap to JWKS (RS256/ES256/EdDSA) — the verifier accepts a public
   key via settings to make that mechanical.
2. **Service JWT** (`verify_service_jwt`): tokens issued by BSVibe-Auth's
   `POST /api/service-tokens/issue` endpoint. Audience-scoped (Lockin §3
   decision #16) and `scope`-claim guarded. The decoded payload must satisfy
   the `ServiceTokenPayload` Pydantic model (mirrors BSVibe-Auth PR #3).
"""

from __future__ import annotations

import hashlib
import hmac
from typing import TYPE_CHECKING, Any

import jwt
import structlog

from .settings import Settings
from .types import ServiceAudience, ServiceTokenPayload, User

if TYPE_CHECKING:
    from .cache import IntrospectionCache
    from .introspection import IntrospectionClient

logger = structlog.get_logger(__name__)


class AuthError(Exception):
    """Authentication failed (invalid signature, expired, wrong audience, ...)."""


def _user_signing_key(settings: Settings) -> str:
    if settings.user_jwt_algorithm == "HS256":
        if not settings.user_jwt_secret:
            raise AuthError("user_jwt_secret not configured")
        return settings.user_jwt_secret
    # RS256/ES256/EdDSA — public key
    if not settings.user_jwt_public_key:
        raise AuthError("user_jwt_public_key not configured")
    return settings.user_jwt_public_key


def verify_user_jwt(token: str, settings: Settings) -> dict[str, Any]:
    """Verify a Supabase / BSVibe-Auth user session JWT, return decoded claims.

    Validates: signature, expiration (exp), audience, issuer (if configured).
    Raises `AuthError` on any failure.
    """
    options = {"require": ["exp", "iat", "sub"]}
    try:
        payload = jwt.decode(
            token,
            _user_signing_key(settings),
            algorithms=[settings.user_jwt_algorithm],
            audience=settings.user_jwt_audience,
            issuer=settings.user_jwt_issuer,
            options=options,
        )
    except jwt.PyJWTError as exc:
        logger.warning("user_jwt_invalid", error=str(exc))
        raise AuthError(f"user JWT verification failed: {exc}") from exc
    return payload


def verify_service_jwt(
    token: str,
    settings: Settings,
    expected_audience: ServiceAudience,
) -> ServiceTokenPayload:
    """Verify a service-to-service JWT for `expected_audience`.

    Strict checks (Lockin §3 #16 + Auth_Design §6.4):
    - audience matches `expected_audience`
    - `token_type == "service"`
    - all scopes in the token are prefixed with the audience
    - signature, exp, iat all valid
    """
    if not settings.service_token_signing_secret:
        raise AuthError("service_token_signing_secret not configured")

    try:
        raw = jwt.decode(
            token,
            settings.service_token_signing_secret,
            algorithms=["HS256"],
            audience=expected_audience,
            issuer=settings.service_token_issuer or settings.bsvibe_auth_url,
            options={"require": ["exp", "iat", "sub", "aud"]},
        )
    except jwt.PyJWTError as exc:
        logger.warning("service_jwt_invalid", error=str(exc))
        raise AuthError(f"service JWT verification failed: {exc}") from exc

    try:
        payload = ServiceTokenPayload(**raw)
    except (ValueError, TypeError) as exc:
        logger.warning("service_jwt_payload_invalid", error=str(exc))
        raise AuthError(f"service JWT payload invalid: {exc}") from exc

    if payload.aud != expected_audience:
        raise AuthError(
            f"service JWT audience mismatch: expected {expected_audience}, got {payload.aud}",
        )

    # Defense-in-depth: re-check scope-audience binding even though the issuer
    # already enforces it. A misconfigured issuer must never silently widen
    # privilege.
    audience_prefix = f"{payload.aud}."
    for scope in payload.scopes:
        if not scope.startswith(audience_prefix):
            raise AuthError(
                f"service JWT scope {scope!r} does not match audience {payload.aud!r}",
            )

    return payload


async def verify_opaque_token(
    token: str,
    client: IntrospectionClient,
    cache: IntrospectionCache,
) -> User:
    """Verify an opaque ``bsv_sk_*`` token via RFC 7662 introspection.

    Caches both active and inactive responses keyed by sha256(token) so that
    revoked tokens cannot stampede the auth server. Raises :class:`AuthError`
    if the token is inactive — error message MUST NOT contain the token.
    """
    token_sha256 = hashlib.sha256(token.encode()).hexdigest()
    response = await cache.get(token_sha256)
    if response is None:
        response = await client.introspect(token)
        await cache.set(token_sha256, response)

    if not response.active:
        logger.info("opaque_token_inactive", token_sha256=token_sha256)
        raise AuthError("opaque token is not active")

    return User(
        id=response.sub or "",
        active_tenant_id=response.tenant,
        scope=list(response.scope or []),
        is_service=False,
        email=None,
    )


def verify_bootstrap_token(token: str, settings: Settings) -> User:
    """Verify a ``bsv_admin_*`` bootstrap token via constant-time digest compare.

    Returns an admin :class:`User` (``id='bootstrap'``, ``scope=['*']``) on
    match. Raises :class:`AuthError` when the digest does not match or when
    ``settings.bootstrap_token_hash`` is empty (bootstrap path disabled).
    """
    expected = settings.bootstrap_token_hash
    if not expected:
        raise AuthError("bootstrap token path is not configured")

    actual = hashlib.sha256(token.encode()).hexdigest()
    if not hmac.compare_digest(actual, expected):
        logger.warning("bootstrap_token_mismatch")
        raise AuthError("bootstrap token does not match")

    logger.info("bootstrap_token_accepted")
    return User(
        id="bootstrap",
        scope=["*"],
        is_service=True,
        email=None,
    )


def parse_user_token(payload: dict[str, Any]) -> User:
    """Translate verified user-JWT claims into a `User`.

    The Phase 0 user JWT is intentionally thin (Auth_Design §4.1) — tenants
    list comes from a separate `/api/session` call, not from claims.
    """
    sub = payload.get("sub")
    if not isinstance(sub, str) or not sub:
        raise AuthError("user JWT missing sub")
    is_service = sub.startswith("service:")
    return User(
        id=sub,
        email=payload.get("email"),
        active_tenant_id=payload.get("active_tenant_id"),
        tenants=[],
        is_service=is_service,
    )
