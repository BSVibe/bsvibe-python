"""``bsvibe-mcp`` Typer entry point.

Two subcommands:

* ``bsvibe-mcp serve [--transport stdio|http]`` — boots the MCP server
  with the requested transport (default stdio).
* ``bsvibe-mcp list-tools [--format text|json]`` — prints the catalogue
  of registered tools (handy for AI-agent setup or debugging).
"""

from __future__ import annotations

import asyncio
import enum
import json
from typing import Annotated

import mcp.types as mcp_types
import typer

from bsvibe_mcp.server import build_server
from bsvibe_mcp.transport import run_http, run_stdio

app = typer.Typer(
    name="bsvibe-mcp",
    help="BSVibe MCP server — exposes product CLIs as MCP tools.",
    no_args_is_help=True,
)


class _Transport(str, enum.Enum):
    stdio = "stdio"
    http = "http"


class _ListFormat(str, enum.Enum):
    text = "text"
    json = "json"


@app.command()
def serve(
    transport: Annotated[
        _Transport,
        typer.Option("--transport", help="MCP transport to use."),
    ] = _Transport.stdio,
    host: Annotated[
        str,
        typer.Option("--host", help="Bind host for HTTP transport."),
    ] = "127.0.0.1",
    port: Annotated[
        int,
        typer.Option("--port", help="Bind port for HTTP transport."),
    ] = 8765,
) -> None:
    """Run the MCP server with the chosen transport."""
    server = build_server()
    if transport is _Transport.stdio:
        asyncio.run(run_stdio(server))
    else:
        asyncio.run(run_http(server, host=host, port=port))


@app.command("list-tools")
def list_tools(
    fmt: Annotated[
        _ListFormat,
        typer.Option("--format", help="Output format."),
    ] = _ListFormat.text,
) -> None:
    """Print the catalog of MCP tools registered on the server."""
    server = build_server()
    handler = server.request_handlers.get(mcp_types.ListToolsRequest)
    if handler is None:
        typer.echo("[]" if fmt is _ListFormat.json else "(no tools registered)")
        return
    result = asyncio.run(handler(mcp_types.ListToolsRequest(method="tools/list")))
    tools = result.root.tools

    if fmt is _ListFormat.json:
        payload = [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.inputSchema,
            }
            for t in tools
        ]
        typer.echo(json.dumps(payload, indent=2))
        return

    for t in tools:
        typer.echo(f"{t.name}\t{t.description or ''}")
