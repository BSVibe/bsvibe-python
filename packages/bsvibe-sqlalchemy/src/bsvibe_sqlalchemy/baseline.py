"""Alembic baseline migration helpers.

Codifies the BSGateway PR #22 S3-5 baseline pattern:

* The baseline migration is **hand-written** (no autogenerate, no ORM
  metadata reflection). ``target_metadata = None`` in env.py.
* Each DDL statement is applied via ``op.execute(...)`` with the
  **verbatim** legacy SQL — including ``CREATE TABLE IF NOT EXISTS``
  and ``CREATE INDEX IF NOT EXISTS`` — so ``alembic stamp head`` on a
  prod DB whose schema already exists is a true no-op.
* The downgrade body issues ``DROP TABLE IF EXISTS … CASCADE`` for
  each known table in reverse dependency order. Prod will never run
  downgrade against the baseline (Lockin decision #3); the body
  exists for staging and for the alembic round-trip parity check.

Lockin decision #3 (BSVibe Execution Lockin §3) governs deployment:

* **staging**: ``alembic upgrade head`` — no data, runs every statement.
* **prod**: one ``alembic stamp head`` (on the existing schema), then
  every subsequent migration via ``alembic upgrade head``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class BaselineStatement:
    """One DDL statement in a baseline migration.

    Attributes:
        name: Human-friendly identifier (table name, index name, etc.)
              used in comments/logging. Not consumed by Alembic.
        ddl: The raw SQL fragment. Should use ``IF NOT EXISTS`` so a
             stamped prod DB whose schema already exists treats every
             statement as a no-op.
    """

    name: str
    ddl: str


_TEMPLATE = '''"""baseline schema (initial Alembic adoption)

Revision ID: {revision_id}
Revises:
Create Date: auto-generated

{description}

This migration is **hand-written** to mirror the legacy raw-SQL
bootstrap. ``target_metadata = None`` in env.py disables autogenerate.
Each statement uses ``op.execute`` with verbatim DDL (``CREATE … IF
NOT EXISTS``) so ``alembic stamp head`` on a prod DB whose schema
already exists is a true no-op.

Lockin decision #3 (BSVibe Execution Lockin §3) governs deployment:

* **staging**: ``alembic upgrade head`` — no data, runs every statement.
* **prod**: one ``alembic stamp head`` once, then normal ``upgrade head``.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "{revision_id}"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# Verbatim DDL — order matters when statements have FK dependencies.
# ---------------------------------------------------------------------------

_STATEMENTS: list[tuple[str, str]] = [
{statements_block}
]

_DROP_TABLES: list[str] = [
{drop_block}
]


def upgrade() -> None:
    """Apply every legacy DDL statement, in order."""
    for _name, ddl in _STATEMENTS:
        op.execute(ddl)


def downgrade() -> None:
    """Drop every table created by ``upgrade`` in reverse order.

    NOTE: prod will never downgrade past the baseline (Lockin decision
    #3). This body exists for staging and the alembic round-trip
    parity check (``upgrade head → downgrade -1 → upgrade head``).
    """
{downgrade_body}
'''


def _format_statements_block(statements: Sequence[BaselineStatement]) -> str:
    lines: list[str] = []
    for stmt in statements:
        # repr() handles embedded newlines + quotes safely.
        lines.append(f"    ({stmt.name!r}, {stmt.ddl!r}),")
    return "\n".join(lines)


def _format_drop_block(drop_tables: Sequence[str]) -> str:
    if not drop_tables:
        return "    # no tables registered for downgrade"
    return "\n".join(f"    {t!r}," for t in drop_tables)


def _format_downgrade_body(drop_tables: Sequence[str]) -> str:
    if not drop_tables:
        return "    pass"
    # Emit one explicit ``op.execute("DROP TABLE IF EXISTS <name> CASCADE")``
    # per table, in reverse upgrade order. This mirrors BSGateway PR #22
    # S3-5 verbatim — generating a loop would hide the table names from
    # ``grep`` and from PR review.
    lines: list[str] = []
    for table in reversed(list(drop_tables)):
        lines.append(f'    op.execute("DROP TABLE IF EXISTS {table} CASCADE")')
    return "\n".join(lines)


def render_baseline_migration(
    *,
    revision_id: str,
    statements: Sequence[BaselineStatement],
    drop_tables: Sequence[str] = (),
    description: str = "",
) -> str:
    """Render a hand-written baseline migration as a Python source string.

    Args:
        revision_id: Stable id for the baseline (e.g. ``0001_baseline``).
            Pin this in tests so prod stamping does not silently drift.
        statements: Ordered DDL statements. Each is wrapped in an
            ``op.execute`` call. Order is load-bearing — FKs require a
            specific upgrade order.
        drop_tables: Table names in **upgrade order**; the rendered
            downgrade reverses them and emits
            ``DROP TABLE IF EXISTS … CASCADE`` for each.
        description: Free-form text inserted in the module docstring
            (typically a short summary of which legacy SQL files this
            migration unifies).

    Returns:
        The full Python source for ``alembic/versions/<revision_id>_baseline_schema.py``.
    """
    if not revision_id:
        raise ValueError("revision_id is required")
    if not statements:
        raise ValueError("at least one statement is required")
    return _TEMPLATE.format(
        revision_id=revision_id,
        description=description or "Baseline schema (extracted from legacy raw SQL).",
        statements_block=_format_statements_block(statements),
        drop_block=_format_drop_block(drop_tables),
        downgrade_body=_format_downgrade_body(drop_tables),
    )


def apply_baseline_statements(op_module: Any, statements: Sequence[BaselineStatement]) -> None:
    """Apply each baseline statement via ``op.execute``.

    Used inside a hand-written migration file as a one-liner upgrade
    body. Accepts the ``alembic.op`` module (or a stub for tests).
    """
    for stmt in statements:
        op_module.execute(stmt.ddl)


def revert_baseline_statements(op_module: Any, *, drop_tables: Sequence[str]) -> None:
    """Drop each table in reverse order via
    ``DROP TABLE IF EXISTS … CASCADE``.

    Mirrors the BSGateway S3-5 downgrade body. Used inside a
    hand-written migration file as a one-liner downgrade body.
    """
    for table in reversed(list(drop_tables)):
        op_module.execute(f"DROP TABLE IF EXISTS {table} CASCADE")


__all__ = [
    "BaselineStatement",
    "render_baseline_migration",
    "apply_baseline_statements",
    "revert_baseline_statements",
]
