"""In-memory permission decision cache (30s TTL by default).

Keyed by ``(user, relation, object_)``. Lock-protected for asyncio safety;
single-process — for multi-process deployments back this with Redis instead
(out of scope for Phase 0, see Auth_Design.md §11).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(slots=True)
class _Entry:
    value: bool
    expires_at: float


class PermissionCache:
    """Thread-safe (asyncio) permission decision cache."""

    def __init__(
        self,
        ttl_s: int = 30,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.ttl_s = ttl_s
        self._clock = clock or time.monotonic
        self._lock = asyncio.Lock()
        self._store: dict[tuple[str, str, str], _Entry] = {}

    @staticmethod
    def _key(user: str, relation: str, object_: str) -> tuple[str, str, str]:
        return (user, relation, object_)

    async def get(self, user: str, relation: str, object_: str) -> bool | None:
        key = self._key(user, relation, object_)
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            if entry.expires_at <= self._clock():
                self._store.pop(key, None)
                return None
            return entry.value

    async def set(self, user: str, relation: str, object_: str, value: bool) -> None:
        key = self._key(user, relation, object_)
        async with self._lock:
            self._store[key] = _Entry(
                value=value,
                expires_at=self._clock() + self.ttl_s,
            )

    async def invalidate(self, user: str, relation: str, object_: str) -> None:
        async with self._lock:
            self._store.pop(self._key(user, relation, object_), None)

    async def invalidate_user(self, user: str) -> None:
        async with self._lock:
            doomed = [k for k in self._store if k[0] == user]
            for k in doomed:
                self._store.pop(k, None)

    async def clear(self) -> None:
        async with self._lock:
            self._store.clear()
