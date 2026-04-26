"""Tests for the Alembic helper module.

Two responsibilities pinned here:

* :func:`resolve_sync_alembic_url` — extracted from BSGateway PR #22
  S3-5. Alembic's ``run_migrations`` helpers are sync, so any
  ``postgresql+asyncpg://...`` runtime DSN must be normalised to a
  sync DBAPI before it is fed to ``engine_from_config``. ``psycopg``
  (v3, sync) is the canonical replacement.
* :func:`verify_alembic_parity` — runs the BSGateway S3-5 parity gate:
  spin up two PG containers, apply (a) the legacy raw-SQL bootstrap
  and (b) ``alembic upgrade head`` against fresh DBs, and diff
  ``pg_dump --schema-only``. Tests use an injectable ``runner`` so we
  do not actually call docker — the integration story is covered by
  the bundled shell script.
"""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock

import pytest

from bsvibe_sqlalchemy.alembic import (
    ParityResult,
    resolve_sync_alembic_url,
    verify_alembic_parity,
)


class TestResolveSyncAlembicUrl:
    """Normalise async DSNs to a sync DBAPI for Alembic.

    BSGateway S3-5 used psycopg v3 (sync) explicitly because it is the
    successor to psycopg2 and works with SQLAlchemy 2.0 ``+psycopg``.
    """

    def test_asyncpg_to_psycopg(self) -> None:
        assert resolve_sync_alembic_url("postgresql+asyncpg://u:p@h/db") == "postgresql+psycopg://u:p@h/db"

    def test_bare_postgresql_to_psycopg(self) -> None:
        assert resolve_sync_alembic_url("postgresql://u:p@h/db") == "postgresql+psycopg://u:p@h/db"

    def test_already_psycopg_passthrough(self) -> None:
        assert resolve_sync_alembic_url("postgresql+psycopg://u:p@h/db") == "postgresql+psycopg://u:p@h/db"

    def test_sqlite_passthrough(self) -> None:
        # SQLite stays as-is — Alembic supports aiosqlite via run_sync
        # bridges in modern envs. Tests using sqlite for migrations
        # should pass an explicit sync URL.
        assert resolve_sync_alembic_url("sqlite:///foo.db") == "sqlite:///foo.db"

    def test_aiosqlite_normalised_to_sqlite(self) -> None:
        # ``aiosqlite`` is async-only. Alembic's sync helpers cannot use
        # it; rewrite to bare sqlite.
        assert resolve_sync_alembic_url("sqlite+aiosqlite:///foo.db") == "sqlite:///foo.db"

    def test_empty_url_raises(self) -> None:
        with pytest.raises(ValueError):
            resolve_sync_alembic_url("")


