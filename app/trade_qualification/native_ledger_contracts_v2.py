"""Explicit native V2 DB0017 request/intent data; never ownership or permission.

No V2 quote/funding/finite financial snapshot is cast to a legacy contract.
Native requests use the existing reservation row, exact RiskCoverage and
control-bound transition journal. Only the live native R7 repository entry can
admit one; decoding this wire format remains historical audit only.
"""

import json
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.domain.source_primitives import canonical as raw_canonical
from app.domain.source_primitives import decode as raw_decode
from app.domain.source_primitives import sha
from app.trade_qualification import (
    owned_current_original_prefix_v1 as _owned_family_results_v2,
)
from app.trade_qualification import reservations as ledger
from app.trade_qualification.event_models import TriggerDetection
from app.trade_qualification.fixed_original_event_path_v1 import (
    POLICY_SHA256 as EVENT_PATH_POLICY_SHA256,
)
from app.trade_qualification.models import EntryQualificationResult
from app.trade_qualification.portfolio import ContractRiskSpec, PortfolioRiskPolicy
from app.trade_qualification.timing import event_identity

REQUEST_VERSION = "ctcc-reservation-request-native-v4"
INTENT_VERSION = "ctcc-demo-submit-intent-native-v2"
MAX_NATIVE_BYTES = 8 * 1024 * 1024


class NativeOriginV2(ledger.LedgerModel):
    contract_version: Literal["ctcc-native-recheck-origin-v2"]
    original_candidate_json: str = Field(min_length=1, max_length=MAX_NATIVE_BYTES)
    original_candidate_sha256: ledger.Digest
    original_event_key: ledger.Digest
    original_policy_sha256: ledger.Digest
    evidence_sha256: ledger.Digest
    report_sha256: ledger.Digest
    claim_sha256: ledger.Digest
    publication_sha256: ledger.Digest
    publication_completed_at: datetime
    deadline: datetime

    @property
    def candidate(self):
        raw = raw_decode(self.original_candidate_json.encode(), MAX_NATIVE_BYTES)
        document = raw["result"]
        if (
            type(document) is dict
            and document.get("strategy")
            in _owned_family_results_v2._OWNED_HISTORY_FAMILIES_V2
        ):
            return _owned_family_results_v2.decode_owned_family_result_v2(document)
        return EntryQualificationResult.model_validate_json(
            raw_canonical(document), strict=True
        )

    @model_validator(mode="after")
    def fixed_original(self):
        raw = raw_decode(self.original_candidate_json.encode(), MAX_NATIVE_BYTES)
        detection = TriggerDetection.model_validate_json(
            raw_canonical(raw["detection"]), strict=True
        )
        candidate = self.candidate
        if (
            raw_canonical(raw).decode() != self.original_candidate_json
            or sha(self.original_candidate_json.encode())
            != self.original_candidate_sha256
            or raw["schema_version"] != "ctcc.native_original_candidate.v2"
            or raw["original_policy_sha256"] != self.original_policy_sha256
            or raw["original_event_key"] != self.original_event_key
            or raw["event_path_policy_sha256"] != EVENT_PATH_POLICY_SHA256
            or event_identity(detection) != self.original_event_key
            or candidate.trigger != detection.trigger
            or candidate.entry_zone.source_sha256 != detection.source_sha256
            or not self.publication_completed_at < self.deadline
            or self.deadline > datetime.fromisoformat(raw["expires_at"])
            or self.deadline
            > min(candidate.trigger.expires_at, candidate.entry_zone.expires_at)
            or len(self.candidate.gates) != 11
            or not all(item.passed for item in self.candidate.gates)
        ):
            raise ValueError("native_origin_original_binding_invalid")
        return self


class NativeRiskInputsV2(ledger.LedgerModel):
    requested_contracts: ledger.Price
    requested_leverage: int = Field(ge=1, le=125)
    instrument: ContractRiskSpec


