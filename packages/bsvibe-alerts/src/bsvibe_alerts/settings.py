"""Alert publisher settings — extends :class:`bsvibe_core.BsvibeSettings`.

Wire-compatible env contract used by the four products:

* ``TELEGRAM_BOT_TOKEN`` / ``TELEGRAM_CHAT_ID`` — empty disables channel.
* ``SLACK_WEBHOOK_URL`` — empty disables channel.
* ``ALERT_INFO_CHANNELS`` / ``ALERT_WARNING_CHANNELS`` /
  ``ALERT_CRITICAL_CHANNELS`` — comma-separated, follow BSupervisor §M18
  ``Annotated[list[str], NoDecode]`` so deployers can drop
  ``ALERT_CRITICAL_CHANNELS=structlog,slack,telegram`` without
  JSON-encoding it.
* ``ALERT_SERVICE_NAME`` — optional label injected into every alert.

The structlog channel is always-on regardless of credentials, so a
production routing table that lists only ``["structlog"]`` for ``info``
is safe.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import NoDecode, SettingsConfigDict

from bsvibe_core import BsvibeSettings, csv_list_field, parse_csv_list

_DEFAULT_INFO: list[str] = ["structlog"]
_DEFAULT_WARNING: list[str] = ["structlog", "slack"]
_DEFAULT_CRITICAL: list[str] = ["structlog", "slack", "telegram"]


class AlertSettings(BsvibeSettings):
    """Settings every product passes to :class:`AlertClient.from_settings`.

    Extends :class:`bsvibe_core.BsvibeSettings` so ``extra="ignore"`` is
    inherited — products can add private settings on top without
    crashing startup. ``populate_by_name=True`` lets callers construct
    instances with field names directly (``AlertSettings(info_channels=[...])``)
    in addition to env aliases.
    """

    model_config = SettingsConfigDict(
        env_file=None,
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    slack_webhook_url: str = ""
    service_name: str | None = Field(
        default=None,
        validation_alias="alert_service_name",
        description="Optional service identifier injected into every alert.",
    )

    info_channels: Annotated[list[str], NoDecode] = csv_list_field(
        default=_DEFAULT_INFO,
        alias="alert_info_channels",
        description="Comma-separated channel names for INFO alerts.",
    )
    warning_channels: Annotated[list[str], NoDecode] = csv_list_field(
        default=_DEFAULT_WARNING,
        alias="alert_warning_channels",
        description="Comma-separated channel names for WARNING alerts.",
    )
    critical_channels: Annotated[list[str], NoDecode] = csv_list_field(
        default=_DEFAULT_CRITICAL,
        alias="alert_critical_channels",
        description="Comma-separated channel names for CRITICAL alerts.",
    )

    @field_validator("info_channels", mode="before")
    @classmethod
    def _parse_info(cls, value: str | list[str] | None) -> list[str]:
        return parse_csv_list(value) or list(_DEFAULT_INFO)

    @field_validator("warning_channels", mode="before")
    @classmethod
    def _parse_warning(cls, value: str | list[str] | None) -> list[str]:
        return parse_csv_list(value) or list(_DEFAULT_WARNING)

    @field_validator("critical_channels", mode="before")
    @classmethod
    def _parse_critical(cls, value: str | list[str] | None) -> list[str]:
        return parse_csv_list(value) or list(_DEFAULT_CRITICAL)

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_bot_token) and bool(self.telegram_chat_id)

    @property
    def slack_enabled(self) -> bool:
        return bool(self.slack_webhook_url)


__all__ = ["AlertSettings"]
