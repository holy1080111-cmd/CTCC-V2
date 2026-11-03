"""Synthetic native wire/geometry only; no actual source or DB acceptance."""

import json
from datetime import timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.database.repositories.qualification_ledger import QualificationLedgerRepository
from app.domain.source_primitives import canonical, sha
from app.trade_qualification import native_ledger_contracts_v2 as native
from app.trade_qualification import reservations as ledger
from tests.unit.qualification_recheck_fixtures import recheck_source


@pytest.fixture(
    scope="module",
    params=[
        ("long", "net_mode"),
        ("short", "net_mode"),
        ("long", "long_short_mode"),
        ("short", "long_short_mode"),
    ],
)
def synthetic(request):
    direction, position_mode = request.param
    source = recheck_source(direction, scenario="next_boundary")
    pre = source.original_run
    deadline = min(
        pre.prefix.intent.expires_at,
        pre.prefix.detection.trigger.expires_at,
        pre.result.entry_zone.expires_at,
        pre.prefix.timing.latest_valid_entry_time,
    )
    scope = ledger.LedgerScope(account_id="123456789", settlement_currency="USDT")
    execution = {
        "schema_version": "ctcc.selected_isolated_execution.v1",
        "captured_execution_mode_sha256": "1" * 64,
        "position_mode": position_mode,
        "position_side": "net" if position_mode == "net_mode" else direction,
        "instrument_id": pre.risk_inputs.instrument.instrument_id,
        "margin_mode": "isolated",
        "leverage": str(pre.risk_inputs.requested_leverage),
        "raw_member_sha256": "2" * 64,
        "source_reference_sha256": "3" * 64,
        "execution_authority": False,
    }
    fixed = canonical(
        {
            "schema_version": "ctcc.native_original_candidate.v2",
            "scope": scope.model_dump(mode="json", round_trip=True),
            "result": pre.result.model_dump(mode="json", round_trip=True),
            "detection": pre.prefix.detection.model_dump(mode="json", round_trip=True),
            "original_event_key": source.original_event_key,
            "event_path_policy_sha256": native.EVENT_PATH_POLICY_SHA256,
            "original_policy_sha256": pre.policy_sha256,
            "credential_session_sha256": "c" * 64,
            "requested_contracts": str(pre.risk_inputs.requested_contracts),
            "requested_leverage": pre.risk_inputs.requested_leverage,
            "execution_mode": execution,
            "expires_at": deadline.isoformat(),
        }
    )
    sample = ledger.ScenarioOperands(
        entry=pre.result.candidate_entry,
        stop_loss=pre.result.stop_loss,
        cost_per_base=pre.economics.cost_per_base,
        contracts=pre.risk_inputs.requested_contracts,
        contract_value=pre.risk_inputs.instrument.contract_value,
        leverage=pre.risk_inputs.requested_leverage,
    )
    coverage = ledger.RiskCoverage(
        candidate=sample, execution=sample, **ledger.exact_coverage(sample, sample)
    )
    packet = canonical({"synthetic_data_only": True, "environment": "demo"})
    proof = canonical(
        {
            "schema_version": "ctcc.owned_native_r7_computation.v2",
            "event_path_policy_sha256": native.EVENT_PATH_POLICY_SHA256,
            "original_candidate_sha256": sha(fixed),
            "original_event_key": source.original_event_key,
            "original_policy_sha256": pre.policy_sha256,
            "g12_sha256": "a" * 64,
            "public_bundle_sha256": "b" * 64,
            "public_packet_sha256": sha(packet),
            "new_issuer_sha256": "d" * 64,
            "new_issued_row_sha256": "e" * 64,
            "new_account_revision": 3,
            "new_ledger_revision": 5,
            "execution_mode": execution,
            "execution_selection_sha256": sha(canonical(execution)),
            "required_event_path_complete": True,
            "continuous_tick_path_claimed": False,
            "execution_authority": False,
            "coverage": coverage.model_dump(mode="json", round_trip=True),
            "observed_at": source.latest_source.evaluated_at.isoformat(),
            "expires_at": deadline.isoformat(),
        }
    )
    request_value = native.NativeReservationRequestV4(
        contract_version=native.REQUEST_VERSION,
        scope=scope,
        origin=native.NativeOriginV2(
            contract_version="ctcc-native-recheck-origin-v2",
            original_candidate_json=fixed.decode(),
            original_candidate_sha256=sha(fixed),
            original_event_key=source.original_event_key,
            original_policy_sha256=pre.policy_sha256,
            evidence_sha256="a" * 64,
            report_sha256="a" * 64,
            claim_sha256="a" * 64,
            publication_sha256="a" * 64,
            publication_completed_at=source.barrier_completed_at,
            deadline=deadline,
        ),
        risk_inputs=native.NativeRiskInputsV2(
            requested_contracts=pre.risk_inputs.requested_contracts,
            requested_leverage=pre.risk_inputs.requested_leverage,
            instrument=pre.risk_inputs.instrument,
        ),
        risk_policy=pre.policy.portfolio,
        expected_account_revision=3,
        expected_ledger_revision=5,
        issuer_sha256="d" * 64,
        issued_row_sha256="e" * 64,
        credential_session_sha256="c" * 64,
        public_packet_json=packet.decode(),
        public_packet_sha256=sha(packet),
        public_custody_id="synthetic-data-only",
        public_bundle_sha256="b" * 64,
        r7_proof_json=proof.decode(),
        r7_proof_sha256=sha(proof),
        coverage=coverage,
    )
    return source, request_value


