#!/usr/bin/env bash
# Schema-parity verification (BSGateway PR #22 S3-5 pattern).
#
# Spins up two ephemeral PG 16 containers, applies (a) the legacy raw-SQL
# bootstrap and (b) `alembic upgrade head` against fresh DBs, and diffs
# the resulting catalogues. Exits 0 on schema parity, non-zero otherwise.
#
# This is the same logic as ``bsvibe_sqlalchemy.alembic.verify_alembic_parity``
# packaged for use from CI / pre-deploy hooks where Python runtime
# bootstrap is undesirable.
#
# Usage:  RAW_SQL_FILES="path/a.sql path/b.sql" ALEMBIC_DIR=. \
#         ./verify_alembic_parity.sh
#
# Required env:
#   RAW_SQL_FILES — space-separated list of legacy SQL files (in order)
#   ALEMBIC_DIR   — directory containing alembic.ini (default: cwd)
# Optional env:
#   RAW_PORT      — host port for raw-SQL container (default 55501)
#   ALEMBIC_PORT  — host port for alembic container (default 55502)
set -euo pipefail

ALEMBIC_DIR="${ALEMBIC_DIR:-$(pwd)}"
RAW_PORT="${RAW_PORT:-55501}"
ALEMBIC_PORT="${ALEMBIC_PORT:-55502}"

if [[ -z "${RAW_SQL_FILES:-}" ]]; then
    echo "ERROR: RAW_SQL_FILES env var required (space-separated list of SQL files)" >&2
    exit 2
fi

CID_RAW="bsvibe-parity-raw-$$"
CID_ALEMBIC="bsvibe-parity-alembic-$$"

cleanup() {
    docker rm -f "$CID_RAW"     >/dev/null 2>&1 || true
    docker rm -f "$CID_ALEMBIC" >/dev/null 2>&1 || true
}
trap cleanup EXIT

start_pg() {
    local name="$1"
    local port="$2"
    docker run -d --rm \
        --name "$name" \
        -e POSTGRES_PASSWORD=parity \
        -e POSTGRES_USER=parity \
        -e POSTGRES_DB=parity \
        -p "$port":5432 \
        postgres:16-alpine >/dev/null
    for _ in $(seq 1 30); do
        if docker exec "$name" pg_isready -U parity >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    echo "ERROR: $name failed to start" >&2
    docker logs "$name" >&2 || true
    return 1
}

dump_schema() {
    local container="$1"
    local out="$2"
    docker exec -e PGPASSWORD=parity "$container" \
        pg_dump -U parity -d parity --schema-only --no-owner --no-privileges \
        > "$out"
    sed -i.bak \
        -e '/^-- Dumped/d' \
        -e '/^-- Started on/d' \
        -e '/^SET /d' \
        -e '/^SELECT pg_catalog/d' \
        -e '/^--$/d' \
        -e '/^$/d' \
        "$out"
    rm -f "$out.bak"
}

echo "==> Starting PG containers..."
start_pg "$CID_RAW"     "$RAW_PORT"
start_pg "$CID_ALEMBIC" "$ALEMBIC_PORT"

echo "==> Applying raw-SQL schema to $CID_RAW..."
for f in $RAW_SQL_FILES; do
    docker exec -i -e PGPASSWORD=parity "$CID_RAW" \
        psql -U parity -d parity -v ON_ERROR_STOP=1 -q < "$f" >/dev/null
done

echo "==> Applying alembic upgrade head to $CID_ALEMBIC..."
(
    cd "$ALEMBIC_DIR"
    DATABASE_URL="postgresql://parity:parity@127.0.0.1:$ALEMBIC_PORT/parity" \
        uv run alembic upgrade head

    echo "==> Round-trip: alembic downgrade -1 -> upgrade head..."
    DATABASE_URL="postgresql://parity:parity@127.0.0.1:$ALEMBIC_PORT/parity" \
        uv run alembic downgrade -1
    DATABASE_URL="postgresql://parity:parity@127.0.0.1:$ALEMBIC_PORT/parity" \
        uv run alembic upgrade head
)

echo "==> Dumping schemas..."
DUMP_RAW="$(mktemp)"
DUMP_ALEMBIC="$(mktemp)"
dump_schema "$CID_RAW"     "$DUMP_RAW"
dump_schema "$CID_ALEMBIC" "$DUMP_ALEMBIC"

echo "==> Diffing schemas..."
ALEMBIC_USER="$(mktemp)"
python3 - "$DUMP_ALEMBIC" "$ALEMBIC_USER" <<'PY'
import re, sys
src, dst = sys.argv[1], sys.argv[2]
lines = open(src).read().splitlines()
out = []
i = 0
n = len(lines)
DROP_OBJECT_NAMES = {"alembic_version", "alembic_version_pkc"}
while i < n:
    line = lines[i]
    if line.startswith("\\restrict") or line.startswith("\\unrestrict"):
        i += 1
        continue
    m = re.match(r"^-- Name: ([^;]+); Type:", line)
    if m:
        slug_tokens = set(m.group(1).split())
        if slug_tokens & DROP_OBJECT_NAMES:
            i += 1
            while i < n:
                stmt_line = lines[i]
                i += 1
                if stmt_line.rstrip().endswith(";"):
                    break
            continue
    out.append(line)
    i += 1
open(dst, "w").write("\n".join(out) + "\n")
PY
DUMP_RAW_CLEAN="$(mktemp)"
grep -v -E '^\\(un)?restrict' "$DUMP_RAW" > "$DUMP_RAW_CLEAN"
DUMP_RAW="$DUMP_RAW_CLEAN"

if diff -u "$DUMP_RAW" "$ALEMBIC_USER"; then
    echo "==> SCHEMA PARITY: OK (raw-SQL == alembic upgrade head)"
    exit 0
else
    echo "==> SCHEMA PARITY: FAILED -- see diff above" >&2
    echo "    Raw dump:     $DUMP_RAW" >&2
    echo "    Alembic dump: $ALEMBIC_USER" >&2
    exit 1
fi
