"""BSVibe MCP server — public API.

Re-exports the :func:`build_server` factory so consumers can do::

    from bsvibe_mcp import build_server
    server = build_server()
"""

from __future__ import annotations

from bsvibe_mcp.server import build_server

__version__ = "0.1.0"

__all__ = ["build_server", "__version__"]
