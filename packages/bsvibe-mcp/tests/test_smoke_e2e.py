"""End-to-end smoke for the default-built MCP server.

Where the per-product modules (``test_server_bsgateway.py``, etc.)
narrow ``build_server(products=...)`` to a single CLI for a focused
assertion, this module runs against the *default* product set — i.e.
the same registration path operators get when they invoke
``bsvibe-mcp serve``. It verifies:

* ``ListTools`` aggregates every installed product's tools.
* For each installed product, one representative tool dispatches via
  ``CallTool`` end-to-end (``--dry-run`` short-circuit, no real HTTP).

Each per-product assertion is gated by ``pytest.importorskip`` so the
suite stays green whether or not bsnexus / bsage / bsupervisor /
bsgateway happen to be installed in the runtime.

Per memory ``mcp-python-sdk-testing``: invoke registered request
handlers directly (no subprocess). Result wrapped in
``ServerResult.root``.
"""

from __future__ import annotations

import json

import mcp.types as mcp_types
import pytest

from bsvibe_mcp import build_server


# (prefix, importable_module, representative_tool, expected_method, expected_path)
_PRODUCT_PROBES: tuple[tuple[str, str, str, str, str], ...] = (
    ("bsgateway", "bsgateway.cli.main", "bsgateway_models_list", "GET", "/admin/models"),
    ("bsage", "bsage.cli.main", "bsage_skills_list", "GET", "/api/skills"),
    ("bsnexus", "bsnexus_cli.main", "bsnexus_projects_list", "GET", ""),
    ("bsupervisor", "bsupervisor.cli.main", "bsupervisor_agents_list", "GET", "/api/rules"),
)


@pytest.fixture(scope="module")
def default_server():
    """Default-products build_server — same path operators run."""
    return build_server()


@pytest.fixture(scope="module")
def list_tools_handler(default_server):
    return default_server.request_handlers[mcp_types.ListToolsRequest]


@pytest.fixture(scope="module")
def call_tool_handler(default_server):
    return default_server.request_handlers[mcp_types.CallToolRequest]


class TestDefaultServerListTools:
    async def test_aggregates_installed_product_tools(self, list_tools_handler) -> None:
        result = await list_tools_handler(mcp_types.ListToolsRequest(method="tools/list"))
        names = {t.name for t in result.root.tools}
        # At least one product must be installed for the smoke to be
        # meaningful — otherwise the catalog is empty and we want a
        # hard failure (CI env regression) rather than a silent pass.
        installed_prefixes = {n.split("_", 1)[0] for n in names}
        assert installed_prefixes, "no product CLIs registered — install at least one"

    async def test_every_tool_has_input_schema(self, list_tools_handler) -> None:
        result = await list_tools_handler(mcp_types.ListToolsRequest(method="tools/list"))
        for tool in result.root.tools:
            assert isinstance(tool.inputSchema, dict)
            assert tool.inputSchema.get("type") == "object"
            props = tool.inputSchema.get("properties", {})
            # Reserved global flags (dry_run/token/tenant/url) must be
            # present on every tool — they're how operators steer auth
            # + dispatch from the MCP client side.
            for global_flag in ("dry_run", "token", "tenant", "url"):
                assert global_flag in props, f"{tool.name} missing global flag {global_flag}"


class TestDefaultServerCallTool:
    @pytest.mark.parametrize(
        "prefix,module_path,tool_name,expected_method,expected_path",
        _PRODUCT_PROBES,
        ids=[p[0] for p in _PRODUCT_PROBES],
    )
    async def test_per_product_dry_run_dispatch(
        self,
        prefix: str,
        module_path: str,
        tool_name: str,
        expected_method: str,
        expected_path: str,
        call_tool_handler,
        list_tools_handler,
    ) -> None:
        # Skip cleanly if this product isn't installed in the runtime.
        pytest.importorskip(module_path)

        # Sanity: the tool we're probing must actually be registered.
        listed = await list_tools_handler(mcp_types.ListToolsRequest(method="tools/list"))
        names = {t.name for t in listed.root.tools}
        assert tool_name in names, f"{tool_name} not registered for {prefix}"

        request = mcp_types.CallToolRequest(
            method="tools/call",
            params=mcp_types.CallToolRequestParams(
                name=tool_name,
                arguments={"dry_run": True},
            ),
        )
        result = (await call_tool_handler(request)).root
        assert result.isError is False, f"{tool_name} dispatch errored: {result.content}"
        payload = json.loads(result.content[0].text)
        assert payload["dry_run"] is True
        assert payload["method"] == expected_method
        if expected_path:
            assert payload["path"] == expected_path
