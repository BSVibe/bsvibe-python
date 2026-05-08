"""``bsvibe-mcp`` Typer entry point.

TASK-001 wires only the app object so the ``[project.scripts]`` entry
resolves; ``serve`` and ``list-tools`` subcommands land in TASK-007.
"""

from __future__ import annotations

import typer

app = typer.Typer(name="bsvibe-mcp", help="BSVibe MCP server — exposes product CLIs as MCP tools.")


@app.callback()
def _root() -> None:
    """Root callback — subcommands wired in TASK-007."""
