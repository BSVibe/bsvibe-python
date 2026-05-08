"""Smoke tests for the transport module skeleton.

TASK-001 only verifies that the transport hooks exist as awaitable
callables — the full stdio + HTTP/SSE wire-up lands in TASK-007.
"""

from __future__ import annotations

import inspect

from bsvibe_mcp import transport


class TestTransportSurface:
    def test_run_stdio_is_async_callable(self) -> None:
        assert callable(transport.run_stdio)
        assert inspect.iscoroutinefunction(transport.run_stdio)

    def test_run_http_is_async_callable(self) -> None:
        assert callable(transport.run_http)
        assert inspect.iscoroutinefunction(transport.run_http)
