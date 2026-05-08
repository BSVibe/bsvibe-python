"""Operator CLI for the BSVibe audit pipeline.

The CLI wraps four operator workflows that come up in production:

1. ``query`` — run an ad-hoc filter against ``POST /api/audit/query`` on
   BSVibe-Auth and render the result as JSON / CSV / a quick text table.
2. ``retry-failed`` — drain the local ``audit_outbox`` of dead-letter
   rows by clearing the dead-letter flag and asking the relay's HTTP
   client to deliver them once. Used after a known transient outage
   (Auth was down for hours, dead-lettered rows pile up).
3. ``retention-export`` — page over the query API for events older than
   ``--before`` and write them as JSON Lines to a file (or — when the
   target is ``s3://...`` — boto3 upload). Hot-tier retention runs at
   90 days; this command archives older events.
4. ``replay`` — walk a time range from ``/api/audit/query`` and feed
   each event through a callback. Default callback is JSON-lines stdout
   so the operator can pipe into ``jq`` / their own tooling.

All commands are deliberately I/O-thin: the heavy lifting lives in
small ``_*`` driver coroutines that take collaborators as arguments,
making them easy to unit-test without touching real infrastructure.

Authentication is identical across commands: a service or user JWT
passed via ``--token`` (or ``BSVIBE_AUTH_AUDIT_SERVICE_TOKEN`` /
``BSVIBE_AUDIT_TOKEN`` env vars) and shipped in the ``X-Service-Token``
header — matching :class:`bsvibe_audit.client.AuditClient`.

This module migrated from click to Typer. The ``[project.scripts]``
entry point name (``bsvibe-audit``), every subcommand name, every
option flag, every environment-variable fallback and every exit code
remains 100% backwards compatible with the click implementation.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import structlog
import typer
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bsvibe_audit.client import AuditClient
from bsvibe_audit.outbox.schema import AuditOutboxRecord
from bsvibe_audit.outbox.store import OutboxStore

_logger = structlog.get_logger("bsvibe_audit.cli")

_VALID_FORMATS = ("json", "csv", "table")

app = typer.Typer(help="bsvibe-audit operator CLI.", no_args_is_help=True, add_completion=False)


def _fail(message: str, *, code: int = 1) -> "typer.Exit":
    """Print ``message`` to stderr in click-compatible ``Error: ...`` form
    and return a :class:`typer.Exit` for the caller to ``raise``.
    """

    typer.echo(f"Error: {message}", err=True)
    return typer.Exit(code=code)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _post_query(
    *,
    audit_url: str,
    token: str,
    body: dict[str, Any],
    timeout_s: float = 10.0,
) -> dict[str, Any]:
    """POST to the audit-query endpoint and return the decoded JSON body."""

    headers = {"X-Service-Token": token}
    with httpx.Client(timeout=timeout_s) as http:
        response = http.post(audit_url, json=body, headers=headers)
    if response.status_code >= 400:
        excerpt = response.text[:300]
        raise _fail(f"audit query failed [{response.status_code}]: {excerpt}")
    try:
        return response.json()
    except ValueError as exc:
        raise _fail(f"audit query returned non-JSON body: {exc}")


def _iter_events(
    *,
    audit_url: str,
    token: str,
    body: dict[str, Any],
    page_size: int = 200,
) -> Iterator[dict[str, Any]]:
    """Yield events from the query API, walking ``next_cursor`` pages."""

    cursor: str | None = None
    while True:
        page_body = dict(body)
        page_body["limit"] = page_size
        if cursor is not None:
            page_body["cursor"] = cursor
        payload = _post_query(audit_url=audit_url, token=token, body=page_body)
        events = payload.get("events", []) or []
        for event in events:
            yield event
        cursor = payload.get("next_cursor")
        if not cursor or not events:
            return


def _format_events(events: list[dict[str, Any]], fmt: str) -> str:
    """Serialise ``events`` for human or downstream consumption."""

    if fmt == "json":
        return json.dumps(events, indent=2, default=str)

    columns = ["event_id", "event_type", "occurred_at", "tenant_id", "actor_id"]

    def _row(event: dict[str, Any]) -> dict[str, str]:
        actor = event.get("actor") or {}
        return {
            "event_id": str(event.get("event_id", "")),
            "event_type": str(event.get("event_type", "")),
            "occurred_at": str(event.get("occurred_at", "")),
            "tenant_id": str(event.get("tenant_id", "")),
            "actor_id": str(actor.get("id", "")),
        }

    if fmt == "csv":
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=columns)
        writer.writeheader()
        for event in events:
            writer.writerow(_row(event))
        return buf.getvalue()

    # table
    rows = [_row(e) for e in events]
    widths = {col: max(len(col), *(len(r[col]) for r in rows), 1) if rows else len(col) for col in columns}
    header = "  ".join(col.ljust(widths[col]) for col in columns)
    sep = "  ".join("-" * widths[col] for col in columns)
    lines = [header, sep]
    for r in rows:
        lines.append("  ".join(r[col].ljust(widths[col]) for col in columns))
    return "\n".join(lines)


def _normalise_format(fmt: str) -> str:
    """Validate ``--format`` (case-insensitive, matches the legacy click Choice)."""

    lower = fmt.lower()
    if lower not in _VALID_FORMATS:
        raise typer.BadParameter(
            f"invalid choice: {fmt!r}. (choose from {', '.join(_VALID_FORMATS)})",
            param_hint="'--format'",
        )
    return lower


# ---------------------------------------------------------------------------
# query
# ---------------------------------------------------------------------------


@app.command("query", help="Query the BSVibe-Auth audit endpoint with the given filter.")
def query_cmd(
    audit_url: str = typer.Option(..., "--audit-url", help="Full URL to POST /api/audit/query."),
    token: str = typer.Option(
        ...,
        "--token",
        envvar=["BSVIBE_AUDIT_TOKEN", "BSVIBE_AUTH_AUDIT_SERVICE_TOKEN"],
        help="Service or user JWT (X-Service-Token header).",
    ),
    tenant_id: str | None = typer.Option(None, "--tenant", help="Tenant filter."),
    event_type: str | None = typer.Option(None, "--event-type", help="Event-type pattern (wildcards allowed)."),
    since: str | None = typer.Option(None, "--since", help="ISO timestamp lower bound (inclusive)."),
    until: str | None = typer.Option(None, "--until", help="ISO timestamp upper bound (exclusive)."),
    limit: int = typer.Option(100, "--limit", help="Maximum events to fetch."),
    fmt: str = typer.Option("table", "--format", help="Output format (json|csv|table)."),
) -> None:
    fmt_lower = _normalise_format(fmt)

    body: dict[str, Any] = {"limit": limit}
    if tenant_id is not None:
        body["tenant_id"] = tenant_id
    if event_type is not None:
        body["event_type"] = event_type
    if since is not None:
        body["since"] = since
    if until is not None:
        body["until"] = until

    payload = _post_query(audit_url=audit_url, token=token, body=body)
    events = payload.get("events", []) or []
    typer.echo(_format_events(events, fmt_lower))


# ---------------------------------------------------------------------------
# retry-failed
# ---------------------------------------------------------------------------


async def _retry_dead_letter(
    *,
    factory: Any,
    client: AuditClient,
    batch_size: int = 50,
) -> int:
    """Re-queue dead-letter rows and ask the audit client to deliver them."""

    store = OutboxStore()
    delivered_total = 0

    async with factory() as session:
        rows = await store.select_dead_letter(session, limit=batch_size)
        if not rows:
            return 0
        ids = [row.id for row in rows]
        payloads = [dict(row.payload) for row in rows]

        for row_id in ids:
            record = await session.get(AuditOutboxRecord, row_id)
            if record is None:
                continue
            record.dead_letter = False
            record.retry_count = 0
            record.next_attempt_at = None
            record.last_error = None
        await session.commit()

        await client.send(payloads)
        await store.mark_delivered(session, ids)
        await session.commit()
        delivered_total = len(ids)

    return delivered_total


@app.command("retry-failed", help="Drain the local outbox of dead-letter rows and retry delivery.")
def retry_failed_cmd(
    db_url: str = typer.Option(..., "--db-url", help="Async SQLAlchemy URL with the audit_outbox table."),
    audit_url: str = typer.Option(..., "--audit-url", help="POST /api/audit/events endpoint."),
    token: str = typer.Option(
        ...,
        "--token",
        envvar=["BSVIBE_AUTH_AUDIT_SERVICE_TOKEN"],
        help="Service JWT.",
    ),
    batch_size: int = typer.Option(50, "--batch-size"),
) -> None:
    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    client = AuditClient(audit_url=audit_url, service_token=token)

    async def _go() -> int:
        try:
            delivered = await _retry_dead_letter(factory=factory, client=client, batch_size=batch_size)
        finally:
            await client.aclose()
            await engine.dispose()
        return delivered

    delivered = asyncio.run(_go())
    typer.echo(str(delivered))


# ---------------------------------------------------------------------------
# retention-export
# ---------------------------------------------------------------------------


def _export_to_file(events: Iterator[dict[str, Any]], output: Path) -> int:
    output.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with output.open("w", encoding="utf-8") as fh:
        for event in events:
            fh.write(json.dumps(event, default=str))
            fh.write("\n")
            count += 1
    return count


def _export_to_s3(events: Iterator[dict[str, Any]], target: str) -> int:
    """Upload as JSON Lines to ``s3://bucket/key`` using boto3 if available."""

    parsed = urlparse(target)
    bucket = parsed.netloc
    key = parsed.path.lstrip("/")
    if not bucket or not key:
        raise _fail(f"invalid s3 target: {target}")

    try:
        import boto3  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover - optional dep
        raise _fail("boto3 is required for s3:// outputs (pip install boto3)") from exc

    buf = io.StringIO()
    count = 0
    for event in events:
        buf.write(json.dumps(event, default=str))
        buf.write("\n")
        count += 1
    boto3.client("s3").put_object(Bucket=bucket, Key=key, Body=buf.getvalue().encode("utf-8"))
    return count


