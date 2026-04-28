"""Severity → channel routing.

Routing is intentionally a pure data structure (a dict) plus a thin
lookup. The :class:`AlertClient` consumes the result list and dispatches.

Defensive policy: if a deployer ships a routing table missing a severity
entry, the lookup falls back to ``["structlog"]`` so alerts are *never*
silently swallowed. The structlog channel is always-on regardless of
credentials, so this fallback is guaranteed to fire.

D18 introduces :class:`CentralAlertRouter` — a runtime-tunable router
backed by ``GET /api/alerts/rules`` on BSVibe-Auth. Producers reach for
``AlertRouter`` (hardcoded defaults) when they want zero network
dependency, and ``CentralAlertRouter`` when they want operators to flip
``enabled`` / change ``severity`` thresholds without redeploys.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

import httpx
import structlog

from bsvibe_alerts.types import Alert, AlertSeverity

if TYPE_CHECKING:
    from bsvibe_alerts.settings import AlertSettings


_DEFAULT_TABLE: dict[AlertSeverity, list[str]] = {
    AlertSeverity.INFO: ["structlog"],
    AlertSeverity.WARNING: ["structlog", "slack"],
    AlertSeverity.CRITICAL: ["structlog", "slack", "telegram"],
}


_FALLBACK_CHANNELS: list[str] = ["structlog"]


class AlertRouter:
    """Map an :class:`Alert` to a list of channel names.

    The mapping is exposed through :meth:`channels_for` (defensive copy
    on every call so callers cannot mutate the underlying table).
    """

    def __init__(self, *, table: dict[AlertSeverity, list[str]]) -> None:
        for key in table:
            if not isinstance(key, AlertSeverity):
                raise TypeError(f"AlertRouter table keys must be AlertSeverity, got {type(key).__name__}")
        # Store our own copies so external mutation is harmless.
        self._table: dict[AlertSeverity, list[str]] = {k: list(v) for k, v in table.items()}

    @classmethod
    def from_defaults(cls) -> AlertRouter:
        return cls(table={k: list(v) for k, v in _DEFAULT_TABLE.items()})

    @classmethod
    def from_settings(cls, settings: AlertSettings) -> AlertRouter:
        return cls(
            table={
                AlertSeverity.INFO: list(settings.info_channels),
                AlertSeverity.WARNING: list(settings.warning_channels),
                AlertSeverity.CRITICAL: list(settings.critical_channels),
            }
        )

    def channels_for(self, alert: Alert) -> list[str]:
        """Return a fresh list of channel names for ``alert.severity``."""

        channels = self._table.get(alert.severity)
        if channels is None:
            return list(_FALLBACK_CHANNELS)
        return list(channels)


_DISPATCH_URL_FALLBACK = "/api/alerts/dispatch"
_DEFAULT_TIMEOUT_S = 5.0


class CentralAlertRouter:
    """Runtime-tunable router backed by BSVibe-Auth ``alert_routes``.

    Unlike :class:`AlertRouter` (pure data structure consulted on the
    publish path), :class:`CentralAlertRouter` issues an async HTTP call
    against ``POST /api/alerts/dispatch`` for every alert. The dispatch
    endpoint loads the runtime ``alert_routes`` table and returns the
    matched routes as a list of channel names — operators can flip
    ``enabled`` / change thresholds without redeploys.

    Failure isolation: when the auth-app is unreachable or returns a
    non-2xx, the router falls back to :class:`AlertRouter` (or a
    minimal ``["structlog"]`` route if no fallback was configured).
    Producers MUST never crash on dispatch failure — alert delivery is
    best-effort, the audit log is the source of truth.

    The class is constructed via :meth:`from_settings` for the typical
    "env-driven" path and accepts a hand-built :class:`httpx.AsyncClient`
    for tests.
    """

    def __init__(
        self,
        *,
        auth_url: str,
        service_token: str,
        fallback: AlertRouter | None = None,
        http: httpx.AsyncClient | None = None,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
    ) -> None:
        if not auth_url:
            raise ValueError("CentralAlertRouter requires a non-empty auth_url")
        if not service_token:
            raise ValueError("CentralAlertRouter requires a non-empty service_token")
        self._dispatch_url = self._resolve_dispatch_url(auth_url)
        self._service_token = service_token
        self._fallback = fallback or AlertRouter.from_defaults()
        self._owned_http = http is None
        self._http = http or httpx.AsyncClient(timeout=timeout_s)
        self._logger = structlog.get_logger("bsvibe_alerts.router.central")

    @staticmethod
    def _resolve_dispatch_url(auth_url: str) -> str:
        trimmed = auth_url.rstrip("/")
        if trimmed.endswith(_DISPATCH_URL_FALLBACK):
            return trimmed
        return f"{trimmed}{_DISPATCH_URL_FALLBACK}"

    async def aclose(self) -> None:
        if self._owned_http and self._http is not None:
            await self._http.aclose()

    @classmethod
    def from_settings(
        cls,
        settings: AlertSettings,
        *,
        auth_url: str,
        service_token: str,
        http: httpx.AsyncClient | None = None,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
    ) -> CentralAlertRouter:
        return cls(
            auth_url=auth_url,
            service_token=service_token,
            fallback=AlertRouter.from_settings(settings),
            http=http,
            timeout_s=timeout_s,
        )

    async def channels_for_async(self, alert: Alert) -> list[str]:
        """Resolve channel names by calling the BSVibe-Auth dispatch API.

        On any failure (network, non-2xx, malformed body) the call falls
        back to :class:`AlertRouter` so a transient outage of the
        central service cannot blackhole alerts.
        """

        payload = _alert_to_dispatch_event(alert)
        headers = {"Authorization": f"Bearer {self._service_token}"}
        try:
            response = await self._http.post(self._dispatch_url, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            self._logger.warning(
                "central_dispatch_unreachable",
                error=repr(exc),
                alert_event=alert.event,
                severity=alert.severity.value,
            )
            return self._fallback.channels_for(alert)

        if not 200 <= response.status_code < 300:
            self._logger.warning(
                "central_dispatch_non_2xx",
                status=response.status_code,
                alert_event=alert.event,
                severity=alert.severity.value,
            )
            return self._fallback.channels_for(alert)

        try:
            data = response.json()
        except ValueError:
            self._logger.warning(
                "central_dispatch_bad_body",
                alert_event=alert.event,
            )
            return self._fallback.channels_for(alert)

        deliveries: Sequence[dict[str, Any]] = data.get("deliveries") or []
        if not deliveries:
            # Zero matches → defensively also emit on the local fallback so
            # operators see *something* during early adoption (mirrors
            # BSVibe_Audit_Design.md §11 "stdout fallback" rule). This is the
            # single most common D18 misconfiguration to surface.
            self._logger.info(
                "central_dispatch_no_match",
                alert_event=alert.event,
                severity=alert.severity.value,
            )
            return self._fallback.channels_for(alert)

        names: list[str] = []
        for d in deliveries:
            channel = d.get("channel")
            if isinstance(channel, str) and channel:
                names.append(channel)
        if not names:
            return self._fallback.channels_for(alert)
        # Preserve order, drop duplicates.
        seen: set[str] = set()
        ordered: list[str] = []
        for n in names:
            if n in seen:
                continue
            seen.add(n)
            ordered.append(n)
        return ordered

    def channels_for(self, alert: Alert) -> list[str]:
        """Synchronous bridge — runs the async resolver via asyncio.

        Provided so existing call sites that consume :class:`AlertRouter`
        synchronously can swap routers without rewriting. When invoked
        from inside a running loop the call falls back to the local
        router (the producer is responsible for using the async
        :meth:`channels_for_async` in async contexts).
        """

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.channels_for_async(alert))
        # Inside an event loop — bail to fallback rather than block.
        self._logger.debug(
            "central_dispatch_sync_in_loop",
            alert_event=alert.event,
        )
        return self._fallback.channels_for(alert)


def _alert_to_dispatch_event(alert: Alert) -> dict[str, Any]:
    """Map an :class:`Alert` to an AuditEventBase-shaped dispatch payload.

    The dispatch endpoint validates the standard audit envelope, so the
    router synthesises one even though we are not persisting an audit
    row. ``data.severity`` carries the alert severity through to the
    severity ladder evaluation on the server.
    """

    import uuid
    from datetime import UTC, datetime

    tenant_id = str(alert.context.get("tenant_id") or "00000000-0000-0000-0000-000000000000")
    actor_id = str(alert.context.get("actor_id") or alert.service or "system")
    event_type = alert.event if "." in alert.event else f"alerts.{alert.event}"
    return {
        "event_id": str(uuid.uuid4()),
        "event_type": event_type,
        "occurred_at": datetime.now(UTC).isoformat(),
        "actor": {"type": "system", "id": actor_id},
        "tenant_id": tenant_id,
        "data": {
            "severity": alert.severity.value,
            "message": alert.message,
            **{k: v for k, v in alert.context.items() if k not in {"tenant_id", "actor_id"}},
        },
    }


__all__ = ["AlertRouter", "CentralAlertRouter"]
