"""Synthetic V7 runtime binding; no real account, TLS session, or order writes."""

import asyncio
import json
from pathlib import Path

import pytest

from app.domain.source_primitives import canonical, sha, utc_from_ns
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_native_clock as native_clock
from app.trade_qualification import account_native_current_components as components
from app.trade_qualification import account_native_proof as proof
from app.trade_qualification import account_native_runtime as runtime
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification import demo_public_origin as demo_origin
from app.trade_qualification import public_source_runtime as public_runtime
from app.trade_qualification.account_runtime import ControlledDemoAccountSession
from tests.unit.test_account_current_history_join import current_plan
from tests.unit.test_account_current_source_v7 import current_plan_v7
from tests.unit.test_account_current_source_verifier import flat_pages
from tests.unit.test_account_native_clock import Samples
from tests.unit.test_account_native_proof import companion_fixture, replay
from tests.unit.test_qualification_account_capture import BARRIER
from tests.unit.test_qualification_account_collector import credentials
from tests.unit.test_qualification_account_v4 import records as packet_records


async def prepared_v7(monkeypatch):
    raw, files, chain, scope, samples = await companion_fixture(
        monkeypatch, current_v7=True
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
    return raw, checked, reference, packet, session, scope, samples


def diagnostic_receipt(raw, checked, reference, issued, *, readback_sha256="b" * 64):
    return canonical(
        {
            "schema_version": "ctcc.initial_native_account_diagnostic.v3",
            "policy_sha256": proof.V7_FLAT_POLICY_SHA256,
            "source_reference": observed.reference_document(reference),
            "proof_sha256": sha(raw),
            "proof_readback_sha256": readback_sha256,
            "current_source_receipt_sha256": sha(checked.current_source_receipt_json),
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


@pytest.mark.asyncio
async def test_v7_diagnostic_raw_lease_is_one_use_and_origin_stays_unapproved(
    monkeypatch,
):
    raw, checked, reference, packet, session, scope, samples = await prepared_v7(
        monkeypatch
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
        receipt = diagnostic_receipt(raw, checked, reference, issued)
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
        value = json.loads(diagnostic.receipt_json)
        assert value["schema_version"] == "ctcc.initial_native_account_diagnostic.v3"
        assert value["policy_sha256"] == proof.V7_FLAT_POLICY_SHA256
        assert value["account_complete"] is value["execution_authority"] is False
        assert value["admission"] == "DENY"
        result = components.observe_native_demo_current_components(diagnostic, session)
        projected = json.loads(result.receipt_json)
        assert projected["schema_version"] == "ctcc.native_demo_current_components.v2"
        assert set(projected["current_inventory_row_counts"]) == {
            "positions",
            "orders_pending",
            *(f"algo_{kind}" for kind in capture.CURRENT_ALGO_ORDER_TYPES_V7),
        }
        assert projected["current_inventory_observed_empty"] is True
        assert (
            projected["account_complete"] is projected["execution_authority"] is False
        )
        assert projected["admission"] == "DENY"
        with pytest.raises(components.NativeCurrentComponentsError):
            components.observe_native_demo_current_components(diagnostic, session)
        with pytest.raises(runtime.NativeAccountOriginError):
            runtime._consume_demo_account_origin(diagnostic, session)


@pytest.mark.asyncio
async def test_v7_source_owned_origin_is_one_use_and_cannot_authorize_public_route(
    monkeypatch,
):
    raw, files, chain, scope, samples = await companion_fixture(
        monkeypatch, current_v7=True
    )
    checked = replay(raw, files, chain, scope)
    reference, packet, _records, joins = proof._source(
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
        receipt = diagnostic_receipt(raw, checked, reference, issued)
        for field, value in (
            ("schema_version", "ctcc.initial_native_account_diagnostic.v2"),
            ("policy_sha256", proof.V3_POLICY_SHA256),
        ):
            wrong = canonical({**json.loads(receipt), field: value})
            with pytest.raises(runtime.NativeAccountOriginError):
                runtime._mint_demo_account_origin(
                    stage,
                    session,
                    packet,
                    reference,
                    joins,
                    receipt_json=wrong,
                    proof_sha256=sha(raw),
                    readback_sha256="b" * 64,
                    issued=issued,
                    expires=checked.expires_at,
                    deadline=checked.monotonic_deadline_ns,
                )
        assert not runtime._ORIGIN_LEASES
        lease = runtime._mint_demo_account_origin(
            stage,
            session,
            packet,
            reference,
            joins,
            receipt_json=receipt,
            proof_sha256=sha(raw),
            readback_sha256="b" * 64,
            issued=issued,
            expires=checked.expires_at,
            deadline=checked.monotonic_deadline_ns,
        )
        diagnostic = runtime.InitialNativeAccountDiagnostic(receipt, lease)
        origin = runtime._consume_demo_account_origin(diagnostic, session)
        assert origin.private_origin == packet.plan.origin
        assert origin.account_plan_sha256 == session._pin
        assert origin.account_packet_sha256 == reference.packet_sha256
        assert origin.native_proof_sha256 == sha(raw)
        assert origin.signed_account_config_observed is True
        assert origin.registration_region_verified is False
        assert origin.public_source_authenticity_verified is False
        assert origin.execution_authority is False and origin.admission == "DENY"
        with pytest.raises(runtime.NativeAccountOriginError):
            runtime._consume_demo_account_origin(diagnostic, session)
        route = demo_origin.reviewed_demo_public_route(packet.plan.registration_region)
        with pytest.raises(
            public_runtime.PublicSourceRuntimeError,
            match="trusted_demo_public_origin_profile_unavailable",
        ):
            public_runtime._require_trusted_v2_demo_origin_profile(
                {
                    "environment": "demo",
                    "registration_region": route.registration_region,
                    "rest_origin": route.rest_origin,
                    "ws_origin": route.ws_origin,
                }
            )
        assert not runtime._ORIGIN_LEASES


@pytest.mark.asyncio
async def test_v7_runtime_mint_rejects_missing_algo_wrong_policy_and_v6_packet(
    monkeypatch,
):
    raw, checked, reference, packet, session, scope, samples = await prepared_v7(
        monkeypatch
    )
    selected_v6, observations_v6 = packet_records(
        selected=current_plan(), pages=flat_pages(), streams=capture.V6_CURRENT_STREAMS
    )
    packet_v6 = capture.verify_demo_account_records(
        observations_v6,
        plan=selected_v6,
        expected_plan_sha256=capture.plan_sha256(selected_v6),
        barrier_completed_at=BARRIER,
    )
    missing_algo = packet.model_copy(
        update={
            "observations": tuple(
                page
                for page in packet.observations
                if page.request.stream != "algo_smart_iceberg"
            )
        }
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
        receipt = diagnostic_receipt(raw, checked, reference, issued)
        wrong_policy = canonical(
            {**json.loads(receipt), "policy_sha256": proof.V3_POLICY_SHA256}
        )
        for candidate, body in (
            (packet, wrong_policy),
            (missing_algo, receipt),
            (packet_v6, receipt),
        ):
            with pytest.raises(runtime.NativeAccountRawPacketError):
                runtime._mint_native_demo_raw_packet(
                    stage,
                    session,
                    candidate,
                    reference,
                    receipt_json=body,
                    issued=issued,
                    expires=checked.expires_at,
                    deadline=checked.monotonic_deadline_ns,
                )
        assert not runtime._RAW_PACKET_LEASES


@pytest.mark.asyncio
async def test_v7_foreign_task_burns_one_use_raw_packet_lease(monkeypatch):
    raw, checked, reference, packet, session, scope, samples = await prepared_v7(
        monkeypatch
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
        receipt = diagnostic_receipt(raw, checked, reference, issued)
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

        async def foreign():
            return components.observe_native_demo_current_components(
                diagnostic, session
            )

        with pytest.raises(components.NativeCurrentComponentsError):
            await asyncio.create_task(foreign())
        with pytest.raises(components.NativeCurrentComponentsError):
            components.observe_native_demo_current_components(diagnostic, session)
        assert not runtime._RAW_PACKET_LEASES


@pytest.mark.asyncio
async def test_v7_failure_diagnostic_is_denied_and_cannot_supply_components(
    monkeypatch,
):
    selected = current_plan_v7()
    session = ControlledDemoAccountSession(
        credentials=credentials(session_binding_id=selected.session_binding_id),
        plan=selected,
        expected_plan_sha256=capture.plan_sha256(selected),
    )
    samples = Samples()
    monkeypatch.setattr(native_clock.clock, "native_stamp", samples)
    monkeypatch.setattr(runtime, "_configured_factory", lambda _factory: True)

    async def fail_before_source(*_args, **_kwargs):
        raise RuntimeError("synthetic source unavailable")

    monkeypatch.setattr(runtime, "_capture_initial_current", fail_before_source)
    diagnostic = await runtime.capture_initial_native_account(
        session, session_factory=object(), proof_root=Path.cwd()
    )
    value = json.loads(diagnostic.receipt_json)
    assert value["schema_version"] == "ctcc.initial_native_account_diagnostic.v3"
    assert value["policy_sha256"] == proof.V7_FLAT_POLICY_SHA256
    assert value["code"] == "native_account_initial_denied"
    assert value["current_native_source_observed"] is False
    assert value["account_complete"] is value["execution_authority"] is False
    assert value["admission"] == "DENY"
    assert diagnostic._origin_lease is diagnostic._raw_packet_lease is None
    assert session._used is True
    with pytest.raises(components.NativeCurrentComponentsError):
        components.observe_native_demo_current_components(diagnostic, session)
    assert not runtime._RAW_PACKET_LEASES
