"""DB0019 control bindings inside DB0017 journals; never a runtime issuer.

Expectations compare a memory-only owner token with current durable control.
They do not authenticate source/account/config inputs or authorize dispatch.
Existing reservation and v1/v2 intent bytes are embedded unchanged.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime

from app.trade_qualification import account_capture
from app.trade_qualification import demo_control as control
from app.trade_qualification import reservations as ledger
from app.trade_qualification.submission_intent import (
    MAX_V2_INTENT_BYTES,
    VERSION_V2,
    SubmissionIntentRecord,
    replay_submission_intent,
)

RESERVED_VERSION = "ctcc-control-bound-reservation-v1"
INTENT_VERSION = "ctcc-control-bound-submit-intent-v1"
RESERVED_REASON = "risk_reserved_control_bound_v1"
CONSUMED_REASON = "consumed_with_control_bound_intent_v1"
MAX_RESERVED_BYTES = 65536


def _deny(code):
    raise ledger.QualificationLedgerError(code)


@dataclass(frozen=True, slots=True, repr=False)
class ControlExpectation:
    """Private comparison input, not a bearer permit or serializable journal."""

    scope: control.ControlScope
    owner_token: bytes = field(repr=False)
    owner_epoch: int
    control_revision: int
    event_sha256: str
    pins: control.ControlPins

    def __post_init__(self):
        control.checked(self.scope, control.ControlScope)
        control.checked(self.pins, control.ControlPins)
        control.owner_binding(self.owner_token)
        control.sha(self.event_sha256)
        if (
            type(self.owner_epoch) is not int
            or type(self.control_revision) is not int
            or not 1 <= self.owner_epoch <= self.control_revision < 2**63
        ):
            _deny("bound_control_expectation_invalid")

    def __reduce_ex__(self, _protocol):
        _deny("bound_control_expectation_not_serializable")


def freeze_expectation(value):
    if type(value) is not ControlExpectation:
        _deny("bound_control_exact_expectation_required")
    value.__post_init__()
    return ControlExpectation(
        control.ControlScope(value.scope.environment, value.scope.account_id),
        value.owner_token,
        value.owner_epoch,
        value.control_revision,
        value.event_sha256,
        control.ControlPins(
            value.pins.config_sha256,
            value.pins.policy_sha256,
            value.pins.credential_session_sha256,
        ),
    )


def _packet_session_sha256(packet_json, packet_sha256, plan_sha256):
    packet = account_capture.verify_demo_account_packet(
        packet_json.encode("utf-8"),
        expected_sha256=packet_sha256,
        expected_plan_sha256=plan_sha256,
    )
    # Same ASCII identifier hashing contract as the B1 acquisition journal.
    # This joins identities only; replay/DTO/hash cannot authenticate a source.
    return control.digest(packet.plan.session_binding_id)


def _guard_request_session(request, state, *, now):
    if type(request) is ledger.ReservationRequestV2:
        _deny("bound_control_account_session_binding_missing")
    if type(request) is ledger.ReservationRequestV3:
        binding = request.replay_binding
        try:
            packet = account_capture.verify_demo_account_packet(
                binding.account_packet_json.encode("utf-8"),
                expected_sha256=binding.account_packet_sha256,
                expected_plan_sha256=binding.account_plan_sha256,
            )
        except (ValueError, TypeError, RecursionError):
            _deny("bound_control_account_session_packet_invalid")
        if (
            packet.plan.expected_uid != request.scope.account_id
            or packet.plan.settlement_currency != request.scope.settlement_currency
            or state.pins.credential_session_sha256
            != control.digest(packet.plan.session_binding_id)
        ):
            _deny("bound_control_account_session_conflict")
        # The packet's own page-chain replay proves each request starts after
        # its barrier. Bind that barrier to THIS G12 before a durable event hold
        # is written; intent-time checking alone would leave a stale hold.
        if (
            packet.barrier_completed_at != request.origin.publication_completed_at
            or packet.completed_at > now
        ):
            _deny("bound_control_account_packet_causality_invalid")


def guard_current_control(
    state, event_sha256, expectation, scope, *, now, request=None
):
    """Validate a row read under the caller's UID transaction; no IO/permit."""
    expectation = freeze_expectation(expectation)
    control.checked(state, control.ControlState)
    control.sha(event_sha256)
    control.utc(now)
    scope = ledger.checked(scope, ledger.LedgerScope)
    if (
        (state.scope.environment, state.scope.account_id)
        != (scope.environment, scope.account_id)
        or state.scope != expectation.scope
        or state.owner_sha256 != control.owner_binding(expectation.owner_token)
        or state.owner_epoch != expectation.owner_epoch
        or state.revision != expectation.control_revision
        or event_sha256 != expectation.event_sha256
        or state.pins != expectation.pins
    ):
        _deny("bound_control_identity_or_revision_conflict")
    if not state.arm_intent_current(now):
        _deny("bound_control_stopped_disarmed_or_expired")
    if request is not None:
        if (
            request.scope != scope
            or state.pins.policy_sha256 != request.origin.original_policy_sha256
        ):
            _deny("bound_control_policy_conflict")
        if not request.origin.publication_completed_at < now < request.origin.deadline:
            _deny("bound_control_candidate_expired")
        _guard_request_session(request, state, now=now)
    return state


