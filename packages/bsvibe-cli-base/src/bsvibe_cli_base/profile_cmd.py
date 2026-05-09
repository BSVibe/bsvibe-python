"""``<product> profile {add,list,use,remove}`` — profile CRUD subapp.

Thin Typer wrapper over :class:`ProfileStore` so users can manage
``~/.bsvibe/config.yaml`` without hand-editing YAML. The store is taken
from ``ctx.obj.profile_store`` so the cli factory's test override flows
through.
"""

from __future__ import annotations

import typer

from bsvibe_cli_base.cli import CliContext
from bsvibe_cli_base.config import Profile
from bsvibe_cli_base.profile import (
    ProfileExistsError,
    ProfileNotFoundError,
    ProfileStore,
)

profile_app = typer.Typer(
    name="profile",
    help="Manage local CLI profiles (~/.bsvibe/config.yaml).",
    add_completion=False,
    no_args_is_help=True,
)


def _store(ctx: typer.Context) -> ProfileStore:
    cli_obj = ctx.obj
    if isinstance(cli_obj, CliContext):
        return cli_obj.profile_store
    return ProfileStore()


@profile_app.command("add")
def add(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Profile name (unique)."),
    url: str = typer.Option(..., "--url", help="Control-plane base URL."),
    tenant: str | None = typer.Option(None, "--tenant", help="Default tenant id."),
    set_default: bool = typer.Option(False, "--default", help="Mark this profile as the active default."),
) -> None:
    store = _store(ctx)
    try:
        store.add_profile(
            Profile(
                name=name,
                url=url,
                tenant_id=tenant,
                default=set_default,
            )
        )
    except ProfileExistsError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=2) from None

    if set_default:
        store.set_active(name)

    typer.echo(f"Profile '{name}' added.")


@profile_app.command("list")
def list_profiles(ctx: typer.Context) -> None:
    store = _store(ctx)
    rows = store.list_profiles()
    if not rows:
        typer.echo("No profiles yet. Run '<cli> login' or '<cli> profile add' first.")
        return
    for prof in rows:
        marker = "*" if prof.default else " "
        tenant = prof.tenant_id or "-"
        typer.echo(f"{marker} {prof.name}\t{prof.url}\t{tenant}")


@profile_app.command("use")
def use(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Profile name to activate."),
) -> None:
    store = _store(ctx)
    try:
        store.set_active(name)
    except ProfileNotFoundError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=2) from None
    typer.echo(f"Active profile: {name}")


@profile_app.command("remove")
def remove(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Profile name to remove."),
) -> None:
    store = _store(ctx)
    try:
        store.remove_profile(name)
    except ProfileNotFoundError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=2) from None
    typer.echo(f"Profile '{name}' removed.")


__all__ = ["profile_app"]
