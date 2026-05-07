"""Permission cache tests — 30s TTL, invalidate, thread-safe."""

from __future__ import annotations

import asyncio
import hashlib

import pytest

from bsvibe_authz.types import IntrospectionResponse


def _sha(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


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


# ------------------------------------------------------------------------
# IntrospectionCache (RFC 7662 response cache, sha256(token)-keyed)
# ------------------------------------------------------------------------


async def test_introspection_cache_set_and_get_hit() -> None:
    from bsvibe_authz.cache import IntrospectionCache

    cache = IntrospectionCache(ttl_s=60)
    key = _sha("bsv_sk_abc")
    response = IntrospectionResponse(active=True, sub="user:1", scope=["read"])
    await cache.set(key, response)

    got = await cache.get(key)
    assert got is response


async def test_introspection_cache_miss_returns_none() -> None:
    from bsvibe_authz.cache import IntrospectionCache

    cache = IntrospectionCache(ttl_s=60)
    assert await cache.get(_sha("nope")) is None


async def test_introspection_cache_ttl_expiry() -> None:
    from bsvibe_authz.cache import IntrospectionCache

    clock = [1000.0]

    def now() -> float:
        return clock[0]

    cache = IntrospectionCache(ttl_s=60, clock=now)
    key = _sha("tok")
    await cache.set(key, IntrospectionResponse(active=True, sub="u"))
    clock[0] += 59
    hit = await cache.get(key)
    assert hit is not None and hit.active is True
    clock[0] += 2
    assert await cache.get(key) is None


async def test_introspection_cache_invalidate() -> None:
    from bsvibe_authz.cache import IntrospectionCache

    cache = IntrospectionCache(ttl_s=60)
    key = _sha("tok")
    await cache.set(key, IntrospectionResponse(active=True, sub="u"))
    await cache.invalidate(key)
    assert await cache.get(key) is None


async def test_introspection_cache_invalidate_unknown_key_is_noop() -> None:
    from bsvibe_authz.cache import IntrospectionCache

    cache = IntrospectionCache(ttl_s=60)
    await cache.invalidate(_sha("never-set"))
    assert await cache.get(_sha("never-set")) is None


async def test_introspection_cache_clear_all() -> None:
    from bsvibe_authz.cache import IntrospectionCache

    cache = IntrospectionCache(ttl_s=60)
    await cache.set(_sha("a"), IntrospectionResponse(active=True))
    await cache.set(_sha("b"), IntrospectionResponse(active=False))
    await cache.clear()
    assert await cache.get(_sha("a")) is None
    assert await cache.get(_sha("b")) is None


async def test_introspection_cache_caches_inactive_responses() -> None:
    """Inactive (active=false) responses are also cached — avoids re-query storms."""
    from bsvibe_authz.cache import IntrospectionCache

    cache = IntrospectionCache(ttl_s=60)
    key = _sha("revoked")
    inactive = IntrospectionResponse(active=False)
    await cache.set(key, inactive)

    got = await cache.get(key)
    assert got is not None
    assert got.active is False


async def test_introspection_cache_concurrent_access_is_safe() -> None:
    from bsvibe_authz.cache import IntrospectionCache

    cache = IntrospectionCache(ttl_s=60)

    async def writer(i: int) -> None:
        await cache.set(_sha(f"t{i}"), IntrospectionResponse(active=True, sub=f"u{i}"))

    async def reader(i: int) -> IntrospectionResponse | None:
        return await cache.get(_sha(f"t{i}"))

    await asyncio.gather(*(writer(i) for i in range(50)))
    results = await asyncio.gather(*(reader(i) for i in range(50)))
    assert all(r is not None and r.active for r in results)


@pytest.mark.parametrize("ttl", [1, 60, 300])
async def test_introspection_cache_ttl_configurable(ttl: int) -> None:
    from bsvibe_authz.cache import IntrospectionCache

    cache = IntrospectionCache(ttl_s=ttl)
    assert cache.ttl_s == ttl
