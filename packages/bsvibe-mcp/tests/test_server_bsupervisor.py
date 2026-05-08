"""Integration smoke for bsupervisor CLI → MCP tool registration.

Skipped unless ``bsupervisor`` is importable in the runtime environment
(local dev installs it via ``uv pip install --no-deps -e
/Users/blasin/Works/BSupervisor/main``).

Per memory ``mcp-python-sdk-testing``: invoke registered request
handlers directly (no subprocess). Result is wrapped in
``ServerResult.root``.
"""

from __future__ import annotations

import json

import mcp.types as mcp_types
import pytest

bsupervisor = pytest.importorskip("bsupervisor.cli.main")  # noqa: F841

from bsvibe_mcp import build_server  # noqa: E402


@pytest.fixture(scope="module")
def bsupervisor_server():
    return build_server(products=(("bsupervisor", "bsupervisor.cli.main"),))


@pytest.fixture(scope="module")
def list_tools_handler(bsupervisor_server):
    return bsupervisor_server.request_handlers[mcp_types.ListToolsRequest]


@pytest.fixture(scope="module")
def call_tool_handler(bsupervisor_server):
    return bsupervisor_server.request_handlers[mcp_types.CallToolRequest]


class TestBsupervisorListTools:
    async def test_registers_bsupervisor_tools(self, list_tools_handler) -> None:
        result = await list_tools_handler(mcp_types.ListToolsRequest(method="tools/list"))
        names = [t.name for t in result.root.tools if t.name.startswith("bsupervisor_")]
        # Surface: agents{list,add,update,delete,run} (5) + incidents{list,show,ack,resolve} (4)
        # + audit{list,show} (2) + costs{report} (1) + settings{get,set} (2) = 14
        assert len(names) >= 14, f"expected >=14 bsupervisor tools, got {len(names)}"

    async def test_includes_representative_subapp_tools(self, list_tools_handler) -> None:
        result = await list_tools_handler(mcp_types.ListToolsRequest(method="tools/list"))
        names = {t.name for t in result.root.tools}
        for expected in (
            "bsupervisor_agents_list",
            "bsupervisor_agents_add",
            "bsupervisor_agents_run",
            "bsupervisor_incidents_list",
            "bsupervisor_incidents_ack",
            "bsupervisor_incidents_resolve",
            "bsupervisor_audit_list",
            "bsupervisor_costs_report",
            "bsupervisor_settings_get",
            "bsupervisor_settings_set",
        ):
            assert expected in names, f"missing tool {expected}"

    async def test_agents_list_schema_has_global_flags(self, list_tools_handler) -> None:
        result = await list_tools_handler(mcp_types.ListToolsRequest(method="tools/list"))
        tool = next(t for t in result.root.tools if t.name == "bsupervisor_agents_list")
        props = tool.inputSchema["properties"]
        for global_flag in ("dry_run", "token", "tenant", "url"):
            assert global_flag in props


class TestBsupervisorCallTool:
    async def test_agents_list_dry_run_returns_structured_payload(self, call_tool_handler) -> None:
        request = mcp_types.CallToolRequest(
            method="tools/call",
            params=mcp_types.CallToolRequestParams(
                name="bsupervisor_agents_list",
                arguments={"dry_run": True},
            ),
        )
        result = (await call_tool_handler(request)).root
        assert result.isError is False
        payload = json.loads(result.content[0].text)
        assert payload["dry_run"] is True
        assert payload["method"] == "GET"
        assert payload["path"] == "/api/rules"

    async def test_incidents_list_dry_run_with_severity_filter(self, call_tool_handler) -> None:
        request = mcp_types.CallToolRequest(
            method="tools/call",
            params=mcp_types.CallToolRequestParams(
                name="bsupervisor_incidents_list",
                arguments={"dry_run": True, "severity": "high"},
            ),
        )
        result = (await call_tool_handler(request)).root
        assert result.isError is False
        payload = json.loads(result.content[0].text)
        assert payload["dry_run"] is True
        assert payload["method"] == "GET"
        assert payload["path"] == "/api/incidents"
