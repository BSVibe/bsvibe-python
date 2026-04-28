"""Public API stability for bsvibe-sqlalchemy.

If any name listed in ``__all__`` is removed or renamed, every product
import will break. Pin the surface here so a refactor that drops one
fails CI.
"""

from __future__ import annotations

import bsvibe_sqlalchemy


def test_version_string() -> None:
    assert isinstance(bsvibe_sqlalchemy.__version__, str)
    assert len(bsvibe_sqlalchemy.__version__) > 0


def test_public_api_includes_engine_factory() -> None:
    assert "create_engine_from_settings" in bsvibe_sqlalchemy.__all__
    from bsvibe_sqlalchemy import create_engine_from_settings  # noqa: F401


def test_public_api_includes_session_helpers() -> None:
    for name in ("create_session_factory", "make_get_db", "dispose_engine"):
        assert name in bsvibe_sqlalchemy.__all__


def test_public_api_includes_settings() -> None:
    assert "DatabaseSettings" in bsvibe_sqlalchemy.__all__


def test_public_api_includes_alembic_helpers() -> None:
    for name in (
        "resolve_sync_alembic_url",
        "verify_alembic_parity",
        "ParityResult",
    ):
        assert name in bsvibe_sqlalchemy.__all__


def test_public_api_includes_baseline_helpers() -> None:
    for name in (
        "BaselineStatement",
        "render_baseline_migration",
        "apply_baseline_statements",
        "revert_baseline_statements",
    ):
        assert name in bsvibe_sqlalchemy.__all__