class TestVerifyAlembicParity:
    """``verify_alembic_parity`` orchestrates the BSGateway S3-5 gate.

    All docker subprocess calls go through an injected ``runner`` so
    these tests can exercise the orchestration logic without spinning
    up containers.
    """

    def _ok_completed(self, stdout: bytes = b"", returncode: int = 0) -> subprocess.CompletedProcess:
        cp: subprocess.CompletedProcess = MagicMock(spec=subprocess.CompletedProcess)
        cp.returncode = returncode
        cp.stdout = stdout
        cp.stderr = b""
        return cp

    def test_parity_match_returns_ok(self, tmp_path) -> None:
        runner = MagicMock(side_effect=self._mk_runner_ok())

        # Same dump for both branches → parity OK.
        result = verify_alembic_parity(
            raw_sql_files=[tmp_path / "schema.sql"],
            alembic_directory=tmp_path,
            runner=runner,
            dump_normaliser=lambda s: s,
        )
        assert isinstance(result, ParityResult)
        assert result.ok is True
        assert result.diff == ""

    def test_parity_drift_returns_failure_with_diff(self, tmp_path) -> None:
        # Build a runner that returns *different* dumps for the two
        # containers so the parity check fails.
        call_count = {"i": 0}

        def runner(cmd: list[str], **_: object) -> subprocess.CompletedProcess:
            call_count["i"] += 1
            cp: subprocess.CompletedProcess = MagicMock(spec=subprocess.CompletedProcess)
            cp.returncode = 0
            cp.stderr = b""
            # ``pg_dump`` is the call we differentiate on. The first
            # pg_dump (raw SQL container) returns dump A, the second
            # (alembic container) returns dump B.
            if "pg_dump" in cmd:
                if "raw" in " ".join(cmd):
                    cp.stdout = b"CREATE TABLE foo (id INT);\n"
                else:
                    cp.stdout = b"CREATE TABLE foo (id BIGINT);\n"
            else:
                cp.stdout = b""
            return cp

        result = verify_alembic_parity(
            raw_sql_files=[tmp_path / "schema.sql"],
            alembic_directory=tmp_path,
            runner=runner,
            dump_normaliser=lambda s: s,
            container_names=("raw_test", "alembic_test"),
        )
        assert result.ok is False
        assert "INT" in result.diff
        assert "BIGINT" in result.diff

    def test_runner_errors_are_propagated(self, tmp_path) -> None:
        def runner(cmd: list[str], **_: object) -> subprocess.CompletedProcess:
            # Simulate "docker run" failing.
            raise subprocess.CalledProcessError(1, cmd)

        with pytest.raises(subprocess.CalledProcessError):
            verify_alembic_parity(
                raw_sql_files=[tmp_path / "schema.sql"],
                alembic_directory=tmp_path,
                runner=runner,
                dump_normaliser=lambda s: s,
            )

    def test_default_dump_normaliser_strips_volatile_lines(self, tmp_path) -> None:
        from bsvibe_sqlalchemy.alembic import default_dump_normaliser

        sample = (
            "-- Dumped by pg_dump version 16\n"
            "-- Started on 2026-04-26 11:00:00 UTC\n"
            "SET statement_timeout = 0;\n"
            "SELECT pg_catalog.set_config('search_path', '', false);\n"
            "\n"
            "CREATE TABLE foo (id INT);\n"
        )
        normalised = default_dump_normaliser(sample)
        assert "Dumped" not in normalised
        assert "Started on" not in normalised
        assert "SET statement_timeout" not in normalised
        assert "pg_catalog" not in normalised
        assert "CREATE TABLE foo" in normalised

    def test_default_normaliser_strips_alembic_version_block(self) -> None:
        from bsvibe_sqlalchemy.alembic import default_dump_normaliser

        sample = (
            "-- Name: alembic_version; Type: TABLE; Schema: public;\n"
            "CREATE TABLE alembic_version (version_num VARCHAR(32));\n"
            "-- Name: foo; Type: TABLE; Schema: public;\n"
            "CREATE TABLE foo (id INT);\n"
        )
        out = default_dump_normaliser(sample)
        assert "alembic_version" not in out
        assert "foo" in out

    def test_default_normaliser_strips_bare_create_alembic_version(self) -> None:
        """Even without the ``-- Name:`` header, a bare CREATE TABLE
        alembic_version statement must be dropped — defensive against
        dumps emitted via the API rather than ``pg_dump``."""
        from bsvibe_sqlalchemy.alembic import default_dump_normaliser

        sample = "CREATE TABLE alembic_version (version_num VARCHAR(32));\nCREATE TABLE foo (id INT);\n"
        out = default_dump_normaliser(sample)
        assert "alembic_version" not in out
        assert "foo" in out

    def test_pg_wait_failure_raises(self, tmp_path) -> None:
        """If pg_isready never returns 0, _wait_for_pg must raise so the
        whole parity gate fails fast rather than silently hanging."""
        from bsvibe_sqlalchemy.alembic import _wait_for_pg

        def runner(cmd: list[str], **_: object) -> subprocess.CompletedProcess:
            cp: subprocess.CompletedProcess = MagicMock(spec=subprocess.CompletedProcess)
            # Simulate pg_isready always returning non-zero.
            cp.returncode = 1
            cp.stdout = b""
            cp.stderr = b""
            return cp

        with pytest.raises(RuntimeError, match="did not become ready"):
            # 2 attempts with a stubbed sleep keeps the test fast.
            with pytest.MonkeyPatch().context() as mp:
                mp.setattr("bsvibe_sqlalchemy.alembic.time.sleep", lambda _: None)
                _wait_for_pg(runner, "test_container", attempts=2)

    def _mk_runner_ok(self):
        """Stateful runner that returns identical pg_dumps for both
        container flavours."""
        identical_dump = b"CREATE TABLE foo (id INT);\n"

        def runner(cmd: list[str], **_: object) -> subprocess.CompletedProcess:
            cp: subprocess.CompletedProcess = MagicMock(spec=subprocess.CompletedProcess)
            cp.returncode = 0
            cp.stderr = b""
            cp.stdout = identical_dump if "pg_dump" in cmd else b""
            return cp

        return runner
