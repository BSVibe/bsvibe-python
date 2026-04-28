"""Tests for :class:`bsvibe_audit.alerts.CentralAlertRuleEngine` (D18).

The engine forwards audit events to BSVibe-Auth's
``POST /api/alerts/dispatch`` via :class:`bsvibe_alerts.CentralDispatchClient`.

Note: this test file deliberately avoids importing the concrete
:class:`CentralDispatchClient` / :class:`CentralDispatchError` / :class:`DispatchResult`
types so it can run on integration branches where those names have not
yet been merged from PR #6 (D18 bsvibe-alerts). The engine itself
imports them lazily; we provide protocol-shaped fakes here.

Once D18 lands on this branch's base, the test will keep passing as-is —
the lazy import resolves to the real types, and the duck-typed fakes still
match.
"""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from bsvibe_audit.alerts import (
    AlertRuleEngine,
    CentralAlertRuleEngine,
    DispatchMode,
    default_rules,
    resolve_dispatch_mode,
)


# ---------------------------------------------------------------------------
# Forward-compatibility shims
# ---------------------------------------------------------------------------
# The real CentralDispatchError lives in bsvibe_alerts.dispatch_client (PR #6).
# Until that branch merges in, we install a stub module so the engine's
# ``from bsvibe_alerts.dispatch_client import CentralDispatchError`` resolves.


@dataclass
class _StubDispatchError(Exception):
    """Mirrors :class:`bsvibe_alerts.CentralDispatchError`."""

    message: str
    retryable: bool = False
    status: int | None = None

    def __post_init__(self) -> None:
        super().__init__(self.message)


@dataclass
class _StubDeliveryDescriptor:
    rule_id: str = "r"
    name: str = "n"
    channel: str = "telegram"
    severity: str = "warning"
    config: dict[str, Any] | None = None
    enabled: bool = True


@dataclass
class _StubDispatchResult:
    matched_rules: int
    deliveries: list[_StubDeliveryDescriptor]
    event_id: str = "x"
    event_type: str = "auth.session.failed"
    tenant_id: str = "t"
    severity: str = "critical"


@pytest.fixture(autouse=True)
def _install_dispatch_client_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure ``bsvibe_alerts.dispatch_client`` exposes ``CentralDispatchError``.

    The real module ships in PR #6. Only install the stub when the real
    one is missing — never overwrite a real module.
    """

    try:
        from bsvibe_alerts import dispatch_client as real

        if hasattr(real, "CentralDispatchError"):
            return
    except ImportError:
        pass

    stub = types.ModuleType("bsvibe_alerts.dispatch_client")
    stub.CentralDispatchError = _StubDispatchError  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "bsvibe_alerts.dispatch_client", stub)


def _result(matched: int) -> _StubDispatchResult:
    return _StubDispatchResult(
        matched_rules=matched,
        deliveries=[_StubDeliveryDescriptor() for _ in range(matched)],
    )


def _event(event_type: str = "auth.session.failed") -> dict[str, Any]:
    return {
        "event_id": "11111111-1111-1111-1111-111111111111",
        "event_type": event_type,
        "occurred_at": "2026-04-28T00:00:00Z",
        "actor": {"type": "user", "id": "u-1"},
        "tenant_id": "00000000-0000-0000-0000-0000000000aa",
        "data": {"severity": "critical"},
    }


class TestCentralEngineEvaluate:
    async def test_forwards_each_event_to_dispatch(self) -> None:
        client = MagicMock()
        client.dispatch = AsyncMock(return_value=_result(2))

        engine = CentralAlertRuleEngine(dispatch_client=client, service="bsage")
        events = [_event(), _event("nexus.run.blocked")]
        matched = await engine.evaluate(events)

        assert matched == 4  # 2 events × 2 matches each
        assert client.dispatch.call_count == 2

    async def test_isolation_on_dispatch_error(self) -> None:
        # Use the real CentralDispatchError if present, else the stub.
        try:
            from bsvibe_alerts.dispatch_client import CentralDispatchError as Err
        except ImportError:
            Err = _StubDispatchError  # type: ignore[assignment, misc]

        client = MagicMock()
        client.dispatch = AsyncMock(
            side_effect=[
                Err("boom", retryable=True, status=503),  # type: ignore[call-arg]
                _result(1),
            ]
        )
        engine = CentralAlertRuleEngine(dispatch_client=client)
        matched = await engine.evaluate([_event(), _event("nexus.run.blocked")])
        assert matched == 1  # second event still counted
        assert client.dispatch.call_count == 2

    async def test_isolation_on_unexpected_exception(self) -> None:
        client = MagicMock()
        client.dispatch = AsyncMock(side_effect=RuntimeError("unexpected"))
        engine = CentralAlertRuleEngine(dispatch_client=client)
        matched = await engine.evaluate([_event()])
        assert matched == 0  # error swallowed, count remains 0

    async def test_empty_events_returns_zero(self) -> None:
        client = MagicMock()
        client.dispatch = AsyncMock(return_value=_result(0))
        engine = CentralAlertRuleEngine(dispatch_client=client)
        matched = await engine.evaluate([])
        assert matched == 0
        client.dispatch.assert_not_called()


class TestResolveDispatchMode:
    def test_default_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("BSVIBE_AUDIT_DISPATCH_MODE", raising=False)
        assert resolve_dispatch_mode() == "hardcoded"

    def test_explicit_argument_wins(self) -> None:
        assert resolve_dispatch_mode("central") == "central"
        assert resolve_dispatch_mode("hardcoded") == "hardcoded"

    def test_env_var_used_when_explicit_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BSVIBE_AUDIT_DISPATCH_MODE", "central")
        assert resolve_dispatch_mode() == "central"

    def test_unknown_value_raises(self) -> None:
        with pytest.raises(ValueError):
            resolve_dispatch_mode("loud")

    def test_normalises_case_and_whitespace(self) -> None:
        assert resolve_dispatch_mode(" Central ") == "central"


class TestBackwardsCompat:
    def test_default_rules_unchanged(self) -> None:
        # Critical regression guard: D18 must not change preset rule
        # count or names — products that depend on the hardcoded engine
        # would break otherwise.
        rules = default_rules()
        names = {r.name for r in rules}
        assert names == {
            "audit.brute-force",
            "audit.budget-exceeded",
            "audit.rate-limit-pressure",
            "audit.anomaly-detected",
            "audit.run-blocked",
        }

    def test_alert_rule_engine_still_exported(self) -> None:
        # Belt-and-braces: the hardcoded path is still accessible
        # alongside the new central engine.
        assert AlertRuleEngine.__name__ == "AlertRuleEngine"
        assert CentralAlertRuleEngine.__name__ == "CentralAlertRuleEngine"
        assert DispatchMode is not None