class NativeReservationRequestV4(ledger.LedgerModel):
    contract_version: Literal["ctcc-reservation-request-native-v4"]
    scope: ledger.LedgerScope
    origin: NativeOriginV2
    risk_inputs: NativeRiskInputsV2
    risk_policy: PortfolioRiskPolicy
    expected_account_revision: ledger.Revision
    expected_ledger_revision: ledger.Revision
    issuer_sha256: ledger.Digest
    issued_row_sha256: ledger.Digest
    credential_session_sha256: ledger.Digest
    public_packet_json: str = Field(min_length=1, max_length=MAX_NATIVE_BYTES)
    public_packet_sha256: ledger.Digest
    public_custody_id: str = Field(min_length=1, max_length=64)
    public_bundle_sha256: ledger.Digest
    r7_proof_json: str = Field(min_length=1, max_length=262144)
    r7_proof_sha256: ledger.Digest
    coverage: ledger.RiskCoverage

    @model_validator(mode="after")
    def source_binding(self):
        proof = raw_decode(self.r7_proof_json.encode(), 262144)
        original = raw_decode(
            self.origin.original_candidate_json.encode(), MAX_NATIVE_BYTES
        )
        execution, prior_execution = proof["execution_mode"], original["execution_mode"]
        if (
            sha(self.public_packet_json.encode()) != self.public_packet_sha256
            or raw_canonical(
                raw_decode(self.public_packet_json.encode(), MAX_NATIVE_BYTES)
            ).decode()
            != self.public_packet_json
            or sha(self.r7_proof_json.encode()) != self.r7_proof_sha256
            or raw_canonical(proof).decode() != self.r7_proof_json
            or proof["schema_version"] != "ctcc.owned_native_r7_computation.v2"
            or proof["original_candidate_sha256"]
            != self.origin.original_candidate_sha256
            or proof["original_event_key"] != self.origin.original_event_key
            or proof["original_policy_sha256"] != self.origin.original_policy_sha256
            or proof["event_path_policy_sha256"] != EVENT_PATH_POLICY_SHA256
            or proof["g12_sha256"] != self.origin.evidence_sha256
            or proof["public_bundle_sha256"] != self.public_bundle_sha256
            or proof["public_packet_sha256"] != self.public_packet_sha256
            or proof["new_issuer_sha256"] != self.issuer_sha256
            or proof["new_issued_row_sha256"] != self.issued_row_sha256
            or proof["new_account_revision"] != self.expected_account_revision
            or proof["new_ledger_revision"] != self.expected_ledger_revision
            or original["scope"] != self.scope.model_dump(mode="json", round_trip=True)
            or original["credential_session_sha256"] != self.credential_session_sha256
            or proof["required_event_path_complete"] is not True
            or proof["continuous_tick_path_claimed"] is not False
            or proof["execution_authority"] is not False
            or self.risk_inputs.requested_contracts
            != Decimal(original["requested_contracts"])
            or self.risk_inputs.requested_leverage != original["requested_leverage"]
            or self.coverage.model_dump(mode="json", round_trip=True)
            != proof["coverage"]
            or datetime.fromisoformat(proof["expires_at"]) != self.origin.deadline
            or sha(raw_canonical(execution)) != proof["execution_selection_sha256"]
            or execution["schema_version"] != "ctcc.selected_isolated_execution.v1"
            or execution["execution_authority"] is not False
            or prior_execution["execution_authority"] is not False
            or execution["position_mode"] not in {"net_mode", "long_short_mode"}
            or execution["position_side"]
            != (
                "net"
                if execution["position_mode"] == "net_mode"
                else self.origin.candidate.direction
            )
            or execution["margin_mode"] != "isolated"
            or execution["instrument_id"] != self.risk_inputs.instrument.instrument_id
            or Decimal(execution["leverage"]) != self.risk_inputs.requested_leverage
            or any(
                execution[name] != prior_execution[name]
                for name in (
                    "position_mode",
                    "position_side",
                    "margin_mode",
                    "instrument_id",
                )
            )
            or Decimal(execution["leverage"]) != Decimal(prior_execution["leverage"])
        ):
            raise ValueError("native_reservation_source_binding_invalid")
        return self


