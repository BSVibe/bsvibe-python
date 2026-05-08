"""MCP server factory.

The factory is the single construction seam for the MCP server. At this
stage it returns a bare :class:`mcp.server.Server` — TASK-003+ will wire
the four product CLIs (bsgateway / bsage / bsnexus / bsupervisor) via
:class:`bsvibe_mcp.registry.MCPToolRegistry`.

Keeping the factory thin (no I/O, no env reads) lets tests instantiate
the server cheaply and lets transports own their own runtime concerns.
"""

from __future__ import annotations

from mcp.server import Server

DEFAULT_SERVER_NAME = "bsvibe-mcp"


def build_server(name: str = DEFAULT_SERVER_NAME) -> Server:
    """Construct a fresh MCP server with no tools registered."""
    return Server(name)
