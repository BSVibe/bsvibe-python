"""Typer → MCP adapter.

:class:`MCPToolRegistry` walks a :class:`typer.Typer` app
(commands + nested sub-typers), derives a JSON-schema input shape
from each command's signature, and wires both ``list_tools`` and
``call_tool`` request handlers on an :class:`mcp.server.Server`.

Tool naming convention: ``{prefix}_{group_path}_{cmd}``, with all
dashes normalised to underscores. Tools are deduplicated across
``register_typer_app`` calls — a duplicate name raises ``ValueError``.

Schema derivation supports the parameter shapes the four product
CLIs actually use: ``str``, ``int``, ``float``, ``bool``, ``Path``,
``Enum``, ``list[T]``, and ``Optional[T]`` of each. Optional types
are encoded as a JSON-schema type union ``[T, "null"]``.

The handlers are wired exactly once per registry; subsequent
``register_typer_app`` calls only extend the in-memory tool table.
"""

from __future__ import annotations

import inspect
import typing
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, get_args, get_origin

import mcp.types as mcp_types
import structlog
import typer
from mcp.server import Server
from typer.models import ParameterInfo

logger = structlog.get_logger(__name__)


@dataclass(slots=True)
class ToolDescriptor:
    """Single tool exposed via the MCP server."""

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[..., Any]
    param_types: dict[str, Any] = field(default_factory=dict)


def _normalize(name: str) -> str:
    return name.replace("-", "_")


def _is_optional(tp: Any) -> tuple[bool, Any]:
    """Return ``(True, inner)`` for ``Optional[X]`` (i.e. ``X | None``)."""
    if get_origin(tp) is typing.Union:
        args = get_args(tp)
        non_none = [a for a in args if a is not type(None)]
        if len(args) == 2 and len(non_none) == 1:
            return True, non_none[0]
    return False, tp


def _map_type(tp: Any) -> dict[str, Any]:
    """Map a non-Optional Python type to a JSON-schema fragment."""
    if tp is str:
        return {"type": "string"}
    if tp is bool:  # bool first — bool is a subclass of int
        return {"type": "boolean"}
    if tp is int:
        return {"type": "integer"}
    if tp is float:
        return {"type": "number"}
    if tp is Path:
        return {"type": "string", "format": "path"}
    if isinstance(tp, type) and issubclass(tp, Enum):
        return {"type": "string", "enum": [member.value for member in tp]}
    origin = get_origin(tp)
    if origin in (list, typing.List):  # noqa: UP006 — covers both runtime forms
        item_args = get_args(tp)
        item_schema = _python_type_to_json_schema(item_args[0]) if item_args else {}
        return {"type": "array", "items": item_schema}
    return {"type": "string"}


def _python_type_to_json_schema(tp: Any) -> dict[str, Any]:
    """Map a Python type (possibly Optional) to a JSON-schema fragment."""
    optional, inner = _is_optional(tp)
    schema = _map_type(inner)
    if optional and isinstance(schema.get("type"), str):
        schema["type"] = [schema["type"], "null"]
    return schema


def _unwrap_annotated(hint: Any) -> tuple[Any, ParameterInfo | None]:
    """Pull the base type and the typer ParameterInfo out of an ``Annotated``."""
    if hasattr(hint, "__metadata__"):
        base = hint.__origin__
        for meta in hint.__metadata__:
            if isinstance(meta, ParameterInfo):
                return base, meta
        return base, None
    return hint, None


def _serialize_default(default: Any) -> Any:
    if isinstance(default, Enum):
        return default.value
    if isinstance(default, Path):
        return str(default)
    return default


def _coerce_arg(value: Any, base_type: Any) -> Any:
    """Coerce a JSON value to the Python type expected by the handler."""
    if value is None:
        return None
    _, inner = _is_optional(base_type)
    if inner is Path:
        return Path(value)
    if isinstance(inner, type) and issubclass(inner, Enum):
        return inner(value)
    return value


