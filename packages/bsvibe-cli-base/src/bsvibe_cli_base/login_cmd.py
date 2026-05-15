"""``<product> login`` — OAuth 2.0 authorization-code + PKCE on loopback.

The Typer app exported here is auto-attached to every product CLI by
:func:`bsvibe_cli_base.cli.cli_app`. It binds an ephemeral
``127.0.0.1`` port, opens the user's browser at the auth server's
``/oauth/authorize`` endpoint, captures the redirect on the loopback
listener, exchanges the code at ``/oauth/token``, and persists the
resulting access + refresh tokens to the OS keyring + the profile
store — no dashboard round-trip needed.

The async :func:`do_login` helper is split out from the Typer wrapper
so unit tests can drive the flow with a mocked
:class:`LoopbackFlowClient` and a synthetic browser callback without
spawning a subprocess or patching ``asyncio.run``.
"""

from __future__ import annotations

import asyncio
import webbrowser
from collections.abc import Callable
from typing import Any

import structlog
import typer

from bsvibe_cli_base import keyring as keyring_mod
from bsvibe_cli_base.config import Profile
from bsvibe_cli_base.loopback_flow import LoopbackFlowClient, LoopbackFlowError
from bsvibe_cli_base.profile import ProfileNotFoundError, ProfileStore

logger = structlog.get_logger(__name__)


async def do_login(
    *,
    flow_client: LoopbackFlowClient,
    profile_store: ProfileStore,
    profile_name: str,
    profile_url: str,
    tenant_id: str | None,
    scope: str | None = None,
    audience: str | None = None,
    open_browser: Callable[[str], Any] | bool = True,
    callback_timeout_s: float = 180.0,
    print_fn: Callable[[str], None] = typer.echo,
) -> None:
    """Run the full loopback handshake and persist the resulting grant.

    ``open_browser``: pass ``True`` for the default
    :func:`webbrowser.open` behaviour, ``False`` for headless mode (the
    authorize URL is printed via ``print_fn`` so a CI driver can curl
    it), or a custom callable for tests.

    The function never catches its own exceptions —
    :class:`LoopbackFlowError` (and its subclasses for timeout / state
    mismatch) propagate up to the Typer wrapper, which converts them
    into a non-zero CLI exit.
    """
    if open_browser is True:
        opener: Callable[[str], Any] = webbrowser.open
    elif open_browser is False:
        # Headless / CI mode — just print so the operator (or driver) can paste.
        opener = lambda _url: None  # noqa: E731 - inline no-op
    else:
        opener = open_browser  # type: ignore[assignment]

    def _announce(url: str) -> None:
        if open_browser is False:
            print_fn("Open the following URL in your browser to authorize:")
            print_fn(f"  {url}")
        else:
            print_fn("Opening your browser to authorize this device ...")
            print_fn(f"  {url}")
        print_fn("Waiting for the redirect on the local loopback listener ...")

    grant = await flow_client.run_login_flow(
        scope=scope,
        audience=audience,
        open_browser=opener,
        announce=_announce,
        callback_timeout_s=callback_timeout_s,
    )

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
        raise LoopbackFlowError(
            "Login completed at the auth server but the local keyring "
            "refused to store the token — see instructions above."
        )

    if grant.refresh_token and not refresh_saved:
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
    help="Authenticate via OAuth 2.0 authorization_code + PKCE on a loopback redirect.",
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
        help="OAuth client id (must be seeded with loopback redirect_uris).",
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
    no_browser: bool = typer.Option(
        False,
        "--no-browser",
        help=(
            "Skip launching the browser. Prints the authorize URL so a headless "
            "driver (CI, devcontainer, ssh session) can drive the approval."
        ),
    ),
    callback_timeout: float = typer.Option(
        180.0,
        "--callback-timeout",
        help="Seconds to wait for the browser redirect before aborting.",
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

    flow_client = LoopbackFlowClient(auth_url, client_id=client_id)

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
                open_browser=not no_browser,
                callback_timeout_s=callback_timeout,
            )
        finally:
            await flow_client.aclose()

    try:
        asyncio.run(_run())
    except LoopbackFlowError as exc:
        typer.echo(f"Login failed: {exc}", err=True)
        raise typer.Exit(code=1) from None


__all__ = ["do_login", "login_app"]
