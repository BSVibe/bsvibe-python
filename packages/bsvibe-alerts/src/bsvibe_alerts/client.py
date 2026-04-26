"""High-level alert publisher coordinating routing and channels.

:class:`AlertClient` is the only object every product is expected to
keep at module scope. It is intentionally tiny:

1. :meth:`publish` — consult the router, dispatch to each named channel
   in parallel, never let a failing channel block its peers.
2. :meth:`emit` — convenience wrapper that builds an :class:`Alert`.
3. :meth:`from_settings` — wire up channels (structlog always-on,
   telegram/slack only when credentials present) using
   :class:`AlertSettings`.

Failure isolation policy: a channel that raises is logged via the
always-on structlog fallback and reported in the dispatch result, but
**never** stops other channels. This is what allows products to publish
critical alerts even when slack is down.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import structlog

from bsvibe_alerts.routing import AlertRouter
from bsvibe_alerts.types import Alert, AlertSeverity

if TYPE_CHECKING:
    from bsvibe_alerts.channels import AlertChannel
    from bsvibe_alerts.settings import AlertSettings


class AlertClient:
    """Publish alerts to one or more channels using a routing rule."""

    def __init__(
        self,
        *,
        channels: list[AlertChannel],
        router: AlertRouter | None = None,
        service_name: str | None = None,
    ) -> None:
        self.channels: list[AlertChannel] = list(channels)
        self.router: AlertRouter = router or AlertRouter.from_defaults()
        self.service_name = service_name
        self._by_name: dict[str, AlertChannel] = {ch.name: ch for ch in self.channels}
        self._fallback_logger = structlog.get_logger("bsvibe_alerts.client")

    @classmethod
    def from_settings(cls, settings: AlertSettings) -> AlertClient:
        """Build a client from :class:`AlertSettings`.

        The structlog channel is always registered. Telegram/slack are
        registered only when their credentials are present in
        ``settings`` (matches the ``telegram_enabled`` / ``slack_enabled``
        properties).
        """

        from bsvibe_alerts.channels import (
            SlackChannel,
            StructlogChannel,
            TelegramChannel,
        )

        channels: list[AlertChannel] = [StructlogChannel()]
        if settings.telegram_enabled:
            channels.append(
                TelegramChannel(
                    bot_token=settings.telegram_bot_token,
                    chat_id=settings.telegram_chat_id,
                )
            )
        if settings.slack_enabled:
            channels.append(SlackChannel(webhook_url=settings.slack_webhook_url))
        return cls(
            channels=channels,
            router=AlertRouter.from_settings(settings),
            service_name=settings.service_name,
        )

    async def publish(self, alert: Alert) -> dict[str, Any]:
        """Dispatch ``alert`` to every channel listed by the router.

        Returns
        -------
        dict
            Mapping ``channel_name -> True`` on success, ``error_repr``
            (string) on failure. Channels named in the route but missing
            from the registry (e.g. ``"slack"`` when slack creds were
            absent at startup) are recorded as ``"missing"`` so the
            caller can audit absent sinks.
        """

        # Auto-attach service name from settings when caller did not.
        if alert.service is None and self.service_name is not None:
            alert.service = self.service_name

        names = self.router.channels_for(alert)
        result: dict[str, Any] = {}

        async def _dispatch(name: str) -> None:
            channel = self._by_name.get(name)
            if channel is None:
                result[name] = "missing"
                # NOTE: structlog's bound logger uses the first positional arg
                # as ``event``. Alert.event is forwarded under
                # ``alert_event`` to avoid kwarg collision.
                self._fallback_logger.warning(
                    "alert_channel_missing",
                    channel=name,
                    alert_event=alert.event,
                    alert_severity=alert.severity.value,
                )
                return
            try:
                await channel.send(alert)
                result[name] = True
            except Exception as exc:  # noqa: BLE001 — failure isolation contract
                result[name] = repr(exc)
                self._fallback_logger.error(
                    "alert_channel_failed",
                    channel=name,
                    alert_event=alert.event,
                    alert_severity=alert.severity.value,
                    error=repr(exc),
                )

        # Dispatch in parallel — any single channel timing out cannot
        # delay the others. ``return_exceptions=True`` is unnecessary
        # because each ``_dispatch`` already swallows its own errors.
        await asyncio.gather(*(_dispatch(name) for name in names))
        return result

    async def emit(
        self,
        *,
        event: str,
        message: str,
        severity: AlertSeverity | str = AlertSeverity.INFO,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Convenience wrapper that builds an :class:`Alert` and publishes."""

        alert = Alert(
            event=event,
            message=message,
            severity=severity,
            context=context or {},
            service=self.service_name,
        )
        return await self.publish(alert)


__all__ = ["AlertClient"]
