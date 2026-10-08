"""Source-owned current Demo components preserve unknown risk dependencies."""

import json

import pytest

from app.domain.source_primitives import canonical, sha, utc_from_ns
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_native_clock as native_clock
from app.trade_qualification import account_native_current_components as components
from app.trade_qualification import account_native_proof as proof
from app.trade_qualification import account_native_runtime as runtime
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification.account_runtime import ControlledDemoAccountSession
from tests.unit.test_account_native_proof import companion_fixture, replay
from tests.unit.test_qualification_account_collector import credentials


async def owned_components(monkeypatch, *, source_case="fresh_flat"):
    raw, files, chain, scope, samples = await companion_fixture(
        monkeypatch, source_case=source_case, current_v7=True
    )
    checked = replay(raw, files, chain, scope)
    reference, packet, _records, _joins = proof._source(
        chain, scope, proof_schema=proof.V7_FLAT_SCHEMA
    )
    session = ControlledDemoAccountSession(
        credentials=credentials(session_binding_id=packet.plan.session_binding_id),
        plan=packet.plan,
        expected_plan_sha256=capture.plan_sha256(packet.plan),
    )
    with (
        native_clock._claim_initial_session(session),
        native_clock._initial_stage(
            plan_sha256=session._pin,
            scope_sha256=proof.scope_sha256(scope),
            _claimed_session=session,
        ) as stage,
    ):
        state = native_clock._state(stage)
        state["closed"] = True
        state["session_claim"]["bootstrap_used"] = True
        issued = samples()
        receipt = canonical(
            {
                "schema_version": "ctcc.initial_native_account_diagnostic.v3",
                "policy_sha256": proof.V7_FLAT_POLICY_SHA256,
                "source_reference": observed.reference_document(reference),
                "proof_sha256": sha(raw),
                "proof_readback_sha256": "b" * 64,
                "current_native_source_observed": True,
                "native_sampled_hwm_verified": False,
                "observed_at": utc_from_ns(issued["utc_ns"]).isoformat(),
                "expires_at": checked.expires_at.isoformat(),
                "snapshot": None,
                "account_complete": False,
                "account_revision_published": False,
                "flat_start_permission": False,
                "execution_authority": False,
                "admission": "DENY",
            }
        )
        lease = runtime._mint_native_demo_raw_packet(
            stage,
            session,
            packet,
            reference,
            receipt_json=receipt,
            issued=issued,
            expires=checked.expires_at,
            deadline=checked.monotonic_deadline_ns,
        )
        diagnostic = runtime.InitialNativeAccountDiagnostic(receipt, None, lease)
        result = components.observe_native_demo_current_components(diagnostic, session)
        with pytest.raises(
            components.NativeCurrentComponentsError,
            match="native_current_components_unavailable",
        ):
            components.observe_native_demo_current_components(diagnostic, session)
    return result


@pytest.mark.asyncio
async def test_native_flat_observation_retains_unknown_risk_components(monkeypatch):
    result = await owned_components(monkeypatch)
    value = json.loads(result.receipt_json)
    assert value["current_inventory_observed_empty"] is True
    assert value["schema_version"] == "ctcc.native_demo_current_components.v2"
    assert len(value["current_inventory_row_counts"]) == 10
    assert all(count == 0 for count in value["current_inventory_row_counts"].values())
    assert value["current_balance"]["equity"] == {
        "numerator": "1000",
        "denominator": "1",
    }
    assert value["current_balance"]["available_equity"] == {
        "numerator": "800",
        "denominator": "1",
    }
    assert value["history_complete"] is None
    assert value["local_exposure_complete"] is None
    assert value["active_protection_complete"] is None
    assert "funding_accrual_unknown" in value["blocking_reasons"]
    assert "local_inflight_and_uncertain_unknown" in value["blocking_reasons"]
    assert value["current_inventory_page_receipts"]
    assert b"700001" not in result.receipt_json
    assert value["snapshot"] is result.snapshot is None
    assert value["account_complete"] is result.account_complete is False
    assert value["execution_authority"] is result.execution_authority is False
    assert value["admission"] == "DENY"


@pytest.mark.asyncio
async def test_native_exposure_stays_outside_current_proof_and_components(monkeypatch):
    # The existing native source proof only mints its lease for verified empty
    # current inventory. Existing exposure needs a separate read-only proof.
    with pytest.raises(
        proof.NativeAccountProofError,
        match="native_account_v7_inventory_incomplete",
    ):
        await owned_components(monkeypatch, source_case="exposed")


@pytest.mark.asyncio
async def test_revoked_four_algo_source_cannot_mint_native_components(monkeypatch):
    raw, files, chain, scope, _samples = await companion_fixture(
        monkeypatch, current_only=True, current_v7=False, verify_baseline=False
    )
    with pytest.raises(
        proof.NativeAccountProofError,
        match="native_account_current_sources_incomplete_or_exposed",
    ):
        replay(raw, files, chain, scope)


@pytest.mark.asyncio
async def test_caller_cannot_supply_fake_current_components(monkeypatch):
    raw, files, chain, scope, _samples = await companion_fixture(
        monkeypatch, current_v7=True
    )
    replay(raw, files, chain, scope)
    packet = proof._source(chain, scope, proof_schema=proof.V7_FLAT_SCHEMA)[1]
    session = ControlledDemoAccountSession(
        credentials=credentials(session_binding_id=packet.plan.session_binding_id),
        plan=packet.plan,
        expected_plan_sha256=capture.plan_sha256(packet.plan),
    )
    forged = runtime.InitialNativeAccountDiagnostic(
        canonical({"admission": "DENY"}), None, None
    )
    with pytest.raises(
        components.NativeCurrentComponentsError,
        match="native_current_components_unavailable",
    ):
        components.observe_native_demo_current_components(forged, session)
