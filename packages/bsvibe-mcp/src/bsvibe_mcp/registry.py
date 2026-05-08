"""Typer → MCP adapter.

:class:`MCPToolRegistry` walks a :class:`typer.Typer` app
(commands + nested sub-typers), derives a JSON-schema input shape
from each command's signature, and wires both ``list_tools`` and
``call_tool`` request handlers on an :class:`mcp.server.Server`.

Tool naming convention: ``{prefix}_{group_path}_{cmd}``, with all
dashes normalised to underscores. Tools are deduplicated across
``register_typer_app`` / ``register_cli_app`` calls — a duplicate
name raises ``ValueError``.

Schema derivation supports the parameter shapes the four product
CLIs actually use: ``str``, ``int``, ``float``, ``bool``, ``Path``,
``Enum``, ``list[T]``, and ``Optional[T]`` of each. Optional types
are encoded as a JSON-schema type union ``[T, "null"]``.

Two registration paths:

* :meth:`register_typer_app` — direct callable dispatch. The tool
  handler is the underlying ``cmd.callback`` invoked with the JSON
  arguments coerced to Python types. Used by synthetic Typer apps
  whose commands accept plain kwargs.
* :meth:`register_cli_app` — CliRunner-based dispatch. Tool args are
  translated to argv (``--flag value`` / positional) and the root app
  is invoked via :class:`typer.testing.CliRunner`. The captured stdout
  is parsed back as JSON. Used for product CLIs (bsgateway, bsage,
  bsnexus, bsupervisor) that rely on ``ctx.obj`` set up by
  :func:`bsvibe_cli_base.cli_app`.

The handlers are wired exactly once per registry; subsequent
register calls only extend the in-memory tool table.
"""

from __future__ import annotations

import inspect
import json
import types
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
from typer.models import ArgumentInfo, OptionInfo, ParameterInfo
from typer.testing import CliRunner

logger = structlog.get_logger(__name__)


@dataclass(slots=True)
class ToolDescriptor:
    """Single tool exposed via the MCP server."""

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[..., Any]
    param_types: dict[str, Any] = field(default_factory=dict)
    skip_coerce: bool = False


def _normalize(name: str) -> str:
    return name.replace("-", "_")


def _is_optional(tp: Any) -> tuple[bool, Any]:
    """Return ``(True, inner)`` for ``Optional[X]`` (i.e. ``X | None``).

    Handles both ``typing.Union[X, None]`` (PEP 484) and ``X | None``
    (PEP 604 — :class:`types.UnionType`).
    """
    origin = get_origin(tp)
    if origin is typing.Union or origin is types.UnionType:
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


def _resolve_param(param: inspect.Parameter, hint: Any) -> tuple[Any, ParameterInfo | None, Any]:
    """Resolve ``(base_type, ParameterInfo|None, actual_default)`` for a Typer parameter.

    Typer accepts two parameter styles:

    * **Annotated** — ``name: Annotated[str, typer.Option("--name")] = "x"``.
      ``ParameterInfo`` lives in the type metadata; the actual default is
      ``param.default``.
    * **Default-value** — ``name: str = typer.Option("x", "--name")``. The
      ``OptionInfo`` instance is itself ``param.default``; the user's default
      is ``info.default`` (``...`` / ``Ellipsis`` means required).
    """
    base, info = _unwrap_annotated(hint)
    if info is not None:
        return base, info, param.default
    if isinstance(param.default, ParameterInfo):
        actual = param.default.default
        if actual is ...:
            actual = inspect.Parameter.empty
        return base, param.default, actual
    return base, None, param.default


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


def _flag_metadata(pname: str, base_type: Any, info: ParameterInfo | None) -> dict[str, Any]:
    """CLI argv metadata for a single parameter (flag name, kind, etc.)."""
    _, inner = _is_optional(base_type)
    is_bool = inner is bool
    origin = get_origin(inner)
    is_list = origin in (list, typing.List)  # noqa: UP006

    if isinstance(info, ArgumentInfo):
        return {"is_argument": True, "is_bool": is_bool, "is_list": is_list}

    decls: tuple[str, ...] | None = None
    if isinstance(info, OptionInfo):
        decls = tuple(info.param_decls or ())

    primary = decls[0] if decls else f"--{pname.replace('_', '-')}"
    flag = primary
    negative_flag: str | None = None
    if "/" in primary:
        flag, negative_flag = primary.split("/", 1)
    return {
        "is_argument": False,
        "is_bool": is_bool,
        "is_list": is_list,
        "flag": flag,
        "negative_flag": negative_flag,
    }


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
        base, info, actual_default = _resolve_param(param, hint)
        # Skip Typer's Context — it's framework plumbing, not user input.
        _, inner = _is_optional(base)
        if isinstance(inner, type) and issubclass(inner, typer.Context):
            continue
        param_types[pname] = base
        prop = _python_type_to_json_schema(base)
        if info is not None and getattr(info, "help", None):
            prop["description"] = info.help
        if actual_default is inspect.Parameter.empty:
            required.append(pname)
        else:
            prop["default"] = _serialize_default(actual_default)
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