def native_binding(request):
    proof = json.loads(request.r7_proof_json)
    execution = proof["execution_mode"]
    return native.NativeExecutionBindingV2(
        contract_version="ctcc-native-execution-binding-v2",
        recheck_json=request.r7_proof_json,
        recheck_sha256=request.r7_proof_sha256,
        position_mode=execution["position_mode"],
        position_side=execution["position_side"],
        configured_isolated_leverage=Decimal(execution["leverage"]),
        execution_selection_sha256=proof["execution_selection_sha256"],
        captured_execution_mode_sha256=execution["captured_execution_mode_sha256"],
        leverage_member_sha256=execution["raw_member_sha256"],
        leverage_source_reference_sha256=execution["source_reference_sha256"],
    )


def test_native_wire_roundtrip_does_not_become_legacy_or_authority(synthetic):
    _, request = synthetic
    raw = ledger.canonical(request)
    assert ledger.decode_reservation_request(raw) == request
    assert not request.execution_authority and not request.source_authenticity_verified
    with pytest.raises(ValidationError):
        ledger.ReservationRequest.model_validate_json(raw, strict=True)


@pytest.mark.asyncio
async def test_caller_constructed_native_request_is_rejected_before_any_db(synthetic):
    _, request = synthetic
    calls = []

    def forbidden_factory():
        calls.append("database")
        raise AssertionError("No source owner exists")

    repository = QualificationLedgerRepository(forbidden_factory, clock=lambda: None)
    with pytest.raises(
        ledger.QualificationLedgerError, match="^native_ledger_owner_unavailable$"
    ):
        await repository._reserve(request)
    assert not calls


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["reserve", "consume"])
async def test_foreign_native_binding_is_denied_before_hash_or_database(
    synthetic, operation
):
    _, request = synthetic
    called = []

    class ForeignBinding:
        def __hash__(self):
            called.append("hash")
            raise AssertionError("Foreign registry keys must never execute")

    def forbidden_factory():
        called.append("database")
        raise AssertionError("No source owner exists")

    repository = QualificationLedgerRepository(forbidden_factory, clock=lambda: None)
    with pytest.raises(
        ledger.QualificationLedgerError, match="^native_ledger_owner_unavailable$"
    ):
        if operation == "reserve":
            await repository._reserve(request, _native_binding=ForeignBinding())
        else:
            await repository._transition(
                request.scope,
                request.origin.original_event_key,
                expected_revision=request.expected_ledger_revision,
                target="consumed",
                record_intent=True,
                _native_binding=ForeignBinding(),
            )
    assert not called


