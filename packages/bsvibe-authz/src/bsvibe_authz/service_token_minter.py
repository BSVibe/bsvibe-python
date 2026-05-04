"""OAuth2 client_credentials minter for cross-product service-to-service calls.

A backend (e.g. BSGateway → BSSupervisor) holds a long-lived ``client_id`` /
``client_secret`` issued out of ``oauth_clients``. At call time it exchanges
those credentials at ``POST /api/oauth/token`` for a short-lived service JWT
and caches the result until expiry minus a safety margin.

Replaces the bootstrap pattern where each backend carried a 1-hour Supabase
admin access_token rotated by a launchd timer.

Per-(audience, scope) instance — keeps cache invariants simple: the cached
JWT's claims always match the minter's configuration.
"""

from __future__ import annotations

import asyncio
import base64
import re
import time
from collections.abc import Iterable

import httpx
import structlog

from .types import SERVICE_AUDIENCES

logger = structlog.get_logger(__name__)

_SCOPE_PATTERN = re.compile(r"^[a-z][a-z0-9-]*\.[a-z][a-z0-9-]*$")


class ServiceTokenMinterError(RuntimeError):
    """Failed to mint a service token (auth-server error / network / config)."""


class ServiceTokenMinter:
    """Mint and cache one service JWT for a fixed (audience, scope) pair.

    Construction validates audience and scope eagerly so config errors fail
    at startup, not at the first cross-product call.
    """

    def __init__(
        self,
        *,
        auth_url: str,
        client_id: str,
        client_secret: str,
        audience: str,
        scope: Iterable[str],
        timeout_s: float = 10.0,
        safety_margin_s: int = 60,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if audience not in SERVICE_AUDIENCES:
            raise ValueError(f"audience must be one of {sorted(SERVICE_AUDIENCES)}, got {audience!r}")
        scopes = list(scope)
        if not scopes:
            raise ValueError("scope must not be empty")
        prefix = f"{audience}."
        for s in scopes:
            if not _SCOPE_PATTERN.match(s):
                raise ValueError(f"invalid scope identifier: {s!r}")
            if not s.startswith(prefix):
                raise ValueError(f"scope {s!r} does not match audience {audience!r}")

        if not client_id or not client_secret:
            raise ValueError("client_id and client_secret must be non-empty")

        self._auth_url = auth_url.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        self._audience = audience
        self._scope = scopes
        self._timeout_s = timeout_s
        self._safety_margin_s = max(0, int(safety_margin_s))
        self._transport = transport

        self._cached_token: str | None = None
        self._cached_exp: int = 0
        self._lock = asyncio.Lock()

    async def get_token(self) -> str:
        """Return a non-expired service JWT, minting on first call / after expiry."""
        if self._is_cache_fresh():
            assert self._cached_token is not None
            return self._cached_token

        async with self._lock:
            if self._is_cache_fresh():
                assert self._cached_token is not None
                return self._cached_token
            await self._mint()
            assert self._cached_token is not None
            return self._cached_token

    def invalidate(self) -> None:
        """Drop the cached token so the next ``get_token`` mints fresh.

        Callers should invoke this on receiving a 401 from a downstream
        service; the next mint may pick up a new signing key after rotation.
        """
        self._cached_token = None
        self._cached_exp = 0

    def _is_cache_fresh(self) -> bool:
        if self._cached_token is None:
            return False
        return int(time.time()) < (self._cached_exp - self._safety_margin_s)

    def _basic_auth_header(self) -> str:
        raw = f"{self._client_id}:{self._client_secret}".encode()
        return "Basic " + base64.b64encode(raw).decode()

    async def _mint(self) -> None:
        url = f"{self._auth_url}/api/oauth/token"
        body = {
            "grant_type": "client_credentials",
            "audience": self._audience,
            "scope": " ".join(self._scope),
        }
        headers = {
            "Authorization": self._basic_auth_header(),
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        }

        client_kwargs: dict[str, object] = {"timeout": self._timeout_s}
        if self._transport is not None:
            client_kwargs["transport"] = self._transport

        try:
            async with httpx.AsyncClient(**client_kwargs) as cli:  # type: ignore[arg-type]
                resp = await cli.post(url=url, data=body, headers=headers)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as exc:
            logger.error(
                "service_token_mint_http_error",
                status=exc.response.status_code,
                audience=self._audience,
                client_id=self._client_id,
            )
            raise ServiceTokenMinterError(f"BSVibe-Auth /api/oauth/token returned {exc.response.status_code}") from exc
        except (httpx.RequestError, ValueError) as exc:
            logger.error(
                "service_token_mint_failed",
                error=str(exc),
                client_id=self._client_id,
            )
            raise ServiceTokenMinterError(f"failed to mint service token: {exc}") from exc

        access_token = data.get("access_token")
        expires_in = data.get("expires_in")
        if not isinstance(access_token, str) or not access_token:
            raise ServiceTokenMinterError("malformed /api/oauth/token response: missing access_token")
        if not isinstance(expires_in, int) or expires_in <= 0:
            raise ServiceTokenMinterError("malformed /api/oauth/token response: missing or invalid expires_in")

        self._cached_token = access_token
        self._cached_exp = int(time.time()) + expires_in
        logger.info(
            "service_token_minted",
            audience=self._audience,
            client_id=self._client_id,
            expires_in=expires_in,
        )
