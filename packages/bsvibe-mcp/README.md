# bsvibe-mcp

MCP server that exposes the four BSVibe product CLIs
(`bsgateway`, `bsage`, `bsnexus`, `bsupervisor`) as structured MCP tools.
Once shipped, AI agents (Claude Code, opencode, Cursor) can call any
admin operation as a tool call instead of shelling out.

## Status

Phase 6, in progress. TASK-001 ships the package skeleton; tools land in
TASK-003+; transports land in TASK-007.

## Quickstart (planned, TASK-007)

```bash
uv run bsvibe-mcp serve --transport stdio
uv run bsvibe-mcp list-tools
```

Claude Code `mcpServers` snippet (planned):

```json
{
  "mcpServers": {
    "bsvibe": {
      "command": "uv",
      "args": ["run", "bsvibe-mcp", "serve", "--transport", "stdio"]
    }
  }
}
```

## Design

- Tool naming: `{product}_{subapp}_{action}` — e.g. `bsgateway_models_list`.
- Tool schemas auto-derived from each Typer command's parameter list.
- Each tool delegates to the existing CLI handler — no duplicate logic.
- Auth: per-call `tenant`/`token` overrides → `MCP_PROFILE` env →
  default profile from `~/.bsvibe/config.yaml` → `BSV_BOOTSTRAP_TOKEN`.
- Two transports: stdio (default for local agents) + HTTP/SSE (hosted).

## Package layout

```
src/bsvibe_mcp/
  __init__.py     # re-exports build_server
  server.py       # MCPServer factory
  transport.py    # stdio + HTTP/SSE wrappers
  cli.py          # bsvibe-mcp Typer entry point
```

The src directory is `bsvibe_mcp/` (NOT `mcp/`) — naming it `mcp/` would
shadow the PyPI `mcp` package in production container layouts.
