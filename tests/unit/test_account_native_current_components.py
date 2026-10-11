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
from tests.unit import test_account_native_proof as native_fixture
from tests.unit.test_account_native_proof import companion_fixture, replay
from tests.unit.test_qualification_account_capture import config
from tests.unit.test_qualification_account_collector import credentials


async def owned_components(
    monkeypatch, *, source_case="fresh_flat", version="v2", config_rows=None
):
    if config_rows is not None:
        original_flat_pages = native_fixture.flat_pages

        def configured_flat_pages():
            pages = original_flat_pages()
            for stream, changes in config_rows.items():
                pages[stream] = [[config(**changes)]]
            return pages

        monkeypatch.setattr(native_fixture, "flat_pages", configured_flat_pages)
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
        project = (
            components.observe_native_demo_current_components_v3
            if version == "v3"
            else components.observe_native_demo_current_components
        )
        result = project(diagnostic, session)
        with pytest.raises(
            components.NativeCurrentComponentsError,
            match="native_current_components_unavailable",
        ):
            project(diagnostic, session)
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
async def test_v3_binds_replayed_key_policy_to_one_use_native_source(monkeypatch):
    observed_v2 = []
    original_project = components._project

    def record_v2(source):
        value = original_project(source)
        observed_v2.append(value.receipt_json)
        return value

    monkeypatch.setattr(components, "_project", record_v2)
    result = await owned_components(
        monkeypatch,
        version="v3",
        config_rows={
            "config_before": {"perm": "read_only,trade", "ip": "8.8.8.8"},
            "config_after": {"perm": "read_only,trade", "ip": "8.8.8.8"},
        },
    )
    value = json.loads(result.receipt_json)
    policy = value["api_key_policy"]
    assert value["schema_version"] == "ctcc.native_demo_current_components.v3"
    assert value["policy_sha256"] == components.V7_KEY_POLICY_SHA256
    assert len(observed_v2) == 1
    assert json.loads(observed_v2[0])["schema_version"] == (
        "ctcc.native_demo_current_components.v2"
    )
    assert value["base_v2_receipt_sha256"] == sha(observed_v2[0])
    assert policy["permissions_observed"] is True
    assert policy["ip_binding_list_observed"] is True
    assert policy["config_policy_consistent"] is True
    assert policy["blocking_reasons"] == []
    assert policy["registration_region_verified"] is False
    assert policy["provider_credential_session_verified"] is False
    assert policy["current_egress_ip_verified"] is False
    assert value["source_authenticity_verified"] is False
    assert value["snapshot"] is result.snapshot is None
    assert value["account_complete"] is result.account_complete is False
    assert value["execution_authority"] is result.execution_authority is False
    assert value["admission"] == "DENY"
    for private in (b"700001", b"8.8.8.8", b"read_only,trade"):
        assert private not in result.receipt_json


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({}, "api_key_ip_binding_missing_or_malformed"),
        (
            {"perm": "read_only,trade,withdraw", "ip": "8.8.8.8"},
            "api_key_withdraw_permission_enabled",
        ),
        (
            {"perm": "read_only,trade", "ip": ""},
            "api_key_ip_binding_missing_or_malformed",
        ),
    ],
)
async def test_v3_native_key_policy_keeps_unsafe_config_as_blocker(
    monkeypatch, changes, reason
):
    result = await owned_components(
        monkeypatch,
        version="v3",
        config_rows={"config_before": changes, "config_after": changes},
    )
    value = json.loads(result.receipt_json)
    assert reason in value["blocking_reasons"]
    assert value["api_key_policy"]["config_policy_consistent"] is False
    assert value["snapshot"] is None
    assert value["admission"] == "DENY"


@pytest.mark.asyncio
async def test_changed_original_config_cannot_mint_native_flat_lease(monkeypatch):
    # The original packet and redacted key-policy readout remain replayable,
    # but the stronger current-source check rejects a flat native proof first.
    with pytest.raises(
        proof.NativeAccountProofError,
        match="native_account_current_sources_incomplete_or_exposed",
    ):
        await owned_components(
            monkeypatch,
            version="v3",
            config_rows={
                "config_before": {"perm": "read_only,trade", "ip": "8.8.8.8"},
                "config_after": {
                    "perm": "read_only,trade",
                    "ip": "1.1.1.1",
                    "label": "changed",
                },
            },
        )


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
