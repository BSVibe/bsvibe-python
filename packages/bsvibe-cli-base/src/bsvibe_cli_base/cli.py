"""Typer factory shared by every BSVibe product CLI.

``cli_app`` returns a pre-wired :class:`typer.Typer` whose root callback
accepts the standard global flag set (``--profile``, ``--output``,
``--tenant``, ``--token``, ``--url``, ``--dry-run``) and stashes a
resolved :class:`CliContext` on ``ctx.obj``. Subcommands then receive
profile / token / formatter without re-implementing argument parsing.

Resolution rules at the root callback:

* **Profile** — ``--profile`` flag → ``$BSVIBE_PROFILE`` env →
  :meth:`ProfileStore.get_active`. May be ``None``; the factory does
  not enforce presence so commands like ``profile add`` can run before
  any profile exists.
* **URL** — ``--url`` flag → profile.url → empty string.
* **Tenant** — ``--tenant`` flag → profile.tenant_id → ``None``.
* **Token** — ``--token`` flag wins; otherwise
  :func:`bsvibe_cli_base.keyring.resolve_token` (keyring → env →
  profile.token_ref). May be ``None`` (e.g. for ``login``).
* **Output** — explicit ``--output`` → autodetect (TTY → ``table``,
  else ``json``).

The factory does NOT verify tokens, refresh them, or talk to any
remote — that lives in :mod:`bsvibe_cli_base.http` (TASK-006).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog
import typer

from bsvibe_cli_base import keyring as keyring_mod
from bsvibe_cli_base.config import Profile
from bsvibe_cli_base.output import OutputFormatter
from bsvibe_cli_base.profile import ProfileNotFoundError, ProfileStore

if TYPE_CHECKING:  # pragma: no cover - typing only
    pass

logger = structlog.get_logger(__name__)


@dataclass
class CliContext:
    """Per-invocation context attached to ``typer.Context.obj``.

    Subcommands access it as ``ctx.obj`` and read whichever fields they
    need; the factory has already merged the flag, env, and profile
    sources into a single resolved view.
    """

    profile: Profile | None
    url: str
    tenant_id: str | None
    token: str | None
    dry_run: bool
    formatter: OutputFormatter
    profile_store: ProfileStore


def cli_app(
    *,
    name: str,
    help: str | None = None,
    profile_store: ProfileStore | None = None,
) -> typer.Typer:
    """Build a Typer app with the standard global flag set wired.

    Parameters
    ----------
    name:
        Command name shown in ``--help``.
    help:
        Top-level help text.
    profile_store:
        Override the profile store (tests pass a tmp-path-backed
        instance). Production callers typically rely on the default
        XDG-resolved store.
    """

    store = profile_store if profile_store is not None else ProfileStore()
    app = typer.Typer(
        name=name,
        help=help or f"{name} — BSVibe CLI",
        no_args_is_help=True,
        add_completion=False,
    )

    @app.callback()
    def _root(
        ctx: typer.Context,
        profile_name: str | None = typer.Option(
            None,
            "--profile",
            "-p",
            envvar="BSVIBE_PROFILE",
            help="Profile name (overrides active profile).",
        ),
        output: str | None = typer.Option(
            None,
            "--output",
            "-o",
            envvar="BSVIBE_OUTPUT",
            help="Output format: json | yaml | tsv | table. Default: TTY→table, else json.",
        ),
        tenant: str | None = typer.Option(
            None,
            "--tenant",
            envvar="BSVIBE_TENANT",
            help="Tenant ID (overrides profile default).",
        ),
        token: str | None = typer.Option(
            None,
            "--token",
            envvar="BSVIBE_TOKEN",
            help="Bearer token (overrides keyring / profile resolution).",
        ),
        url: str | None = typer.Option(
            None,
            "--url",
            envvar="BSVIBE_URL",
            help="Control-plane URL (overrides profile.url).",
        ),
        dry_run: bool = typer.Option(
            False,
            "--dry-run",
            help="Show planned actions without executing.",
        ),
    ) -> None:
        resolved_profile = _resolve_profile(store, profile_name)

        resolved_url = url if url else (resolved_profile.url if resolved_profile else "")
        resolved_tenant = tenant if tenant else (resolved_profile.tenant_id if resolved_profile else None)
        if token:
            resolved_token: str | None = token
        elif resolved_profile is not None:
            resolved_token = keyring_mod.resolve_token(resolved_profile)
        else:
            resolved_token = None

        try:
            formatter = OutputFormatter(format=output, stream=sys.stdout)
        except ValueError as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(code=2) from None

        ctx.obj = CliContext(
            profile=resolved_profile,
            url=resolved_url,
            tenant_id=resolved_tenant,
            token=resolved_token,
            dry_run=dry_run,
            formatter=formatter,
            profile_store=store,
        )

    return app


def _resolve_profile(store: ProfileStore, name: str | None) -> Profile | None:
    if name:
        try:
            return store.get_profile(name)
        except ProfileNotFoundError:
            typer.echo(f"Error: profile not found: {name}", err=True)
            raise typer.Exit(code=2) from None
    return store.get_active()


__all__ = ["CliContext", "cli_app"]