def _build_param_meta(callback: Callable[..., Any]) -> dict[str, dict[str, Any]]:
    """Per-parameter argv metadata used by the CliRunner-based handler."""
    sig = inspect.signature(callback)
    hints = typing.get_type_hints(callback, include_extras=True)
    meta: dict[str, dict[str, Any]] = {}
    for pname, param in sig.parameters.items():
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        hint = hints.get(pname, str)
        base, info, _ = _resolve_param(param, hint)
        _, inner = _is_optional(base)
        if isinstance(inner, type) and issubclass(inner, typer.Context):
            continue
        meta[pname] = _flag_metadata(pname, base, info)
    return meta


def _argv_value(value: Any) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _make_cli_handler(
    root_app: typer.Typer,
    command_path: list[str],
    param_meta: dict[str, dict[str, Any]],
) -> Callable[..., Any]:
    """Build a handler that invokes ``root_app`` via :class:`CliRunner`.

    Translates JSON arguments to argv, captures stdout, and parses it
    back as JSON. Global flags carried as reserved kwargs:

    * ``dry_run`` (bool) → ``--dry-run`` (short-circuits HTTP).
    * ``token`` (str) → ``--token`` (per-call auth override).
    * ``tenant`` (str) → ``--tenant`` (per-call tenant override).
    * ``url`` (str) → ``--url`` (per-call control-plane override).

    Reserved names are stripped before mapping the rest to subcommand
    flags so a tool's own params can co-exist.
    """
    runner = CliRunner(mix_stderr=False)
    reserved: set[str] = {"dry_run", "token", "tenant", "url", "_profile"}

    def handler(**kwargs: Any) -> dict[str, Any]:
        argv: list[str] = ["--output", "json"]
        if kwargs.pop("dry_run", False):
            argv.append("--dry-run")
        for global_flag in ("token", "tenant", "url"):
            value = kwargs.pop(global_flag, None)
            if value is not None:
                argv.extend([f"--{global_flag}", str(value)])
        # Strip any other reserved kwargs we don't directly map.
        for k in list(kwargs):
            if k in reserved:
                kwargs.pop(k)

        argv.extend(command_path)

        positional: list[str] = []
        for pname, value in kwargs.items():
            if value is None:
                continue
            meta = param_meta.get(pname, {})
            if meta.get("is_argument"):
                positional.append(_argv_value(value))
                continue
            flag = meta.get("flag") or f"--{pname.replace('_', '-')}"
            if meta.get("is_bool"):
                if value:
                    argv.append(flag)
                else:
                    neg = meta.get("negative_flag")
                    if neg:
                        argv.append(neg)
                continue
            if meta.get("is_list"):
                for item in value:
                    argv.extend([flag, _argv_value(item)])
                continue
            argv.extend([flag, _argv_value(value)])
        argv.extend(positional)

        result = runner.invoke(root_app, argv, catch_exceptions=False)
        if result.exit_code != 0:
            return {
                "ok": False,
                "exit_code": result.exit_code,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
        out = result.stdout.strip()
        if not out:
            return {"ok": True}
        try:
            payload = json.loads(out)
        except json.JSONDecodeError:
            return {"output": out}
        if isinstance(payload, dict):
            return payload
        return {"result": payload}

    return handler


class MCPToolRegistry:
    """Registers Typer commands as MCP tools on a single Server."""

    def __init__(self, server: Server) -> None:
        self._server = server
        self._tools: dict[str, ToolDescriptor] = {}
        self._handlers_wired = False

    def register_typer_app(self, app: typer.Typer, prefix: str) -> list[ToolDescriptor]:
        """Walk ``app``'s commands and sub-typers, register tools under ``prefix``.

        Handlers dispatch directly to the underlying command callable. Use this
        for synthetic Typer apps where commands accept plain kwargs (no
        ``ctx.obj`` plumbing). Product CLIs go through :meth:`register_cli_app`.
        """
        added: list[ToolDescriptor] = []
        for descriptor in self._iter_descriptors(app, [_normalize(prefix)]):
            self._add(descriptor)
            added.append(descriptor)
        self._wire_handlers()
        logger.info("registry_typer_app_registered", prefix=prefix, count=len(added))
        return added

    def register_cli_app(self, root_app: typer.Typer, prefix: str) -> list[ToolDescriptor]:
        """Register a ``cli_app()``-style root Typer app via CliRunner dispatch.

        Tool args are translated to argv (``--flag value`` / positional) and the
        root app is invoked end-to-end so the global root callback can resolve
        profile / token / tenant / output / dry_run on ``ctx.obj``. Captured
        stdout (configured via ``--output json``) is parsed back as JSON.

        Each tool gains four global flags as reserved schema params:
        ``dry_run`` (bool), ``token``, ``tenant``, ``url``. They map to the
        same-named ``--`` flags on the root callback.
        """
        added: list[ToolDescriptor] = []
        for descriptor in self._iter_cli_descriptors(root_app, root_app, [_normalize(prefix)], []):
            self._add(descriptor)
            added.append(descriptor)
        self._wire_handlers()
        logger.info("registry_cli_app_registered", prefix=prefix, count=len(added))
        return added

    def tools(self) -> list[ToolDescriptor]:
        return list(self._tools.values())

    def _add(self, descriptor: ToolDescriptor) -> None:
        if descriptor.name in self._tools:
            raise ValueError(f"duplicate tool name: {descriptor.name}")
        self._tools[descriptor.name] = descriptor

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

    def _iter_cli_descriptors(
        self,
        root_app: typer.Typer,
        app: typer.Typer,
        name_path: list[str],
        cli_path: list[str],
    ) -> Iterator[ToolDescriptor]:
        for cmd in app.registered_commands:
            if cmd.callback is None:
                continue
            raw_name = _command_name(cmd)
            tool_name = "_".join([*name_path, _normalize(raw_name)])
            cmd_path = [*cli_path, raw_name]
            descriptor = _build_descriptor(tool_name, cmd.callback, _command_help(cmd))
            yield self._cli_descriptor(descriptor, root_app, cmd_path, cmd.callback)
        for grp in app.registered_groups:
            grp_name = grp.name or ""
            sub_name_path = [*name_path, _normalize(grp_name)]
            sub_cli_path = [*cli_path, grp_name]
            sub = grp.typer_instance
            cb = sub.registered_callback
            # ``invoke_without_command=True`` callback-only sub-app — treat the
            # callback as the tool. (e.g. ``bsgateway execute``)
            if (
                cb is not None
                and cb.callback is not None
                and getattr(cb, "invoke_without_command", False)
                and not sub.registered_commands
                and not sub.registered_groups
            ):
                tool_name = "_".join(sub_name_path)
                description = _typer_help(sub) or _command_help_callback(cb)
                descriptor = _build_descriptor(tool_name, cb.callback, description)
                yield self._cli_descriptor(descriptor, root_app, sub_cli_path, cb.callback)
                continue
            yield from self._iter_cli_descriptors(root_app, sub, sub_name_path, sub_cli_path)

    @staticmethod
    def _cli_descriptor(
        descriptor: ToolDescriptor,
        root_app: typer.Typer,
        cli_path: list[str],
        callback: Callable[..., Any],
    ) -> ToolDescriptor:
        """Wrap a descriptor with a CliRunner-based handler + global-flag schema."""
        param_meta = _build_param_meta(callback)
        handler = _make_cli_handler(root_app, cli_path, param_meta)
        schema = dict(descriptor.input_schema)
        properties = dict(schema.get("properties", {}))
        properties.setdefault(
            "dry_run", {"type": "boolean", "default": False, "description": "Render planned action without executing."}
        )
        properties.setdefault(
            "token", {"type": ["string", "null"], "default": None, "description": "Bearer token override."}
        )
        properties.setdefault(
            "tenant", {"type": ["string", "null"], "default": None, "description": "Tenant id override."}
        )
        properties.setdefault(
            "url", {"type": ["string", "null"], "default": None, "description": "Control-plane URL override."}
        )
        schema["properties"] = properties
        return ToolDescriptor(
            name=descriptor.name,
            description=descriptor.description,
            input_schema=schema,
            handler=handler,
            param_types=descriptor.param_types,
            skip_coerce=True,
        )

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
            if descriptor.skip_coerce:
                kwargs = dict(arguments)
            else:
                kwargs = {key: _coerce_arg(value, descriptor.param_types.get(key)) for key, value in arguments.items()}
            result = descriptor.handler(**kwargs)
            if inspect.isawaitable(result):
                result = await result
            if result is None:
                return {"ok": True}
            if isinstance(result, dict):
                return result
            return {"result": result}


def _typer_help(app: typer.Typer) -> str:
    info = getattr(app, "info", None)
    if info is None:
        return ""
    return (getattr(info, "help", None) or "").strip()


def _command_help_callback(cb: Any) -> str:
    fn = getattr(cb, "callback", None)
    if fn is not None and fn.__doc__:
        return fn.__doc__.strip().splitlines()[0]
    return ""
