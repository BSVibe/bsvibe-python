"""bsvibe-demo — shared helpers for BSVibe public interactive demo stack.

Stable public surface:

.. code-block:: python

    from bsvibe_demo import (
        # JWT
        mint_demo_jwt,
        decode_demo_jwt,
        DemoClaims,
        DemoJWTError,
        # Guard
        is_demo_mode,
        enforce_demo_llm_mock,
        DemoLLMBlockedError,
        DEMO_MOCK_RESPONSE,
        # GC
        demo_gc,
        find_expired_tenants,
    )

Each product wires its own ``/api/v1/demo/session`` router and
per-tenant seed data; this package contains only the parts that are
identical across products (JWT, LLM guard, GC).
"""

from __future__ import annotations

from bsvibe_demo.gc import demo_gc, find_expired_tenants
from bsvibe_demo.session import (
    DemoSessionResult,
    DemoSessionService,
    SeedFn,
)
from bsvibe_demo.guard import (
    DEMO_MOCK_RESPONSE,
    DemoLLMBlockedError,
    enforce_demo_llm_mock,
    is_demo_mode,
)
from bsvibe_demo.jwt import (
    DemoClaims,
    DemoJWTError,
    decode_demo_jwt,
    mint_demo_jwt,
)

# SQLAlchemy variants are optional — products that don't use SQLA can
# skip the install extra and ignore these names.
try:
    from bsvibe_demo.sqlalchemy import (
        DemoSessionServiceSqlAlchemy,
        SqlAlchemySeedFn,
        demo_gc_sqlalchemy,
        find_expired_tenants_sqlalchemy,
    )

    _HAS_SQLALCHEMY = True
except ImportError:  # SQLAlchemy not installed — product uses asyncpg
    _HAS_SQLALCHEMY = False

__version__ = "0.1.0"

__all__ = [
    "DEMO_MOCK_RESPONSE",
    "DemoClaims",
    "DemoJWTError",
    "DemoLLMBlockedError",
    "DemoSessionResult",
    "DemoSessionService",
    "SeedFn",
    "decode_demo_jwt",
    "demo_gc",
    "enforce_demo_llm_mock",
    "find_expired_tenants",
    "is_demo_mode",
    "mint_demo_jwt",
    "__version__",
]

if _HAS_SQLALCHEMY:
    __all__ += [
        "DemoSessionServiceSqlAlchemy",
        "SqlAlchemySeedFn",
        "demo_gc_sqlalchemy",
        "find_expired_tenants_sqlalchemy",
    ]
