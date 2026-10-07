"""Synthetic recorded-chain replay exercises the exposed V6 DENY path."""

import json
from pathlib import Path

import pytest

from app.domain.source_primitives import canonical
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_native_exposed_observation as exposed
from app.trade_qualification import account_native_proof as proof
from app.trade_qualification import account_native_runtime as runtime
from app.trade_qualification.account_runtime import ControlledDemoAccountSession
from tests.unit.test_account_native_proof import companion_fixture, replay
from tests.unit.test_qualification_account_collector import credentials


async def recorded_exposure(monkeypatch):
    raw, files, chain, scope, _samples = await companion_fixture(
        monkeypatch, source_case="exposed", current_only=True
    )
    reference, packet, _records, _joins = proof._source(
        chain, scope, proof_schema=proof.V3_SCHEMA
    )
    return raw, files, chain, scope, reference, packet


@pytest.mark.asyncio
async def test_exposed_chain_is_observable_but_flat_native_proof_still_denies(
    monkeypatch,
):
    raw, files, chain, scope, reference, packet = await recorded_exposure(monkeypatch)
    with pytest.raises(
        proof.NativeAccountProofError,
        match="native_account_current_sources_incomplete_or_exposed",
    ):
        replay(raw, files, chain, scope)

    result = exposed.observe_recorded_native_v6_exposure(
        chain=chain,
        reference=reference,
        packet=packet,
        scope=scope,
        validated_at=packet.completed_at,
    )
    receipt = json.loads(result.receipt_json)
    assert result.receipt_json == canonical(receipt)
    assert receipt["source_reference"]["packet_sha256"] == reference.packet_sha256
    assert receipt["recorded_source_chain_replayed"] is True
    assert receipt["current_exposure_rows_observed"] is True
    assert receipt["current_inventory_row_counts"]["positions"] == 1
    assert receipt["current_inventory_row_counts"]["orders_pending"] == 1
    assert receipt["current_inventory_row_counts"]["algo_conditional"] == 1
    pending = [
        page for page in receipt["current_pages"] if page["stream"] == "orders_pending"
    ]
    assert len(pending) == 2
    assert pending[-1]["terminal"] is True
    assert pending[-1]["row_count"] == 0
    assert pending[-1]["previous_page_sha256"] == pending[0]["receipt_sha256"]
    assert len(receipt["current_rows"]) >= 3
    assert "native_clock_companion_proof_missing" in receipt["blocking_reasons"]
    assert "active_protection_coverage_unknown" in receipt["blocking_reasons"]
    assert receipt["history_complete"] is None
    assert receipt["local_exposure_complete"] is None
    assert receipt["active_protection_complete"] is None
    assert receipt["snapshot"] is result.snapshot is None
    assert receipt["account_complete"] is result.account_complete is False
    assert receipt["flat_start_permission"] is False
    assert receipt["execution_authority"] is result.execution_authority is False
    assert receipt["admission"] == "DENY"
    assert packet.plan.expected_uid.encode() not in result.receipt_json
    assert packet.plan.expected_main_uid.encode() not in result.receipt_json
    assert packet.plan.session_binding_id.encode() not in result.receipt_json


@pytest.mark.asyncio
async def test_exposure_observation_rejects_flat_or_changed_source(monkeypatch):
    _raw, _files, chain, scope, reference, packet = await recorded_exposure(monkeypatch)
    with pytest.raises(exposed.NativeExposedObservationError):
        exposed.observe_recorded_native_v6_exposure(
            chain=chain[:-1],
            reference=reference,
            packet=packet,
            scope=scope,
            validated_at=packet.completed_at,
        )
    with pytest.raises(exposed.NativeExposedObservationError):
        exposed.observe_recorded_native_v6_exposure(
            chain=chain,
            reference=reference,
            packet=packet,
            scope=scope,
            validated_at=packet.completed_at.replace(year=2020),
        )
    flat_raw, flat_files, flat_chain, flat_scope, _ = await companion_fixture(
        monkeypatch, source_case="fresh_flat", current_only=True
    )
    replay(flat_raw, flat_files, flat_chain, flat_scope)
    flat_reference, flat_packet, _, _ = proof._source(
        flat_chain, flat_scope, proof_schema=proof.V3_SCHEMA
    )
    with pytest.raises(
        exposed.NativeExposedObservationError,
        match="native_exposure_not_observed",
    ):
        exposed.observe_recorded_native_v6_exposure(
            chain=flat_chain,
            reference=flat_reference,
            packet=flat_packet,
            scope=flat_scope,
            validated_at=flat_packet.completed_at,
        )


@pytest.mark.asyncio
async def test_read_only_runtime_branch_never_mints_flat_boundary(monkeypatch):
    _raw, _files, chain, scope, reference, packet = await recorded_exposure(monkeypatch)
    observation = exposed.observe_recorded_native_v6_exposure(
        chain=chain,
        reference=reference,
        packet=packet,
        scope=scope,
        validated_at=packet.completed_at,
    )
    session = ControlledDemoAccountSession(
        credentials=credentials(session_binding_id=packet.plan.session_binding_id),
        plan=packet.plan,
        expected_plan_sha256=capture.plan_sha256(packet.plan),
    )
    monkeypatch.setattr(runtime, "_configured_factory", lambda _factory: True)

    async def exposed_only(_stage, _session, _factory, _root, *, _exposure_sink):
        _exposure_sink.append(observation)
        raise proof.NativeAccountProofError(
            "native_account_current_sources_incomplete_or_exposed"
        )

    monkeypatch.setattr(runtime, "_capture_initial_current", exposed_only)
    result = await runtime.capture_native_exposed_account_observation(
        session, session_factory=object(), proof_root=Path.cwd()
    )
    assert result == observation
    assert session._used is True
    assert not runtime._CAPTURES
    assert not runtime._ORIGIN_LEASES
    assert not runtime._RAW_PACKET_LEASES
    with pytest.raises(proof.NativeAccountProofError):
        await runtime.capture_native_exposed_account_observation(
            session, session_factory=object(), proof_root=Path.cwd()
        )

    fresh_session = ControlledDemoAccountSession(
        credentials=credentials(session_binding_id=packet.plan.session_binding_id),
        plan=packet.plan,
        expected_plan_sha256=capture.plan_sha256(packet.plan),
    )

    async def no_observation(_stage, _session, _factory, _root, *, _exposure_sink):
        raise proof.NativeAccountProofError("native_account_source_expired")

    monkeypatch.setattr(runtime, "_capture_initial_current", no_observation)
    denied = await runtime.capture_native_exposed_account_observation(
        fresh_session, session_factory=object(), proof_root=Path.cwd()
    )
    assert json.loads(denied.receipt_json)["current_exposure_rows_observed"] is None
    assert denied.snapshot is None
    assert denied.flat_start_permission is denied.execution_authority is False
    assert denied.admission == "DENY"