class NativeExecutionBindingV2(ledger.LedgerModel):
    contract_version: Literal["ctcc-native-execution-binding-v2"]
    recheck_json: str = Field(min_length=1, max_length=262144)
    recheck_sha256: ledger.Digest
    position_mode: Literal["net_mode", "long_short_mode"]
    position_side: Literal["net", "long", "short"]
    configured_isolated_leverage: Decimal = Field(ge=1, le=125)
    execution_selection_sha256: ledger.Digest
    captured_execution_mode_sha256: ledger.Digest
    leverage_member_sha256: ledger.Digest
    leverage_source_reference_sha256: ledger.Digest


def is_native_request(value):
    return type(value) is NativeReservationRequestV4


def _guard(value, depth=0, budget=None):
    if budget is None:
        budget = [300000, 3 * MAX_NATIVE_BYTES]
    budget[0] -= 1
    if depth > 32 or budget[0] < 0:
        raise ValueError("native_ledger_tree_unbounded")
    if isinstance(value, BaseModel):
        if (
            type(value)
            not in {
                NativeOriginV2,
                NativeRiskInputsV2,
                NativeReservationRequestV4,
                NativeExecutionBindingV2,
                ledger.LedgerScope,
                ledger.RiskCoverage,
                ledger.ScenarioOperands,
                ContractRiskSpec,
                PortfolioRiskPolicy,
            }
            or set(value.__dict__) != set(type(value).model_fields)
            or value.__pydantic_extra__
        ):
            raise ValueError("native_ledger_exact_tree_required")
        for item in value.__dict__.values():
            _guard(item, depth + 1, budget)
    elif type(value) is str:
        budget[1] -= len(value.encode())
        if budget[1] < 0:
            raise ValueError("native_ledger_bytes_unbounded")
    elif type(value) is Decimal:
        if (
            not value.is_finite()
            or len(value.as_tuple().digits) > 128
            or abs(value.as_tuple().exponent) > 128
        ):
            raise ValueError("native_ledger_decimal_unbounded")
    elif type(value) is datetime:
        ledger.require_aware(value)
    elif value is None or type(value) in (bool, int):
        if type(value) is int and abs(value) > 10**40:
            raise ValueError("native_ledger_integer_unbounded")
    elif isinstance(value, Enum):
        _guard(value.value, depth + 1, budget)
    else:
        raise ValueError("native_ledger_exact_scalar_required")


def checked_request(value):
    if not is_native_request(value):
        raise ValueError("native_ledger_exact_request_required")
    _guard(value)
    return NativeReservationRequestV4.model_validate_json(
        value.model_dump_json(round_trip=True), strict=True
    )


