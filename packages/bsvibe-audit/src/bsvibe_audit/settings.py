"""Audit publisher settings — extends :class:`bsvibe_core.BsvibeSettings`.

Wire-compatible env contract used by every product:

* ``BSVIBE_AUTH_AUDIT_URL`` — full URL to ``POST /api/audit/events``.
  Empty means the relay stays disabled (dev-friendly default).
* ``BSVIBE_AUTH_AUDIT_SERVICE_TOKEN`` — service JWT used as
  ``X-Service-Token``. Empty in dev.
* ``AUDIT_OUTBOX_TABLE_NAME`` — table to read from (default
  ``audit_outbox``).
* ``AUDIT_BATCH_SIZE`` — relay batch size (default ``50``).
* ``AUDIT_RELAY_INTERVAL_S`` — polling interval (default ``5.0`` seconds).
* ``AUDIT_MAX_RETRIES`` — dead-letter threshold (default ``5``).
* ``AUDIT_RELAY_ENABLED`` — explicit override; ``true`` only meaningful
  when ``BSVIBE_AUTH_AUDIT_URL`` is set.
* ``AUDIT_SERVICE_NAME`` — optional label echoed into emitted events
  (defaults to empty string).
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import SettingsConfigDict

from bsvibe_core import BsvibeSettings


class AuditSettings(BsvibeSettings):
    """Settings every product passes to :class:`AuditClient.from_settings`."""

    model_config = SettingsConfigDict(
        env_file=None,
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )

    auth_audit_url: str = Field(
        default="",
        validation_alias="bsvibe_auth_audit_url",
        description="POST URL on BSVibe-Auth that ingests audit events.",
    )
    auth_service_token: str = Field(
        default="",
        validation_alias="bsvibe_auth_audit_service_token",
        description="Service JWT used in the X-Service-Token header.",
    )
    outbox_table_name: str = Field(
        default="audit_outbox",
        validation_alias="audit_outbox_table_name",
        description="Database table the outbox records live in.",
    )
    batch_size: int = Field(
        default=50,
        validation_alias="audit_batch_size",
        ge=1,
        description="Maximum rows per relay batch.",
    )
    relay_interval_s: float = Field(
        default=5.0,
        validation_alias="audit_relay_interval_s",
        ge=0.05,
        description="Seconds between relay poll iterations.",
    )
    max_retries: int = Field(
        default=5,
        validation_alias="audit_max_retries",
        ge=1,
        description="Failed deliveries beyond this count are dead-lettered.",
    )
    relay_enabled_override: bool | None = Field(
        default=None,
        validation_alias="audit_relay_enabled",
        description="Optional explicit on/off override for the relay.",
    )
    service_name: str = Field(
        default="",
        validation_alias="audit_service_name",
        description="Optional service label echoed into event metadata.",
    )

    @property
    def relay_enabled(self) -> bool:
        """Whether the OutboxRelay should actually run.

        Disabled when no audit URL is configured (dev-friendly), enabled
        otherwise unless ``AUDIT_RELAY_ENABLED=false`` overrides.
        """
        if self.relay_enabled_override is not None:
            return self.relay_enabled_override
        return bool(self.auth_audit_url)


__all__ = ["AuditSettings"]
