"""Tests for the ``bsvibe-audit`` Typer CLI.

The CLI is intentionally thin: every command shells out to a small
function ("driver") that does the actual work. Tests target the Typer
wiring (option parsing, exit codes, formatting) via
:class:`typer.testing.CliRunner` and the driver functions directly so
we can keep coverage high without touching the Typer context.

Network and DB collaborators are always mocked — these tests must not
touch real endpoints or databases.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

from typer.testing import CliRunner

from bsvibe_audit.cli import app, main


# ---------------------------------------------------------------------------
# query
# ---------------------------------------------------------------------------


class _DummyResponse:
    def __init__(self, *, status_code: int = 200, payload: dict[str, Any] | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {"events": []}
        self.text = json.dumps(self._payload)

    def json(self) -> dict[str, Any]:
        return self._payload


def test_query_outputs_json_table_default() -> None:
    runner = CliRunner()

    sample_events = [
        {
            "event_id": "e-1",
            "event_type": "auth.user.created",
            "occurred_at": "2026-04-25T12:00:00+00:00",
            "actor": {"type": "user", "id": "u-1"},
            "tenant_id": "t-1",
            "data": {"email": "a@b.test"},
        },
        {
            "event_id": "e-2",
            "event_type": "auth.session.failed",
            "occurred_at": "2026-04-25T12:05:00+00:00",
            "actor": {"type": "user", "id": "u-2"},
            "tenant_id": "t-1",
            "data": {},
        },
    ]
    response = _DummyResponse(payload={"events": sample_events, "next_cursor": None})

    with patch("bsvibe_audit.cli.httpx.Client") as MockClient:
        MockClient.return_value.__enter__.return_value.post.return_value = response
        result = runner.invoke(
            app,
            [
                "query",
                "--audit-url",
                "https://auth.test/api/audit/query",
                "--token",
                "svc-token",
                "--tenant",
                "t-1",
                "--event-type",
                "auth.*",
                "--limit",
                "10",
                "--format",
                "json",
            ],
        )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert len(payload) == 2
    assert payload[0]["event_id"] == "e-1"


def test_query_emits_csv_output() -> None:
    runner = CliRunner()
    response = _DummyResponse(
        payload={
            "events": [
                {
                    "event_id": "e-1",
                    "event_type": "auth.user.created",
                    "occurred_at": "2026-04-25T12:00:00+00:00",
                    "actor": {"type": "user", "id": "u-1"},
                    "tenant_id": "t-1",
                    "data": {},
                }
            ]
        }
    )
    with patch("bsvibe_audit.cli.httpx.Client") as MockClient:
        MockClient.return_value.__enter__.return_value.post.return_value = response
        result = runner.invoke(
            app,
            [
                "query",
                "--audit-url",
                "https://auth.test/api/audit/query",
                "--token",
                "svc",
                "--tenant",
                "t-1",
                "--format",
                "csv",
            ],
        )
    assert result.exit_code == 0, result.output
    lines = result.output.strip().splitlines()
    assert lines[0].startswith("event_id,event_type")
    assert "e-1" in lines[1]


def test_query_table_format_renders_header() -> None:
    runner = CliRunner()
    response = _DummyResponse(payload={"events": []})
    with patch("bsvibe_audit.cli.httpx.Client") as MockClient:
        MockClient.return_value.__enter__.return_value.post.return_value = response
        result = runner.invoke(
            app,
            [
                "query",
                "--audit-url",
                "https://auth.test/api/audit/query",
                "--token",
                "svc",
                "--tenant",
                "t-1",
                "--format",
                "table",
            ],
        )
    assert result.exit_code == 0
    assert "event_id" in result.output
    assert "event_type" in result.output


def test_query_format_is_case_insensitive() -> None:
    """Backwards-compat: the click CLI used Choice(case_sensitive=False)."""

    runner = CliRunner()
    response = _DummyResponse(payload={"events": []})
    with patch("bsvibe_audit.cli.httpx.Client") as MockClient:
        MockClient.return_value.__enter__.return_value.post.return_value = response
        result = runner.invoke(
            app,
            [
                "query",
                "--audit-url",
                "https://auth.test/api/audit/query",
                "--token",
                "svc",
                "--format",
                "JSON",
            ],
        )
    assert result.exit_code == 0, result.output


def test_query_rejects_unknown_format() -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "query",
            "--audit-url",
            "https://auth.test/api/audit/query",
            "--token",
            "svc",
            "--format",
            "xml",
        ],
    )
    assert result.exit_code != 0


def test_query_requires_token() -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "query",
            "--audit-url",
            "https://auth.test/api/audit/query",
            "--tenant",
            "t-1",
        ],
    )
    # Typer prints the missing-option message and exits non-zero.
    assert result.exit_code != 0
    assert "token" in result.output.lower() or "missing option" in result.output.lower()


def test_query_propagates_http_error() -> None:
    runner = CliRunner()
    response = _DummyResponse(status_code=500, payload={"error": "boom"})
    with patch("bsvibe_audit.cli.httpx.Client") as MockClient:
        MockClient.return_value.__enter__.return_value.post.return_value = response
        result = runner.invoke(
            app,
            [
                "query",
                "--audit-url",
                "https://auth.test/api/audit/query",
                "--token",
                "svc",
                "--tenant",
                "t-1",
            ],
        )
    assert result.exit_code == 1


def test_query_token_envvar_fallback(monkeypatch) -> None:
    """Backwards-compat: --token reads from BSVIBE_AUDIT_TOKEN / BSVIBE_AUTH_AUDIT_SERVICE_TOKEN."""

    runner = CliRunner()
    response = _DummyResponse(payload={"events": []})
    monkeypatch.setenv("BSVIBE_AUDIT_TOKEN", "from-env")
    with patch("bsvibe_audit.cli.httpx.Client") as MockClient:
        MockClient.return_value.__enter__.return_value.post.return_value = response
        result = runner.invoke(
            app,
            [
                "query",
                "--audit-url",
                "https://auth.test/api/audit/query",
                "--format",
                "json",
            ],
        )
    assert result.exit_code == 0, result.output


def test_query_propagates_non_json_body() -> None:
    runner = CliRunner()

    class _BadJson(_DummyResponse):
        def json(self) -> dict[str, Any]:
            raise ValueError("not json")

    response = _BadJson(payload={})
    with patch("bsvibe_audit.cli.httpx.Client") as MockClient:
        MockClient.return_value.__enter__.return_value.post.return_value = response
        result = runner.invoke(
            app,
            [
                "query",
                "--audit-url",
                "https://auth.test/api/audit/query",
                "--token",
                "svc",
            ],
        )
    assert result.exit_code == 1
    assert "non-json" in result.output.lower()


# ---------------------------------------------------------------------------
# retry-failed
# ---------------------------------------------------------------------------


def test_retry_failed_invokes_outbox_helper(monkeypatch) -> None:
    runner = CliRunner()

    fake_helper = AsyncMock(return_value=2)
    monkeypatch.setattr("bsvibe_audit.cli._retry_dead_letter", fake_helper)

    # AuditClient.aclose is awaited in the cli wrapper; AuditClient.__init__
    # would otherwise instantiate a real httpx client, which is fine, but we
    # replace the whole class to avoid SQL engine creation as well.
    fake_engine_dispose = AsyncMock()

    class _FakeEngine:
        dispose = fake_engine_dispose

    monkeypatch.setattr("bsvibe_audit.cli.create_async_engine", lambda url: _FakeEngine())
    monkeypatch.setattr("bsvibe_audit.cli.async_sessionmaker", lambda engine, **kw: object())

    result = runner.invoke(
        app,
        [
            "retry-failed",
            "--db-url",
            "sqlite+aiosqlite:///:memory:",
            "--audit-url",
            "https://auth.test/api/audit/events",
            "--token",
            "svc",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "2" in result.output
    fake_helper.assert_awaited_once()


# ---------------------------------------------------------------------------
# retention-export
# ---------------------------------------------------------------------------


def test_retention_export_writes_file(tmp_path: Path) -> None:
    runner = CliRunner()

    sample = [
        {
            "event_id": "e-1",
            "event_type": "auth.user.created",
            "occurred_at": "2026-01-01T00:00:00+00:00",
            "actor": {"type": "user", "id": "u-1"},
            "tenant_id": "t-1",
            "data": {},
        }
    ]
    response = _DummyResponse(payload={"events": sample, "next_cursor": None})
    out_file = tmp_path / "archive.jsonl"

    with patch("bsvibe_audit.cli.httpx.Client") as MockClient:
        MockClient.return_value.__enter__.return_value.post.return_value = response
        result = runner.invoke(
            app,
            [
                "retention-export",
                "--audit-url",
                "https://auth.test/api/audit/query",
                "--token",
                "svc",
                "--tenant",
                "t-1",
                "--before",
                "2026-04-01T00:00:00Z",
                "--output",
                str(out_file),
            ],
        )

    assert result.exit_code == 0, result.output
    contents = out_file.read_text().strip().splitlines()
    assert len(contents) == 1
    assert json.loads(contents[0])["event_id"] == "e-1"


def test_retention_export_rejects_unsupported_scheme(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "retention-export",
            "--audit-url",
            "https://auth.test/api/audit/query",
            "--token",
            "svc",
            "--tenant",
            "t-1",
            "--before",
            "2026-04-01T00:00:00Z",
            "--output",
            "ftp://example.com/foo",
        ],
    )
    assert result.exit_code != 0
    assert "ftp" in result.output.lower() or "unsupported" in result.output.lower()


# ---------------------------------------------------------------------------
# replay
# ---------------------------------------------------------------------------


def test_replay_invokes_helper(monkeypatch) -> None:
    runner = CliRunner()

    fake_replay = AsyncMock(return_value=5)
    monkeypatch.setattr("bsvibe_audit.cli._replay_events", fake_replay)

    since = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    until = datetime.now(UTC).isoformat()
    result = runner.invoke(
        app,
        [
            "replay",
            "--audit-url",
            "https://auth.test/api/audit/query",
            "--token",
            "svc",
            "--since",
            since,
            "--until",
            until,
        ],
    )
    assert result.exit_code == 0, result.output
    assert "5" in result.output
    fake_replay.assert_awaited_once()


def test_replay_rejects_invalid_iso() -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "replay",
            "--audit-url",
            "https://auth.test/api/audit/query",
            "--token",
            "svc",
            "--since",
            "not-a-date",
            "--until",
            "2026-04-25T00:00:00Z",
        ],
    )
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def test_main_help_lists_all_commands() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ("query", "retry-failed", "retention-export", "replay"):
        assert cmd in result.output


def test_main_entrypoint_callable() -> None:
    """The console-script entry point ``bsvibe_audit.cli:main`` stays callable."""

    assert callable(main)


async def test_retry_dead_letter_helper_drains_dead_letter_rows() -> None:
    """The internal retry-failed driver clears dead_letter rows and re-attempts delivery."""

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from bsvibe_audit import (
        AuditEmitter,
        AuditOutboxBase,
        OutboxStore,
    )
    from bsvibe_audit.cli import _retry_dead_letter
    from bsvibe_audit.client import AuditClient
    from bsvibe_audit.events import AuditActor
    from bsvibe_audit.events.auth import UserCreated

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(AuditOutboxBase.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    emitter = AuditEmitter()
    store = OutboxStore()
    actor = AuditActor(type="user", id="u-1")
    async with factory() as session:
        await emitter.emit(UserCreated(actor=actor, tenant_id="t-1", data={"e": "x"}), session=session)
        await session.commit()
    async with factory() as session:
        rows = await store.select_undelivered(session, batch_size=10)
        await store.mark_dead_letter(session, rows[0].id, error="prior 4xx")
        await session.commit()

    fake_client = AuditClient.__new__(AuditClient)
    fake_client._owned_http = False
    fake_client._http = None  # type: ignore[assignment]
    fake_client.audit_url = "https://auth.test"
    fake_client.service_token = "svc"
    fake_client.send = AsyncMock()  # type: ignore[method-assign]
    fake_client.aclose = AsyncMock()  # type: ignore[method-assign]

    delivered = await _retry_dead_letter(factory=factory, client=fake_client)
    assert delivered == 1
    fake_client.send.assert_awaited_once()  # type: ignore[attr-defined]


async def test_replay_events_helper_paginates_results() -> None:
    """The replay helper walks pages from /api/audit/query and yields events for each batch."""

    from bsvibe_audit.cli import _replay_events

    page_one = {
        "events": [
            {
                "event_id": "e-1",
                "event_type": "auth.user.created",
                "occurred_at": "2026-04-25T00:00:00+00:00",
                "actor": {"type": "user", "id": "u-1"},
                "tenant_id": "t-1",
                "data": {},
            }
        ],
        "next_cursor": "cur-1",
    }
    page_two = {
        "events": [
            {
                "event_id": "e-2",
                "event_type": "auth.session.failed",
                "occurred_at": "2026-04-25T00:01:00+00:00",
                "actor": {"type": "user", "id": "u-2"},
                "tenant_id": "t-1",
                "data": {},
            }
        ],
        "next_cursor": None,
    }

    responses = [_DummyResponse(payload=page_one), _DummyResponse(payload=page_two)]

    seen: list[dict[str, Any]] = []

    def _on_event(event: dict[str, Any]) -> None:
        seen.append(event)

    with patch("bsvibe_audit.cli.httpx.Client") as MockClient:
        client_ctx = MockClient.return_value.__enter__.return_value
        client_ctx.post.side_effect = responses
        delivered = await _replay_events(
            audit_url="https://auth.test/api/audit/query",
            token="svc",
            since="2026-04-25T00:00:00Z",
            until="2026-04-26T00:00:00Z",
            tenant_id=None,
            event_type=None,
            on_event=_on_event,
        )
    assert delivered == 2
    assert [e["event_id"] for e in seen] == ["e-1", "e-2"]


def test_cli_module_does_not_import_click() -> None:
    """Ensure the migration removed the click dependency from cli.py."""

    import ast

    import bsvibe_audit.cli as cli_module

    tree = ast.parse(Path(cli_module.__file__).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] != "click", "click is still imported"
        elif isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] != "click", "click is still imported from"
    assert "click" not in cli_module.__dict__


def test_unused_httpx_import_attribute_present() -> None:
    """Tests patch ``bsvibe_audit.cli.httpx.Client``; ensure httpx is re-exported."""

    import bsvibe_audit.cli as cli_module

    assert hasattr(cli_module, "httpx")
