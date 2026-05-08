"""Tests for the :func:`bsvibe_mcp.build_server` factory.

The factory is the only stable construction seam for the MCP server.
At this stage (TASK-001) it returns a plain ``mcp.server.Server`` with
no tools registered yet — TASK-003+ will wire the four product CLIs
through :class:`bsvibe_mcp.registry.MCPToolRegistry`.

The factory MUST:
  - return an ``mcp.server.Server`` instance
  - accept a custom server name (default ``"bsvibe-mcp"``)
  - be importable from the package root (``from bsvibe_mcp import build_server``)
"""

from __future__ import annotations

from mcp.server import Server

from bsvibe_mcp import build_server


class TestBuildServer:
    def test_returns_mcp_server_instance(self) -> None:
        server = build_server()
        assert isinstance(server, Server)

    def test_default_name(self) -> None:
        server = build_server()
        assert server.name == "bsvibe-mcp"

    def test_custom_name(self) -> None:
        server = build_server(name="custom")
        assert server.name == "custom"

    def test_factory_returns_fresh_instances(self) -> None:
        a = build_server()
        b = build_server()
        assert a is not b