def test_native_intent_keeps_original_size_geometry_and_exact_fok(synthetic):
    source, request = synthetic
    at = source.latest_source.evaluated_at
    consumed = ledger.ReservationReceipt(
        scope=request.scope,
        reservation_id=ledger.reservation_id(
            request.scope, request.origin.original_event_key
        ),
        original_event_key=request.origin.original_event_key,
        report_id=request.origin.candidate.report_id,
        instrument_id=request.risk_inputs.instrument.instrument_id,
        direction=request.origin.candidate.direction,
        correlation_group=request.risk_inputs.instrument.correlation_group,
        request_sha256=ledger.digest(request),
        coverage=request.coverage,
        state="consumed",
        state_revision=2,
        account_revision=3,
        ledger_revision=7,
        created_at=at - timedelta(microseconds=1),
        updated_at=at,
        deadline=request.origin.deadline,
    )
    binding = native_binding(request)
    intent = native.build_native_intent(request, consumed, execution_binding=binding)
    parsed = json.loads(intent.canonical_json)
    order = parsed["exact_order"]
    assert order["ordType"] == "fok"
    assert order["sz"] == str(request.risk_inputs.requested_contracts)
    assert (
        order["posSide"]
        == json.loads(request.r7_proof_json)["execution_mode"]["position_side"]
    )
    assert order["px"] == str(request.coverage.execution.entry)
    assert order["attachAlgoOrds"][0]["slTriggerPx"] == str(
        request.origin.candidate.stop_loss
    )
    assert order["attachAlgoOrds"][0]["tpTriggerPx"] == str(
        request.origin.candidate.take_profit
    )
    protection_id = order["attachAlgoOrds"][0]["attachAlgoClOrdId"]
    assert (
        len(protection_id) == 32 and protection_id.isascii() and protection_id.isalnum()
    )
    assert protection_id != order["clOrdId"]
    assert parsed["protection_client_order_id"] == protection_id
    assert not intent.execution_authority and not intent.order_retry_authority
    assert native.replay_native_intent(
        intent.canonical_json, request, expected_sha256=intent.sha256
    ) == (intent, consumed)
    parsed["exact_order"]["ordType"] = "market"
    changed = json.dumps(
        parsed, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    with pytest.raises(ValueError, match="^native_intent_replay_mismatch$"):
        native.replay_native_intent(
            changed, request, expected_sha256=sha(changed.encode())
        )


@pytest.mark.parametrize(
    "field,value", [("requested_contracts", "0.1"), ("requested_leverage", 1)]
)
def test_new_size_cannot_repair_original_risk(synthetic, field, value):
    _, request = synthetic
    raw = json.loads(ledger.canonical(request))
    raw["risk_inputs"][field] = value
    with pytest.raises(ValidationError):
        native.NativeReservationRequestV4.model_validate_json(
            json.dumps(raw), strict=True
        )


def test_hidden_top_level_serializer_rejected_before_callback(synthetic):
    _, request = synthetic
    called = []
    dirty = request.model_copy(deep=True)
    dirty.__dict__["model_dump_json"] = lambda **kwargs: called.append("foreign")
    with pytest.raises(ValueError, match="^native_ledger_exact_tree_required$"):
        native.checked_request(dirty)
    assert not called


def test_hidden_nested_serializer_rejected_before_callback(synthetic):
    _, request = synthetic
    called = []
    dirty = request.model_copy(deep=True)
    dirty.risk_inputs.instrument.__dict__["model_dump"] = lambda **kwargs: (
        called.append("foreign")
    )
    with pytest.raises(ValueError, match="^native_ledger_exact_tree_required$"):
        native.checked_request(dirty)
    assert not called


def test_runtime_flags_cannot_be_copied_into_native_data_permission(synthetic):
    _, request = synthetic
    dirty = request.model_copy(update={"execution_authority": True})
    with pytest.raises(ValidationError):
        native.checked_request(dirty)


def test_original_expiry_cannot_be_renewed_in_the_request(synthetic):
    _, request = synthetic
    dirty = request.model_copy(deep=True)
    changed_origin = dirty.origin.model_copy(
        update={"deadline": dirty.origin.deadline + timedelta(days=1)}
    )
    dirty = dirty.model_copy(update={"origin": changed_origin})
    with pytest.raises(ValidationError):
        native.checked_request(dirty)


def test_original_event_identity_cannot_be_changed_by_rehashing_only_outer_data(
    synthetic,
):
    _, request = synthetic
    body = json.loads(ledger.canonical(request))
    body["origin"]["original_event_key"] = "f" * 64
    with pytest.raises(ValidationError):
        native.NativeReservationRequestV4.model_validate_json(
            json.dumps(body), strict=True
        )


@pytest.mark.parametrize(
    "name", ["position_side", "position_mode", "configured_isolated_leverage"]
)
def test_intent_cannot_repair_source_position_mode_or_leverage(synthetic, name):
    source, request = synthetic
    at = source.latest_source.evaluated_at
    consumed = ledger.ReservationReceipt(
        scope=request.scope,
        reservation_id=ledger.reservation_id(
            request.scope, request.origin.original_event_key
        ),
        original_event_key=request.origin.original_event_key,
        report_id=request.origin.candidate.report_id,
        instrument_id=request.risk_inputs.instrument.instrument_id,
        direction=request.origin.candidate.direction,
        correlation_group=request.risk_inputs.instrument.correlation_group,
        request_sha256=ledger.digest(request),
        coverage=request.coverage,
        state="consumed",
        state_revision=2,
        account_revision=3,
        ledger_revision=7,
        created_at=at - timedelta(microseconds=1),
        updated_at=at,
        deadline=request.origin.deadline,
    )
    binding = native_binding(request)
    changes = {
        "position_side": "short" if binding.position_side != "short" else "long",
        "position_mode": "net_mode"
        if binding.position_mode == "long_short_mode"
        else "long_short_mode",
        "configured_isolated_leverage": Decimal(
            124 if request.risk_inputs.requested_leverage != 124 else 123
        ),
    }
    changed = binding.model_copy(update={name: changes[name]})
    with pytest.raises(ValueError, match="^native_intent_reservation_binding_invalid$"):
        native.build_native_intent(request, consumed, execution_binding=changed)
