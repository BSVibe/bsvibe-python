"""Render CLI command results as JSON, YAML, TSV, or a rich table.

The formatter is the single seam every product CLI uses to print
structured data, so two operators on different terminals get behavior
that matches their context:

* Interactive shell → ``table`` (rich-rendered, easy to scan).
* Pipe / redirect / CI → ``json`` (machine-readable, ``jq``-friendly).

The TTY heuristic mirrors GNU coreutils' ``--color=auto`` convention.
The explicit ``--output`` flag always wins over autodetect, so users
can force machine output even from a terminal (handy for ``| pbcopy``
on macOS) and force a table from a non-TTY for screenshot-quality
docs.

Format names are accepted case-insensitively; unknown names raise
``ValueError`` at construction time so a typo surfaces immediately
rather than after a long-running command.
"""

from __future__ import annotations

import io
import json
import sys
from typing import Any, TextIO

import yaml
from rich.console import Console
from rich.table import Table

FORMATS: tuple[str, ...] = ("json", "yaml", "tsv", "table")


class OutputFormatter:
    """Render structured data in one of :data:`FORMATS`.

    Parameters
    ----------
    format:
        Explicit format name (case-insensitive). ``None`` means
        autodetect from ``is_tty`` (TTY → ``table``, else ``json``).
    is_tty:
        Whether the destination stream is a TTY. Defaults to
        ``stream.isatty()`` when ``stream`` is given, else ``False``.
    stream:
        Output stream for :meth:`emit`. Defaults to ``sys.stdout``.
    """

    format: str

    def __init__(
        self,
        *,
        format: str | None = None,
        is_tty: bool | None = None,
        stream: TextIO | None = None,
    ) -> None:
        self._stream: TextIO = stream if stream is not None else sys.stdout
        if is_tty is None:
            is_tty = bool(getattr(self._stream, "isatty", lambda: False)())
        if format is None:
            self.format = "table" if is_tty else "json"
        else:
            normalised = format.lower()
            if normalised not in FORMATS:
                raise ValueError(f"Unknown output format {format!r}; expected one of {FORMATS}")
            self.format = normalised

    def render(self, data: Any) -> str:
        """Render ``data`` to a string in the configured format."""
        if self.format == "json":
            return _render_json(data)
        if self.format == "yaml":
            return _render_yaml(data)
        if self.format == "tsv":
            return _render_tsv(data)
        return _render_table(data)

    def emit(self, data: Any) -> None:
        """Write rendered output to the bound stream with a trailing newline."""
        rendered = self.render(data)
        if not rendered.endswith("\n"):
            rendered += "\n"
        self._stream.write(rendered)


def _render_json(data: Any) -> str:
    return json.dumps(data, indent=2, default=str, ensure_ascii=False)


def _render_yaml(data: Any) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True).rstrip("\n")


def _render_tsv(data: Any) -> str:
    rows = _normalise_rows(data)
    if not rows:
        return ""
    headers: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row.keys():
            if key not in seen:
                seen.add(key)
                headers.append(key)
    lines = ["\t".join(headers)]
    for row in rows:
        lines.append("\t".join(_tsv_cell(row.get(h, "")) for h in headers))
    return "\n".join(lines)


def _tsv_cell(value: Any) -> str:
    text = "" if value is None else str(value)
    # Escape control characters that would corrupt the row layout.
    return text.replace("\t", " ").replace("\n", " ").replace("\r", " ")


def _render_table(data: Any) -> str:
    rows = _normalise_rows(data)
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120)
    if not rows:
        return ""
    headers: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row.keys():
            if key not in seen:
                seen.add(key)
                headers.append(key)
    table = Table(show_header=True, header_style="bold")
    for header in headers:
        table.add_column(header)
    for row in rows:
        table.add_row(*[str(row.get(h, "")) for h in headers])
    console.print(table)
    return buf.getvalue()


def _normalise_rows(data: Any) -> list[dict[str, Any]]:
    """Coerce input to ``list[dict]`` for tabular renderers."""
    if isinstance(data, list):
        return [r if isinstance(r, dict) else {"value": r} for r in data]
    if isinstance(data, dict):
        return [data]
    return [{"value": data}]


__all__ = ["OutputFormatter", "FORMATS"]
