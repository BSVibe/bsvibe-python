"""Alembic helpers — DSN normalisation + parity verification.

Two concerns:

* :func:`resolve_sync_alembic_url` rewrites async DSNs to a sync DBAPI
  for Alembic's sync ``run_migrations`` helpers. ``psycopg`` (v3) is
  the canonical sync replacement for ``asyncpg`` (BSGateway PR #22
  S3-5).
* :func:`verify_alembic_parity` orchestrates the BSGateway S3-5 parity
  gate: spin up two PG containers, apply (a) the legacy raw-SQL
  bootstrap and (b) ``alembic upgrade head`` against fresh DBs, and
  diff ``pg_dump --schema-only``. The function takes an injectable
  ``runner`` so tests can mock ``subprocess`` calls without spinning
  up real containers.

The bundled shell version of the parity gate lives in
``scripts/verify_alembic_parity.sh`` for use from CI / pre-deploy
hooks. The Python helper is the same logic, factored for reuse.
"""

from __future__ import annotations

import difflib
import re
import subprocess
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

# Type alias for the subprocess runner. Tests inject a stub.
SubprocessRunner = Callable[..., subprocess.CompletedProcess]


@dataclass(frozen=True)
class ParityResult:
    """Outcome of a parity check.

    Attributes:
        ok: ``True`` iff the two normalised dumps are byte-identical.
        diff: A unified diff of the two dumps when ``ok`` is ``False``,
              empty string otherwise.
        raw_dump: Normalised dump from the raw-SQL container.
        alembic_dump: Normalised dump from the Alembic container.
    """

    ok: bool
    diff: str
    raw_dump: str
    alembic_dump: str


def resolve_sync_alembic_url(url: str) -> str:
    """Rewrite an async DSN to a sync DBAPI Alembic can drive.

    Examples:

    >>> resolve_sync_alembic_url("postgresql+asyncpg://u:p@h/db")
    'postgresql+psycopg://u:p@h/db'
    >>> resolve_sync_alembic_url("postgresql://u:p@h/db")
    'postgresql+psycopg://u:p@h/db'
    >>> resolve_sync_alembic_url("sqlite+aiosqlite:///foo.db")
    'sqlite:///foo.db'
    """
    if not url:
        raise ValueError("resolve_sync_alembic_url requires a non-empty URL")
    if url.startswith("postgresql+asyncpg://"):
        return url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    if url.startswith("sqlite+aiosqlite://"):
        return url.replace("sqlite+aiosqlite://", "sqlite://", 1)
    return url


# Volatile pg_dump lines that must be stripped before diffing — extracted
# from BSGateway scripts/verify_alembic_parity.sh.
_VOLATILE_PATTERNS = (
    re.compile(r"^-- Dumped.*$", re.MULTILINE),
    re.compile(r"^-- Started on.*$", re.MULTILINE),
    re.compile(r"^SET .*$", re.MULTILINE),
    re.compile(r"^SELECT pg_catalog.*$", re.MULTILINE),
    re.compile(r"^\\restrict.*$", re.MULTILINE),
    re.compile(r"^\\unrestrict.*$", re.MULTILINE),
    re.compile(r"^--$", re.MULTILINE),
    re.compile(r"^\s*$", re.MULTILINE),
)

_ALEMBIC_VERSION_TOKENS = ("alembic_version", "alembic_version_pkc")


def default_dump_normaliser(dump: str) -> str:
    """Strip volatile lines + the ``alembic_version`` bookkeeping table.

    This mirrors the BSGateway shell parity gate exactly so the Python
    helper produces byte-identical output for the same dump input.
    """
    text = dump
    # Drop the alembic_version block first (object-aware) so the
    # subsequent volatile-line strip does not destroy structural lines
    # we still need.
    text = _strip_alembic_version_block(text)
    for pattern in _VOLATILE_PATTERNS:
        text = pattern.sub("", text)
    # Collapse repeated newlines that the line drops created.
    text = re.sub(r"\n{2,}", "\n", text).strip("\n")
    return text


def _strip_alembic_version_block(text: str) -> str:
    """Drop the ``-- Name: alembic_version; Type: ...`` header + body.

    Identical algorithm to the inline python3 block in BSGateway
    ``scripts/verify_alembic_parity.sh`` — token-aware so we do not
    accidentally drop user-table CONSTRAINT lines whose header comment
    happened to mention ``alembic_version``.
    """
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        m = re.match(r"^-- Name: ([^;]+); Type:", line)
        if m:
            slug_tokens = set(m.group(1).split())
            if slug_tokens & set(_ALEMBIC_VERSION_TOKENS):
                # Skip header + the following statement until ';' on
                # its own line (or end of input).
                i += 1
                while i < n:
                    stmt = lines[i]
                    i += 1
                    if stmt.rstrip().endswith(";"):
                        break
                continue
        # Drop bare CREATE TABLE alembic_version ... statements that
        # were synthesised without the conventional header (e.g. when
        # alembic emits them via API rather than pg_dump).
        if re.match(r"\s*CREATE TABLE\s+alembic_version\b", line, re.IGNORECASE):
            i += 1
            while i < n and not lines[i - 1].rstrip().endswith(";"):
                i += 1
            continue
        out.append(line)
        i += 1
    return "\n".join(out)


def _wait_for_pg(runner: SubprocessRunner, container: str, attempts: int = 30) -> None:
    """Poll ``pg_isready`` until the container accepts connections."""
    for _ in range(attempts):
        cp = runner(
            ["docker", "exec", container, "pg_isready", "-U", "parity"],
            check=False,
            capture_output=True,
        )
        if cp.returncode == 0:
            return
        time.sleep(1)
    raise RuntimeError(f"PG container {container!r} did not become ready")


