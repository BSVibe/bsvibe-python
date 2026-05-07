"""Workspace-level import smoke test.

Each package's own suite lives under ``packages/<pkg>/tests/``. This
top-level suite exists so that:

* ``pytest tests/`` (used by the local pre-commit verifier) has a
  collection target at the workspace root, and
* every workspace member is at minimum importable after ``uv sync`` —
  catching path/install drift the per-package suites can't see.

Add an entry here for any new workspace member.
"""

from __future__ import annotations

import importlib

import pytest

WORKSPACE_PACKAGES = (
    "bsvibe_alerts",
    "bsvibe_audit",
    "bsvibe_authz",
    "bsvibe_cli_base",
    "bsvibe_core",
    "bsvibe_fastapi",
    "bsvibe_llm",
    "bsvibe_sqlalchemy",
)


@pytest.mark.parametrize("module_name", WORKSPACE_PACKAGES)
def test_workspace_member_importable(module_name: str) -> None:
    importlib.import_module(module_name)
