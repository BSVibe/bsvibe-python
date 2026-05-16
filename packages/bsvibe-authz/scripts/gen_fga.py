#!/usr/bin/env python3
"""Generate ``schema/bsvibe.fga`` from ``schema/permission_matrix.yaml``.

The matrix is the single source of truth for per-resource permissions
(Tier 5). Run after editing the matrix:

    uv run --with pyyaml python scripts/gen_fga.py

The byte-identical copy at ``BSVibe-Auth/infra/openfga/bsvibe.fga`` must
be re-synced separately (drift-guard CI checks it).
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schema"
MATRIX_PATH = SCHEMA_DIR / "permission_matrix.yaml"
FGA_PATH = SCHEMA_DIR / "bsvibe.fga"

# Static preamble — identity + platform + tenant-role types. The tenant
# type's per-resource relations are generated and appended below.
PREAMBLE = """\
model
  schema 1.1

# BSVibe Authorization Model — GENERATED FILE, do not hand-edit.
#
# Source: schema/permission_matrix.yaml. Regenerate with
#   uv run --with pyyaml python scripts/gen_fga.py
#
# Tier 5 (2026-05-16): per-resource granularity. Each
# `<product>.<resource>.<action>` permission is a distinct relation on
# the `tenant` type, mapped to a minimum role. Resource-instance types
# (project/task/vault/note/rule/apikey/cost_report/alert) were removed —
# no route did instance-scoped checks.

# ─── Identity types ───────────────────────────────────────────────
type user

type service

# ─── Platform-wide ────────────────────────────────────────────────
type system
  relations
    define admin: [user]
    define audit_reader: [user] or admin
    define support: [user] or admin

# ─── Subscription plan ────────────────────────────────────────────
type plan
  relations
    define subscriber: [tenant]
    define read: subscriber

# ─── Feature gating (caveat-based) ────────────────────────────────
type feature
  relations
    define enabled_for: [plan]
    define read: enabled_for
"""

ROLE_BLOCK = """\

# ─── Tenant ───────────────────────────────────────────────────────
# 4 fixed roles, hierarchical: owner ⊃ admin ⊃ member ⊃ viewer.
# Per-resource relations below are generated from permission_matrix.yaml;
# each is a thin alias over the role hierarchy.
type tenant
  relations
    define owner: [user]
    define admin: [user] or owner
    define member: [user, service] or admin
    define viewer: [user, service] or member
    define plan: [plan]
"""


def main() -> int:
    matrix = yaml.safe_load(MATRIX_PATH.read_text())
    roles = set(matrix["roles"])

    lines = [PREAMBLE, ROLE_BLOCK]
    gen: list[str] = []
    for product, resources in sorted(matrix["products"].items()):
        gen.append(f"    # {product}")
        for resource, actions in sorted(resources.items()):
            for action, role in sorted(actions.items()):
                if role not in roles:
                    print(
                        f"error: {product}.{resource}.{action} -> unknown role {role!r}",
                        file=sys.stderr,
                    )
                    return 1
                rel = f"{product}_{resource}_{action}"
                gen.append(f"    define {rel}: {role}")
    lines.append("\n".join(gen) + "\n")

    FGA_PATH.write_text("".join(lines))
    print(f"wrote {FGA_PATH} ({len(gen) - len(matrix['products'])} relations)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
