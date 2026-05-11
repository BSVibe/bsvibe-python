"""OAuth 2.0 Device Authorization Grant client for ``auth.bsvibe.dev``.

The device flow is the BSVibe CLI's bootstrap path: a fresh user runs
``<cli> login`` and the CLI prints a short user code + verification URL,
the user authenticates in their browser, and meanwhile the CLI polls
``/oauth/device/token`` until the server returns a fresh access /
refresh token pair.

Wire format — RFC 8628 §3.5 standard polling responses:

* ``200`` + ``{access_token, ...}``   → flow complete (success).
* ``400`` + ``{"error": "authorization_pending"}`` → user not yet approved,
                                                    keep polling.
* ``400`` + ``{"error": "slow_down"}`` → bump interval by 5s, keep polling.
* ``400`` + ``{"error": "access_denied"}`` / ``"expired_token"`` /
  ``"invalid_grant"``                 → terminal :class:`DeviceFlowError`.

If the wall clock exceeds :attr:`DeviceCode.expires_in`, the poller
raises :class:`DeviceFlowTimeoutError` instead of polling forever.

Built on :class:`bsvibe_core.http.HttpClientBase` so retry, structured
logging, and credential redaction are inherited.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx
import structlog

from bsvibe_core.http import HttpClientBase

logger = structlog.get_logger(__name__)

# RFC 8628 §3.5 — transient errors the client must tolerate and re-poll on.
_PENDING_ERRORS: frozenset[str] = frozenset({"authorization_pending", "slow_down"})
_SLOW_DOWN_BUMP_S: float = 5.0


class DeviceFlowError(Exception):
    """Raised on any non-recoverable device-flow response."""


class DeviceFlowTimeoutError(DeviceFlowError):
    """Polling exceeded :attr:`DeviceCode.expires_in`."""


@dataclass(frozen=True)
class DeviceCode:
    """Server response to :meth:`DeviceFlowClient.request_code`."""

    device_code: str
    user_code: str
    verification_uri: str
    expires_in: int
    interval: int


@dataclass(frozen=True)
class DeviceTokenGrant:
    """Token pair returned once the user approves the device."""

    access_token: str
    refresh_token: str | None = None
    expires_in: int | None = None
    token_type: str = "Bearer"


_AsyncSleep = Callable[[float], Awaitable[None]]


class DeviceFlowClient(HttpClientBase):
    """Client for ``/oauth/device/code`` and ``/oauth/device/token``."""

    def __init__(
        self,
        base_url: str,
        *,
        client_id: str,
        http: httpx.AsyncClient | None = None,
        timeout_s: float = 10.0,
        retries: int = 1,
        code_path: str = "/oauth/device/code",
        token_path: str = "/oauth/device/token",
    ) -> None:
        super().__init__(base_url, http=http, timeout_s=timeout_s, retries=retries)
        self._client_id = client_id
        self._code_path = code_path
        self._token_path = token_path
        self._monotonic: Callable[[], float] = time.monotonic

    async def request_code(
        self,
        *,
        scope: str | None = None,
        audience: str | None = None,
    ) -> DeviceCode:
        """Request a fresh device + user code pair from the auth server.

        ``audience`` is a BSVibe extension carried verbatim in the JSON
        body so a single PAT can be scoped to multiple downstream
        products (e.g. ``"gateway,sage,nexus,supervisor"``).
        """
        body: dict[str, Any] = {"client_id": self._client_id}
        if scope:
            body["scope"] = scope
        if audience:
            body["audience"] = audience
        resp = await self.post(self._code_path, json=body)
        if resp.status_code >= 400:
            raise DeviceFlowError(f"device_code request failed: {resp.status_code} {_error_msg(resp)}")
        data = resp.json()
        return DeviceCode(
            device_code=data["device_code"],
            user_code=data["user_code"],
            verification_uri=data["verification_uri"],
            expires_in=int(data.get("expires_in", 600)),
            interval=int(data.get("interval", 5)),
        )

    async def poll_token(
        self,
        code: DeviceCode,
        *,
        sleep: _AsyncSleep,
    ) -> DeviceTokenGrant:
        """Poll the token endpoint until approval, denial, or expiry.

        Polls immediately (a freshly authorized session may already be
        ready) and sleeps ``interval`` seconds between attempts. Treats
        the wire per RFC 8628 §3.5 — ``200`` carries the grant,
        ``400 {"error": "authorization_pending"}`` means keep polling,
        ``400 {"error": "slow_down"}`` means keep polling at a bumped
        interval, any other 4xx is terminal. The deadline is checked
        after each poll so that an in-flight request returning the grant
        near expiry still succeeds.
        """
        interval = float(code.interval)
        start = self._monotonic()
        deadline = start + float(code.expires_in)

        while True:
            resp = await self.post(
                self._token_path,
                json={
                    "client_id": self._client_id,
                    "device_code": code.device_code,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                },
            )

            if resp.status_code < 400:
                payload = resp.json()
                access = payload.get("access_token")
                if not access:
                    raise DeviceFlowError(f"device_token returned 2xx without access_token: {payload!r}")
                return DeviceTokenGrant(
                    access_token=access,
                    refresh_token=payload.get("refresh_token"),
                    expires_in=payload.get("expires_in"),
                    token_type=payload.get("token_type", "Bearer"),
                )

            # 4xx/5xx — inspect RFC 8628 error code.
            error_code = _error_code(resp)
            if error_code not in _PENDING_ERRORS:
                raise DeviceFlowError(
                    f"device_token request failed: {resp.status_code} {error_code or _error_msg(resp)}"
                )
            if error_code == "slow_down":
                interval += _SLOW_DOWN_BUMP_S
                logger.info("device_flow_slow_down", new_interval_s=interval)

            if self._monotonic() >= deadline:
                raise DeviceFlowTimeoutError(f"device authorization expired after {code.expires_in}s")
            await sleep(interval)


def _error_msg(resp: httpx.Response) -> str:
    """Extract a short error description from a 4xx/5xx response."""
    try:
        data = resp.json()
    except ValueError:
        return resp.text[:200]
    if isinstance(data, dict):
        return str(data.get("error") or data.get("message") or data)
    return str(data)


def _error_code(resp: httpx.Response) -> str | None:
    """Return the RFC 8628 ``error`` field (e.g. ``"authorization_pending"``)
    from a 4xx response body, or ``None`` if the body isn't a JSON object with
    an ``error`` string."""
    try:
        data = resp.json()
    except ValueError:
        return None
    if isinstance(data, dict):
        err = data.get("error")
        return err if isinstance(err, str) else None
    return None


__all__ = [
    "DeviceCode",
    "DeviceTokenGrant",
    "DeviceFlowClient",
    "DeviceFlowError",
    "DeviceFlowTimeoutError",
]
