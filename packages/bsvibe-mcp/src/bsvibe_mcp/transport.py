"""MCP transport hooks (stdio + HTTP/SSE).

Two transports:

* :func:`run_stdio` — wraps the MCP SDK's :func:`stdio_server` async
  context manager and runs the server against stdin/stdout. Used by
  Claude Code, Cursor, opencode, etc., which spawn the server as a
  subprocess and speak JSON-RPC over the pipe.

* :func:`run_http` — mounts the MCP SDK's :class:`SseServerTransport`
  on a Starlette app at ``/sse`` (event stream) + ``/messages/`` (POST
  endpoint), and serves it via uvicorn. Used for hosted deployments.

:func:`build_http_app` is split out so tests can assert the route
table without spinning up uvicorn.
"""

from __future__ import annotations

import structlog
import uvicorn
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.server.stdio import stdio_server
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Mount, Route

logger = structlog.get_logger(__name__)

_SSE_MESSAGE_PATH = "/messages/"


async def run_stdio(server: Server) -> None:
    """Run the MCP server over stdio (stdin/stdout JSON-RPC)."""
    logger.info("mcp_transport_start", transport="stdio")
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def build_http_app(server: Server) -> Starlette:
    """Construct a Starlette ASGI app exposing the MCP server over SSE."""
    sse = SseServerTransport(_SSE_MESSAGE_PATH)

    async def handle_sse(request: Request) -> Response:
        async with sse.connect_sse(request.scope, request.receive, request._send) as (
            read_stream,
            write_stream,
        ):
            await server.run(read_stream, write_stream, server.create_initialization_options())
        return Response()

    return Starlette(
        routes=[
            Route("/sse", endpoint=handle_sse, methods=["GET"]),
            Mount(_SSE_MESSAGE_PATH, app=sse.handle_post_message),
        ]
    )


async def run_http(server: Server, host: str = "127.0.0.1", port: int = 8765) -> None:
    """Run the MCP server over HTTP/SSE, served by uvicorn."""
    logger.info("mcp_transport_start", transport="http", host=host, port=port)
    app = build_http_app(server)
    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    uv_server = uvicorn.Server(config)
    await uv_server.serve()
