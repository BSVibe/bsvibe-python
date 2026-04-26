"""Wire-compatibility tests for ``RunAuditMetadata``.

These tests pin the BSGateway / BSNexus shared metadata contract
(``BSGATEWAY_PR#24`` + ``BSNEXUS_METADATA_CONTRACT.md``). Drift here
breaks the audit pipeline: BSGateway's ``RunMetadata.from_request_metadata``
silently returns ``None`` when ``run_id`` / ``tenant_id`` are missing,
so ``bsvibe-llm`` MUST forward them under exactly those names.
"""

from __future__ import annotations

import pytest

from bsvibe_llm.metadata import RunAuditMetadata


class TestRunAuditMetadataContract:
    def test_minimal_required_fields_only(self):
        meta = RunAuditMetadata(tenant_id="t-1", run_id="r-1")

        payload = meta.to_metadata()

        # The 2 hard-required keys for BSGateway's ``from_request_metadata``.
        assert payload["tenant_id"] == "t-1"
        assert payload["run_id"] == "r-1"
        # Optional fields that are ``None`` MUST be omitted — keeps payload
        # terse, matches BSGateway ``RunMetadata.to_dict`` behaviour.
        assert "request_id" not in payload
        assert "parent_run_id" not in payload
        assert "agent_name" not in payload
        assert "cost_estimate_cents" not in payload
        assert "project_id" not in payload
        assert "composition_id" not in payload

    def test_full_payload_matches_contract_keys(self):
        """All 8 BSGateway contract keys + extras."""
        meta = RunAuditMetadata(
            tenant_id="t-1",
            run_id="r-1",
            request_id="req-1",
            parent_run_id="parent-1",
            agent_name="composer",
            cost_estimate_cents=42,
            project_id="proj-1",
            composition_id="comp-1",
            extras={"trace_id": "tr-1"},
        )

        payload = meta.to_metadata()

        assert payload == {
            "tenant_id": "t-1",
            "run_id": "r-1",
            "request_id": "req-1",
            "parent_run_id": "parent-1",
            "agent_name": "composer",
            "cost_estimate_cents": 42,
            "project_id": "proj-1",
            "composition_id": "comp-1",
            "trace_id": "tr-1",
        }

    def test_missing_tenant_id_raises(self):
        with pytest.raises((TypeError, ValueError)):
            RunAuditMetadata(tenant_id="", run_id="r-1")  # type: ignore[arg-type]

    def test_missing_run_id_raises(self):
        with pytest.raises((TypeError, ValueError)):
            RunAuditMetadata(tenant_id="t-1", run_id="")  # type: ignore[arg-type]

    def test_cost_estimate_cents_must_be_int(self):
        with pytest.raises((TypeError, ValueError)):
            RunAuditMetadata(  # type: ignore[arg-type]
                tenant_id="t-1",
                run_id="r-1",
                cost_estimate_cents="cheap",
            )

    def test_extras_keys_do_not_collide_with_named_fields(self):
        """Named fields take precedence over extras (BSGateway does the same)."""
        meta = RunAuditMetadata(
            tenant_id="t-1",
            run_id="r-1",
            agent_name="real-agent",
            extras={"agent_name": "fake-agent"},
        )

        payload = meta.to_metadata()

        assert payload["agent_name"] == "real-agent"

    def test_round_trip_via_bsgateway_from_request_metadata_shape(self):
        """The dict we emit MUST satisfy BSGateway's parser.

        BSGateway's ``RunMetadata.from_request_metadata`` returns ``None``
        when either ``tenant_id`` or ``run_id`` is missing. Anything we
        produce with both fields populated MUST round-trip.
        """
        meta = RunAuditMetadata(
            tenant_id="t-1",
            run_id="r-1",
            request_id="req-1",
            parent_run_id="parent-1",
            agent_name="composer",
            cost_estimate_cents=99,
        )

        payload = meta.to_metadata()

        # Mirror BSGateway parser preconditions.
        assert payload.get("tenant_id")
        assert payload.get("run_id")
        # Any extra keys we add MUST be string-coercible (BSGateway treats
        # unknown keys as opaque ``extras``).
        for k in payload:
            assert isinstance(k, str)

    def test_from_litellm_metadata_inverse(self):
        """We accept the same metadata dict shape BSGateway accepts."""
        raw = {
            "tenant_id": "t-1",
            "run_id": "r-1",
            "request_id": "req-1",
            "agent_name": "composer",
            "cost_estimate_cents": 42,
            "extra_thing": "ok",
        }

        meta = RunAuditMetadata.from_metadata(raw)

        assert meta is not None
        assert meta.tenant_id == "t-1"
        assert meta.run_id == "r-1"
        assert meta.request_id == "req-1"
        assert meta.agent_name == "composer"
        assert meta.cost_estimate_cents == 42
        assert meta.extras == {"extra_thing": "ok"}

    def test_from_metadata_returns_none_when_required_missing(self):
        # No run_id → unforwardable.
        assert RunAuditMetadata.from_metadata({"tenant_id": "t-1"}) is None
        # No tenant_id → unforwardable.
        assert RunAuditMetadata.from_metadata({"run_id": "r-1"}) is None

    def test_from_metadata_coerces_cost_estimate(self):
        meta = RunAuditMetadata.from_metadata({"tenant_id": "t-1", "run_id": "r-1", "cost_estimate_cents": "12"})
        assert meta is not None
        assert meta.cost_estimate_cents == 12

    def test_from_metadata_drops_uncoercible_cost(self):
        meta = RunAuditMetadata.from_metadata({"tenant_id": "t-1", "run_id": "r-1", "cost_estimate_cents": "cheap"})
        assert meta is not None
        assert meta.cost_estimate_cents is None
