"""Integration smoke for bsnexus CLI → MCP tool registration.

Skipped unless ``bsnexus_cli.main`` is importable. The bsnexus wheel
remaps ``backend/src/cli/`` to ``bsnexus_cli/`` via hatch
``force-include`` but the file's absolute imports still reference
``backend.src.cli.commands`` — so the installed entrypoint fails to
import in standard pip-install layouts. This test will skip until
upstream bsnexus fixes the packaging (relative imports inside
``main.py``).

Per memory ``mcp-python-sdk-testing``: invoke registered request
handlers directly (no subprocess). Result is wrapped in
``ServerResult.root``.
"""

from __future__ import annotations

import json

import mcp.types as mcp_types
import pytest

bsnexus = pytest.importorskip("bsnexus_cli.main")  # noqa: F841

from bsvibe_mcp import build_server  # noqa: E402


@pytest.fixture(scope="module")
def bsnexus_server():
    return build_server(products=(("bsnexus", "bsnexus_cli.main"),))


@pytest.fixture(scope="module")
def list_tools_handler(bsnexus_server):
    return bsnexus_server.request_handlers[mcp_types.ListToolsRequest]


@pytest.fixture(scope="module")
def call_tool_handler(bsnexus_server):
    return bsnexus_server.request_handlers[mcp_types.CallToolRequest]


class TestBsnexusListTools:
    async def test_registers_bsnexus_tools(self, list_tools_handler) -> None:
        result = await list_tools_handler(mcp_types.ListToolsRequest(method="tools/list"))
        names = [t.name for t in result.root.tools if t.name.startswith("bsnexus_")]
        assert len(names) >= 6, f"expected >=6 bsnexus tools, got {len(names)}"

    async def test_includes_representative_subapp_tools(self, list_tools_handler) -> None:
        result = await list_tools_handler(mcp_types.ListToolsRequest(method="tools/list"))
        names = {t.name for t in result.root.tools}
        for expected in (
            "bsnexus_projects_list",
            "bsnexus_requests_list",
            "bsnexus_decisions_list",
            "bsnexus_deliverables_list",
            "bsnexus_events_list",
            "bsnexus_integrations_list",
        ):
            assert expected in names, f"missing tool {expected}"

    async def test_projects_list_schema_has_global_flags(self, list_tools_handler) -> None:
        result = await list_tools_handler(mcp_types.ListToolsRequest(method="tools/list"))
        tool = next(t for t in result.root.tools if t.name == "bsnexus_projects_list")
        props = tool.inputSchema["properties"]
        for global_flag in ("dry_run", "token", "tenant", "url"):
            assert global_flag in props


class TestBsnexusCallTool:
    async def test_projects_list_dry_run_returns_structured_payload(self, call_tool_handler) -> None:
        request = mcp_types.CallToolRequest(
            method="tools/call",
            params=mcp_types.CallToolRequestParams(
                name="bsnexus_projects_list",
                arguments={"dry_run": True},
            ),
        )
        result = (await call_tool_handler(request)).root
        assert result.isError is False
        payload = json.loads(result.content[0].text)
        assert payload["dry_run"] is True
        assert payload["method"] == "GET"
