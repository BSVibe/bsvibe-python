# bsvibe-python

BSVibe shared Python libraries.

A [uv workspace](https://docs.astral.sh/uv/concepts/workspaces/) targeting Python 3.11+.

## Planned Packages

- **bsvibe-authz** — shared authorization library (first package, current placeholder)
- **bsvibe-core** — shared utilities (Phase A)
- **bsvibe-fastapi** — FastAPI helpers (Phase A)
- **bsvibe-sqlalchemy** — SQLAlchemy helpers (Phase A)
- **bsvibe-llm** — LLM client utilities (Phase A)
- **bsvibe-alerts** — alerting helpers (Phase A)
- **bsvibe-audit** — audit logging (Phase A)

## Status

`bsvibe-authz` is the first placeholder package. Other packages will be added in Phase A.

## Development

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run ruff check .
uv run pytest
```
