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

import click
import httpx
import structlog

from bsvibe_audit.client import AuditClient
from bsvibe_audit.outbox.schema import AuditOutboxRecord
from bsvibe_audit.outbox.store import OutboxStore

_logger = structlog.get_logger("bsvibe_audit.cli")


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
    """POST to the audit-query endpoint and return the decoded JSON body.

    Raises a :class:`click.ClickException` when the endpoint reports a
    non-2xx status so the caller never has to translate ``httpx``
    exceptions back into shell exit codes.
    """

    headers = {"X-Service-Token": token}
    with httpx.Client(timeout=timeout_s) as http:
        response = http.post(audit_url, json=body, headers=headers)
    if response.status_code >= 400:
        excerpt = response.text[:300]
        raise click.ClickException(f"audit query failed [{response.status_code}]: {excerpt}")
    try:
        return response.json()
    except ValueError as exc:
        raise click.ClickException(f"audit query returned non-JSON body: {exc}")


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


# ---------------------------------------------------------------------------
# query
# ---------------------------------------------------------------------------


@click.group(help="bsvibe-audit operator CLI.")
def main() -> None:
    """Entry point. Each subcommand is independently testable."""


@main.command("query")
@click.option("--audit-url", required=True, help="Full URL to POST /api/audit/query.")
@click.option(
    "--token",
    required=True,
    envvar=["BSVIBE_AUDIT_TOKEN", "BSVIBE_AUTH_AUDIT_SERVICE_TOKEN"],
    help="Service or user JWT (X-Service-Token header).",
)
@click.option("--tenant", "tenant_id", default=None, help="Tenant filter.")
@click.option("--event-type", default=None, help="Event-type pattern (wildcards allowed).")
@click.option("--since", default=None, help="ISO timestamp lower bound (inclusive).")
@click.option("--until", default=None, help="ISO timestamp upper bound (exclusive).")
@click.option("--limit", type=int, default=100, show_default=True, help="Maximum events to fetch.")
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["json", "csv", "table"], case_sensitive=False),
    default="table",
    show_default=True,
    help="Output format.",
)
def query_cmd(
    audit_url: str,
    token: str,
    tenant_id: str | None,
    event_type: str | None,
    since: str | None,
    until: str | None,
    limit: int,
    fmt: str,
) -> None:
    """Query the BSVibe-Auth audit endpoint with the given filter."""

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
    click.echo(_format_events(events, fmt.lower()))


# ---------------------------------------------------------------------------
# retry-failed
# ---------------------------------------------------------------------------


async def _retry_dead_letter(
    *,
    factory: Any,
    client: AuditClient,
    batch_size: int = 50,
) -> int:
    """Re-queue dead-letter rows and ask the audit client to deliver them.

    Returns the number of rows actually delivered. The function clears
    the ``dead_letter`` flag in batches of ``batch_size`` and then
    forwards the payloads to :meth:`AuditClient.send`. Failures are
    propagated so the caller can decide whether to log/exit non-zero.
    """

    store = OutboxStore()
    delivered_total = 0

    async with factory() as session:
        rows = await store.select_dead_letter(session, limit=batch_size)
        if not rows:
            return 0
        ids = [row.id for row in rows]
        payloads = [dict(row.payload) for row in rows]

        # Reset the dead-letter flag and retry counter so a subsequent
        # crash in send() leaves the rows visible to the relay (instead
        # of stuck in dead-letter forever).
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


@main.command("retry-failed")
@click.option("--db-url", required=True, help="Async SQLAlchemy URL with the audit_outbox table.")
@click.option("--audit-url", required=True, help="POST /api/audit/events endpoint.")
@click.option("--token", required=True, envvar=["BSVIBE_AUTH_AUDIT_SERVICE_TOKEN"], help="Service JWT.")
@click.option("--batch-size", type=int, default=50, show_default=True)
def retry_failed_cmd(db_url: str, audit_url: str, token: str, batch_size: int) -> None:
    """Drain the local outbox of dead-letter rows and retry delivery."""

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

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
    click.echo(str(delivered))


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
        raise click.ClickException(f"invalid s3 target: {target}")

    try:
        import boto3  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover - optional dep
        raise click.ClickException("boto3 is required for s3:// outputs (pip install boto3)") from exc

    buf = io.StringIO()
    count = 0
    for event in events:
        buf.write(json.dumps(event, default=str))
        buf.write("\n")
        count += 1
    boto3.client("s3").put_object(Bucket=bucket, Key=key, Body=buf.getvalue().encode("utf-8"))
    return count


@main.command("retention-export")
@click.option("--audit-url", required=True, help="POST /api/audit/query endpoint.")
@click.option("--token", required=True, envvar=["BSVIBE_AUTH_AUDIT_SERVICE_TOKEN"], help="Service JWT.")
@click.option("--tenant", "tenant_id", default=None, help="Tenant filter (optional, all tenants if omitted).")
@click.option("--before", required=True, help="ISO timestamp; only events older than this are exported.")
@click.option("--output", required=True, help="Local file path or s3://bucket/key URL.")
@click.option("--page-size", type=int, default=500, show_default=True)
def retention_export_cmd(
    audit_url: str,
    token: str,
    tenant_id: str | None,
    before: str,
    output: str,
    page_size: int,
) -> None:
    """Page over old events and archive them outside the hot tier."""

    parsed = urlparse(output)
    scheme = parsed.scheme.lower()
    if scheme not in ("", "file", "s3"):
        raise click.ClickException(f"unsupported output scheme: {output!r}")

    body: dict[str, Any] = {"until": before}
    if tenant_id is not None:
        body["tenant_id"] = tenant_id

    events = _iter_events(audit_url=audit_url, token=token, body=body, page_size=page_size)

    if scheme == "s3":
        count = _export_to_s3(events, output)
    else:
        target = Path(parsed.path) if scheme == "file" else Path(output)
        count = _export_to_file(events, target)

    click.echo(f"exported {count} events to {output}")


# ---------------------------------------------------------------------------
# replay
# ---------------------------------------------------------------------------


def _validate_iso(label: str, value: str) -> str:
    """Raise a Click error early when ``value`` is not parseable."""

    from datetime import datetime

    try:
        # Accept Z and +00:00 by normalising.
        cleaned = value.replace("Z", "+00:00")
        datetime.fromisoformat(cleaned)
    except ValueError as exc:
        raise click.BadParameter(f"--{label}: invalid ISO timestamp {value!r} ({exc})")
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
    """Walk ``[since, until)`` and invoke ``on_event`` for each row.

    Implementation note: the helper runs synchronously under
    ``asyncio.run`` for now (httpx Client is sync). It is exposed as an
    ``async def`` so future migrations to ``httpx.AsyncClient`` are
    backwards-compatible — callers already ``await`` this function.
    """

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


@main.command("replay")
@click.option("--audit-url", required=True, help="POST /api/audit/query endpoint.")
@click.option("--token", required=True, envvar=["BSVIBE_AUTH_AUDIT_SERVICE_TOKEN"], help="Service JWT.")
@click.option("--since", required=True, help="ISO timestamp lower bound (inclusive).")
@click.option("--until", required=True, help="ISO timestamp upper bound (exclusive).")
@click.option("--tenant", "tenant_id", default=None)
@click.option("--event-type", default=None)
def replay_cmd(
    audit_url: str,
    token: str,
    since: str,
    until: str,
    tenant_id: str | None,
    event_type: str | None,
) -> None:
    """Replay events from a time range, one JSON line per event on stdout."""

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
    click.echo(str(delivered))


__all__ = [
    "main",
    "_replay_events",
    "_retry_dead_letter",
]