def _command_help(cmd: typer.models.CommandInfo) -> str:
    text = (cmd.help or cmd.short_help or "").strip()
    if text:
        return text
    if cmd.callback is not None and cmd.callback.__doc__:
        return cmd.callback.__doc__.strip().splitlines()[0]
    return ""


def _command_name(cmd: typer.models.CommandInfo) -> str:
    if cmd.name:
        return cmd.name
    if cmd.callback is not None:
        return cmd.callback.__name__
    return ""


def _build_descriptor(name: str, callback: Callable[..., Any], description: str) -> ToolDescriptor:
    sig = inspect.signature(callback)
    hints = typing.get_type_hints(callback, include_extras=True)
    properties: dict[str, dict[str, Any]] = {}
    required: list[str] = []
    param_types: dict[str, Any] = {}

    for pname, param in sig.parameters.items():
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        hint = hints.get(pname, str)
        base, info = _unwrap_annotated(hint)
        # Skip Typer's Context — it's framework plumbing, not user input.
        _, inner = _is_optional(base)
        if isinstance(inner, type) and issubclass(inner, typer.Context):
            continue
        param_types[pname] = base
        prop = _python_type_to_json_schema(base)
        if info is not None and getattr(info, "help", None):
            prop["description"] = info.help
        if param.default is inspect.Parameter.empty:
            required.append(pname)
        else:
            prop["default"] = _serialize_default(param.default)
        properties[pname] = prop

    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required

    return ToolDescriptor(
        name=name,
        description=description or name,
        input_schema=schema,
        handler=callback,
        param_types=param_types,
    )


class MCPToolRegistry:
    """Registers Typer commands as MCP tools on a single Server."""

    def __init__(self, server: Server) -> None:
        self._server = server
        self._tools: dict[str, ToolDescriptor] = {}
        self._handlers_wired = False

    def register_typer_app(self, app: typer.Typer, prefix: str) -> list[ToolDescriptor]:
        """Walk ``app``'s commands and sub-typers, register tools under ``prefix``."""
        added: list[ToolDescriptor] = []
        for descriptor in self._iter_descriptors(app, [_normalize(prefix)]):
            if descriptor.name in self._tools:
                raise ValueError(f"duplicate tool name: {descriptor.name}")
            self._tools[descriptor.name] = descriptor
            added.append(descriptor)
        self._wire_handlers()
        logger.info("registry_typer_app_registered", prefix=prefix, count=len(added))
        return added

    def tools(self) -> list[ToolDescriptor]:
        return list(self._tools.values())

    def _iter_descriptors(self, app: typer.Typer, path: list[str]) -> Iterator[ToolDescriptor]:
        for cmd in app.registered_commands:
            if cmd.callback is None:
                continue
            cmd_name = _normalize(_command_name(cmd))
            tool_name = "_".join([*path, cmd_name])
            yield _build_descriptor(tool_name, cmd.callback, _command_help(cmd))
        for grp in app.registered_groups:
            sub_path = [*path, _normalize(grp.name or "")]
            yield from self._iter_descriptors(grp.typer_instance, sub_path)

    def _wire_handlers(self) -> None:
        if self._handlers_wired:
            return
        self._handlers_wired = True

        @self._server.list_tools()
        async def _list_tools() -> list[mcp_types.Tool]:
            return [
                mcp_types.Tool(
                    name=descriptor.name,
                    description=descriptor.description,
                    inputSchema=descriptor.input_schema,
                )
                for descriptor in self._tools.values()
            ]

        @self._server.call_tool()
        async def _call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
            descriptor = self._tools.get(name)
            if descriptor is None:
                raise ValueError(f"unknown tool: {name}")
            kwargs = {key: _coerce_arg(value, descriptor.param_types.get(key)) for key, value in arguments.items()}
            result = descriptor.handler(**kwargs)
            if inspect.isawaitable(result):
                result = await result
            if result is None:
                return {"ok": True}
            if isinstance(result, dict):
                return result
            return {"result": result}
