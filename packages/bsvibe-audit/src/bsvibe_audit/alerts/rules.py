"""Declarative audit-event alert rule.

A rule has three responsibilities:

1. Decide whether an audit event payload matches its
   ``event_type_pattern`` (supports trailing ``*`` and dotted prefixes).
2. Track recent matches per "key" (default = global, can be tenant-
   scoped via ``threshold_key``) so the engine can fire only when a
   threshold count is reached inside a sliding time window.
3. Render a human-readable alert message from the event payload using
   :py:meth:`str.format` semantics.

The class is deliberately stateful — sliding-window deques are kept on
the instance so a single rule object can be reused across thousands of
events. Callers who need isolation (per-test, per-tenant) instantiate
new rule objects.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from bsvibe_alerts import AlertSeverity


def _default_clock() -> datetime:
    return datetime.now(UTC)


def _pattern_matches(pattern: str, value: str) -> bool:
    """Tiny glob-style matcher.

    Supports trailing ``*`` (``auth.*`` matches ``auth.session.failed``)
    and exact equality. Internal asterisks (``a.*.b``) are not supported
    — keep this simple; the audit event taxonomy is dotted-namespace
    flat enough that prefix wildcards cover the operational rules.
    """

    if pattern == "*" or pattern == value:
        return True
    if pattern.endswith(".*"):
        prefix = pattern[:-2]
        return value == prefix or value.startswith(prefix + ".")
    return False


@dataclass
class AuditAlertRule:
    """One declarative audit → alert rule.

    Parameters
    ----------
    name:
        Stable identifier (kebab-case). Used as the alert event suffix
        (``audit.<name>``) and as the dedup key for documentation.
    event_type_pattern:
        ``event_type`` to match. Supports trailing ``*`` (e.g.
        ``auth.*``).
    severity:
        Severity to emit when the rule fires.
    message_template:
        ``str.format`` template. Interpolated against a flat view of the
        event (top-level fields plus ``actor_id``, ``actor_type`` for
        convenience).
    threshold_count, threshold_window_s:
        When set, the rule only fires after observing
        ``threshold_count`` matching events within ``threshold_window_s``
        seconds. Defaults (``threshold_count=1``) make the rule fire on
        every match.
    threshold_key:
        Tuple of event field names (``("tenant_id",)`` is typical) that
        scope the sliding window. With ``threshold_key=()`` (default) all
        matches share one window.
    clock:
        Time source — overridable for tests.
    """

    name: str
    event_type_pattern: str
    severity: AlertSeverity
    message_template: str
    threshold_count: int = 1
    threshold_window_s: float = 0.0
    threshold_key: tuple[str, ...] = field(default_factory=tuple)
    clock: Callable[[], datetime] = field(default=_default_clock)

    # Per-instance sliding-window storage. Each key gets its own deque
    # of timestamps; we trim to ``threshold_window_s`` on every push.
    _windows: dict[tuple[Any, ...], deque[datetime]] = field(
        default_factory=dict, init=False, repr=False, compare=False
    )

    def matches(self, event: dict[str, Any]) -> bool:
        """Return True iff ``event_type`` satisfies the rule's pattern."""

        return _pattern_matches(self.event_type_pattern, str(event.get("event_type", "")))

    def render(self, event: dict[str, Any]) -> str:
        """Render the alert body. Missing keys fall back to ``"-"``."""

        ctx = self._format_context(event)
        try:
            return self.message_template.format_map(_DefaultDict(ctx))
        except Exception:  # noqa: BLE001 — template renders must never crash the engine
            return self.message_template

    def should_fire(self, event: dict[str, Any]) -> bool:
        """Record ``event`` and return True iff the threshold tripped.

        Non-matching events are ignored. When no threshold is configured
        (the default), the rule fires on every match. Otherwise the
        rule maintains a sliding window per ``threshold_key`` tuple of
        field values.
        """

        if not self.matches(event):
            return False

        if self.threshold_count <= 1 and self.threshold_window_s <= 0:
            return True

        now = self.clock()
        key = self._compute_key(event)
        window = self._windows.setdefault(key, deque())
        window.append(now)

        # Evict timestamps outside the window so the deque length is the
        # current count.
        if self.threshold_window_s > 0:
            cutoff = now - _timedelta_seconds(self.threshold_window_s)
            while window and window[0] < cutoff:
                window.popleft()

        return len(window) >= self.threshold_count

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _compute_key(self, event: dict[str, Any]) -> tuple[Any, ...]:
        return tuple(event.get(field_name) for field_name in self.threshold_key)

    def _format_context(self, event: dict[str, Any]) -> dict[str, Any]:
        ctx: dict[str, Any] = {
            "event_id": event.get("event_id"),
            "event_type": event.get("event_type"),
            "occurred_at": event.get("occurred_at"),
            "tenant_id": event.get("tenant_id"),
        }
        actor = event.get("actor") or {}
        ctx["actor_id"] = actor.get("id")
        ctx["actor_type"] = actor.get("type")
        # Promote any data field for templating convenience.
        data = event.get("data") or {}
        for k, v in data.items():
            ctx.setdefault(k, v)
        return ctx


class _DefaultDict(dict[str, Any]):
    """``str.format_map`` helper that returns ``"-"`` for missing keys."""

    def __missing__(self, key: str) -> str:
        return "-"


def _timedelta_seconds(seconds: float):
    """Importing ``timedelta`` lazily to keep this file's surface tight."""

    from datetime import timedelta

    return timedelta(seconds=seconds)


__all__ = ["AuditAlertRule"]
