"""``<product> login`` — OAuth 2.0 Device Authorization Grant subapp.

The Typer app exported here is auto-attached to every product CLI by
:func:`bsvibe_cli_base.cli.cli_app`. It runs the device flow against an
auth server (default ``https://auth.bsvibe.dev``), prints the user code
+ verification URL while the human approves in their browser, and on
approval persists both tokens to the OS keyring + the profile store —
no dashboard round-trip needed.

The async :func:`do_login` helper is split out from the Typer wrapper
so unit tests can drive the flow with a mocked :class:`DeviceFlowClient`
and an in-memory keyring backend without spawning a subprocess or
patching ``asyncio.run``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import structlog
import typer

from bsvibe_cli_base import keyring as keyring_mod
from bsvibe_cli_base.config import Profile
from bsvibe_cli_base.device_flow import DeviceFlowClient, DeviceFlowError
from bsvibe_cli_base.profile import ProfileNotFoundError, ProfileStore

logger = structlog.get_logger(__name__)


async def do_login(
    *,
    flow_client: DeviceFlowClient,
    profile_store: ProfileStore,
    profile_name: str,
    profile_url: str,
    tenant_id: str | None,
    scope: str | None = None,
    audience: str | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    print_fn: Callable[[str], None] = typer.echo,
) -> None:
    """Run the full device-flow handshake and persist the resulting grant.

    Parameters mirror the Typer wrapper one-to-one. The function never
    catches its own exceptions — :class:`DeviceFlowError` /
    :class:`DeviceFlowTimeoutError` propagate up to the wrapper, which
    converts them into a non-zero CLI exit.
    """
    code = await flow_client.request_code(scope=scope, audience=audience)
    print_fn("Open the following URL in your browser to approve this device:")
    print_fn(f"  {code.verification_uri}")
    print_fn(f"User code: {code.user_code}")
    print_fn("Waiting for approval ...")

    grant = await flow_client.poll_token(code, sleep=sleep)

    keyring_mod.set_token(profile_name, grant.access_token)
    if grant.refresh_token:
        keyring_mod.set_refresh_token(profile_name, grant.refresh_token)

    try:
        profile_store.get_profile(profile_name)
        # Existing profile — keep its url/tenant, only token refs change.
    except ProfileNotFoundError:
        profile_store.add_profile(
            Profile(
                name=profile_name,
                url=profile_url,
                tenant_id=tenant_id,
                default=True,
                token_ref=profile_name,
                refresh_token_ref=profile_name if grant.refresh_token else None,
            )
        )
        profile_store.set_active(profile_name)

    print_fn(f"Saved PAT to keyring for profile '{profile_name}'.")


login_app = typer.Typer(
    name="login",
    help="Authenticate via OAuth 2.0 Device Authorization Grant.",
    add_completion=False,
    invoke_without_command=True,
    no_args_is_help=False,
)


@login_app.callback(invoke_without_command=True)
def login(
    ctx: typer.Context,
    auth_url: str = typer.Option(
        "https://auth.bsvibe.dev",
        "--auth-url",
        envvar="BSVIBE_AUTH_URL",
        help="Auth server base URL.",
    ),
    client_id: str = typer.Option(
        "cli",
        "--client-id",
        envvar="BSVIBE_CLIENT_ID",
        help="OAuth client id (must be seeded as a public device-flow client).",
    ),
    scope: str | None = typer.Option(
        None,
        "--scope",
        help="Space-separated scopes (e.g. 'gateway:* sage:*').",
    ),
    audience: str | None = typer.Option(
        None,
        "--audience",
        help="Comma-separated audiences (e.g. 'gateway,sage,nexus,supervisor').",
    ),
    profile_name: str | None = typer.Option(
        None,
        "--profile-name",
        help="Profile name to create or update. Defaults to the active profile or 'default'.",
    ),
    profile_url: str | None = typer.Option(
        None,
        "--profile-url",
        help="Control-plane URL bound to the profile (required when creating a new profile).",
    ),
    tenant: str | None = typer.Option(
        None,
        "--tenant-id",
        help="Tenant ID bound to the profile.",
    ),
) -> None:
    if ctx.invoked_subcommand is not None:
        return

    cli_obj = ctx.obj
    active = getattr(cli_obj, "profile", None)
    store: ProfileStore = getattr(cli_obj, "profile_store", None) or ProfileStore()

    name = profile_name or (active.name if active else "default")
    url = profile_url or (active.url if active else "")
    if not url:
        typer.echo(
            "Error: --profile-url is required when no profile exists yet.",
            err=True,
        )
        raise typer.Exit(code=2)

    flow_client = DeviceFlowClient(auth_url, client_id=client_id)
    try:
        asyncio.run(
            do_login(
                flow_client=flow_client,
                profile_store=store,
                profile_name=name,
                profile_url=url,
                tenant_id=tenant,
                scope=scope,
                audience=audience,
            )
        )
    except DeviceFlowError as exc:
        typer.echo(f"Login failed: {exc}", err=True)
        raise typer.Exit(code=1) from None
    finally:
        asyncio.run(flow_client.aclose())


__all__ = ["do_login", "login_app"]
