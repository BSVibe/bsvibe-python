"""Central alert dispatch client (D18).

The :class:`CentralDispatchClient` is a thin wrapper around
``POST <auth_url>/api/alerts/dispatch`` on BSVibe-Auth. Producers hand it
an :class:`bsvibe_audit.AuditEventBase`-shaped event (or a plain dict
matching that wire shape) and it returns the ``deliveries`` list the
auth service computed from the runtime ``alert_routes`` table.

Design contract (BSVibe_Audit_Design.md §11 D-Z2 + Shared_Library_Roadmap D18):

* The client is the only object every product instantiates for D18 fan-out.
* It does NOT itself know about Slack / Telegram / structlog — channel
  selection is delegated to the auth-app's runtime routing table.
* It MUST never raise on transport failure: callers wrap dispatch in a
  best-effort path so audit / outbox loops continue when BSVibe-Auth is
  briefly unavailable. Failures are logged via structlog (the built-in
  fallback in the broader bsvibe-alerts pipeline).
* Auth: ``Authorization: Bearer <service_jwt>`` with the ``alerts.dispatch``
  scope. The audience must match ``bsvibe-auth``.

Why a separate client (instead of bolting onto :class:`AlertClient`)?
``AlertClient`` runs *inside* a producer and decides which local channel
to call. ``CentralDispatchClient`` runs *between* a producer and the
single source of truth (the auth-app). The two coexist: a deployment
that sets ``BSVIBE_AUTH_ALERTS_URL`` swaps the local channel router for
a thin remote call (see :class:`bsvibe_alerts.router.CentralAlertRouter`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx
import structlog

_DEFAULT_TIMEOUT_S = 5.0


class CentralDispatchError(Exception):
    """Raised when the dispatch endpoint returns a non-retryable error.

    ``retryable`` separates transient (5xx, network) from permanent
    failures (4xx). Producers can ignore ``retryable=True`` and let the
    next dispatch retry; ``retryable=False`` indicates a contract
    violation (bad event shape, missing scope) that needs operator
    intervention.
    """

    def __init__(self, message: str, *, retryable: bool, status: int | None = None) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status = status


@dataclass(frozen=True)
class DeliveryDescriptor:
    """One matched route the dispatch endpoint produced.

    The auth service does not itself fan out to Slack/Telegram in Phase 0
    — it returns the matched routes so the caller (or a follow-up
    fan-out worker) can dispatch. Each descriptor encodes the same shape
    as :class:`bsvibe_alerts.routing` channel names plus the runtime-
    editable ``config`` blob.
    """

    rule_id: str
    name: str
    channel: str
    severity: str
    config: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True

    @classmethod
    def from_payload(cls, raw: dict[str, Any]) -> DeliveryDescriptor:
        return cls(
            rule_id=str(raw.get("rule_id", "")),
            name=str(raw.get("name", "")),
            channel=str(raw.get("channel", "")),
            severity=str(raw.get("severity", "")),
            config=dict(raw.get("config") or {}),
            enabled=bool(raw.get("enabled", True)),
        )


@dataclass(frozen=True)
class DispatchResult:
    """Aggregate response from POST /api/alerts/dispatch."""

    event_id: str
    event_type: str
    tenant_id: str
    severity: str
    matched_rules: int
    deliveries: list[DeliveryDescriptor]

    @classmethod
    def from_payload(cls, raw: dict[str, Any]) -> DispatchResult:
        deliveries_raw = raw.get("deliveries") or []
        return cls(
            event_id=str(raw.get("event_id", "")),
            event_type=str(raw.get("event_type", "")),
            tenant_id=str(raw.get("tenant_id", "")),
            severity=str(raw.get("severity", "")),
            matched_rules=int(raw.get("matched_rules", 0)),
            deliveries=[DeliveryDescriptor.from_payload(d) for d in deliveries_raw],
        )


def _event_to_payload(event: Any) -> dict[str, Any]:
    """Coerce an event (pydantic model, dataclass, dict) into the wire dict.

    Accepts:
    * :class:`pydantic.BaseModel` (e.g. :class:`bsvibe_audit.AuditEventBase`)
      via ``model_dump(mode="json")`` — preserves UUID / datetime ISO
      formatting the dispatch handler expects.
    * Plain ``dict[str, Any]`` matching the AuditEventBase schema.
    """

    if isinstance(event, dict):
        return dict(event)
    dump = getattr(event, "model_dump", None)
    if callable(dump):
        return dump(mode="json")
    raise TypeError(
        "CentralDispatchClient.dispatch() expects a dict or a pydantic BaseModel "
        f"with model_dump(); got {type(event).__name__}"
    )


class CentralDispatchClient:
    """Async wrapper around POST /api/alerts/dispatch on BSVibe-Auth.

    Owns nothing but an :class:`httpx.AsyncClient`. The recommended
    lifecycle is one client per process (kept alive for the duration of
    the producer's event loop) — call :meth:`aclose` on shutdown.
    """

    def __init__(
        self,
        *,
        auth_url: str,
        service_token: str,
        http: httpx.AsyncClient | None = None,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
    ) -> None:
        if not auth_url:
            raise ValueError("CentralDispatchClient requires a non-empty auth_url")
        if not service_token:
            raise ValueError("CentralDispatchClient requires a non-empty service_token")
        self._dispatch_url = _build_dispatch_url(auth_url)
        self._service_token = service_token
        self._owned_http = http is None
        self._http = http or httpx.AsyncClient(timeout=timeout_s)
        self._logger = structlog.get_logger("bsvibe_alerts.dispatch_client")

    @property
    def dispatch_url(self) -> str:
        return self._dispatch_url

    async def aclose(self) -> None:
        if self._owned_http and self._http is not None:
            await self._http.aclose()

    async def dispatch(self, event: Any) -> DispatchResult:
        """POST one event to /api/alerts/dispatch and return the result.

        Raises :class:`CentralDispatchError` on transport / contract
        failure. Callers that want best-effort semantics (audit outbox
        loop, fire-and-forget) should wrap this in a try/except and log
        via structlog.
        """

        payload = _event_to_payload(event)
        headers = {"Authorization": f"Bearer {self._service_token}"}

        try:
            response = await self._http.post(self._dispatch_url, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise CentralDispatchError(f"network error: {exc!r}", retryable=True) from exc

        if 200 <= response.status_code < 300:
            try:
                data = response.json()
            except ValueError as exc:
                raise CentralDispatchError(
                    f"non-JSON response: {response.text[:200]!r}",
                    retryable=False,
                    status=response.status_code,
                ) from exc
            return DispatchResult.from_payload(data)

        excerpt = response.text[:200]
        if response.status_code >= 500:
            raise CentralDispatchError(
                f"dispatch endpoint {response.status_code}: {excerpt}",
                retryable=True,
                status=response.status_code,
            )
        raise CentralDispatchError(
            f"dispatch endpoint {response.status_code}: {excerpt}",
            retryable=False,
            status=response.status_code,
        )


def _build_dispatch_url(auth_url: str) -> str:
    """Resolve the dispatch endpoint from a base auth URL.

    Accepts either a bare host (``https://auth.bsvibe.dev``) or a full
    endpoint URL (``https://auth.bsvibe.dev/api/alerts/dispatch``) so the
    same env var works for direct config and reverse-proxied setups.
    """

    trimmed = auth_url.rstrip("/")
    if trimmed.endswith("/api/alerts/dispatch"):
        return trimmed
    return f"{trimmed}/api/alerts/dispatch"


__all__ = [
    "CentralDispatchClient",
    "CentralDispatchError",
    "DeliveryDescriptor",
    "DispatchResult",
]