def canonical_request(value):
    checked = checked_request(value)
    raw = json.dumps(
        checked.model_dump(mode="json", round_trip=True),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    if len(raw.encode()) > MAX_NATIVE_BYTES:
        raise ValueError("native_ledger_request_unbounded")
    return raw


def build_native_intent(request, consumed, *, execution_binding):
    from app.trade_qualification.submission_intent import SubmissionIntentRecord

    request = checked_request(request)
    consumed = ledger.checked(consumed, ledger.ReservationReceipt)
    if type(execution_binding) is not NativeExecutionBindingV2:
        raise ValueError("native_intent_exact_binding_required")
    _guard(execution_binding)
    proof = raw_decode(execution_binding.recheck_json.encode(), 262144)
    execution = proof["execution_mode"]
    if (
        sha(execution_binding.recheck_json.encode()) != execution_binding.recheck_sha256
        or proof["original_candidate_sha256"]
        != request.origin.original_candidate_sha256
        or proof["new_issuer_sha256"] != request.issuer_sha256
        or proof["new_issued_row_sha256"] != request.issued_row_sha256
        or consumed.request_sha256 != sha(canonical_request(request).encode())
        or consumed.state != "consumed"
        or consumed.state_revision != 2
        or consumed.scope != request.scope
        or consumed.account_revision != request.expected_account_revision
        or consumed.ledger_revision != request.expected_ledger_revision + 2
        or consumed.original_event_key != request.origin.original_event_key
        or consumed.coverage != request.coverage
        or not request.origin.publication_completed_at
        <= consumed.created_at
        <= consumed.updated_at
        < request.origin.deadline
        or execution_binding.position_mode != execution["position_mode"]
        or execution_binding.position_side != execution["position_side"]
        or execution_binding.position_side
        != (
            "net"
            if execution_binding.position_mode == "net_mode"
            else request.origin.candidate.direction
        )
        or execution_binding.configured_isolated_leverage
        != Decimal(execution["leverage"])
        or execution_binding.configured_isolated_leverage
        != request.risk_inputs.requested_leverage
        or execution_binding.execution_selection_sha256
        != proof["execution_selection_sha256"]
        or execution_binding.execution_selection_sha256 != sha(raw_canonical(execution))
        or execution_binding.captured_execution_mode_sha256
        != execution["captured_execution_mode_sha256"]
        or execution_binding.leverage_member_sha256 != execution["raw_member_sha256"]
        or execution_binding.leverage_source_reference_sha256
        != execution["source_reference_sha256"]
    ):
        raise ValueError("native_intent_reservation_binding_invalid")
    candidate = request.origin.candidate
    protection_id = "CTA" + consumed.reservation_id[:29]
    order = {
        "instId": request.risk_inputs.instrument.instrument_id,
        "tdMode": "isolated",
        "side": "buy" if candidate.direction == "long" else "sell",
        "posSide": execution_binding.position_side,
        "ordType": "fok",
        "px": str(request.coverage.execution.entry),
        "sz": str(request.risk_inputs.requested_contracts),
        "reduceOnly": False,
        "clOrdId": "ctcc" + consumed.reservation_id[:28],
        "attachAlgoOrds": [
            {
                "attachAlgoClOrdId": protection_id,
                "slTriggerPx": str(candidate.stop_loss),
                "slOrdPx": "-1",
                "tpTriggerPx": str(candidate.take_profit),
                "tpOrdPx": "-1",
                "slTriggerPxType": "mark",
                "tpTriggerPxType": "mark",
            }
        ],
    }
    body = {
        "version": INTENT_VERSION,
        "request_sha256": consumed.request_sha256,
        "reservation_receipt": consumed.model_dump(mode="json", round_trip=True),
        "credential_session_sha256": request.credential_session_sha256,
        "issuer_sha256": request.issuer_sha256,
        "issued_row_sha256": request.issued_row_sha256,
        "g12_claim_sha256": request.origin.claim_sha256,
        "g12_publication_sha256": request.origin.publication_sha256,
        "protection_client_order_id": protection_id,
        "execution_binding": execution_binding.model_dump(mode="json", round_trip=True),
        "exact_order": order,
        "execution_authority": False,
        "order_retry_authority": False,
    }
    raw = json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )
    if len(raw.encode()) > MAX_NATIVE_BYTES:
        raise ValueError("native_intent_unbounded")
    return SubmissionIntentRecord(raw, sha(raw.encode()))


def replay_native_intent(raw, request, *, expected_sha256):
    body = raw_decode(raw.encode(), MAX_NATIVE_BYTES)
    consumed = ledger.decode(
        json.dumps(
            body["reservation_receipt"],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ),
        ledger.ReservationReceipt,
    )
    binding = NativeExecutionBindingV2.model_validate_json(
        raw_canonical(body["execution_binding"]), strict=True
    )
    replayed = build_native_intent(request, consumed, execution_binding=binding)
    if replayed.canonical_json != raw or replayed.sha256 != expected_sha256:
        raise ValueError("native_intent_replay_mismatch")
    return replayed, consumed
