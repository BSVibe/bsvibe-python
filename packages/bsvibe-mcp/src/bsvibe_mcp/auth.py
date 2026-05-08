"""Auth context resolution for MCP tool calls.

MCP tools accept optional ``token`` / ``tenant`` / ``url`` per-call
overrides (see ``MCPToolRegistry.register_cli_app``'s reserved schema
keys). Before each call the server resolves an :class:`AuthContext` so
the underlying CLI invocation knows which tenant + bearer token to use.

Resolution order — first source that yields a non-empty value wins for
each field independently:

1. **Per-call** — explicit kwargs passed to :func:`resolve_auth`.
2. **Profile** — name from ``profile_name`` arg, the ``MCP_PROFILE``
   env var, or the active default profile recorded in
   ``~/.bsvibe/config.yaml``. The token comes from
   :func:`bsvibe_cli_base.keyring.resolve_token` (which itself walks
   keyring → ``BSVIBE_TOKEN`` env → raw ``token_ref``).
3. **Bootstrap** — ``BSV_BOOTSTRAP_TOKEN`` env, the admin escape hatch
   used during platform bootstrap before any profile exists.

Never logs token values. Profile *names* and source labels are safe to
log; the secret material is left out entirely. Callers that want to
correlate must use :func:`token_fingerprint`, which only exposes the
length of the secret.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import structlog
from bsvibe_cli_base.keyring import resolve_token
from bsvibe_cli_base.profile import ProfileStore

logger = structlog.get_logger(__name__)

ENV_PROFILE = "MCP_PROFILE"
ENV_BOOTSTRAP = "BSV_BOOTSTRAP_TOKEN"


@dataclass(frozen=True, slots=True)
class AuthContext:
    """Resolved credentials + connection metadata for a single MCP call.

    ``source`` records *where the token came from* — useful for log
    correlation without leaking the secret. Possible values:

    * ``"per_call"`` — caller supplied ``token`` directly.
    * ``"profile:<name>"`` — read from the named profile.
    * ``"bootstrap"`` — ``BSV_BOOTSTRAP_TOKEN`` admin escape.
    * ``"none"`` — nothing configured; downstream handlers decide
      whether anonymous access is allowed.
    """

    token: str | None
    tenant: str | None
    url: str | None
    source: str


def token_fingerprint(token: str | None) -> str:
    """Return a length-only fingerprint, never the raw value."""
    if not token:
        return "none"
    return f"len={len(token)}"


def resolve_auth(
    *,
    token: str | None = None,
    tenant: str | None = None,
    url: str | None = None,
    profile_name: str | None = None,
    store: ProfileStore | None = None,
) -> AuthContext:
    """Resolve the auth context for a single MCP tool call.

    Parameters
    ----------
    token, tenant, url:
        Per-call overrides. Whichever is non-``None`` short-circuits
        the corresponding profile / env lookup.
    profile_name:
        Optional profile selector. When ``None``, falls back to
        ``$MCP_PROFILE``, then the store's active default.
    store:
        :class:`ProfileStore` instance. Default constructs one pointing
        at ``~/.bsvibe/config.yaml`` (or ``$XDG_CONFIG_HOME/bsvibe/``).

    Returns
    -------
    AuthContext
        Frozen dataclass with the resolved fields and a ``source``
        label.

    Raises
    ------
    bsvibe_cli_base.profile.ProfileNotFoundError
        When ``profile_name`` (or ``$MCP_PROFILE``) names a profile
        that doesn't exist. The active-default lookup is non-fatal —
        if there's no default we silently move on to the bootstrap
        fallback.
    """
    if store is None:
        store = ProfileStore()

    resolved_token = token
    resolved_tenant = tenant
    resolved_url = url
    profile_label: str | None = None

    needs_profile = resolved_token is None or resolved_tenant is None or resolved_url is None
    if needs_profile:
        profile = _load_profile(store, profile_name)
        if profile is not None:
            profile_label = profile.name
            if resolved_token is None:
                resolved_token = resolve_token(profile)
            if resolved_tenant is None:
                resolved_tenant = profile.tenant_id
            if resolved_url is None:
                resolved_url = profile.url

    bootstrap_used = False
    if resolved_token is None:
        boot = os.environ.get(ENV_BOOTSTRAP)
        if boot:
            resolved_token = boot
            bootstrap_used = True

    source = _source_label(
        per_call_token=token is not None,
        profile_label=profile_label,
        bootstrap_used=bootstrap_used,
        token_resolved=resolved_token is not None,
    )

    logger.debug(
        "mcp_auth_resolved",
        source=source,
        profile=profile_label,
        tenant=resolved_tenant,
        url=resolved_url,
        token_fingerprint=token_fingerprint(resolved_token),
    )

    return AuthContext(
        token=resolved_token,
        tenant=resolved_tenant,
        url=resolved_url,
        source=source,
    )


def _load_profile(store: ProfileStore, profile_name: str | None):  # type: ignore[no-untyped-def]
    """Return the profile selected by name → env → active-default.

    Explicit names raise :class:`ProfileNotFoundError` on miss. The
    active-default lookup is best-effort — missing default returns
    ``None`` so the bootstrap fallback can still kick in.
    """
    name = profile_name or os.environ.get(ENV_PROFILE)
    if name:
        return store.get_profile(name)
    return store.get_active()


def _source_label(
    *,
    per_call_token: bool,
    profile_label: str | None,
    bootstrap_used: bool,
    token_resolved: bool,
) -> str:
    if per_call_token:
        return "per_call"
    if bootstrap_used:
        return "bootstrap"
    if profile_label is not None and token_resolved:
        return f"profile:{profile_label}"
    if profile_label is not None:
        # Profile loaded for tenant/url but had no token — still tagged
        # against the profile so logs aren't misleading.
        return f"profile:{profile_label}"
    return "none"


__all__ = [
    "AuthContext",
    "resolve_auth",
    "token_fingerprint",
    "ENV_PROFILE",
    "ENV_BOOTSTRAP",
]
