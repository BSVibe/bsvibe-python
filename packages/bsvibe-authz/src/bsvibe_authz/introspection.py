"""RFC 7662 OAuth2 Token Introspection client.

Used by resource servers (BSGateway, BSAge, …) to validate opaque
``bsv_sk_*`` tokens against BSVibe-Auth's introspection endpoint. Returns
``IntrospectionResponse(active=False)`` on any HTTP / network / parse error
so callers can treat unreachable auth-server as "token rejected" without
leaking the failure mode through the auth path.

Token values are NEVER logged.
"""

from __future__ import annotations

import base64

import httpx
import structlog
from pydantic import ValidationError

from .types import IntrospectionResponse

logger = structlog.get_logger(__name__)


class IntrospectionClient:
    """RFC 7662 introspection client.

    Pass a shared ``httpx.AsyncClient`` for connection-pool reuse in
    production; if omitted, a one-shot client is created per call.
    """

    def __init__(
        self,
        introspection_url: str,
        client_id: str,
        client_secret: str,
        http: httpx.AsyncClient | None = None,
        timeout_s: float = 5.0,
    ) -> None:
        if not introspection_url:
            raise ValueError("introspection_url must not be empty")
        if not client_id or not client_secret:
            raise ValueError("client_id and client_secret must be non-empty")

        self._url = introspection_url
        self._client_id = client_id
        self._client_secret = client_secret
        self._http = http
        self._timeout_s = timeout_s

    def _basic_auth_header(self) -> str:
        raw = f"{self._client_id}:{self._client_secret}".encode()
        return "Basic " + base64.b64encode(raw).decode()

    async def introspect(self, token: str) -> IntrospectionResponse:
        body = {"token": token, "token_type_hint": "access_token"}
        headers = {
            "Authorization": self._basic_auth_header(),
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        }

        try:
            if self._http is not None:
                resp = await self._http.post(self._url, data=body, headers=headers, timeout=self._timeout_s)
            else:
                async with httpx.AsyncClient(timeout=self._timeout_s) as cli:
                    resp = await cli.post(self._url, data=body, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "introspection_http_error",
                status=exc.response.status_code,
                url=self._url,
            )
            return IntrospectionResponse(active=False)
        except (httpx.RequestError, ValueError) as exc:
            logger.warning(
                "introspection_request_failed",
                error=str(exc),
                url=self._url,
            )
            return IntrospectionResponse(active=False)

        try:
            return IntrospectionResponse.model_validate(data)
        except ValidationError as exc:
            logger.warning(
                "introspection_parse_failed",
                error=str(exc),
                url=self._url,
            )
            return IntrospectionResponse(active=False)