@app.command("retention-export", help="Page over old events and archive them outside the hot tier.")
def retention_export_cmd(
    audit_url: str = typer.Option(..., "--audit-url", help="POST /api/audit/query endpoint."),
    token: str = typer.Option(
        ...,
        "--token",
        envvar=["BSVIBE_AUTH_AUDIT_SERVICE_TOKEN"],
        help="Service JWT.",
    ),
    tenant_id: str | None = typer.Option(None, "--tenant", help="Tenant filter (optional, all tenants if omitted)."),
    before: str = typer.Option(..., "--before", help="ISO timestamp; only events older than this are exported."),
    output: str = typer.Option(..., "--output", help="Local file path or s3://bucket/key URL."),
    page_size: int = typer.Option(500, "--page-size"),
) -> None:
    parsed = urlparse(output)
    scheme = parsed.scheme.lower()
    if scheme not in ("", "file", "s3"):
        raise _fail(f"unsupported output scheme: {output!r}")

    body: dict[str, Any] = {"until": before}
    if tenant_id is not None:
        body["tenant_id"] = tenant_id

    events = _iter_events(audit_url=audit_url, token=token, body=body, page_size=page_size)

    if scheme == "s3":
        count = _export_to_s3(events, output)
    else:
        target = Path(parsed.path) if scheme == "file" else Path(output)
        count = _export_to_file(events, target)

    typer.echo(f"exported {count} events to {output}")


