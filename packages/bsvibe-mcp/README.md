# bsvibe-mcp

MCP server that exposes the four BSVibe product CLIs
(`bsgateway`, `bsage`, `bsnexus`, `bsupervisor`) as structured MCP tools.
Once running, AI agents (Claude Code, opencode, Cursor, …) can call any
admin operation as a typed tool call instead of shelling out.

The server delegates each tool to the existing CLI handler — no
duplicated logic, same auth path, same `--dry-run` semantics, same
`--output json` invariant.

## Quickstart

```bash
# stdio transport (default — used by Claude Code / Cursor)
uv run bsvibe-mcp serve

# HTTP/SSE transport (hosted multi-client use)
uv run bsvibe-mcp serve --transport http --host 0.0.0.0 --port 8765

# Inspect the registered tool catalog (text or json)
uv run bsvibe-mcp list-tools
uv run bsvibe-mcp list-tools --format json
```

### Claude Code `mcpServers` snippet

Add to `~/.claude/claude_desktop_config.json` (or the equivalent for your
client):

```json
{
  "mcpServers": {
    "bsvibe": {
      "command": "uv",
      "args": ["run", "bsvibe-mcp", "serve", "--transport", "stdio"],
      "env": {
        "MCP_PROFILE": "default"
      }
    }
  }
}
```

For HTTP/SSE clients, point the client at `http://HOST:PORT/sse`.

## Tool catalog

Tools are registered as `{product}_{subapp}_{action}` — e.g.
`bsgateway_models_list`, `bsage_canon_apply`. Every tool exposes the
following reserved input keys alongside its derived parameters:

| key       | purpose                                                  |
|-----------|----------------------------------------------------------|
| `dry_run` | short-circuits before HTTP — returns `{method, path, …}` |
| `token`   | per-call token override (else falls back to profile)     |
| `tenant`  | per-call tenant override                                 |
| `url`     | per-call API base URL override                           |

Default registration (per product):

| prefix         | tools                                                     |
|----------------|-----------------------------------------------------------|
| `bsgateway_*`  | models / routes / rules / intents / presets / tenants /   |
|                | audit / usage / feedback / workers / execute (~30 tools)  |
| `bsage_*`      | run / skills / plugins / garden / canon / settings        |
| `bsnexus_*`    | projects / requests / decisions / deliverables /          |
|                | integrations / events                                     |
| `bsupervisor_*`| agents / incidents / audit / costs / settings             |

Products that aren't installed in the runtime are skipped with a
single warning at startup — the server stays usable for the products
that *are* installed.

## Auth resolution

Each tool call resolves an `AuthContext` in this order (per-field, so
an explicit `tenant` override doesn't drop the profile's token):

1. **Per-call args** — `token` / `tenant` / `url` passed in the MCP
   tool arguments win over everything.
2. **Profile** — name from the `MCP_PROFILE` env var, otherwise the
   active default profile from `~/.bsvibe/config.yaml`. Token resolved
   via `bsvibe_cli_base.keyring.resolve_token` (keyring → `BSVIBE_TOKEN`
   env → raw token reference).
3. **Bootstrap** — `BSV_BOOTSTRAP_TOKEN` env var as token-only fallback
   when no profile is configured (admin escape hatch — never ship to
   end-user agents).

Token values are never logged. The `mcp_auth_resolved` structlog event
emits a fingerprint (`len=NN`) only.

## Testing locally

```bash
uv run ruff check .
uv run pytest --cov=bsvibe_mcp --cov-fail-under=80
```

The integration tests for each product CLI use `pytest.importorskip`
so the suite stays green even if a product isn't installed in the
runtime. The MCP request handlers are invoked in-process (no
subprocess spawn) per the `mcp-python-sdk-testing` pattern.

## Package layout

```
src/bsvibe_mcp/
  __init__.py     # re-exports build_server
  server.py       # MCPServer factory — registers product CLIs lazily
  registry.py     # Typer → MCP tool adapter (schema + dispatch)
  auth.py         # AuthContext resolution + token redaction
  transport.py    # stdio + HTTP/SSE wrappers
  cli.py          # bsvibe-mcp Typer entry point (serve / list-tools)
```

The src directory is `bsvibe_mcp/` (NOT `mcp/`) — naming it `mcp/`
would shadow the PyPI `mcp` package in production container layouts.

## Related

- [BSGateway CLI](../bsgateway-cli/) — model / routing admin
- [BSage CLI](../bsage-cli/) — canon / skills / garden
- [BSNexus CLI](../bsnexus-cli/) — project lifecycle
- [BSupervisor CLI](../bsupervisor-cli/) — agents / incidents
- [`bsvibe-cli-base`](../bsvibe-cli-base/) — shared profile / http /
  output / keyring helpers
