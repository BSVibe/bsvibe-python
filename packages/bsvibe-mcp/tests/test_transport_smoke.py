"""Transport smoke tests — stdio + HTTP/SSE wire-up.

The MCP SDK provides ``stdio_server()`` (async context manager yielding
read/write streams) and ``SseServerTransport`` (Starlette-mountable
ASGI app). We verify the bsvibe_mcp transport module wires those
correctly without actually running a network listener.
"""

from __future__ import annotations

import inspect
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.applications import Starlette

from bsvibe_mcp import transport


class TestTransportSurface:
    def test_run_stdio_is_async_callable(self) -> None:
        assert callable(transport.run_stdio)
        assert inspect.iscoroutinefunction(transport.run_stdio)

    def test_run_http_is_async_callable(self) -> None:
        assert callable(transport.run_http)
        assert inspect.iscoroutinefunction(transport.run_http)


class TestRunStdio:
    @pytest.mark.asyncio
    async def test_run_stdio_invokes_server_run_with_stdio_streams(self, monkeypatch) -> None:
        read_stream = MagicMock(name="read")
        write_stream = MagicMock(name="write")

        @asynccontextmanager
        async def fake_stdio_server():
            yield (read_stream, write_stream)

        monkeypatch.setattr(transport, "stdio_server", fake_stdio_server)

        server = MagicMock()
        server.run = AsyncMock()
        init_opts = MagicMock(name="init_options")
        server.create_initialization_options = MagicMock(return_value=init_opts)

        await transport.run_stdio(server)

        server.create_initialization_options.assert_called_once()
        server.run.assert_awaited_once_with(read_stream, write_stream, init_opts)


class TestBuildHttpApp:
    def test_returns_starlette_app_with_sse_and_messages_routes(self) -> None:
        server = MagicMock()
        server.create_initialization_options = MagicMock(return_value=MagicMock())

        app = transport.build_http_app(server)
        assert isinstance(app, Starlette)

        paths = {getattr(r, "path", None) for r in app.routes}
        assert "/sse" in paths
        # Mount paths surface as path attribute as well
        assert any(p == "/messages" for p in paths) or any(p == "/messages/" for p in paths)


class TestRunHttp:
    @pytest.mark.asyncio
    async def test_run_http_starts_uvicorn_with_host_and_port(self, monkeypatch) -> None:
        captured = {}

        class FakeConfig:
            def __init__(self, app, host, port, log_level):  # noqa: ANN001
                captured["app"] = app
                captured["host"] = host
                captured["port"] = port
                captured["log_level"] = log_level

        class FakeServer:
            def __init__(self, config):  # noqa: ANN001
                self.config = config

            async def serve(self) -> None:
                captured["served"] = True

        fake_uvicorn = MagicMock()
        fake_uvicorn.Config = FakeConfig
        fake_uvicorn.Server = FakeServer
        monkeypatch.setattr(transport, "uvicorn", fake_uvicorn)

        server = MagicMock()
        server.create_initialization_options = MagicMock(return_value=MagicMock())

        await transport.run_http(server, host="0.0.0.0", port=9999)

        assert captured["served"] is True
        assert captured["host"] == "0.0.0.0"
        assert captured["port"] == 9999
        assert isinstance(captured["app"], Starlette)
