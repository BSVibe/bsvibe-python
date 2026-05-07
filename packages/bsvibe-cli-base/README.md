# bsvibe-cli-base

Shared CLI foundation for BSVibe Python tools. Provides the building blocks that every BSVibe operator/admin CLI needs, so individual tools (`bsvibe-audit`, future `bsvibe-authz`, `bsvibe-alerts`, etc.) only have to declare their commands.

## What's in the box

| Module | Purpose |
| --- | --- |
| `config` | `Profile` + `CliConfig` Pydantic schemas (`extra='forbid'`). |
| `profile` | `ProfileStore` — read/write `~/.bsvibe/config.yaml` (or `$XDG_CONFIG_HOME/bsvibe/config.yaml`). Atomic writes via `tempfile` + `Path.replace`. |
| `keyring` | `set_token`, `get_token`, `delete_token`, `resolve_token(profile)` (keyring → `BSVIBE_TOKEN` env → `profile.token_ref` fallback). Backend failures degrade with a `structlog` warning. |
| `output` | `OutputFormatter` — `json` / `yaml` / `tsv` / `table` with TTY auto-detect (TTY → `table`, non-TTY → `json`). |
| `cli` | `cli_app(name, help, profile_store=...)` factory returning a `typer.Typer` app pre-wired with global flags `--profile`, `--output`, `--tenant`, `--token`, `--url`, `--dry-run`. The active `CliContext` is exposed via `ctx.obj`. |
| `device_flow` | Async `DeviceFlowClient` for the OAuth 2.0 Device Authorization Grant (RFC 8628) — `request_code()` + `poll_token()`. |
| `http` | `CliHttpClient` extends `bsvibe_core.HttpClientBase`. Injects the bearer token, transparently refreshes on `401` via `refresh_token` grant, and replays the original request once. |

## Quick start

```python
from bsvibe_cli_base import cli_app, OutputFormatter

app = cli_app(name="bsvibe-example", help="Example BSVibe operator tool")

@app.command()
def whoami(ctx: typer.Context) -> None:
    cli_ctx = ctx.obj  # CliContext
    cli_ctx.formatter.emit({"profile": cli_ctx.profile.name, "url": cli_ctx.url})
```

## Design rules (do not violate)

- **No JWT verify, no token introspection here.** Those belong in `bsvibe-authz`. This package only ferries opaque token strings between keyring/env/profile and the HTTP layer.
- **No retry / header-injection logic of its own.** `CliHttpClient` builds on `bsvibe_core.HttpClientBase`; refresh-on-401 is the only CLI-specific behaviour added.
- **Never log token values.** Use `structlog` and let `bsvibe-core` redact `Authorization` / `X-Service-Token` headers.

## Dependencies

`bsvibe-core`, `bsvibe-authz`, `typer>=0.12`, `httpx>=0.27`, `keyring>=24`, `pyyaml>=6`, `pydantic>=2`, `pydantic-settings>=2.5`, `structlog>=24`.
