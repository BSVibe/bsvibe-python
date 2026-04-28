"""Database settings shared by every BSVibe FastAPI service.

Extracted verbatim from BSupervisor PR #13 §M20 (the "DB pool sizing
knobs" block) and made into a reusable mixin. Inherits
:class:`bsvibe_core.BsvibeSettings` so every product gets the same
``extra="ignore"`` + ``case_sensitive=False`` behaviour.

Wire format (pinned by tests):

* ``database_url``: required.
* ``db_pool_size``: int, default ``10``.
* ``db_max_overflow``: int, default ``20``.
* ``db_pool_timeout``: int, default ``30`` (seconds).
* ``db_pool_recycle``: int, default ``1800`` (seconds = 30 min).
* ``db_pool_pre_ping``: bool, default ``True`` — every BSVibe service
  defaults this on so long-lived idle connections that the DB or
  load balancer dropped get re-established silently.
* ``db_echo``: bool, default ``False`` — SQLAlchemy ``echo=`` flag.

Any change to these defaults is a breaking change for the four
products' deployments.
"""

from __future__ import annotations

from pydantic import Field

from bsvibe_core import BsvibeSettings


class DatabaseSettings(BsvibeSettings):
    """Settings mixin that wires :func:`create_engine_from_settings`.

    Products typically subclass this together with their own product
    settings:

    .. code-block:: python

        from bsvibe_sqlalchemy import DatabaseSettings

        class Settings(DatabaseSettings):
            # product-specific fields here
            ...
    """

    database_url: str
    db_pool_size: int = Field(default=10, ge=0)
    db_max_overflow: int = Field(default=20, ge=0)
    db_pool_timeout: int = Field(default=30, ge=0)
    db_pool_recycle: int = Field(default=1800, ge=0)
    db_pool_pre_ping: bool = True
    db_echo: bool = False


__all__ = ["DatabaseSettings"]