def _json(value, maximum=MAX_V2_INTENT_BYTES):
    raw = control.canonical(value)
    if len(raw.encode("utf-8")) > maximum:
        _deny("bound_control_evidence_too_large")
    return raw


def _document(raw, *, maximum=MAX_V2_INTENT_BYTES):
    if type(raw) is not str or not 0 < len(raw.encode("utf-8")) <= maximum:
        _deny("bound_control_evidence_invalid")
    try:
        value = json.loads(raw)
        if type(value) is not dict or _json(value, maximum) != raw:
            raise ValueError
        return value
    except (ValueError, TypeError, RecursionError):
        _deny("bound_control_evidence_invalid")


def _no_authority():
    return {
        "admission": "DENY",
        "reason": "trusted_runtime_producers_unavailable",
        "execution_authority": False,
        "order_retry_authority": False,
        "owned_policy_verified": False,
        "account_complete": False,
        "source_authenticity_verified": False,
    }


def _state_for_request(raw, event_sha256, request, at):
    state = control.decode(raw)
    control.sha(event_sha256)
    control.utc(at)
    if (
        (state.scope.environment, state.scope.account_id)
        != (request.scope.environment, request.scope.account_id)
        or not state.arm_intent_current(at)
        or state.pins.policy_sha256 != request.origin.original_policy_sha256
        or not request.origin.publication_completed_at < at < request.origin.deadline
    ):
        _deny("bound_control_evidence_scope_or_time_invalid")
    _guard_request_session(request, state, now=at)
    return state


def build_reserved_evidence(request, reserved, state, event_sha256, *, checked_at):
    request = ledger.checked_reservation_request(request)
    reserved = ledger.checked(reserved, ledger.ReservationReceipt)
    control.checked(state, control.ControlState)
    state_json = control.canonical(control.document(state))
    _state_for_request(state_json, event_sha256, request, checked_at)
    if (
        reserved.state != "reserved"
        or reserved.state_revision != 1
        or reserved.scope != request.scope
        or reserved.reservation_id
        != ledger.reservation_id(request.scope, request.origin.original_event_key)
        or reserved.original_event_key != request.origin.original_event_key
        or reserved.request_sha256 != ledger.digest(request)
        or reserved.account_revision != request.expected_account_revision
        or reserved.ledger_revision != request.expected_ledger_revision + 1
        or reserved.created_at != checked_at
        or reserved.updated_at != checked_at
        or reserved.deadline != request.origin.deadline
    ):
        _deny("bound_control_reserved_receipt_invalid")
    return _json(
        {
            "version": RESERVED_VERSION,
            "request_sha256": ledger.digest(request),
            "reserved_receipt": reserved.model_dump(mode="json", round_trip=True),
            "control_state_json": state_json,
            "control_event_sha256": event_sha256,
            "checked_at": checked_at.isoformat(),
            **_no_authority(),
        },
        MAX_RESERVED_BYTES,
    )


