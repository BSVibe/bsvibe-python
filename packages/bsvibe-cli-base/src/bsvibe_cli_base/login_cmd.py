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

    # Persist tokens to the system keyring BEFORE updating the profile —
    # if the keyring backend refuses (Phase 8 dogfood 2026-05-11 hit
    # macOS errSecInteractionNotAllowed -25308 from a non-GUI CLI
    # context), the previous code printed 'Saved PAT to keyring'
    # anyway and the next CLI invocation 401'd with no clue why.
    access_saved = keyring_mod.set_token(profile_name, grant.access_token)
    refresh_saved = False
    if grant.refresh_token:
        refresh_saved = keyring_mod.set_refresh_token(profile_name, grant.refresh_token)

    if not access_saved:
        # Surface the failure with an actionable next step + the raw token
        # so the operator isn't locked out. The token is shown ONCE on
        # stdout — secure-paste-then-clear is the explicit contract.
        print_fn("")
        print_fn(
            "⚠️  Could not save the access token to the system keyring "
            "(backend unavailable or refused — see `python -m keyring "
            "--list-backends`)."
        )
        print_fn("")
        print_fn("    Try one of:")
        print_fn("      • macOS: run `security unlock-keychain` then retry")
        print_fn("      • Force a file backend (less secure, plaintext):")
        print_fn("          export PYTHON_KEYRING_BACKEND=keyrings.alt.file.PlaintextKeyring")
        print_fn("          (install: `pip install keyrings.alt`)")
        print_fn("      • Pass the token explicitly each call:")
        print_fn("          export BSVIBE_TOKEN='<paste below>'")
        print_fn("")
        print_fn("    Raw access token (NOT saved — copy now):")
        print_fn(f"      {grant.access_token}")
        if grant.refresh_token:
            print_fn("    Raw refresh token (NOT saved):")
            print_fn(f"      {grant.refresh_token}")
        print_fn("")
        raise DeviceFlowError(
            "Login completed at the auth server but the local keyring "
            "refused to store the token — see instructions above."
        )

    if grant.refresh_token and not refresh_saved:
        # Access saved but refresh dropped — annoying (no auto-rotation)
        # but not fatal. Continue with a clear warning instead of
        # silently shipping a profile that promises refresh but lacks it.
        print_fn(
            "⚠️  Access token saved, but refresh token write failed. "
            "Auto-rotation will not work; re-run `login` when the access "
            "token expires."
        )

    new_profile = Profile(
        name=profile_name,
        url=profile_url,
        tenant_id=tenant_id,
        default=True,
        token_ref=profile_name,
        refresh_token_ref=profile_name if refresh_saved else None,
    )
    try:
        existing = profile_store.get_profile(profile_name)
        # Preserve operator-set fields (url, tenant_id) that login wasn't
        # asked to overwrite — only refresh token refs change here.
        merged = Profile(
            name=existing.name,
            url=existing.url if not profile_url or profile_url == existing.url else profile_url,
            tenant_id=existing.tenant_id if tenant_id is None else tenant_id,
            default=True,
            token_ref=profile_name,
            refresh_token_ref=profile_name if refresh_saved else None,
        )
        profile_store.update_profile(merged)
    except ProfileNotFoundError:
        profile_store.add_profile(new_profile)
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

    # do_login + aclose MUST share one asyncio loop. Running aclose from an
    # outer ``finally:`` via a second ``asyncio.run`` leaves httpx's
    # connection pool bound to the now-closed first loop and crashes with
    # ``RuntimeError: Event loop is closed`` after every login failure
    # (Phase 8 dogfood 2026-05-11).
    async def _run() -> None:
        try:
            await do_login(
                flow_client=flow_client,
                profile_store=store,
                profile_name=name,
                profile_url=url,
                tenant_id=tenant,
                scope=scope,
                audience=audience,
            )
        finally:
            await flow_client.aclose()

    try:
        asyncio.run(_run())
    except DeviceFlowError as exc:
        typer.echo(f"Login failed: {exc}", err=True)
        raise typer.Exit(code=1) from None


__all__ = ["do_login", "login_app"]
