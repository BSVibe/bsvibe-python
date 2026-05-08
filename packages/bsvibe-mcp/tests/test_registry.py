"""Tests for :class:`bsvibe_mcp.registry.MCPToolRegistry`.

The registry is the Typer → MCP adapter: it walks a Typer app
(commands + sub-typers), generates a JSON-schema input shape per
command, and wires ``list_tools`` / ``call_tool`` handlers on an
``mcp.server.Server``.

These tests use a synthetic Typer app — they do NOT touch the four
product CLIs. TASK-003+ exercise registry against real bsgateway/bsage/
bsnexus/bsupervisor apps.

Per memory ``mcp-python-sdk-testing``: invoke the registered request
handlers directly (no subprocess). Result is wrapped in
``ServerResult.root``.
"""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Annotated, Optional

import mcp.types as mcp_types
import pytest
import typer
from mcp.server import Server

from bsvibe_mcp.registry import MCPToolRegistry, ToolDescriptor


class Color(str, Enum):
    RED = "red"
    BLUE = "blue"


def _build_demo_app() -> typer.Typer:
    """Synthetic Typer app exercising 6+ parameter shapes."""
    app = typer.Typer()
    items = typer.Typer()
    app.add_typer(items, name="items")

    @items.command("list")
    def items_list(
        name: Annotated[str, typer.Argument(help="Item name")],
        count: Annotated[int, typer.Option("--count", help="How many")] = 1,
        verbose: Annotated[bool, typer.Option("--verbose")] = False,
        color: Annotated[Color, typer.Option("--color")] = Color.RED,
        out: Annotated[Optional[Path], typer.Option("--out")] = None,
        tags: Annotated[Optional[list[str]], typer.Option("--tag")] = None,
    ) -> dict:
        return {
            "name": name,
            "count": count,
            "verbose": verbose,
            "color": color.value,
            "out": str(out) if out is not None else None,
            "tags": tags,
        }

    @app.command("ping")
    def ping() -> dict:
        """Health check."""
        return {"pong": True}

    @app.command("echo-async")
    async def echo_async(message: Annotated[str, typer.Argument()]) -> dict:
        return {"echo": message}

    return app


@pytest.fixture
def registry_with_demo() -> tuple[Server, MCPToolRegistry, dict[str, ToolDescriptor]]:
    server = Server("t")
    registry = MCPToolRegistry(server)
    descriptors = registry.register_typer_app(_build_demo_app(), prefix="demo")
    return server, registry, {d.name: d for d in descriptors}


class TestRegisterTyperApp:
    def test_walks_subapps_and_commands(self, registry_with_demo) -> None:
        _, _, by_name = registry_with_demo
        assert "demo_items_list" in by_name
        assert "demo_ping" in by_name

    def test_normalizes_dashes_in_command_name(self, registry_with_demo) -> None:
        _, _, by_name = registry_with_demo
        assert "demo_echo_async" in by_name

    def test_returns_tool_descriptors(self, registry_with_demo) -> None:
        _, _, by_name = registry_with_demo
        for descriptor in by_name.values():
            assert isinstance(descriptor, ToolDescriptor)
            assert descriptor.input_schema["type"] == "object"

    def test_duplicate_name_raises(self) -> None:
        server = Server("t")
        registry = MCPToolRegistry(server)
        registry.register_typer_app(_build_demo_app(), prefix="demo")
        with pytest.raises(ValueError, match="duplicate"):
            registry.register_typer_app(_build_demo_app(), prefix="demo")

    def test_tools_accessor_returns_all(self, registry_with_demo) -> None:
        _, registry, by_name = registry_with_demo
        assert {t.name for t in registry.tools()} == set(by_name.keys())


