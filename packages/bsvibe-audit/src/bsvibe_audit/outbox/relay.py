"""Background relay that ships outbox rows to BSVibe-Auth.

Lifecycle:

* ``OutboxRelay.from_settings(...)`` — wires up the SQLAlchemy session
  factory + an :class:`AuditClient`. When the URL/token are missing
  the relay returns a no-op singleton (``is_running()`` always False).
* ``await relay.start()`` — schedules a long-running asyncio task that
  loops ``run_once`` at the configured interval.
* ``await relay.stop()`` — cancels the task and awaits cleanup.
* ``await relay.run_once()`` — direct, deterministic single-iteration
  call used by tests and ad-hoc operator commands.

Failure isolation: any exception inside ``run_once`` is logged and
swallowed so the loop survives — outbox failures must never crash the
host service.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bsvibe_audit.client import AuditClient, AuditDeliveryError
from bsvibe_audit.outbox.store import OutboxStore

if TYPE_CHECKING:
    from bsvibe_audit.settings import AuditSettings


class OutboxRelay:
    """Polls the outbox and ships batches to BSVibe-Auth."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession] | None,
        client: AuditClient | None,
        store: OutboxStore | None = None,
        batch_size: int = 50,
        interval_s: float = 5.0,
        max_retries: int = 5,
        enabled: bool = True,
    ) -> None:
        self._session_factory = session_factory
        self._client = client
        self._store = store or OutboxStore()
        self._batch_size = batch_size
        self._interval_s = interval_s
        self._max_retries = max_retries
        self._enabled = enabled and session_factory is not None and client is not None
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._logger = structlog.get_logger("bsvibe_audit.outbox.relay")

    @classmethod
    def from_settings(
        cls,
        settings: AuditSettings,
        *,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        client: AuditClient | None = None,
    ) -> OutboxRelay:
        if not settings.relay_enabled:
            return cls(
                session_factory=None,
                client=None,
                enabled=False,
            )
        if client is None:
            client = AuditClient.from_settings(
                audit_url=settings.auth_audit_url,
                service_token=settings.auth_service_token,
            )
        return cls(
            session_factory=session_factory,
            client=client,
            batch_size=settings.batch_size,
            interval_s=settings.relay_interval_s,
            max_retries=settings.max_retries,
            enabled=session_factory is not None,
        )

    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if not self._enabled:
            self._logger.info("audit_relay_disabled")
            return
        if self.is_running():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_loop(), name="bsvibe-audit-relay")

    async def stop(self) -> None:
        self._stop_event.set()
        task = self._task
        if task is None:
            return
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, BaseException):  # noqa: BLE001 - cleanup
            pass
        self._task = None
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:  # noqa: BLE001 - best-effort
                pass

    async def run_once(self) -> int:
        """Drain one batch. Returns the number of rows successfully delivered."""

        if not self._enabled or self._session_factory is None or self._client is None:
            return 0

        async with self._session_factory() as session:
            rows = await self._store.select_undelivered(session, batch_size=self._batch_size)
            if not rows:
                return 0

            payloads: list[dict[str, Any]] = [dict(row.payload) for row in rows]
            ids = [row.id for row in rows]
            try:
                await self._client.send(payloads)
            except AuditDeliveryError as exc:
                for row_id in ids:
                    if exc.retryable:
                        await self._store.record_failure(
                            session,
                            row_id,
                            error=str(exc),
                            max_retries=self._max_retries,
                        )
                    else:
                        await self._store.mark_dead_letter(
                            session,
                            row_id,
                            error=str(exc),
                        )
                await session.commit()
                self._logger.warning(
                    "audit_batch_failed",
                    rows=len(rows),
                    retryable=exc.retryable,
                    error=str(exc),
                )
                return 0

            await self._store.mark_delivered(session, ids)
            await session.commit()
            self._logger.debug("audit_batch_delivered", rows=len(rows))
            return len(rows)

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self.run_once()
            except Exception as exc:  # noqa: BLE001 — relay must survive
                self._logger.error("audit_relay_iteration_failed", error=repr(exc))
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._interval_s)
            except asyncio.TimeoutError:
                continue


__all__ = ["OutboxRelay"]
