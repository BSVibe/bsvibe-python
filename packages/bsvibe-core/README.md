# bsvibe-core

Shared core library for the four BSVibe Python services (BSGateway,
BSNexus, BSupervisor, BSage). Provides the **settings mixin baseline**
and **structlog configuration** every service migrates onto in Phase A.

## Public API

```python
from bsvibe_core import (
    BsvibeSettings,        # pydantic-settings BaseSettings with BSVibe defaults
    configure_logging,     # structlog JSON wire format used by all four products
    BsvibeError,           # common exception base
    ConfigurationError,    # subclass: invalid config at startup
    ValidationError,       # subclass: business-rule violation
    NotFoundError,         # subclass: missing resource for caller
    csv_list_field,        # Field() helper for Annotated[list[str], NoDecode]
    parse_csv_list,        # CSV split helper (pinned wire format)
)

# Type aliases
from bsvibe_core.types import TenantId, UserId, RequestId, JsonDict, JsonValue
```

## Settings — CSV list pattern (extracted from BSupervisor PR #13 §M18)

`pydantic-settings>=2` JSON-decodes any `list[str]` env var by default. The
existing four products read CORS origins, alert recipients, etc. as
`os.environ.get(...).split(",")` — incompatible with that JSON decode
unless we opt out via `Annotated[list[str], NoDecode]`. This package
ships the canonical pattern so all four products share one wire format.

```python
from typing import Annotated
from pydantic import field_validator
from pydantic_settings import NoDecode

from bsvibe_core import BsvibeSettings, csv_list_field, parse_csv_list


class Settings(BsvibeSettings):
    cors_allowed_origins: Annotated[list[str], NoDecode] = csv_list_field(
        default=["http://localhost:3500"],
        alias="cors_allowed_origins",
    )

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def _parse_cors(cls, v: str | list[str] | None) -> list[str]:
        return parse_csv_list(v) or ["http://localhost:3500"]
```

`BsvibeSettings` defaults: `extra="ignore"` (products carry private
settings on top), `case_sensitive=False`, `env_file=None` (deployment
opts in).

## Logging — `configure_logging()`

```python
from bsvibe_core import configure_logging

configure_logging(
    level="info",            # str name or numeric logging.* level
    json_output=True,        # False → ConsoleRenderer for dev
    service_name="bsage",    # injected as `service` on every event
)
```

Wire format pinned by tests:

```json
{
  "event": "task_completed",
  "timestamp": "2026-04-26T07:00:00.000000Z",
  "level": "info",
  "service": "bsage",
  "request_id": "req-123",
  "task_name": "process-data"
}
```

`request_id` and any other contextvars value flow through automatically
via `structlog.contextvars.merge_contextvars`.

## Migration cheatsheet (4-product Phase A follow-up)

| Today | After Phase A |
|---|---|
| BSGateway `bsgateway/core/logging.py` | `from bsvibe_core import configure_logging` |
| BSage `bsage/core/logging.py` | same |
| BSNexus stdlib `logging.basicConfig` | same |
| BSupervisor bare `structlog.get_logger` | same |
| BSupervisor `cors_allowed_origins` `Annotated[list[str], NoDecode]` + inline validator | `csv_list_field()` + `parse_csv_list()` |
| BSGateway `os.environ.get("CORS_ALLOWED_ORIGINS").split(",")` | same |

## Install

The package lives in the `bsvibe-python` workspace. Products consume it
via the established `git+https` pattern:

```toml
# in product pyproject.toml
[project]
dependencies = [
    "bsvibe-core @ git+https://github.com/BSVibe/bsvibe-python.git@v0.1.0#subdirectory=packages/bsvibe-core",
]
```

## Tests

```bash
uv run pytest packages/bsvibe-core --cov=bsvibe_core --cov-fail-under=80
uv run ruff check packages/bsvibe-core/
uv run ruff format --check packages/bsvibe-core/
```
