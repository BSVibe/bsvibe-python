"""Demo session creation + tenant lifecycle.

Each demo visitor gets a fresh ephemeral tenant. Workflow:
1. POST /api/v1/demo/session → DemoSessionService.create_session()
2. Service inserts a new tenant (is_demo=true, last_active_at=now())
3. Service awaits seed_fn(tenant_id, conn) to populate demo data
4. Service mints a JWT signed with DEMO_JWT_SECRET
5. Existing tenant scoping middleware naturally isolates the visitor

Touching last_active_at on requests defers GC until the visitor is gone.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import UUID, uuid4

import asyncpg
import structlog

from bsvibe_demo.jwt import mint_demo_jwt

logger = structlog.get_logger(__name__)

SeedFn = Callable[..., Awaitable[None]]


@dataclass(frozen=True)
class DemoSessionResult:
    """Result of creating a demo session."""

    tenant_id: UUID
    token: str
    expires_in: int


class DemoSessionService:
    """Service for creating + maintaining ephemeral demo tenants."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        jwt_secret: str,
        seed_fn: SeedFn,
        session_ttl_seconds: int = 7200,
    ) -> None:
        self._pool = pool
        self._jwt_secret = jwt_secret
        self._seed_fn = seed_fn
        self._ttl = session_ttl_seconds

    async def create_session(self) -> DemoSessionResult:
        """Create a fresh ephemeral tenant + seed it + mint a JWT."""
        tenant_id = uuid4()
        short_id = str(tenant_id)[:8]

        async with self._pool.acquire() as conn:
            async with conn.transaction():
                # Insert demo tenant. The settings JSONB carries the demo
                # marker + last_active_at without requiring a schema migration
                # (kept loose so existing tenant queries work unchanged).
                await conn.execute(
                    """
                    INSERT INTO tenants (id, name, slug, is_active, settings)
                    VALUES ($1, $2, $3, TRUE,
                            jsonb_build_object(
                                'is_demo', TRUE,
                                'last_active_at', extract(epoch from now())
                            ))
                    """,
                    tenant_id,
                    f"demo-{short_id}",
                    f"demo-{short_id}",
                )
                await self._seed_fn(tenant_id=tenant_id, conn=conn)

        token = mint_demo_jwt(
            tenant_id, secret=self._jwt_secret, ttl_seconds=self._ttl
        )

        logger.info(
            "demo_session_created",
            tenant_id=str(tenant_id),
            ttl_seconds=self._ttl,
        )

        return DemoSessionResult(
            tenant_id=tenant_id,
            token=token,
            expires_in=self._ttl,
        )

    async def touch_last_active(self, tenant_id: UUID) -> None:
        """Update ``last_active_at`` so GC won't reap an active session."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE tenants
                SET settings = jsonb_set(
                    settings,
                    '{last_active_at}',
                    to_jsonb(extract(epoch from now()))
                )
                WHERE id = $1 AND settings->>'is_demo' = 'true'
                """,
                tenant_id,
            )
