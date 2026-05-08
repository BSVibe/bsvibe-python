"""Tests for :meth:`bsvibe_mcp.registry.MCPToolRegistry.register_cli_app`.

These exercise the CliRunner-based dispatch path that wraps a
``cli_app()``-style root Typer app — the shape every product CLI
(bsgateway, bsage, bsnexus, bsupervisor) uses. Synthetic apps stand
in for the product CLIs so the unit tests stay fast and free of
cross-repo deps.

Per memory ``mcp-python-sdk-testing``: invoke registered request
handlers directly (no subprocess). Result is wrapped in
``ServerResult.root``.
"""

from __future__ import annotations

import json

import mcp.types as mcp_types
import pytest
import typer
from bsvibe_cli_base import cli_app
from bsvibe_cli_base.profile import ProfileStore
from mcp.server import Server

from bsvibe_mcp.registry import MCPToolRegistry


def _build_fake_product_app(tmp_profile_dir) -> typer.Typer:
    """Synthetic ``cli_app()``-style root with two sub-apps + a callback-only one."""
    store = ProfileStore(path=tmp_profile_dir / "config.yaml")
    app = cli_app(name="fakeprod", help="Fake product CLI", profile_store=store)

    items = typer.Typer(name="items", no_args_is_help=True, add_completion=False)

    @items.command("list")
    def items_list(
        ctx: typer.Context,
        type_filter: str = typer.Option("all", "--type", help="Filter."),
    ) -> None:
        ctx.obj.formatter.emit({"action": "list", "filter": type_filter, "dry_run": ctx.obj.dry_run})

    @items.command("add")
    def items_add(
        ctx: typer.Context,
        name: str = typer.Option(..., "--name"),
        passthrough: bool = typer.Option(True, "--passthrough/--no-passthrough"),
        tags: list[str] | None = typer.Option(None, "--tag"),
    ) -> None:
        ctx.obj.formatter.emit({"action": "add", "name": name, "passthrough": passthrough, "tags": tags or []})

    @items.command("remove")
    def items_remove(
        ctx: typer.Context,
        item_id: str = typer.Argument(..., help="Item id."),
    ) -> None:
        ctx.obj.formatter.emit({"action": "remove", "id": item_id})

    runtask = typer.Typer(name="runtask", invoke_without_command=True, no_args_is_help=False)

    @runtask.callback(invoke_without_command=True)
    def runtask_cb(
        ctx: typer.Context,
        prompt: str = typer.Argument(None, help="Prompt."),
        wait: bool = typer.Option(True, "--wait/--no-wait"),
    ) -> None:
        if prompt is None:
            return
        ctx.obj.formatter.emit({"action": "runtask", "prompt": prompt, "wait": wait})

    app.add_typer(items, name="items")
    app.add_typer(runtask, name="runtask")
    return app


@pytest.fixture
def fake_app(tmp_path) -> typer.Typer:
    return _build_fake_product_app(tmp_path)


@pytest.fixture
def registry_with_fake(fake_app) -> tuple[Server, MCPToolRegistry]:
    server = Server("t")
    registry = MCPToolRegistry(server)
    registry.register_cli_app(fake_app, prefix="fakeprod")
    return server, registry


class TestRegisterCliApp:
    def test_walks_subapp_commands(self, registry_with_fake) -> None:
        _, registry = registry_with_fake
        names = {t.name for t in registry.tools()}
        assert {"fakeprod_items_list", "fakeprod_items_add", "fakeprod_items_remove"}.issubset(names)

    def test_registers_callback_only_subapp_as_tool(self, registry_with_fake) -> None:
        _, registry = registry_with_fake
        names = {t.name for t in registry.tools()}
        # ``runtask`` is invoke_without_command=True, no commands — the callback
        # itself becomes the tool.
        assert "fakeprod_runtask" in names

    def test_global_flags_in_schema(self, registry_with_fake) -> None:
        _, registry = registry_with_fake
        descriptor = next(t for t in registry.tools() if t.name == "fakeprod_items_list")
        props = descriptor.input_schema["properties"]
        for global_flag in ("dry_run", "token", "tenant", "url"):
            assert global_flag in props
        assert props["dry_run"]["type"] == "boolean"

    def test_required_arg_in_schema(self, registry_with_fake) -> None:
        _, registry = registry_with_fake
        descriptor = next(t for t in registry.tools() if t.name == "fakeprod_items_remove")
        # ``item_id`` is a typer.Argument(...) — should appear in `required`.
        assert "item_id" in descriptor.input_schema.get("required", [])

    def test_optional_with_default_not_required(self, registry_with_fake) -> None:
        _, registry = registry_with_fake
        descriptor = next(t for t in registry.tools() if t.name == "fakeprod_items_add")
        props = descriptor.input_schema["properties"]
        # ``passthrough`` defaults to True via typer.Option(True, ...)
        assert props["passthrough"]["default"] is True


class TestCliRunnerDispatch:
    async def test_call_tool_dry_run_emits_payload(self, registry_with_fake) -> None:
        server, _ = registry_with_fake
        handler = server.request_handlers[mcp_types.CallToolRequest]
        request = mcp_types.CallToolRequest(
            method="tools/call",
            params=mcp_types.CallToolRequestParams(
                name="fakeprod_items_list",
                arguments={"type_filter": "custom", "dry_run": True},
            ),
        )
        result = (await handler(request)).root
        assert result.isError is False
        text = result.content[0].text
        payload = json.loads(text)
        assert payload["filter"] == "custom"
        assert payload["dry_run"] is True

    async def test_call_tool_with_argument_positional(self, registry_with_fake) -> None:
        server, _ = registry_with_fake
        handler = server.request_handlers[mcp_types.CallToolRequest]
        request = mcp_types.CallToolRequest(
            method="tools/call",
            params=mcp_types.CallToolRequestParams(
                name="fakeprod_items_remove",
                arguments={"item_id": "abc-123"},
            ),
        )
        result = (await handler(request)).root
        assert result.isError is False
        payload = json.loads(result.content[0].text)
        assert payload == {"action": "remove", "id": "abc-123"}

    async def test_call_tool_with_negative_bool_flag(self, registry_with_fake) -> None:
        server, _ = registry_with_fake
        handler = server.request_handlers[mcp_types.CallToolRequest]
        request = mcp_types.CallToolRequest(
            method="tools/call",
            params=mcp_types.CallToolRequestParams(
                name="fakeprod_items_add",
                arguments={"name": "alpha", "passthrough": False, "tags": ["t1", "t2"]},
            ),
        )
        result = (await handler(request)).root
        assert result.isError is False
        payload = json.loads(result.content[0].text)
        assert payload["passthrough"] is False
        assert payload["tags"] == ["t1", "t2"]

    async def test_call_tool_callback_only_subapp(self, registry_with_fake) -> None:
        server, _ = registry_with_fake
        handler = server.request_handlers[mcp_types.CallToolRequest]
        request = mcp_types.CallToolRequest(
            method="tools/call",
            params=mcp_types.CallToolRequestParams(
                name="fakeprod_runtask",
                arguments={"prompt": "hello", "wait": False},
            ),
        )
        result = (await handler(request)).root
        assert result.isError is False
        payload = json.loads(result.content[0].text)
        assert payload == {"action": "runtask", "prompt": "hello", "wait": False}

    async def test_duplicate_register_raises(self, fake_app) -> None:
        server = Server("t")
        registry = MCPToolRegistry(server)
        registry.register_cli_app(fake_app, prefix="fakeprod")
        with pytest.raises(ValueError, match="duplicate"):
            registry.register_cli_app(fake_app, prefix="fakeprod")
