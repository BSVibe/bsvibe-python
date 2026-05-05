"""SQLAlchemy variant of demo session + GC.

For products using SQLAlchemy 2.0 async (BSNexus, BSupervisor) instead of
raw asyncpg (BSGateway). Same semantics, different driver.

The session service inserts a row into the ``tenants`` table whose schema
includes a ``settings`` JSON column carrying ``is_demo=true`` and
``last_active_at=<unix_ts>``. Per-product seed runs after insert in the
same transaction.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID, uuid4

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bsvibe_demo.jwt import mint_demo_jwt
from bsvibe_demo.session import DemoSessionResult

logger = structlog.get_logger(__name__)

# seed_fn signature: (tenant_id, session) -> awaitable
SqlAlchemySeedFn = Callable[..., Awaitable[None]]


class DemoSessionServiceSqlAlchemy:
    """SQLAlchemy variant of :class:`DemoSessionService`.

    Each demo visitor gets a fresh tenant. The seed_fn receives the new
    tenant_id + the active SQLAlchemy AsyncSession so it can insert
    product-specific demo data within the same transaction.
    """

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        jwt_secret: str,
        seed_fn: SqlAlchemySeedFn,
        session_ttl_seconds: int = 7200,
        tenant_extra_columns: dict[str, Any] | None = None,
    ) -> None:
        """Create a DemoSessionServiceSqlAlchemy.

        ``tenant_extra_columns`` lets products inject required NOT NULL
        columns on their tenants table (e.g. BSNexus has ``owner_user_id``
        NOT NULL). Keys are column names; values are SQL literals or
        bindparams.
        """
        self._sessionmaker = sessionmaker
        self._jwt_secret = jwt_secret
        self._seed_fn = seed_fn
        self._ttl = session_ttl_seconds
        self._extra_cols = tenant_extra_columns or {}

    async def create_session(self) -> DemoSessionResult:
        tenant_id = uuid4()
        short_id = str(tenant_id)[:8]

        # Compose INSERT — bind core cols + caller-provided extras
        cols = ["id", "name", "slug", "settings"]
        params: dict[str, Any] = {
            "id": tenant_id,
            "name": f"demo-{short_id}",
            "slug": f"demo-{short_id}",
            "settings": '{"is_demo": true, "last_active_at": 0}',
        }
        for col, val in self._extra_cols.items():
            cols.append(col)
            params[col] = val

        placeholders = ", ".join(f":{c}" for c in cols)
        col_list = ", ".join(cols)

        async with self._sessionmaker() as db:
            async with db.begin():
                await db.execute(
                    text(
                        f"INSERT INTO tenants ({col_list}) VALUES ({placeholders})"
                    ),
                    params,
                )
                # Stamp last_active_at via a separate UPDATE so we use PG's now()
                await db.execute(
                    text(
                        "UPDATE tenants SET settings = jsonb_set("
                        "settings::jsonb, '{last_active_at}', "
                        "to_jsonb(extract(epoch from now()))) WHERE id = :tid"
                    ),
                    {"tid": tenant_id},
                )
                await self._seed_fn(tenant_id=tenant_id, session=db)

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
        async with self._sessionmaker() as db:
            async with db.begin():
                await db.execute(
                    text(
                        "UPDATE tenants SET settings = jsonb_set("
                        "settings::jsonb, '{last_active_at}', "
                        "to_jsonb(extract(epoch from now()))) "
                        "WHERE id = :tid AND settings->>'is_demo' = 'true'"
                    ),
                    {"tid": tenant_id},
                )


async def find_expired_tenants_sqlalchemy(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    ttl_seconds: int = 7200,
) -> list[UUID]:
    """SQLAlchemy variant of :func:`find_expired_tenants`."""
    async with sessionmaker() as db:
        result = await db.execute(
            text(
                "SELECT id FROM tenants "
                "WHERE settings->>'is_demo' = 'true' "
                "AND COALESCE((settings->>'last_active_at')::float, 0) "
                "< extract(epoch from now()) - :ttl"
            ),
            {"ttl": ttl_seconds},
        )
    return [row[0] for row in result.all()]


async def demo_gc_sqlalchemy(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    ttl_seconds: int = 7200,
) -> int:
    """SQLAlchemy variant of :func:`demo_gc`."""
    expired = await find_expired_tenants_sqlalchemy(
        sessionmaker, ttl_seconds=ttl_seconds
    )
    if not expired:
        return 0

    async with sessionmaker() as db:
        async with db.begin():
            await db.execute(
                text(
                    "DELETE FROM tenants WHERE id = ANY(:ids) "
                    "AND settings->>'is_demo' = 'true'"
                ),
                {"ids": expired},
            )

    logger.info(
        "demo_gc_complete", deleted=len(expired), ttl_seconds=ttl_seconds
    )
    return len(expired)
