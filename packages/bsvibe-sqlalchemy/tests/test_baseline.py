"""Tests for the baseline migration helpers.

These helpers codify the BSGateway PR #22 S3-5 pattern:

* The baseline migration is hand-written (no autogenerate, no ORM
  metadata reflection). ``target_metadata = None`` in env.py.
* Each DDL statement is applied via ``op.execute(...)`` with the
  **verbatim** legacy SQL — including ``CREATE TABLE IF NOT EXISTS``
  and ``CREATE INDEX IF NOT EXISTS`` so ``alembic stamp head`` on a
  prod DB whose schema already exists is a no-op apart from recording
  the revision.
* :func:`render_baseline_migration` returns the migration body as a
  string. Products call it from their own Alembic ``versions/`` dir
  (or copy-paste the result into a hand-written file).

We do not exercise Alembic itself in unit tests — that lives in the
parity gate (``verify_alembic_parity``) and the bundled shell script.
"""

from __future__ import annotations

import re

import pytest

from bsvibe_sqlalchemy.baseline import (
    BaselineStatement,
    apply_baseline_statements,
    render_baseline_migration,
    revert_baseline_statements,
)


SAMPLE_DDL = [
    BaselineStatement(
        name="routing_logs",
        ddl="""
        CREATE TABLE IF NOT EXISTS routing_logs (
            id SERIAL PRIMARY KEY,
            tier TEXT NOT NULL
        )
        """,
    ),
    BaselineStatement(
        name="routing_logs_idx_tier",
        ddl="CREATE INDEX IF NOT EXISTS idx_routing_logs_tier ON routing_logs(tier)",
    ),
]


class TestBaselineStatement:
    def test_constructs_with_required_fields(self) -> None:
        stmt = BaselineStatement(name="t", ddl="CREATE TABLE t (id INT)")
        assert stmt.name == "t"
        assert stmt.ddl.strip().startswith("CREATE TABLE")


class TestRenderBaselineMigration:
    def test_includes_revision_id(self) -> None:
        body = render_baseline_migration(
            revision_id="0001_baseline",
            statements=SAMPLE_DDL,
        )
        assert 'revision: str = "0001_baseline"' in body

    def test_down_revision_is_none(self) -> None:
        body = render_baseline_migration(
            revision_id="0001_baseline",
            statements=SAMPLE_DDL,
        )
        # Match either single or no-quotes form; pin the convention.
        assert re.search(r"down_revision[^=]*=\s*None", body)

    def test_upgrade_calls_op_execute_per_statement(self) -> None:
        body = render_baseline_migration(
            revision_id="0001_baseline",
            statements=SAMPLE_DDL,
        )
        assert "def upgrade()" in body
        # Each statement's DDL must appear verbatim.
        assert "CREATE TABLE IF NOT EXISTS routing_logs" in body
        assert "idx_routing_logs_tier" in body
        # Must use op.execute(...) not autogenerate's op.create_table(...)
        assert "op.execute" in body
        assert "op.create_table(" not in body

    def test_downgrade_drops_in_reverse_order(self) -> None:
        body = render_baseline_migration(
            revision_id="0001_baseline",
            statements=SAMPLE_DDL,
            drop_tables=["routing_logs"],
        )
        assert "def downgrade()" in body
        assert "DROP TABLE IF EXISTS routing_logs" in body
        # CASCADE is the BSGateway S3-5 convention.
        assert "CASCADE" in body

    def test_no_drop_tables_yields_empty_downgrade(self) -> None:
        body = render_baseline_migration(
            revision_id="0001_baseline",
            statements=SAMPLE_DDL,
            drop_tables=[],
        )
        assert "def downgrade()" in body
        # A pass body is acceptable.
        assert "pass" in body or "DROP TABLE" not in body

    def test_includes_module_docstring(self) -> None:
        body = render_baseline_migration(
            revision_id="0001_baseline",
            statements=SAMPLE_DDL,
            description="Sample legacy schema",
        )
        assert '"""' in body
        assert "Sample legacy schema" in body

    def test_revision_id_required(self) -> None:
        with pytest.raises(ValueError):
            render_baseline_migration(revision_id="", statements=SAMPLE_DDL)

    def test_statements_required(self) -> None:
        with pytest.raises(ValueError):
            render_baseline_migration(revision_id="0001_baseline", statements=[])


class TestApplyBaselineStatements:
    """``apply_baseline_statements`` runs each DDL via ``op.execute``.

    Used inside a hand-written migration file to keep the upgrade body
    a one-liner. The mock here stands in for ``alembic.op``.
    """

    def test_executes_each_statement_in_order(self) -> None:
        executed: list[str] = []

        class FakeOp:
            @staticmethod
            def execute(stmt: str) -> None:
                executed.append(stmt)

        apply_baseline_statements(FakeOp, SAMPLE_DDL)
        assert len(executed) == 2
        assert "routing_logs" in executed[0]
        assert "idx_routing_logs_tier" in executed[1]

    def test_revert_drops_tables_in_reverse(self) -> None:
        executed: list[str] = []

        class FakeOp:
            @staticmethod
            def execute(stmt: str) -> None:
                executed.append(stmt)

        revert_baseline_statements(FakeOp, drop_tables=["a", "b", "c"])
        assert executed == [
            "DROP TABLE IF EXISTS c CASCADE",
            "DROP TABLE IF EXISTS b CASCADE",
            "DROP TABLE IF EXISTS a CASCADE",
        ]
