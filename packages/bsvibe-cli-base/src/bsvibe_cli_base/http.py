"""CLI-side HTTP client with transparent token refresh.

:class:`CliHttpClient` extends :class:`bsvibe_core.http.HttpClientBase`
with one extra behaviour: when an outbound request returns 401 and a
refresh token is available, the client posts the refresh token to
``/oauth/token`` (RFC 6749 §6 ``refresh_token`` grant), updates its own
``Authorization`` header, and replays the original request **exactly
once**. The replay's status code (200, 401, anything) is returned to
the caller without further intervention so subcommands stay simple:

.. code-block:: python

    resp = await client.get("/items")
    if resp.status_code == 401:
        # refresh failed AND replay was still 401 — login expired.
        ...

If the refresh endpoint itself returns 4xx/5xx, the client raises
:class:`CliHttpAuthError` so the calling CLI layer can print a friendly
"please run `<cli> login` again" message instead of a stack trace.

The optional ``on_token_refreshed`` callback fires with the new
:class:`DeviceTokenGrant` so the CLI can persist the rotated refresh
token (and the access token) to keyring before the next request.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import structlog

from bsvibe_core.http import HttpClientBase

from bsvibe_cli_base.device_flow import DeviceTokenGrant

logger = structlog.get_logger(__name__)


class CliHttpAuthError(Exception):
    """Raised when token refresh fails on a 401."""


class CliHttpClient(HttpClientBase):
    """HTTP client used by every BSVibe product CLI."""

    def __init__(
        self,
        base_url: str,
        *,
        token: str | None = None,
        refresh_token: str | None = None,
        client_id: str | None = None,
        token_endpoint: str = "/oauth/token",
        http: httpx.AsyncClient | None = None,
        on_token_refreshed: Callable[[DeviceTokenGrant], None] | None = None,
        timeout_s: float = 5.0,
        retries: int = 2,
        headers: dict[str, str] | None = None,
    ) -> None:
        merged: dict[str, str] = dict(headers) if headers else {}
        if token:
            merged["Authorization"] = f"Bearer {token}"
        super().__init__(
            base_url,
            http=http,
            timeout_s=timeout_s,
            retries=retries,
            headers=merged,
        )
        self._token = token
        self._refresh_token = refresh_token
        self._client_id = client_id
        self._token_endpoint = token_endpoint
        self._on_token_refreshed = on_token_refreshed

    @property
    def token(self) -> str | None:
        return self._token

    @property
    def refresh_token(self) -> str | None:
        return self._refresh_token

    async def request(
        self,
        method: str,
        path: str,
        **kw: Any,
    ) -> httpx.Response:
        """Issue a request; on 401 try one refresh + replay."""
        resp = await super().request(method, path, **kw)
        if resp.status_code != 401 or not self._refresh_token:
            return resp

        # Drain the body so the connection can be reused.
        await resp.aread()

        grant = await self._refresh()
        self._apply_grant(grant)
        return await super().request(method, path, **kw)

    async def _refresh(self) -> DeviceTokenGrant:
        body: dict[str, Any] = {
            "grant_type": "refresh_token",
            "refresh_token": self._refresh_token,
        }
        if self._client_id:
            body["client_id"] = self._client_id

        # Refresh with no Authorization header so a stale token doesn't
        # confuse the auth server.
        resp = await super().request(
            "POST",
            self._token_endpoint,
            json=body,
            headers={"Authorization": ""},
        )
        if resp.status_code >= 400:
            raise CliHttpAuthError(f"token refresh failed: {resp.status_code} {_error_msg(resp)}")
        payload = resp.json()
        return DeviceTokenGrant(
            access_token=payload["access_token"],
            refresh_token=payload.get("refresh_token"),
            expires_in=payload.get("expires_in"),
            token_type=payload.get("token_type", "Bearer"),
        )

    def _apply_grant(self, grant: DeviceTokenGrant) -> None:
        self._token = grant.access_token
        if grant.refresh_token:
            self._refresh_token = grant.refresh_token
        self._headers["Authorization"] = f"Bearer {grant.access_token}"
        if self._on_token_refreshed is not None:
            self._on_token_refreshed(grant)


def _error_msg(resp: httpx.Response) -> str:
    try:
        data = resp.json()
    except ValueError:
        return resp.text[:200]
    if isinstance(data, dict):
        return str(data.get("error") or data.get("message") or data)
    return str(data)


__all__ = ["CliHttpClient", "CliHttpAuthError"]