class TestSchemaDerivation:
    def test_string_argument_required(self, registry_with_demo) -> None:
        _, _, by_name = registry_with_demo
        descriptor = by_name["demo_items_list"]
        props = descriptor.input_schema["properties"]
        assert props["name"]["type"] == "string"
        assert "name" in descriptor.input_schema["required"]

    def test_int_option_with_default_not_required(self, registry_with_demo) -> None:
        _, _, by_name = registry_with_demo
        descriptor = by_name["demo_items_list"]
        props = descriptor.input_schema["properties"]
        assert props["count"]["type"] == "integer"
        assert props["count"]["default"] == 1
        assert "count" not in descriptor.input_schema.get("required", [])

    def test_bool_option(self, registry_with_demo) -> None:
        _, _, by_name = registry_with_demo
        prop = by_name["demo_items_list"].input_schema["properties"]["verbose"]
        assert prop["type"] == "boolean"
        assert prop["default"] is False

    def test_enum_as_string_with_enum_values(self, registry_with_demo) -> None:
        _, _, by_name = registry_with_demo
        prop = by_name["demo_items_list"].input_schema["properties"]["color"]
        assert prop["type"] == "string"
        assert prop["enum"] == ["red", "blue"]
        assert prop["default"] == "red"

    def test_optional_path_is_nullable_string(self, registry_with_demo) -> None:
        _, _, by_name = registry_with_demo
        prop = by_name["demo_items_list"].input_schema["properties"]["out"]
        assert prop["type"] == ["string", "null"]
        assert prop.get("format") == "path"

    def test_optional_list_str_is_nullable_array(self, registry_with_demo) -> None:
        _, _, by_name = registry_with_demo
        prop = by_name["demo_items_list"].input_schema["properties"]["tags"]
        assert prop["type"] == ["array", "null"]
        assert prop["items"]["type"] == "string"

    def test_help_text_carried_into_description(self, registry_with_demo) -> None:
        _, _, by_name = registry_with_demo
        prop = by_name["demo_items_list"].input_schema["properties"]["name"]
        assert prop["description"] == "Item name"

    def test_command_help_used_as_tool_description(self, registry_with_demo) -> None:
        _, _, by_name = registry_with_demo
        # `ping` has docstring "Health check." — used as fallback description
        assert "Health check" in by_name["demo_ping"].description


class TestServerHandlers:
    async def test_list_tools_handler_returns_all(self, registry_with_demo) -> None:
        server, _, by_name = registry_with_demo
        handler = server.request_handlers[mcp_types.ListToolsRequest]
        request = mcp_types.ListToolsRequest(method="tools/list")
        server_result = await handler(request)
        names = [tool.name for tool in server_result.root.tools]
        assert set(names) == set(by_name.keys())

    async def test_call_tool_dispatches_with_coerced_args(self, registry_with_demo) -> None:
        server, _, _ = registry_with_demo
        handler = server.request_handlers[mcp_types.CallToolRequest]
        request = mcp_types.CallToolRequest(
            method="tools/call",
            params=mcp_types.CallToolRequestParams(
                name="demo_items_list",
                arguments={"name": "alpha", "count": 3, "color": "blue"},
            ),
        )
        server_result = await handler(request)
        result = server_result.root
        assert result.isError is False
        # Structured content carried through
        assert result.structuredContent == {
            "name": "alpha",
            "count": 3,
            "verbose": False,
            "color": "blue",
            "out": None,
            "tags": None,
        }
        # Unstructured content is JSON text
        text = result.content[0].text
        payload = json.loads(text)
        assert payload["name"] == "alpha"

    async def test_call_tool_coerces_path_argument(self, registry_with_demo) -> None:
        server, _, _ = registry_with_demo
        handler = server.request_handlers[mcp_types.CallToolRequest]
        request = mcp_types.CallToolRequest(
            method="tools/call",
            params=mcp_types.CallToolRequestParams(
                name="demo_items_list",
                arguments={"name": "x", "out": "/tmp/foo.json"},
            ),
        )
        server_result = await handler(request)
        assert server_result.root.structuredContent["out"] == "/tmp/foo.json"

    async def test_call_tool_unknown_returns_error_result(self, registry_with_demo) -> None:
        server, _, _ = registry_with_demo
        handler = server.request_handlers[mcp_types.CallToolRequest]
        request = mcp_types.CallToolRequest(
            method="tools/call",
            params=mcp_types.CallToolRequestParams(name="does_not_exist", arguments={}),
        )
        server_result = await handler(request)
        result = server_result.root
        assert result.isError is True
        assert "unknown tool" in result.content[0].text.lower()

    async def test_call_tool_dispatches_async_handler(self, registry_with_demo) -> None:
        server, _, _ = registry_with_demo
        handler = server.request_handlers[mcp_types.CallToolRequest]
        request = mcp_types.CallToolRequest(
            method="tools/call",
            params=mcp_types.CallToolRequestParams(
                name="demo_echo_async",
                arguments={"message": "hi"},
            ),
        )
        server_result = await handler(request)
        assert server_result.root.structuredContent == {"echo": "hi"}
