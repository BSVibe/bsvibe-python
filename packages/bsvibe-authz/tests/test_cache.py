"""Permission cache tests — 30s TTL, invalidate, thread-safe."""

from __future__ import annotations

import asyncio

import pytest


async def test_cache_set_and_get() -> None:
    from bsvibe_authz.cache import PermissionCache

    cache = PermissionCache(ttl_s=30)
    await cache.set("user:1", "read", "doc:1", True)
    assert await cache.get("user:1", "read", "doc:1") is True


async def test_cache_miss_returns_none() -> None:
    from bsvibe_authz.cache import PermissionCache

    cache = PermissionCache(ttl_s=30)
    assert await cache.get("user:1", "read", "doc:1") is None


async def test_cache_ttl_expiry() -> None:
    from bsvibe_authz.cache import PermissionCache

    clock = [1000.0]

    def now() -> float:
        return clock[0]

    cache = PermissionCache(ttl_s=30, clock=now)
    await cache.set("u", "r", "o", True)
    clock[0] += 29
    assert await cache.get("u", "r", "o") is True
    clock[0] += 2
    assert await cache.get("u", "r", "o") is None


async def test_cache_invalidate_specific_entry() -> None:
    from bsvibe_authz.cache import PermissionCache

    cache = PermissionCache(ttl_s=30)
    await cache.set("u", "r", "o", True)
    await cache.invalidate("u", "r", "o")
    assert await cache.get("u", "r", "o") is None


async def test_cache_invalidate_user() -> None:
    from bsvibe_authz.cache import PermissionCache

    cache = PermissionCache(ttl_s=30)
    await cache.set("u1", "r", "o1", True)
    await cache.set("u1", "r", "o2", False)
    await cache.set("u2", "r", "o1", True)
    await cache.invalidate_user("u1")
    assert await cache.get("u1", "r", "o1") is None
    assert await cache.get("u1", "r", "o2") is None
    assert await cache.get("u2", "r", "o1") is True


async def test_cache_clear_all() -> None:
    from bsvibe_authz.cache import PermissionCache

    cache = PermissionCache(ttl_s=30)
    await cache.set("u", "r", "o", True)
    await cache.clear()
    assert await cache.get("u", "r", "o") is None


async def test_cache_concurrent_access_is_safe() -> None:
    """The cache must not corrupt under high concurrency. Smoke test."""
    from bsvibe_authz.cache import PermissionCache

    cache = PermissionCache(ttl_s=30)

    async def writer(i: int) -> None:
        await cache.set(f"u{i}", "r", f"o{i}", True)

    async def reader(i: int) -> bool | None:
        return await cache.get(f"u{i}", "r", f"o{i}")

    await asyncio.gather(*(writer(i) for i in range(50)))
    results = await asyncio.gather(*(reader(i) for i in range(50)))
    assert all(r is True for r in results)


@pytest.mark.parametrize("ttl", [1, 30, 300])
async def test_cache_ttl_configurable(ttl: int) -> None:
    from bsvibe_authz.cache import PermissionCache

    cache = PermissionCache(ttl_s=ttl)
    assert cache.ttl_s == ttl
