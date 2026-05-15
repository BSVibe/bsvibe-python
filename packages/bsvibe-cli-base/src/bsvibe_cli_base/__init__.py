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
from bsvibe_cli_base.http import CliHttpAuthError, CliHttpClient
from bsvibe_cli_base.login_cmd import do_login, login_app
from bsvibe_cli_base.loopback_flow import (
    LoopbackFlowClient,
    LoopbackFlowError,
    LoopbackFlowStateMismatchError,
    LoopbackFlowTimeoutError,
    TokenGrant,
)
from bsvibe_cli_base.output import FORMATS, OutputFormatter
from bsvibe_cli_base.profile import (
    ProfileExistsError,
    ProfileNotFoundError,
    ProfileStore,
)
from bsvibe_cli_base.profile_cmd import profile_app

__version__ = "0.2.0"

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
    "CliHttpClient",
    "CliHttpAuthError",
    "TokenGrant",
    "LoopbackFlowClient",
    "LoopbackFlowError",
    "LoopbackFlowTimeoutError",
    "LoopbackFlowStateMismatchError",
    "do_login",
    "login_app",
    "profile_app",
    "__version__",
]
