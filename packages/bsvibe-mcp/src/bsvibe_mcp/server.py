"""MCP server factory.

Builds an :class:`mcp.server.Server` and registers the four product
CLIs (bsgateway / bsage / bsnexus / bsupervisor) as MCP tools via
:class:`bsvibe_mcp.registry.MCPToolRegistry`. Each product's CLI is
imported lazily — if the product package isn't installed in the
runtime environment, registration is skipped with a single
``structlog`` warning so the server stays usable for the products
that *are* installed.

Tool naming follows ``{product}_{group}_{cmd}`` — e.g.
``bsgateway_models_list``, ``bsage_canon_apply``. Args carry through
the root callback's ``--token`` / ``--tenant`` / ``--url`` /
``--dry-run`` flags as reserved input-schema keys (see
:class:`MCPToolRegistry.register_cli_app`).
"""

from __future__ import annotations

import importlib

import structlog
from mcp.server import Server

from bsvibe_mcp.registry import MCPToolRegistry

logger = structlog.get_logger(__name__)

DEFAULT_SERVER_NAME = "bsvibe-mcp"

# (prefix, dotted import path of the root Typer app)
#
# bsnexus's wheel ships ``bsnexus_cli/main.py`` (force-include of
# ``backend/src/cli/``) but the file's absolute imports still reference
# ``backend.src.cli.commands`` — so the wheel-installed module fails to
# import in normal environments. ``_try_register`` catches that and
# emits a structlog warning; tests for bsnexus use ``importorskip`` and
# skip until the upstream wheel is fixed.
_PRODUCT_CLI_MODULES: tuple[tuple[str, str], ...] = (
    ("bsgateway", "bsgateway.cli.main"),
    ("bsage", "bsage.cli.main"),
    ("bsnexus", "bsnexus_cli.main"),
    ("bsupervisor", "bsupervisor.cli.main"),
)


def build_server(
    name: str = DEFAULT_SERVER_NAME,
    *,
    products: tuple[tuple[str, str], ...] | None = None,
) -> Server:
    """Construct an MCP server with product CLIs registered as tools.

    Parameters
    ----------
    name:
        MCP server name advertised to clients.
    products:
        Override the default product list (``[(prefix, module_path)]``).
        Tests pass a custom tuple to keep registration narrow.
    """
    server = Server(name)
    registry = MCPToolRegistry(server)
    for prefix, module_path in products if products is not None else _PRODUCT_CLI_MODULES:
        _try_register(registry, prefix, module_path)
    return server


def _try_register(registry: MCPToolRegistry, prefix: str, module_path: str) -> None:
    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        logger.warning("product_cli_not_installed", prefix=prefix, module=module_path, error=str(exc))
        return
    app = getattr(module, "app", None)
    if app is None:
        logger.warning("product_cli_missing_app", prefix=prefix, module=module_path)
        return
    registry.register_cli_app(app, prefix=prefix)