# ---------------------------------------------------------------------------
# replay
# ---------------------------------------------------------------------------


def _validate_iso(label: str, value: str) -> str:
    """Raise a Typer error early when ``value`` is not parseable."""

    from datetime import datetime

    try:
        cleaned = value.replace("Z", "+00:00")
        datetime.fromisoformat(cleaned)
    except ValueError as exc:
        raise typer.BadParameter(
            f"--{label}: invalid ISO timestamp {value!r} ({exc})",
            param_hint=f"'--{label}'",
        )
    return value


async def _replay_events(
    *,
    audit_url: str,
    token: str,
    since: str,
    until: str,
    tenant_id: str | None,
    event_type: str | None,
    on_event: Callable[[dict[str, Any]], None],
    page_size: int = 200,
) -> int:
    """Walk ``[since, until)`` and invoke ``on_event`` for each row."""

    body: dict[str, Any] = {"since": since, "until": until}
    if tenant_id is not None:
        body["tenant_id"] = tenant_id
    if event_type is not None:
        body["event_type"] = event_type

    count = 0
    for event in _iter_events(audit_url=audit_url, token=token, body=body, page_size=page_size):
        on_event(event)
        count += 1
    return count


@app.command("replay", help="Replay events from a time range, one JSON line per event on stdout.")
def replay_cmd(
    audit_url: str = typer.Option(..., "--audit-url", help="POST /api/audit/query endpoint."),
    token: str = typer.Option(
        ...,
        "--token",
        envvar=["BSVIBE_AUTH_AUDIT_SERVICE_TOKEN"],
        help="Service JWT.",
    ),
    since: str = typer.Option(..., "--since", help="ISO timestamp lower bound (inclusive)."),
    until: str = typer.Option(..., "--until", help="ISO timestamp upper bound (exclusive)."),
    tenant_id: str | None = typer.Option(None, "--tenant"),
    event_type: str | None = typer.Option(None, "--event-type"),
) -> None:
    _validate_iso("since", since)
    _validate_iso("until", until)

    def _emit(event: dict[str, Any]) -> None:
        sys.stdout.write(json.dumps(event, default=str))
        sys.stdout.write("\n")

    delivered = asyncio.run(
        _replay_events(
            audit_url=audit_url,
            token=token,
            since=since,
            until=until,
            tenant_id=tenant_id,
            event_type=event_type,
            on_event=_emit,
        )
    )
    typer.echo(str(delivered))


def main() -> None:
    """Console-script entry point. Kept as a function so the existing
    ``[project.scripts]`` ``bsvibe-audit = "bsvibe_audit.cli:main"``
    target keeps resolving after the click→Typer migration.
    """

    app()


__all__ = [
    "app",
    "main",
    "_replay_events",
    "_retry_dead_letter",
]
