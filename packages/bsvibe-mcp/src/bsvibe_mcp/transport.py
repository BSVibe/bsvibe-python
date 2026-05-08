"""MCP transport hooks (stdio + HTTP/SSE).

TASK-001 ships placeholder coroutines so callers, tests, and the
``bsvibe-mcp serve`` Typer app can import the module. TASK-007 fills
in the real wire-up against the MCP SDK transport primitives.
"""

from __future__ import annotations

from mcp.server import Server


async def run_stdio(server: Server) -> None:
    """Run the MCP server over stdio. Implemented in TASK-007."""
    raise NotImplementedError("stdio transport lands in TASK-007")


async def run_http(server: Server, host: str = "127.0.0.1", port: int = 8765) -> None:
    """Run the MCP server over HTTP/SSE. Implemented in TASK-007."""
    raise NotImplementedError("http transport lands in TASK-007")
