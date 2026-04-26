"""Async engine factory.

Extracted verbatim from BSupervisor PR #13 §M20:

* SQLite branch (any URL whose scheme starts with ``sqlite``) skips
  ``pool_size`` / ``max_overflow`` / ``pool_timeout`` / ``pool_recycle``
  / ``pool_pre_ping`` because SQLAlchemy wires SQLite engines through
  ``NullPool`` (or ``StaticPool`` for ``:memory:``), which raises
  ``TypeError`` if ``pool_size`` is supplied.
* Postgres branch (everything else) forwards all five knobs.

The ``echo`` flag is mapped from ``settings.db_echo`` on both branches.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from bsvibe_sqlalchemy.settings import DatabaseSettings


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


def create_engine_from_settings(s: DatabaseSettings) -> AsyncEngine:
    """Build an :class:`AsyncEngine` honouring the pool config in ``s``.

    Args:
        s: A :class:`DatabaseSettings` instance (or subclass) — the
           factory only depends on the database-related fields, so any
           product Settings that inherits from :class:`DatabaseSettings`
           is acceptable.

    Returns:
        An :class:`AsyncEngine` ready for use as the source of an
        ``async_sessionmaker``.

    SQLite (used in tests) wires through ``NullPool`` / ``StaticPool``,
    which doesn't accept pool sizing arguments — those are skipped
    automatically.
    """
    kwargs: dict[str, Any] = {"echo": s.db_echo}
    if not _is_sqlite(s.database_url):
        kwargs.update(
            pool_size=s.db_pool_size,
            max_overflow=s.db_max_overflow,
            pool_timeout=s.db_pool_timeout,
            pool_recycle=s.db_pool_recycle,
            pool_pre_ping=s.db_pool_pre_ping,
        )
    return create_async_engine(s.database_url, **kwargs)


__all__ = ["create_engine_from_settings"]
