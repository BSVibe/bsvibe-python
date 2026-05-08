"""Integration smoke for bsgateway CLI → MCP tool registration.

Skipped unless ``bsgateway`` is importable in the runtime environment
(local dev installs it via ``uv pip install --no-deps -e <path>``).
The mechanism itself is unit-tested in ``test_registry_cli_app.py`` —
this module asserts the *real* bsgateway CLI surface produces the
expected number of tools and a representative tool dispatches.

Per memory ``mcp-python-sdk-testing``: invoke registered request
handlers directly (no subprocess). Result is wrapped in
``ServerResult.root``.
"""

from __future__ import annotations

import json

import mcp.types as mcp_types
import pytest

bsgateway = pytest.importorskip("bsgateway")  # noqa: F841

from bsvibe_mcp import build_server  # noqa: E402


@pytest.fixture(scope="module")
def bsgateway_server():
    return build_server(products=(("bsgateway", "bsgateway.cli.main"),))


@pytest.fixture(scope="module")
def list_tools_handler(bsgateway_server):
    return bsgateway_server.request_handlers[mcp_types.ListToolsRequest]


@pytest.fixture(scope="module")
def call_tool_handler(bsgateway_server):
    return bsgateway_server.request_handlers[mcp_types.CallToolRequest]


class TestBsgatewayListTools:
    async def test_registers_at_least_30_bsgateway_tools(self, list_tools_handler) -> None:
        result = await list_tools_handler(mcp_types.ListToolsRequest(method="tools/list"))
        names = [t.name for t in result.root.tools if t.name.startswith("bsgateway_")]
        assert len(names) >= 30, f"expected 30+ bsgateway tools, got {len(names)}"

    async def test_includes_models_subapp_tools(self, list_tools_handler) -> None:
        result = await list_tools_handler(mcp_types.ListToolsRequest(method="tools/list"))
        names = {t.name for t in result.root.tools}
        for expected in ("bsgateway_models_list", "bsgateway_models_add", "bsgateway_models_remove"):
            assert expected in names

    async def test_includes_callback_only_execute_subapp(self, list_tools_handler) -> None:
        result = await list_tools_handler(mcp_types.ListToolsRequest(method="tools/list"))
        names = {t.name for t in result.root.tools}
        # ``bsgateway execute`` is invoke_without_command=True — registered as
        # ``bsgateway_execute`` (no inner command name).
        assert "bsgateway_execute" in names

    async def test_models_list_schema_has_global_flags(self, list_tools_handler) -> None:
        result = await list_tools_handler(mcp_types.ListToolsRequest(method="tools/list"))
        tool = next(t for t in result.root.tools if t.name == "bsgateway_models_list")
        props = tool.inputSchema["properties"]
        for global_flag in ("dry_run", "token", "tenant", "url"):
            assert global_flag in props


class TestBsgatewayCallTool:
    async def test_models_list_dry_run_returns_structured_payload(self, call_tool_handler) -> None:
        request = mcp_types.CallToolRequest(
            method="tools/call",
            params=mcp_types.CallToolRequestParams(
                name="bsgateway_models_list",
                arguments={"type_filter": "custom", "dry_run": True},
            ),
        )
        result = (await call_tool_handler(request)).root
        assert result.isError is False
        payload = json.loads(result.content[0].text)
        # bsgateway models list emits ``{dry_run, method, path, filter}``
        assert payload["dry_run"] is True
        assert payload["method"] == "GET"
        assert payload["path"] == "/admin/models"
        assert payload["filter"]["type"] == "custom"
