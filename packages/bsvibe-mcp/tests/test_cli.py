"""``bsvibe-mcp`` Typer CLI tests.

Covers ``serve`` (stdio + http transport dispatch) and ``list-tools``
(catalog dump).
"""

from __future__ import annotations

import re
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from bsvibe_mcp import cli

runner = CliRunner()


def _strip_ansi(s: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", s)


def _empty_server() -> MagicMock:
    server = MagicMock(name="server")
    server.request_handlers = {}
    return server


class TestRootHelp:
    def test_help_renders(self) -> None:
        result = runner.invoke(cli.app, ["--help"])
        assert result.exit_code == 0
        out = _strip_ansi(result.output)
        assert "serve" in out
        assert "list-tools" in out


class TestServeCommand:
    def test_serve_stdio_invokes_run_stdio(self) -> None:
        server = _empty_server()
        with (
            patch.object(cli, "build_server", return_value=server) as build,
            patch.object(cli, "run_stdio") as run_stdio,
            patch.object(cli, "run_http") as run_http,
        ):
            result = runner.invoke(cli.app, ["serve", "--transport", "stdio"])
        assert result.exit_code == 0, result.output
        build.assert_called_once()
        run_stdio.assert_called_once_with(server)
        run_http.assert_not_called()

    def test_serve_http_invokes_run_http_with_host_and_port(self) -> None:
        server = _empty_server()
        with (
            patch.object(cli, "build_server", return_value=server),
            patch.object(cli, "run_stdio") as run_stdio,
            patch.object(cli, "run_http") as run_http,
        ):
            result = runner.invoke(
                cli.app,
                ["serve", "--transport", "http", "--host", "0.0.0.0", "--port", "9000"],
            )
        assert result.exit_code == 0, result.output
        run_http.assert_called_once_with(server, host="0.0.0.0", port=9000)
        run_stdio.assert_not_called()

    def test_serve_default_transport_is_stdio(self) -> None:
        server = _empty_server()
        with (
            patch.object(cli, "build_server", return_value=server),
            patch.object(cli, "run_stdio") as run_stdio,
            patch.object(cli, "run_http") as run_http,
        ):
            result = runner.invoke(cli.app, ["serve"])
        assert result.exit_code == 0
        run_stdio.assert_called_once_with(server)
        run_http.assert_not_called()

    def test_serve_rejects_unknown_transport(self) -> None:
        with patch.object(cli, "build_server", return_value=_empty_server()):
            result = runner.invoke(cli.app, ["serve", "--transport", "websocket"])
        assert result.exit_code != 0


class TestListToolsCommand:
    def test_list_tools_prints_registered_names(self) -> None:
        # Build a server with two synthetic tools registered.
        from mcp.server import Server as _Server

        from bsvibe_mcp.registry import MCPToolRegistry, ToolDescriptor

        server = _Server("bsvibe-mcp-test")
        registry = MCPToolRegistry(server)
        registry._add(
            ToolDescriptor(
                name="alpha_tool_one",
                description="first",
                input_schema={"type": "object", "properties": {}, "required": []},
                handler=lambda **kw: {"ok": True},
                param_types={},
            )
        )
        registry._add(
            ToolDescriptor(
                name="beta_tool_two",
                description="second",
                input_schema={"type": "object", "properties": {}, "required": []},
                handler=lambda **kw: {"ok": True},
                param_types={},
            )
        )
        # Wire handlers (call once on first add — but _add doesn't wire; force
        # by calling registry's wiring path via a no-op register call).
        # Easier: directly call the internal wiring helper.
        registry._wire_handlers()

        with patch.object(cli, "build_server", return_value=server):
            result = runner.invoke(cli.app, ["list-tools"])

        assert result.exit_code == 0, result.output
        out = _strip_ansi(result.output)
        assert "alpha_tool_one" in out
        assert "beta_tool_two" in out

    def test_list_tools_json_output(self) -> None:
        import json as _json

        from mcp.server import Server as _Server

        from bsvibe_mcp.registry import MCPToolRegistry, ToolDescriptor

        server = _Server("bsvibe-mcp-test")
        registry = MCPToolRegistry(server)
        registry._add(
            ToolDescriptor(
                name="gamma_one",
                description="g",
                input_schema={"type": "object", "properties": {}, "required": []},
                handler=lambda **kw: {"ok": True},
                param_types={},
            )
        )
        registry._wire_handlers()

        with patch.object(cli, "build_server", return_value=server):
            result = runner.invoke(cli.app, ["list-tools", "--format", "json"])

        assert result.exit_code == 0, result.output
        out = _strip_ansi(result.output)
        payload = _json.loads(out)
        names = [t["name"] for t in payload]
        assert "gamma_one" in names