def _start_pg(runner: SubprocessRunner, container: str, port: int) -> None:
    """Start an ephemeral PG 16 container for the parity check."""
    runner(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            container,
            "-e",
            "POSTGRES_PASSWORD=parity",
            "-e",
            "POSTGRES_USER=parity",
            "-e",
            "POSTGRES_DB=parity",
            "-p",
            f"{port}:5432",
            "postgres:16-alpine",
        ],
        check=True,
        capture_output=True,
    )
    _wait_for_pg(runner, container)


def _stop_pg(runner: SubprocessRunner, container: str) -> None:
    runner(
        ["docker", "rm", "-f", container],
        check=False,
        capture_output=True,
    )


def _apply_raw_sql(
    runner: SubprocessRunner,
    container: str,
    files: Sequence[Path],
) -> None:
    for f in files:
        runner(
            [
                "docker",
                "exec",
                "-i",
                "-e",
                "PGPASSWORD=parity",
                container,
                "psql",
                "-U",
                "parity",
                "-d",
                "parity",
                "-v",
                "ON_ERROR_STOP=1",
                "-q",
                "-f",
                "-",
            ],
            input=f.read_bytes() if f.exists() else b"",
            check=True,
            capture_output=True,
        )


def _alembic_upgrade(
    runner: SubprocessRunner,
    alembic_directory: Path,
    database_url: str,
) -> None:
    runner(
        ["uv", "run", "alembic", "upgrade", "head"],
        cwd=str(alembic_directory),
        env={"DATABASE_URL": database_url},
        check=True,
        capture_output=True,
    )


def _pg_dump(runner: SubprocessRunner, container: str) -> str:
    cp = runner(
        [
            "docker",
            "exec",
            "-e",
            "PGPASSWORD=parity",
            container,
            "pg_dump",
            "-U",
            "parity",
            "-d",
            "parity",
            "--schema-only",
            "--no-owner",
            "--no-privileges",
        ],
        check=True,
        capture_output=True,
    )
    return cp.stdout.decode("utf-8") if isinstance(cp.stdout, bytes) else str(cp.stdout)


def verify_alembic_parity(
    *,
    raw_sql_files: Sequence[Path],
    alembic_directory: Path,
    runner: SubprocessRunner | None = None,
    dump_normaliser: Callable[[str], str] | None = None,
    container_names: tuple[str, str] | None = None,
    raw_port: int = 55501,
    alembic_port: int = 55502,
) -> ParityResult:
    """Run the BSGateway S3-5 parity gate.

    Spins up two PG containers, applies ``raw_sql_files`` to one and
    ``alembic upgrade head`` to the other, then diffs the normalised
    ``pg_dump`` outputs.

    Args:
        raw_sql_files: Ordered legacy schema files to apply via
            ``psql``.
        alembic_directory: Directory containing ``alembic.ini`` (passed
            as cwd to ``alembic upgrade head``).
        runner: Subprocess runner — defaults to :func:`subprocess.run`.
            Tests inject a stub.
        dump_normaliser: Function that strips volatile lines from a
            ``pg_dump`` output. Defaults to
            :func:`default_dump_normaliser`.
        container_names: ``(raw, alembic)`` container name pair.
            Defaults to a uniquely-suffixed pair so concurrent runs do
            not collide.
        raw_port: Host port mapped to the raw-SQL container.
        alembic_port: Host port mapped to the alembic container.

    Returns:
        :class:`ParityResult` with ``ok=True`` iff the two normalised
        dumps are identical.

    Raises:
        Anything the runner raises (typically
        :class:`subprocess.CalledProcessError`) when a docker / psql /
        alembic step fails.
    """
    actual_runner: SubprocessRunner = runner if runner is not None else subprocess.run
    actual_normaliser = dump_normaliser if dump_normaliser is not None else default_dump_normaliser
    if container_names is None:
        suffix = uuid.uuid4().hex[:8]
        container_names = (f"bsvibe-parity-raw-{suffix}", f"bsvibe-parity-alembic-{suffix}")
    raw_container, alembic_container = container_names

    try:
        _start_pg(actual_runner, raw_container, raw_port)
        _start_pg(actual_runner, alembic_container, alembic_port)

        _apply_raw_sql(actual_runner, raw_container, raw_sql_files)
        _alembic_upgrade(
            actual_runner,
            alembic_directory,
            f"postgresql://parity:parity@127.0.0.1:{alembic_port}/parity",
        )

        raw_dump = actual_normaliser(_pg_dump(actual_runner, raw_container))
        alembic_dump = actual_normaliser(_pg_dump(actual_runner, alembic_container))

        if raw_dump == alembic_dump:
            return ParityResult(ok=True, diff="", raw_dump=raw_dump, alembic_dump=alembic_dump)

        diff = "\n".join(
            difflib.unified_diff(
                raw_dump.splitlines(),
                alembic_dump.splitlines(),
                fromfile="raw_sql",
                tofile="alembic_upgrade_head",
                lineterm="",
            )
        )
        return ParityResult(ok=False, diff=diff, raw_dump=raw_dump, alembic_dump=alembic_dump)
    finally:
        _stop_pg(actual_runner, raw_container)
        _stop_pg(actual_runner, alembic_container)


__all__ = [
    "ParityResult",
    "resolve_sync_alembic_url",
    "default_dump_normaliser",
    "verify_alembic_parity",
]
