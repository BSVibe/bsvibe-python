"""Tests for ``create_engine_from_settings``.

Extracted verbatim from BSupervisor PR #13 §M20:

* SQLite branch skips the pool sizing knobs (NullPool would TypeError).
* Postgres branch wires ``pool_size``, ``max_overflow``, ``pool_timeout``,
  ``pool_recycle``, ``pool_pre_ping``.
* ``echo`` always honors ``settings.db_echo``.

The contract is **wire-compatible** with the four products' existing
factories. Changes here force coordinated migration across them.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from bsvibe_sqlalchemy.engine import create_engine_from_settings
from bsvibe_sqlalchemy.settings import DatabaseSettings


def _settings(url: str, **overrides: object) -> DatabaseSettings:
    """Build a DatabaseSettings instance with explicit values.

    Bypasses env loading so each test asserts a specific knob shape.
    """
    base: dict[str, object] = {"database_url": url}
    base.update(overrides)
    return DatabaseSettings.model_validate(base)


class TestSqliteBranch:
    """SQLite must NOT receive pool_size/max_overflow/pool_timeout."""

    def test_returns_async_engine(self) -> None:
        s = _settings("sqlite+aiosqlite:///:memory:")
        engine = create_engine_from_settings(s)
        assert isinstance(engine, AsyncEngine)

    def test_sqlite_uses_null_pool(self) -> None:
        from sqlalchemy.pool import NullPool, StaticPool

        s = _settings("sqlite+aiosqlite:///:memory:")
        engine = create_engine_from_settings(s)
        # SQLAlchemy default for sqlite+aiosqlite is StaticPool/NullPool —
        # crucially NOT QueuePool (which would accept pool_size). The
        # factory must avoid passing pool_size to the SQLite branch.
        assert isinstance(engine.pool, (NullPool, StaticPool))

    def test_sqlite_file_url_branch(self, tmp_path) -> None:
        url = f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
        s = _settings(url)
        engine = create_engine_from_settings(s)
        assert isinstance(engine, AsyncEngine)

    def test_sqlite_does_not_pass_pool_args(self) -> None:
        """Regression test: passing pool_size to NullPool raises TypeError.

        If the factory forwards pool_size for sqlite, engine creation
        fails with ``TypeError: NullPool received unexpected pool_size``.
        """
        s = _settings(
            "sqlite+aiosqlite:///:memory:",
            db_pool_size=10,
            db_max_overflow=20,
        )
        # Must not raise.
        engine = create_engine_from_settings(s)
        assert isinstance(engine, AsyncEngine)


class TestPostgresBranch:
    """Postgres must wire all five pool knobs through."""

    def test_returns_async_engine(self) -> None:
        s = _settings("postgresql+asyncpg://u:p@host/db")
        engine = create_engine_from_settings(s)
        assert isinstance(engine, AsyncEngine)

    def test_postgres_pool_size_propagates(self) -> None:
        s = _settings(
            "postgresql+asyncpg://u:p@host/db",
            db_pool_size=15,
        )
        engine = create_engine_from_settings(s)
        # SQLAlchemy stores pool_size on the pool object.
        assert engine.pool.size() == 15

    def test_postgres_pool_recycle_propagates(self) -> None:
        s = _settings(
            "postgresql+asyncpg://u:p@host/db",
            db_pool_recycle=900,
        )
        engine = create_engine_from_settings(s)
        # ``QueuePool._recycle`` holds the value.
        assert engine.pool._recycle == 900

    def test_postgres_pool_pre_ping_default_true(self) -> None:
        """pool_pre_ping=True must be wired through by default."""
        s = _settings("postgresql+asyncpg://u:p@host/db")
        engine = create_engine_from_settings(s)
        assert engine.pool._pre_ping is True

    def test_postgres_pool_pre_ping_can_be_disabled(self) -> None:
        s = _settings(
            "postgresql+asyncpg://u:p@host/db",
            db_pool_pre_ping=False,
        )
        engine = create_engine_from_settings(s)
        assert engine.pool._pre_ping is False

    def test_postgres_pool_timeout_propagates(self) -> None:
        s = _settings(
            "postgresql+asyncpg://u:p@host/db",
            db_pool_timeout=45,
        )
        engine = create_engine_from_settings(s)
        # ``QueuePool._timeout`` holds the connection-acquire timeout.
        assert engine.pool._timeout == 45

    def test_postgres_max_overflow_propagates(self) -> None:
        s = _settings(
            "postgresql+asyncpg://u:p@host/db",
            db_max_overflow=30,
        )
        engine = create_engine_from_settings(s)
        # ``QueuePool._max_overflow`` holds the overflow allowance.
        assert engine.pool._max_overflow == 30


class TestEchoFlag:
    """``db_echo`` should map to SQLAlchemy ``echo``."""

    def test_echo_default_false(self) -> None:
        s = _settings("sqlite+aiosqlite:///:memory:")
        engine = create_engine_from_settings(s)
        assert engine.echo is False

    def test_echo_true_propagates(self) -> None:
        s = _settings(
            "sqlite+aiosqlite:///:memory:",
            db_echo=True,
        )
        engine = create_engine_from_settings(s)
        # SQLAlchemy normalises True/"debug" into a logger-friendly
        # value — the truthy assertion is what we care about.
        assert bool(engine.echo) is True


class TestDispatchByUrl:
    """The branch decision MUST be based on the URL, not on key presence.

    Several products carry SQLite test settings that include a
    ``db_pool_size`` value (defaulted from BsvibeSettings) — the factory
    must still drop those for SQLite.
    """

    @pytest.mark.parametrize(
        "url",
        [
            "sqlite+aiosqlite:///:memory:",
            "sqlite+aiosqlite:////tmp/foo.db",
        ],
    )
    def test_sqlite_variants_skip_pool_args(self, url: str) -> None:
        # The factory dispatches on ``url.startswith("sqlite")`` so any
        # sqlite variant must be safe even with non-zero pool knobs.
        s = _settings(url, db_pool_size=10, db_max_overflow=20)
        engine = create_engine_from_settings(s)
        assert isinstance(engine, AsyncEngine)

    def test_postgres_asyncpg_uses_pool_args(self) -> None:
        s = _settings("postgresql+asyncpg://u:p@host/db", db_pool_size=7)
        engine = create_engine_from_settings(s)
        assert engine.pool.size() == 7
