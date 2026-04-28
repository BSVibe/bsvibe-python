"""Tests for the ``bsvibe-audit`` CLI.

The CLI is intentionally thin: every command shells out to a small
function ("driver") that does the actual work. Tests target both the
Click wiring (option parsing, exit codes, formatting) via ``CliRunner``
and the driver functions directly so we can keep coverage high without
mocking the Click context.

Network and DB collaborators are always mocked — these tests must not
touch real endpoints or databases.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
from click.testing import CliRunner

from bsvibe_audit.cli import main


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

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=None)  # type: ignore[arg-type]


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
            main,
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
            main,
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
            main,
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


def test_query_requires_token() -> None:
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "query",
            "--audit-url",
            "https://auth.test/api/audit/query",
            "--tenant",
            "t-1",
        ],
    )
    # Click prints the missing-option message and exits 2.
    assert result.exit_code != 0
    assert "token" in result.output.lower() or "Missing option" in result.output


def test_query_propagates_http_error() -> None:
    runner = CliRunner()
    response = _DummyResponse(status_code=500, payload={"error": "boom"})
    response.raise_for_status = lambda: (_ for _ in ()).throw(  # type: ignore[assignment]
        httpx.HTTPStatusError("boom", request=None, response=None)  # type: ignore[arg-type]
    )
    with patch("bsvibe_audit.cli.httpx.Client") as MockClient:
        MockClient.return_value.__enter__.return_value.post.return_value = response
        result = runner.invoke(
            main,
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


# ---------------------------------------------------------------------------
# retry-failed
# ---------------------------------------------------------------------------


def test_retry_failed_invokes_outbox_helper(monkeypatch) -> None:
    runner = CliRunner()

    fake_helper = AsyncMock(return_value=2)
    monkeypatch.setattr("bsvibe_audit.cli._retry_dead_letter", fake_helper)

    result = runner.invoke(
        main,
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
            main,
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
        main,
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
        main,
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
        main,
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
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    for cmd in ("query", "retry-failed", "retention-export", "replay"):
        assert cmd in result.output


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
