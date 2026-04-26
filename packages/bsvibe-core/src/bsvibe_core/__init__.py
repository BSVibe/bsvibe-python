"""BSVibe shared core library — public API.

Stable imports for product code:

.. code-block:: python

    from bsvibe_core import (
        BsvibeSettings,
        configure_logging,
        BsvibeError,
        ConfigurationError,
        ValidationError,
        NotFoundError,
        csv_list_field,
        parse_csv_list,
    )
    from bsvibe_core.types import TenantId, UserId, RequestId, JsonDict, JsonValue
"""

from __future__ import annotations

from bsvibe_core.exceptions import (
    BsvibeError,
    ConfigurationError,
    NotFoundError,
    ValidationError,
)
from bsvibe_core.logging import configure_logging
from bsvibe_core.settings import (
    BsvibeSettings,
    csv_list_field,
    parse_csv_list,
)

__version__ = "0.1.0"

__all__ = [
    "BsvibeSettings",
    "configure_logging",
    "BsvibeError",
    "ConfigurationError",
    "ValidationError",
    "NotFoundError",
    "csv_list_field",
    "parse_csv_list",
    "__version__",
]
