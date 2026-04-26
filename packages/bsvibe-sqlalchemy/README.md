# bsvibe-sqlalchemy

Shared SQLAlchemy + Alembic primitives for the four BSVibe products
(BSGateway, BSNexus, BSupervisor, BSage).

## What's in here

| Module | Purpose | Source |
|---|---|---|
| `settings.DatabaseSettings` | pydantic-settings mixin with the five pool knobs + `pool_pre_ping=True` default + `db_echo` | BSupervisor PR #13 §M20 |
| `engine.create_engine_from_settings` | async engine factory with SQLite/Postgres branching | BSupervisor PR #13 §M20 |
| `session.create_session_factory` | `async_sessionmaker(..., expire_on_commit=False)` wrapper | 4-product convention |
| `session.make_get_db` | `Depends(get_db)` async generator | BSNexus `storage/database.py` |
| `session.dispose_engine` | lifespan-friendly idempotent dispose | (new helper) |
| `alembic.resolve_sync_alembic_url` | rewrite async DSNs to `+psycopg` for sync Alembic | BSGateway PR #22 S3-5 `env.py` |
| `alembic.verify_alembic_parity` | docker-driven raw-SQL ↔ `alembic upgrade head` parity gate | BSGateway PR #22 S3-5 `verify_alembic_parity.sh` |
| `alembic.default_dump_normaliser` | strip volatile lines + `alembic_version` block | same |
| `baseline.render_baseline_migration` | render a hand-written verbatim-DDL baseline | BSGateway PR #22 S3-5 `0001_baseline_schema.py` |
| `baseline.apply_baseline_statements` / `revert_baseline_statements` | one-liner upgrade / downgrade bodies | same |

## Installation

`bsvibe-python` is a uv workspace. Inside the workspace, declare the
dependency in your product `pyproject.toml`:

```toml
[project]
dependencies = [
    "bsvibe-sqlalchemy @ git+https://github.com/BSVibe/bsvibe-python.git@v0.1.0#subdirectory=packages/bsvibe-sqlalchemy",
    # bsvibe-sqlalchemy declares bsvibe-core[workspace] as a sibling
    # dependency — uv resolves it via the workspace.
]
```

## Usage

### 1. Settings

```python
from bsvibe_sqlalchemy import DatabaseSettings


class Settings(DatabaseSettings):
    # Add product-specific fields here. The five pool knobs +
    # pool_pre_ping=True + db_echo + database_url come from the mixin.
    debug: bool = False
    redis_url: str = ""


settings = Settings()  # auto-loads from env / .env
```

### 2. Engine + sessions

```python
from bsvibe_sqlalchemy import (
    create_engine_from_settings,
    create_session_factory,
    make_get_db,
    dispose_engine,
)

engine = create_engine_from_settings(settings)
async_session = create_session_factory(engine)
get_db = make_get_db(async_session)

# FastAPI lifespan
@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await dispose_engine(engine)


# FastAPI route
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession


@router.get("/items")
async def list_items(db: AsyncSession = Depends(get_db)) -> ...:
    ...
```

The factory branches on `settings.database_url`:

* `sqlite[+aiosqlite]://...` → no `pool_size` etc. (SQLite uses
  `NullPool` / `StaticPool`, which would `TypeError` on pool args).
* anything else → wires `pool_size`, `max_overflow`, `pool_timeout`,
  `pool_recycle`, `pool_pre_ping` from settings.

### 3. Alembic env.py

```python
# alembic/env.py
from alembic import context
from sqlalchemy import engine_from_config, pool

from bsvibe_sqlalchemy import resolve_sync_alembic_url
from myproduct.config import settings

config = context.config

# Hand-written, verbatim-DDL baseline → no autogenerate, no metadata.
target_metadata = None


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = resolve_sync_alembic_url(settings.database_url)
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
```

### 4. Baseline migration (BSGateway S3-5 pattern)

Write a hand-written `alembic/versions/0001_baseline_schema.py` that
mirrors your legacy raw-SQL bootstrap **byte-for-byte**:

```python
# alembic/versions/0001_baseline_schema.py
from __future__ import annotations
from collections.abc import Sequence

from alembic import op
from bsvibe_sqlalchemy import (
    BaselineStatement,
    apply_baseline_statements,
    revert_baseline_statements,
)

revision: str = "0001_baseline"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_STATEMENTS = [
    BaselineStatement(
        name="routing_logs",
        ddl="""
        CREATE TABLE IF NOT EXISTS routing_logs (
            id SERIAL PRIMARY KEY,
            tier TEXT NOT NULL
        )
        """,
    ),
    # ... one per legacy DDL fragment
]
_DROP_TABLES = ["routing_logs"]  # upgrade order; downgrade reverses


def upgrade() -> None:
    apply_baseline_statements(op, _STATEMENTS)


def downgrade() -> None:
    revert_baseline_statements(op, drop_tables=_DROP_TABLES)
```

Or render the entire file from a list of statements:

```python
from bsvibe_sqlalchemy import render_baseline_migration, BaselineStatement

source = render_baseline_migration(
    revision_id="0001_baseline",
    statements=[BaselineStatement(name="...", ddl="...")],
    drop_tables=["..."],
    description="Union of legacy raw-SQL files",
)
Path("alembic/versions/0001_baseline_schema.py").write_text(source)
```

### 5. Parity verification (Lockin decision #3)

Before stamping prod, verify that `alembic upgrade head` produces a
schema byte-identical to the legacy raw-SQL bootstrap:

```bash
RAW_SQL_FILES="src/sql/schema.sql src/sql/tenant_schema.sql" \
    ALEMBIC_DIR=. \
    ./scripts/verify_alembic_parity.sh
```

Or from Python (e.g. inside a CI step):

```python
from pathlib import Path
from bsvibe_sqlalchemy import verify_alembic_parity

result = verify_alembic_parity(
    raw_sql_files=[Path("src/sql/schema.sql"), Path("src/sql/tenant_schema.sql")],
    alembic_directory=Path("."),
)
if not result.ok:
    print(result.diff)
    raise SystemExit(1)
```

## Migration pattern (per product)

The four products migrate sequentially (BSupervisor → BSage → BSGateway
→ BSNexus). Each product PR:

1. Adds `bsvibe-sqlalchemy` to `pyproject.toml`.
2. Replaces local `database.py` / `engine.py` / `session.py` with
   `from bsvibe_sqlalchemy import ...` calls.
3. Updates `alembic/env.py` to call `resolve_sync_alembic_url`.
4. (If not already done) writes the baseline migration via
   `apply_baseline_statements` + `revert_baseline_statements`.
5. Wires `verify_alembic_parity.sh` into pre-deploy CI.
6. **Lockin decision #3**: prod gets one `alembic stamp head` after
   merge; staging runs `alembic upgrade head` against a fresh DB.

## Versioning

`bsvibe-sqlalchemy` follows the workspace's git tag versioning. Pin the
git ref (`@v0.1.0`) in product dependencies — major bumps (e.g.
flipping the `expire_on_commit` default) are wire-breaking.

## See also

- `BSVibe_Execution_Lockin.md` §3 (decision #3 — Alembic baseline
  procedure)
- `BSVibe_Shared_Library_Roadmap.md` §4 (`bsvibe-sqlalchemy` package
  scope)
- `bsvibe-core` — shared `BsvibeSettings` base + structlog setup
- `bsvibe-fastapi` — shared CORS / health router (sibling package)