def replay_reserved_evidence(raw, request):
    try:
        body = _document(raw, maximum=MAX_RESERVED_BYTES)
        reserved = ledger.decode(
            _json(body["reserved_receipt"]), ledger.ReservationReceipt
        )
        state = control.decode(body["control_state_json"])
        at = datetime.fromisoformat(body["checked_at"])
        rebuilt = build_reserved_evidence(
            request, reserved, state, body["control_event_sha256"], checked_at=at
        )
        if rebuilt != raw:
            raise ValueError
        return reserved, state, body["control_event_sha256"]
    except (ValueError, TypeError, KeyError, RecursionError, OverflowError):
        _deny("bound_control_reserved_evidence_invalid")


@dataclass(frozen=True, slots=True, repr=False)
class ControlBoundSubmissionIntentRecord:
    canonical_json: str
    sha256: str

    @property
    def execution_authority(self):
        return False

    @property
    def order_retry_authority(self):
        return False

    @property
    def admission(self):
        return "DENY"


def build_control_bound_intent(request, inner, reserved_raw, state, event_sha256):
    """Envelope exact existing v2 bytes; a new wire format, never a permit."""
    if type(inner) is not SubmissionIntentRecord:
        _deny("bound_control_exact_intent_required")
    replayed, consumed = replay_submission_intent(
        inner.canonical_json, request, expected_sha256=inner.sha256
    )
    return _assemble_control_bound_intent(
        request, replayed, consumed, reserved_raw, state, event_sha256
    )


def _assemble_control_bound_intent(
    request, replayed, consumed, reserved_raw, state, event_sha256
):
    """Join already replayed inputs within one public call; never a permit."""
    inner_document = json.loads(replayed.canonical_json)
    if inner_document["version"] != VERSION_V2:
        _deny("bound_control_v2_intent_required")
    reserved, prior_state, prior_event = replay_reserved_evidence(reserved_raw, request)
    control.checked(state, control.ControlState)
    state_json = control.canonical(control.document(state))
    _state_for_request(state_json, event_sha256, request, consumed.updated_at)
    binding = inner_document["execution_binding"]
    if state.pins.credential_session_sha256 != _packet_session_sha256(
        binding["account_packet_json"],
        binding["account_packet_sha256"],
        binding["account_plan_sha256"],
    ):
        _deny("bound_control_account_session_conflict")
    if (
        state_json != control.canonical(control.document(prior_state))
        or event_sha256 != prior_event
        or consumed.coverage != reserved.coverage
        or consumed.reservation_id != reserved.reservation_id
        or consumed.created_at != reserved.created_at
        or consumed.updated_at < reserved.updated_at
        or consumed.account_revision != reserved.account_revision
        or consumed.ledger_revision <= reserved.ledger_revision
    ):
        _deny("bound_control_consume_binding_changed")
    raw = _json(
        {
            "version": INTENT_VERSION,
            "intent_json": replayed.canonical_json,
            "intent_sha256": replayed.sha256,
            "reservation_binding_json": reserved_raw,
            "reservation_binding_sha256": control.digest(reserved_raw),
            "consume_control_state_json": state_json,
            "consume_control_event_sha256": event_sha256,
            "consume_checked_at": consumed.updated_at.isoformat(),
            **_no_authority(),
        }
    )
    return ControlBoundSubmissionIntentRecord(raw, control.digest(raw))


def replay_control_bound_intent(raw, request, *, expected_sha256):
    try:
        control.sha(expected_sha256)
        body = _document(raw)
        if control.digest(raw) != expected_sha256:
            raise ValueError
        inner, consumed = replay_submission_intent(
            body["intent_json"], request, expected_sha256=body["intent_sha256"]
        )
        state = control.decode(body["consume_control_state_json"])
        rebuilt = _assemble_control_bound_intent(
            request,
            inner,
            consumed,
            body["reservation_binding_json"],
            state,
            body["consume_control_event_sha256"],
        )
        if rebuilt.canonical_json != raw or rebuilt.sha256 != expected_sha256:
            raise ValueError
        return rebuilt, consumed
    except (ValueError, TypeError, KeyError, RecursionError, OverflowError):
        _deny("bound_control_intent_invalid")
