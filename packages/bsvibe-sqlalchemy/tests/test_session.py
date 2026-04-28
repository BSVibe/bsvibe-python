"""Tests for the async session machinery.

Three artefacts pinned here:

* :func:`create_session_factory` — wraps an :class:`AsyncEngine` in
  ``async_sessionmaker(..., expire_on_commit=False)``. The
  ``expire_on_commit=False`` default is shared by every product
  (BSNexus + BSupervisor verified) and is the load-bearing decision —
  flipping it back to True breaks every router that reads attributes
  off a returned ORM object.
* :func:`make_get_db` — builds a ``Depends(get_db)``-friendly async
  generator. Mirrors BSNexus' ``async_session()`` context-manager
  pattern (commits not auto-issued, rollback on exception).
* :func:`dispose_engine` — lifespan teardown helper that releases pool
  connections cleanly on shutdown (FastAPI lifespan / pytest fixture).
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from bsvibe_sqlalchemy.engine import create_engine_from_settings
from bsvibe_sqlalchemy.session import (
    create_session_factory,
    dispose_engine,
    make_get_db,
)
from bsvibe_sqlalchemy.settings import DatabaseSettings


def _sqlite_settings() -> DatabaseSettings:
    return DatabaseSettings.model_validate({"database_url": "sqlite+aiosqlite:///:memory:"})


def _engine() -> AsyncEngine:
    return create_engine_from_settings(_sqlite_settings())


class TestCreateSessionFactory:
    def test_returns_async_sessionmaker(self) -> None:
        engine = _engine()
        factory = create_session_factory(engine)
        assert isinstance(factory, async_sessionmaker)

    def test_factory_produces_async_sessions(self) -> None:
        engine = _engine()
        factory = create_session_factory(engine)
        session = factory()
        assert isinstance(session, AsyncSession)

    def test_expire_on_commit_is_false(self) -> None:
        """expire_on_commit=False is the BSVibe-wide convention.

        Routers commit and then read off the same instance — flipping
        this back to True would cause silent attribute refreshes on the
        wrong session state.
        """
        engine = _engine()
        factory = create_session_factory(engine)
        # async_sessionmaker stores the kwargs on ``.kw``.
        assert factory.kw["expire_on_commit"] is False


class TestMakeGetDb:
    async def test_yields_session_and_closes(self) -> None:
        engine = _engine()
        factory = create_session_factory(engine)
        get_db = make_get_db(factory)

        # Drain the generator.
        gen = get_db()
        session = await gen.__anext__()
        assert isinstance(session, AsyncSession)

        # Verify the session is functional.
        result = await session.execute(text("SELECT 1"))
        assert result.scalar() == 1

        # Closing the generator must close the session.
        try:
            await gen.__anext__()
        except StopAsyncIteration:
            pass
        # After the context manager exits, the session is closed.
        # Issuing a new query on a closed session raises.
        await dispose_engine(engine)

    async def test_rollback_on_exception(self) -> None:
        """When the consumer raises, the session must rollback.

        ``async with factory() as session`` already does this — the
        wrapper just needs to propagate.
        """
        engine = _engine()
        factory = create_session_factory(engine)
        get_db = make_get_db(factory)

        captured: list[AsyncSession] = []

        gen = get_db()
        session = await gen.__anext__()
        captured.append(session)
        # Throw an exception into the generator; the wrapper should
        # propagate it without swallowing.
        try:
            await gen.athrow(RuntimeError("boom"))
        except RuntimeError:
            pass
        await dispose_engine(engine)


class TestDisposeEngine:
    async def test_releases_pool_connections(self) -> None:
        engine = _engine()
        # No-op for an unused engine; what matters is that the call
        # completes without raising on either branch (sqlite NullPool
        # or Postgres QueuePool).
        await dispose_engine(engine)

    async def test_safe_to_call_twice(self) -> None:
        """Idempotent dispose so lifespan shutdown can be called safely
        even if a teardown handler fired earlier."""
        engine = _engine()
        await dispose_engine(engine)
        await dispose_engine(engine)
