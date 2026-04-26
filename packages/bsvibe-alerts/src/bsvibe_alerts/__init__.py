"""bsvibe-alerts — multi-channel alert publishing for BSVibe products.

Stable public surface:

.. code-block:: python

    from bsvibe_alerts import (
        Alert,
        AlertSeverity,
        AlertSettings,
        AlertClient,
        AlertRouter,
        AlertChannel,
        StructlogChannel,
        TelegramChannel,
        SlackChannel,
    )

Typical wiring:

.. code-block:: python

    from bsvibe_alerts import AlertClient, AlertSettings

    settings = AlertSettings()           # picks env vars up
    alerts = AlertClient.from_settings(settings)

    await alerts.emit(
        event="rate_limit_exceeded",
        message="quota hit for tenant t-1",
        severity="warning",
        context={"tenant_id": "t-1"},
    )

Severity → channel routing comes from settings (with a sane default
table). Channels not registered (because their credentials were absent)
are silently skipped — production cannot crash when slack/telegram are
unconfigured.
"""

from __future__ import annotations

from bsvibe_alerts.channels import (
    AlertChannel,
    SlackChannel,
    StructlogChannel,
    TelegramChannel,
)
from bsvibe_alerts.client import AlertClient
from bsvibe_alerts.routing import AlertRouter
from bsvibe_alerts.settings import AlertSettings
from bsvibe_alerts.types import Alert, AlertSeverity

__version__ = "0.1.0"

__all__ = [
    "Alert",
    "AlertSeverity",
    "AlertSettings",
    "AlertClient",
    "AlertRouter",
    "AlertChannel",
    "StructlogChannel",
    "TelegramChannel",
    "SlackChannel",
    "__version__",
]
