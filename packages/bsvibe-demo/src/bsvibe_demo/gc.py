"""Demo tenant garbage collection.

Hourly cron entrypoint that deletes demo tenants inactive for >TTL.
Existing FK ``ON DELETE CASCADE`` on tenant_id columns handles dependent
rows (api_keys, routing_logs, rules, intents, etc.).

Safety: query is filtered on ``settings->>'is_demo' = 'true'`` so this
never touches a prod tenant even if last_active_at is missing.
"""

from __future__ import annotations

from uuid import UUID

import asyncpg
import structlog

logger = structlog.get_logger(__name__)


async def find_expired_tenants(
    pool: asyncpg.Pool, *, ttl_seconds: int = 7200
) -> list[UUID]:
    """Return tenant_ids of demo tenants inactive for >ttl_seconds."""
    sql = """
    SELECT id FROM tenants
    WHERE settings->>'is_demo' = 'true'
      AND COALESCE((settings->>'last_active_at')::float, 0)
          < extract(epoch from now()) - $1
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, ttl_seconds)
    return [row["id"] for row in rows]


async def demo_gc(pool: asyncpg.Pool, *, ttl_seconds: int = 7200) -> int:
    """Delete demo tenants inactive for >ttl_seconds. Returns count deleted."""
    expired = await find_expired_tenants(pool, ttl_seconds=ttl_seconds)
    if not expired:
        return 0

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                DELETE FROM tenants
                WHERE id = ANY($1::uuid[])
                  AND settings->>'is_demo' = 'true'
                """,
                expired,
            )

    logger.info("demo_gc_complete", deleted=len(expired), ttl_seconds=ttl_seconds)
    return len(expired)


async def run_gc_cli(database_url: str, *, ttl_seconds: int = 7200) -> int:
    """Helper for product CLI entrypoints — open pool, GC, close.

    Per-product `__main__` should be a thin wrapper around this so the
    shared lib stays free of product-specific config imports.
    """
    pool = await asyncpg.create_pool(database_url)
    try:
        return await demo_gc(pool, ttl_seconds=ttl_seconds)
    finally:
        await pool.close()
