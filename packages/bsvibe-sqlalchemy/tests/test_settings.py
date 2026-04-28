"""Tests for the DatabaseSettings mixin.

DatabaseSettings is a BsvibeSettings subclass shared by all four BSVibe
products. The pool sizing knobs and ``pool_pre_ping=True`` default were
extracted verbatim from BSupervisor PR #13 §M20.

The wire format pinned here:

* ``database_url`` is required.
* ``db_pool_size``, ``db_max_overflow``, ``db_pool_timeout``,
  ``db_pool_recycle`` are all ``int`` fields with sensible defaults
  matching BSupervisor §M20.
* ``db_pool_pre_ping`` defaults to ``True`` (Sprint 3 / Audit roll-up
  decision — every product turns this on by default to survive
  long-lived connections being closed by the DB or load balancer).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from bsvibe_sqlalchemy.settings import DatabaseSettings


class TestDatabaseSettingsDefaults:
    def test_database_url_is_required(self) -> None:
        with pytest.raises(ValidationError):
            DatabaseSettings()  # type: ignore[call-arg]

    def test_pool_size_default_is_ten(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@h/db")
        s = DatabaseSettings()
        assert s.db_pool_size == 10

    def test_max_overflow_default_is_twenty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@h/db")
        s = DatabaseSettings()
        assert s.db_max_overflow == 20

    def test_pool_timeout_default_is_thirty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@h/db")
        s = DatabaseSettings()
        assert s.db_pool_timeout == 30

    def test_pool_recycle_default_is_1800(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@h/db")
        s = DatabaseSettings()
        assert s.db_pool_recycle == 1800

    def test_pool_pre_ping_default_is_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """pool_pre_ping=True is the BSVibe default (audit roll-up)."""
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@h/db")
        s = DatabaseSettings()
        assert s.db_pool_pre_ping is True

    def test_echo_default_is_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@h/db")
        s = DatabaseSettings()
        assert s.db_echo is False


class TestDatabaseSettingsEnvOverride:
    def test_database_url_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@h/db")
        s = DatabaseSettings()
        assert s.database_url == "postgresql+asyncpg://u:p@h/db"

    def test_pool_size_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@h/db")
        monkeypatch.setenv("DB_POOL_SIZE", "25")
        s = DatabaseSettings()
        assert s.db_pool_size == 25

    def test_pool_pre_ping_can_be_disabled_via_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@h/db")
        monkeypatch.setenv("DB_POOL_PRE_PING", "false")
        s = DatabaseSettings()
        assert s.db_pool_pre_ping is False


class TestDatabaseSettingsValidation:
    def test_negative_pool_size_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@h/db")
        monkeypatch.setenv("DB_POOL_SIZE", "-1")
        with pytest.raises(ValidationError):
            DatabaseSettings()

    def test_negative_max_overflow_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@h/db")
        monkeypatch.setenv("DB_MAX_OVERFLOW", "-1")
        with pytest.raises(ValidationError):
            DatabaseSettings()


class TestDatabaseSettingsExtra:
    def test_extra_env_vars_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Inherits BsvibeSettings.model_config(extra='ignore').

        Products carry their own settings, so unknown env vars must not
        crash startup.
        """
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@h/db")
        monkeypatch.setenv("BSVIBE_UNKNOWN_FIELD", "x")
        s = DatabaseSettings()
        assert s.database_url == "postgresql+asyncpg://u:p@h/db"
