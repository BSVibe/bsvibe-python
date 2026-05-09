"""Pydantic schemas for the on-disk CLI config (``~/.bsvibe/config.yaml``).

A *profile* binds a name to a control-plane URL and an optional tenant /
token reference. ``token_ref`` is an opaque handle resolved later by the
keyring layer (``bsvibe_cli_base.keyring``) — never the raw secret.

Forbid extra fields so a typo in the YAML is rejected at load instead of
silently dropped.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Profile(BaseModel):
    """A single connection profile (one entry in ``profiles:``)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, description="Human-readable profile name (unique).")
    url: str = Field(..., min_length=1, description="Control-plane base URL.")
    tenant_id: str | None = Field(default=None, description="Optional default tenant.")
    default: bool = Field(default=False, description="True if this is the active profile.")
    token_ref: str | None = Field(
        default=None,
        description="Opaque handle resolved via keyring or env at request time.",
    )
    refresh_token_ref: str | None = Field(
        default=None,
        description="Opaque handle for the refresh token, resolved via keyring.",
    )


class CliConfig(BaseModel):
    """Top-level shape persisted to YAML."""

    model_config = ConfigDict(extra="forbid")

    profiles: list[Profile] = Field(default_factory=list)


__all__ = ["Profile", "CliConfig"]
