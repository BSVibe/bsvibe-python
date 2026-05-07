"""BSVibe shared CLI foundation — public API.

Stable imports for product CLIs:

.. code-block:: python

    from bsvibe_cli_base import (
        Profile,
        CliConfig,
        ProfileStore,
        ProfileNotFoundError,
        ProfileExistsError,
    )
"""

from __future__ import annotations

from bsvibe_cli_base.cli import CliContext, cli_app
from bsvibe_cli_base.config import CliConfig, Profile
from bsvibe_cli_base.output import FORMATS, OutputFormatter
from bsvibe_cli_base.profile import (
    ProfileExistsError,
    ProfileNotFoundError,
    ProfileStore,
)

__version__ = "0.1.0"

__all__ = [
    "Profile",
    "CliConfig",
    "ProfileStore",
    "ProfileNotFoundError",
    "ProfileExistsError",
    "OutputFormatter",
    "FORMATS",
    "CliContext",
    "cli_app",
    "__version__",
]
