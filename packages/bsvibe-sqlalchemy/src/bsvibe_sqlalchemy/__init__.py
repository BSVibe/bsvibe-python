"""BSVibe shared SQLAlchemy library — public API.

Stable imports for product code:

.. code-block:: python

    from bsvibe_sqlalchemy import (
        DatabaseSettings,
        create_engine_from_settings,
        create_session_factory,
        make_get_db,
        dispose_engine,
        # Alembic
        resolve_sync_alembic_url,
        verify_alembic_parity,
        ParityResult,
        # Baseline migration helpers
        BaselineStatement,
        render_baseline_migration,
        apply_baseline_statements,
        revert_baseline_statements,
    )

The four products migrate to this package by:

1. Replacing local ``database.py`` with a thin wrapper over
   :func:`create_engine_from_settings` + :func:`create_session_factory`.
2. Replacing local ``alembic/env.py`` URL resolution with
   :func:`resolve_sync_alembic_url`.
3. Adopting :func:`render_baseline_migration` (or hand-writing a
   migration that calls :func:`apply_baseline_statements` /
   :func:`revert_baseline_statements`) for the next baseline revision.
4. Wiring ``scripts/verify_alembic_parity.sh`` into pre-deploy CI.
"""

from __future__ import annotations

from bsvibe_sqlalchemy.alembic import (
    ParityResult,
    default_dump_normaliser,
    resolve_sync_alembic_url,
    verify_alembic_parity,
)
from bsvibe_sqlalchemy.baseline import (
    BaselineStatement,
    apply_baseline_statements,
    render_baseline_migration,
    revert_baseline_statements,
)
from bsvibe_sqlalchemy.engine import create_engine_from_settings
from bsvibe_sqlalchemy.session import (
    create_session_factory,
    dispose_engine,
    make_get_db,
)
from bsvibe_sqlalchemy.settings import DatabaseSettings

__version__ = "0.1.0"

__all__ = [
    # Settings
    "DatabaseSettings",
    # Engine + sessions
    "create_engine_from_settings",
    "create_session_factory",
    "make_get_db",
    "dispose_engine",
    # Alembic
    "resolve_sync_alembic_url",
    "verify_alembic_parity",
    "ParityResult",
    "default_dump_normaliser",
    # Baseline
    "BaselineStatement",
    "render_baseline_migration",
    "apply_baseline_statements",
    "revert_baseline_statements",
    "__version__",
]
