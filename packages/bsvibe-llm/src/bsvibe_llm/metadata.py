"""Run-audit metadata forwarded to BSGateway / BSupervisor.

This module is the **wire contract** between BSVibe products and the
gateway audit pipeline. Drift here is a release-gate failure.

Pinned by:
- ``BSGateway`` PR #24 — ``RunMetadata.from_request_metadata`` parser
  (`/Users/blasin/Works/BSGateway/phase0/bsgateway/supervisor/client.py`).
- ``BSNexus`` PR #38 — chat-service plumb that emits this exact dict
  shape via the LiteLLM ``metadata`` kwarg.
- ``docs/BSNEXUS_METADATA_CONTRACT.md`` (BSGateway phase0).

Required keys: ``tenant_id`` + ``run_id``.
Recommended keys: ``request_id``, ``agent_name``, ``cost_estimate_cents``.
Optional keys: ``parent_run_id``, ``project_id``, ``composition_id``.
Anything else passes through unchanged so consumers (BSupervisor incident
dashboards, BSage trace ingestion) can adopt new keys without a
``bsvibe-llm`` release.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Named keys we own. Extras pass through verbatim.
_NAMED_KEYS: frozenset[str] = frozenset(
    {
        "tenant_id",
        "run_id",
        "request_id",
        "parent_run_id",
        "agent_name",
        "cost_estimate_cents",
        "project_id",
        "composition_id",
    }
)


@dataclass(frozen=True, slots=True)
class RunAuditMetadata:
    """Per-call metadata forwarded to BSGateway under ``metadata=`` kwarg.

    Required:
        tenant_id: Caller's tenant UUID. BSGateway rejects (treats as
            anonymous proxy traffic) when missing.
        run_id: BSNexus ``ExecutionRun`` id. BSGateway skips
            ``run.pre`` / ``run.post`` to BSupervisor when missing.

    Recommended:
        request_id: Mirrors ``Request.id`` so the founder can trace
            which user message produced which audit row.
        agent_name: Defaults to ``service:bsgateway`` on BSGateway side
            when missing — pass it for clearer incident attribution.
        cost_estimate_cents: Surfaces in incident dashboards alongside
            the actual cost reported by ``run.post``.

    Optional:
        parent_run_id: Set on hierarchical runs (subagent, retry).
        project_id: Lets BSupervisor scope incidents per project.
        composition_id: ``CompositionSnapshot.id`` from BSNexus.
        extras: Arbitrary additional keys forwarded verbatim. Named
            fields take precedence on conflict.
    """

    tenant_id: str
    run_id: str
    request_id: str | None = None
    parent_run_id: str | None = None
    agent_name: str | None = None
    cost_estimate_cents: int | None = None
    project_id: str | None = None
    composition_id: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.tenant_id:
            raise ValueError("RunAuditMetadata.tenant_id is required (non-empty string)")
        if not self.run_id:
            raise ValueError("RunAuditMetadata.run_id is required (non-empty string)")
        if self.cost_estimate_cents is not None and not isinstance(self.cost_estimate_cents, int):
            raise TypeError(
                f"RunAuditMetadata.cost_estimate_cents must be int, got {type(self.cost_estimate_cents).__name__}"
            )

    def to_metadata(self) -> dict[str, Any]:
        """Flatten into the dict shape BSGateway expects.

        Drops ``None`` values, then merges ``extras`` underneath named
        fields (named fields win on collision — symmetry with
        ``BSGateway.RunMetadata.to_dict``).
        """
        # Start with extras so named fields can overwrite collisions.
        out: dict[str, Any] = dict(self.extras)
        named: dict[str, Any] = {
            "tenant_id": self.tenant_id,
            "run_id": self.run_id,
            "request_id": self.request_id,
            "parent_run_id": self.parent_run_id,
            "agent_name": self.agent_name,
            "cost_estimate_cents": self.cost_estimate_cents,
            "project_id": self.project_id,
            "composition_id": self.composition_id,
        }
        for k, v in named.items():
            if v is not None:
                out[k] = v
        return out

    @classmethod
    def from_metadata(cls, metadata: dict[str, Any]) -> RunAuditMetadata | None:
        """Parse a dict back into :class:`RunAuditMetadata`.

        Returns ``None`` when ``run_id`` or ``tenant_id`` are missing —
        mirrors BSGateway's ``from_request_metadata`` precondition so
        round-trips are idempotent.
        """
        run_id = metadata.get("run_id")
        tenant_id = metadata.get("tenant_id")
        if not run_id or not tenant_id:
            return None

        cost_raw = metadata.get("cost_estimate_cents")
        cost_cents: int | None
        if cost_raw is None:
            cost_cents = None
        else:
            try:
                cost_cents = int(cost_raw)
            except (TypeError, ValueError):
                cost_cents = None

        extras = {k: v for k, v in metadata.items() if k not in _NAMED_KEYS}

        return cls(
            tenant_id=str(tenant_id),
            run_id=str(run_id),
            request_id=_str_or_none(metadata.get("request_id")),
            parent_run_id=_str_or_none(metadata.get("parent_run_id")),
            agent_name=_str_or_none(metadata.get("agent_name")),
            cost_estimate_cents=cost_cents,
            project_id=_str_or_none(metadata.get("project_id")),
            composition_id=_str_or_none(metadata.get("composition_id")),
            extras=extras,
        )


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


__all__ = ["RunAuditMetadata"]
