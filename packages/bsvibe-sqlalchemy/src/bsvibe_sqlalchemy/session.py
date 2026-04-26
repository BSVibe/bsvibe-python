"""Async session factory + FastAPI ``Depends(get_db)`` helper.

Pulled together from the four products' near-identical patterns:

* BSNexus ``backend/src/storage/database.py``
* BSupervisor ``bsupervisor/models/database.py``
* BSGateway and BSage use the same shape via custom callsites.

Wire-compatible defaults pinned by tests:

* ``async_sessionmaker(..., expire_on_commit=False)`` — every product
  reads attributes off committed instances, so flipping this back to
  the SQLAlchemy default would silently re-fetch them on access.
* ``get_db`` yields the session from an ``async with factory() as
  session:`` block; if the consumer raises, the context manager
  rolls back automatically. Routers may explicitly ``await
  session.commit()``; the wrapper does not auto-commit.
* ``dispose_engine`` is a tiny helper for FastAPI's lifespan teardown
  that swallows the no-op idempotency case (calling ``dispose`` twice
  must not raise).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Build the async sessionmaker that all routers consume.

    ``expire_on_commit=False`` is the BSVibe convention — flipping it
    forces SQLAlchemy to re-fetch attributes after every commit, which
    breaks router code that reads off the same instance.
    """
    return async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


def make_get_db(
    factory: async_sessionmaker[AsyncSession],
) -> Callable[[], AsyncGenerator[AsyncSession, None]]:
    """Return a ``Depends(get_db)``-compatible async generator.

    Usage:

    .. code-block:: python

        engine = create_engine_from_settings(settings)
        async_session = create_session_factory(engine)
        get_db = make_get_db(async_session)

        @router.get("/items")
        async def list_items(db: AsyncSession = Depends(get_db)) -> ...:
            ...
    """

    async def get_db() -> AsyncGenerator[AsyncSession, None]:
        async with factory() as session:
            yield session

    return get_db


async def dispose_engine(engine: AsyncEngine) -> None:
    """Release pool connections on shutdown.

    Idempotent — calling twice (e.g. lifespan teardown plus a pytest
    fixture) must not raise.
    """
    await engine.dispose()


__all__ = ["create_session_factory", "make_get_db", "dispose_engine"]
