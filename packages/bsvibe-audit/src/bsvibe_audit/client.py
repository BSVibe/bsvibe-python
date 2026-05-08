"""HTTP client for the BSVibe-Auth audit endpoint.

Wire contract:

* ``POST <auth_audit_url>`` with body ``{"events": [<payload>, ...]}``.
* Header ``X-Service-Token: <service_jwt>`` authenticates the caller.
* ``2xx`` — accepted (idempotent on ``event_id``).
* ``4xx`` — non-retryable, mark the row dead-letter.
* ``5xx`` / network error — retryable, schedule backoff.

The client owns no state beyond an ``httpx.AsyncClient``. The
:class:`OutboxRelay` runs ``send`` once per batch and translates failures
into outbox state transitions.

Built on :class:`bsvibe_core.http.HttpClientBase` for shared retry +
redacted-logging infrastructure. ``X-Service-Token`` is masked in logs.
"""

from __future__ import annotations

from typing import Any

import httpx
from bsvibe_core.http import HttpClientBase


class AuditDeliveryError(Exception):
    """Raised when a batch could not be delivered.

    ``retryable`` distinguishes transient failures (5xx, timeouts, DNS
    errors) from permanent ones (4xx). The relay uses this flag to
    decide between exponential backoff and dead-lettering.
    """

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


class AuditDeliveryResult:
    """Successful delivery summary returned to the relay."""

    __slots__ = ("accepted", "raw")

    def __init__(self, *, accepted: bool, raw: dict[str, Any] | None = None) -> None:
        self.accepted = accepted
        self.raw = raw or {}


class AuditClient(HttpClientBase):
    """Async client to ``POST /api/audit/events`` on BSVibe-Auth."""

    def __init__(
        self,
        *,
        audit_url: str,
        service_token: str,
        http: httpx.AsyncClient | None = None,
        timeout_s: float = 5.0,
    ) -> None:
        self.audit_url = audit_url
        self.service_token = service_token
        super().__init__(
            "",
            http=http,
            timeout_s=timeout_s,
            retries=0,
            headers={"X-Service-Token": service_token},
        )

    @classmethod
    def from_settings(
        cls,
        *,
        audit_url: str,
        service_token: str,
        timeout_s: float = 5.0,
    ) -> AuditClient:
        return cls(
            audit_url=audit_url,
            service_token=service_token,
            timeout_s=timeout_s,
        )

    async def send(self, payloads: list[dict[str, Any]]) -> AuditDeliveryResult:
        """Send one batch. Raises :class:`AuditDeliveryError` on failure."""

        if not payloads:
            return AuditDeliveryResult(accepted=True)

        body = {"events": payloads}

        try:
            response = await self.post(self.audit_url, json=body)
        except httpx.HTTPError as exc:
            raise AuditDeliveryError(f"network error: {exc!r}", retryable=True) from exc

        if 200 <= response.status_code < 300:
            data: dict[str, Any]
            try:
                data = response.json()
            except ValueError:
                data = {}
            return AuditDeliveryResult(accepted=True, raw=data)

        body_excerpt = response.text[:200]
        if response.status_code >= 500:
            raise AuditDeliveryError(
                f"audit endpoint {response.status_code}: {body_excerpt}",
                retryable=True,
            )
        raise AuditDeliveryError(
            f"audit endpoint {response.status_code}: {body_excerpt}",
            retryable=False,
        )


__all__ = [
    "AuditClient",
    "AuditDeliveryError",
    "AuditDeliveryResult",
]
