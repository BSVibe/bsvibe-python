"""Integration smoke for bsage CLI → MCP tool registration.

Skipped unless ``bsage`` is importable in the runtime environment
(local dev installs it via ``uv pip install --no-deps -e <path>`` plus
its backend deps so ``bsage.gateway.routes`` resolves).

Per memory ``mcp-python-sdk-testing``: invoke registered request
handlers directly (no subprocess). Result is wrapped in
``ServerResult.root``.
"""

from __future__ import annotations

import json

import mcp.types as mcp_types
import pytest

bsage = pytest.importorskip("bsage.cli.main")  # noqa: F841

from bsvibe_mcp import build_server  # noqa: E402


@pytest.fixture(scope="module")
def bsage_server():
    return build_server(products=(("bsage", "bsage.cli.main"),))


@pytest.fixture(scope="module")
def list_tools_handler(bsage_server):
    return bsage_server.request_handlers[mcp_types.ListToolsRequest]


@pytest.fixture(scope="module")
def call_tool_handler(bsage_server):
    return bsage_server.request_handlers[mcp_types.CallToolRequest]


class TestBsageListTools:
    async def test_registers_bsage_tools(self, list_tools_handler) -> None:
        result = await list_tools_handler(mcp_types.ListToolsRequest(method="tools/list"))
        names = [t.name for t in result.root.tools if t.name.startswith("bsage_")]
        # Tree: run + skills{list,run} + plugins{list,install,enable,disable}
        # + garden{list} + canon{list,draft,apply,status} + settings{get,set} = 14
        assert len(names) >= 14, f"expected >=14 bsage tools, got {len(names)}"

    async def test_includes_representative_subapp_tools(self, list_tools_handler) -> None:
        result = await list_tools_handler(mcp_types.ListToolsRequest(method="tools/list"))
        names = {t.name for t in result.root.tools}
        for expected in (
            "bsage_run",
            "bsage_skills_list",
            "bsage_skills_run",
            "bsage_plugins_list",
            "bsage_plugins_install",
            "bsage_garden_list",
            "bsage_canon_list",
            "bsage_canon_apply",
            "bsage_canon_status",
            "bsage_settings_get",
            "bsage_settings_set",
        ):
            assert expected in names, f"missing tool {expected}"

    async def test_canon_apply_schema_has_global_flags(self, list_tools_handler) -> None:
        result = await list_tools_handler(mcp_types.ListToolsRequest(method="tools/list"))
        tool = next(t for t in result.root.tools if t.name == "bsage_canon_apply")
        props = tool.inputSchema["properties"]
        for global_flag in ("dry_run", "token", "tenant", "url"):
            assert global_flag in props


class TestBsageCallTool:
    async def test_skills_list_dry_run_returns_structured_payload(self, call_tool_handler) -> None:
        request = mcp_types.CallToolRequest(
            method="tools/call",
            params=mcp_types.CallToolRequestParams(
                name="bsage_skills_list",
                arguments={"dry_run": True},
            ),
        )
        result = (await call_tool_handler(request)).root
        assert result.isError is False
        payload = json.loads(result.content[0].text)
        assert payload["dry_run"] is True
        assert payload["method"] == "GET"
        assert payload["path"] == "/api/skills"

    async def test_settings_get_dry_run_returns_structured_payload(self, call_tool_handler) -> None:
        request = mcp_types.CallToolRequest(
            method="tools/call",
            params=mcp_types.CallToolRequestParams(
                name="bsage_settings_get",
                arguments={"dry_run": True},
            ),
        )
        result = (await call_tool_handler(request)).root
        assert result.isError is False
        payload = json.loads(result.content[0].text)
        assert payload["dry_run"] is True
        assert payload["method"] == "GET"
