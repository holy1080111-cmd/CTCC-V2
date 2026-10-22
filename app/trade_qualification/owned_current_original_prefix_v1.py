"""One-use current-frame ORIGINAL base G1--G5, never candidate/order authority.

Only a live original frame supplies raw operands. Existing gate arithmetic is
reused under the unchanged uncalibrated inspection assumptions. A saved result,
DTO or receipt is not a source owner; complete event history is still unknown.
"""

import asyncio
import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal
from weakref import WeakKeyDictionary

from app.domain.analysis import MultiTimeframeAnalysis
from app.domain.market import MarketSnapshot
from app.domain.source_primitives import canonical, decode, sha
from app.trade_qualification import data, data_v2
from app.trade_qualification import original_candidate_precursor_v2 as precursor
from app.trade_qualification import owned_current_original_frame_v1 as frame
from app.trade_qualification import public_market_collector_v2 as public_v2
from app.trade_qualification.captured_instrument_rules import (
    derive_captured_instrument_rules,
)
from app.trade_qualification.current_conditions import (
    _BASE_STRATEGIES,
    _evaluate_base_g2_g4,
)
from app.trade_qualification.events import TriggerDetection, extract_trigger
from app.trade_qualification.models import (
    EntryQualificationResult,
    EntryZone,
    GateAssessment,
    QualificationGate,
)
from app.trade_qualification.native_original_g1_policy_v1 import (
    DATA_POLICY_SHA256,
    fixed_native_original_g1_policy,
)
from app.trade_qualification.service import (
    QualificationIntent,
    QualificationPrefixPolicy,
)
from app.trade_qualification.timing import TIMING_POLICIES, event_identity

_FRAME_KEY = object()
_PREFIXES = WeakKeyDictionary()
_FALSE_FIELDS = (
    "calibrated_for_trading",
    "historical_first_availability_verified",
    "event_ledger_authenticated",
    "g6_evaluated",
    "g7_evaluated",
    "g8_g11_complete",
    "account_complete",
    "account_revision_published",
    "candidate_qualified",
    "g12_published",
    "post_g12_eligible",
    "atomic_risk_reserved",
    "durable_intent",
    "execution_authority",
)


class CurrentOriginalPrefixError(ValueError):
    """Only fixed source/gate codes; never raw account, credential or DB text."""


def _deny(code="current_original_prefix_unavailable"):
    raise CurrentOriginalPrefixError(code)


class OwnedCurrentOriginalBasePrefixV1:
    __slots__ = ("__weakref__",)

    def __new__(cls):
        _deny()

    def __copy__(self):
        _deny()

    def __deepcopy__(self, memo):
        _deny()

    def __reduce_ex__(self, protocol):
        _deny()

    def close(self):
        state = (
            _PREFIXES.pop(self, None)
            if type(self) is OwnedCurrentOriginalBasePrefixV1
            else None
        )
        if state is not None:
            frame._release_original_base_prefix_v1(state["frame"], key=_FRAME_KEY)


@dataclass(frozen=True, slots=True, repr=False)
class CurrentOriginalBasePrefixInputsV1:
    """Computed operands only: constructing this DTO never creates an owner."""

    intent: QualificationIntent
    g1: data_v2.DataQualificationResultV2
    result: EntryQualificationResult
    detection: TriggerDetection
    original_zone: EntryZone
    receipt_json: bytes
    account_complete: Literal[False] = field(default=False, init=False)
    account_revision_published: Literal[False] = field(default=False, init=False)
    candidate_qualified: Literal[False] = field(default=False, init=False)
    execution_authority: Literal[False] = field(default=False, init=False)


def _compute(replay):
    """Pure arithmetic on already privately replayed originals, not an issuer."""
    packet, private, _, g1, original, created, deadline = replay
    if (
        type(g1) is not data_v2.DataQualificationResultV2
        or not g1.passed
        or type(original) is not precursor.OriginalCandidatePrecursorV2
        or type(original.intent) is not QualificationIntent
        or original.intent.strategy not in _BASE_STRATEGIES
    ):
        _deny("original_base_prefix_intent_required")
    intent = original.intent
    document = decode(original.receipt_json, precursor._MAX_RECEIPT)
    checked, (_, quote, _, candle_packet, _, _) = public_v2._parts(packet)
    rules = derive_captured_instrument_rules(
        private.packet,
        instrument_id=intent.instrument_id,
        expected_plan_sha256=private.packet.plan_sha256,
        expected_packet_sha256=private.reference.packet_sha256,
    )
    policy = fixed_native_original_g1_policy()
    if (
        sha(data._canonical(policy)) != DATA_POLICY_SHA256
        or g1.evaluated_at != created
        or g1.policy != policy
        or g1.source_json is None
        or g1.source_sha256 is None
        or created >= min(deadline, intent.expires_at)
        or intent.created_at != created
        or intent.expires_at > deadline
        or intent.report_id != g1.report_id
        or intent.instrument_id != g1.instrument_id
        or document["initial_entry"] != str(intent.candidate_entry)
        or document["expires_at"] != intent.expires_at.isoformat()
        or document["created_at"] != created.isoformat()
        or document["service_deadline"] != deadline.isoformat()
        or document["intent"] != precursor._intent_tree(intent)
        or document["public_bundle_sha256"] != checked.bundle_sha256
        or document["account_packet_sha256"] != private.reference.packet_sha256
        or document["account_plan_sha256"] != private.packet.plan_sha256
        or document["instrument_rules_sha256"] != rules.receipt_sha256
        or document["g1"]["evaluation_sha256"] != g1.evaluation_sha256
        or document["g1"]["source_sha256"] != g1.source_sha256
        or document["g1"]["quote_bundle_sha256"] != g1.quote_bundle_sha256
    ):
        _deny("original_base_prefix_lineage_changed")
    # Exactly the V6 base inspection semantics, not a new calibrated policy.
    prefix_policy = QualificationPrefixPolicy(
        policy_id="ctcc-native-original-base-g1-g4-inspection-v6",
        data=policy,
        minimum_score=85,
        tick_size=rules.tick_size,
        max_allowed_drift_bps=Decimal(30),
        maximum_strategy_spread_bps=Decimal(3),
        maximum_adverse_funding_bps=Decimal(5),
    )
    source = decode(g1.source_json.encode(), data.MAX_SOURCE_BYTES)
    market = MarketSnapshot.model_validate_json(
        json.dumps(source["market"], allow_nan=False), strict=True
    )
    analysis = MultiTimeframeAnalysis.model_validate_json(
        json.dumps(source["analysis"], allow_nan=False), strict=True
    )
    values = {
        "report_id": intent.report_id,
        "symbol": market.symbol,
        "strategy": intent.strategy,
        "direction": intent.direction,
        "evaluated_at": created,
        "candidate_entry": intent.candidate_entry,
        "raw_score": 0,
        "effective_score": 0,
    }
    gates = [g1.gate]

    def gate(kind, code, reason, measured):
        item = GateAssessment(
            report_id=intent.report_id,
            gate=kind,
            passed=code == "passed",
            code=code,
            reason=reason,
            measured_values=measured,
        )
        gates.append(item)
        return item.passed

    _evaluate_base_g2_g4(
        market,
        analysis,
        intent=intent,
        policy=prefix_policy,
        source_sha256=g1.source_sha256,
        quote_bundle_sha256=g1.quote_bundle_sha256,
        bid=quote.ticker.bid,
        ask=quote.ticker.ask,
        funding_rate=quote.funding.forecast.rate,
        values=values,
        gate=gate,
    )
    if len(gates) != 4 or not all(item.passed for item in gates):
        _deny("original_prefix_" + gates[-1].gate.value.lower() + "_" + gates[-1].code)
    if (
        values["raw_score"] != document["conditions"]["score"]
        or values["direction"] != document["direction"]
    ):
        _deny("original_base_prefix_conditions_changed")
    timing = TIMING_POLICIES[intent.strategy]
    detection = extract_trigger(
        market,
        analysis,
        report_id=intent.report_id,
        strategy=intent.strategy,
        direction=intent.direction,
        observed_at=created,
        trigger_ttl_seconds=timing.trigger_ttl_seconds,
    )
    trigger = detection.trigger
    code = (
        "trigger_source_mismatch"
        if detection.source_sha256 != g1.source_sha256
        else detection.fail_codes[0]
        if detection.fail_codes
        else "trigger_missing"
        if trigger is None
        else "trigger_invalidated"
        if trigger.invalidation_reason is not None
        else "trigger_expired"
        if created >= trigger.expires_at
        else "passed"
    )
    gate(
        QualificationGate.TRIGGER,
        code,
        "Replay the original closed-candle trigger, never find a newer event.",
        {
            "source_sha256": detection.source_sha256,
            "setup_time": detection.setup_time.isoformat()
            if detection.setup_time
            else None,
            "trigger_time": trigger.trigger_time.isoformat() if trigger else None,
            "trigger_type": trigger.trigger_type if trigger else None,
            "trigger_expires_at": trigger.expires_at.isoformat() if trigger else None,
        },
    )
    if code != "passed":
        _deny("original_prefix_g5_" + code)
    timeline = precursor._timeline(candle_packet, created)
    witness = precursor._prefix_witness(timeline, detection) if timeline else None
    if (
        detection.model_dump(mode="json") != document["detection"]
        or detection.setup_time is None
        or trigger.trigger_time > created
        or event_identity(detection) != document["event_key"]
        or trigger.expires_at.isoformat() != document["original_event_expires_at"]
        or timeline is None
        or timeline != document["timeline"]
        or sha(canonical(timeline)) != document["timeline_sha256"]
        or witness is None
        or witness != document["event_prefix_witness"]
    ):
        _deny("original_base_prefix_event_changed")
    # Preserve the precursor zone as an original operand. This does not run G7.
    zone = EntryZone.model_validate_json(
        json.dumps(document["zone"], allow_nan=False), strict=True
    )
    if (
        zone.report_id != intent.report_id
        or zone.instrument_id != intent.instrument_id
        or zone.direction != intent.direction
        or zone.source_sha256 != g1.source_sha256
        or zone.expires_at != intent.expires_at
        or not zone.zone_low <= intent.candidate_entry <= zone.zone_high
    ):
        _deny("original_base_prefix_zone_changed")
    values["trigger"] = trigger
    result = EntryQualificationResult(**values, gates=tuple(gates))
    receipt = canonical(
        {
            "schema_version": "ctcc.current_original_base_prefix.v1",
            "stage": "original_g1_g5_computed",
            "public_packet_sha256": checked.bundle_sha256,
            "account_packet_sha256": private.reference.packet_sha256,
            "account_plan_sha256": private.packet.plan_sha256,
            "precursor_sha256": sha(original.receipt_json),
            "intent_sha256": sha(canonical(document["intent"])),
            "g1_sha256": g1.evaluation_sha256,
            "prefix_policy_sha256": sha(
                canonical(prefix_policy.model_dump(mode="json", round_trip=True))
            ),
            "result_sha256": sha(
                canonical(result.model_dump(mode="json", round_trip=True))
            ),
            "detection_sha256": sha(canonical(document["detection"])),
            "event_key": document["event_key"],
            "zone_sha256": sha(canonical(document["zone"])),
            "timeline_sha256": document["timeline_sha256"],
            "created_at": created.isoformat(),
            "deadline": deadline.isoformat(),
            "original_expires_at": intent.expires_at.isoformat(),
            "gates": [item.model_dump(mode="json") for item in gates],
            **{name: False for name in _FALSE_FIELDS},
        }
    )
    return CurrentOriginalBasePrefixInputsV1(
        intent, g1, result, detection, zone, receipt
    )


def consume_current_original_base_prefix_v1(original_frame):
    """Burn the original frame, compute real G1--G5, retain a prefix-only owner."""
    state = None
    issued = False
    try:
        state, replay = frame._take_original_base_prefix_v1(
            original_frame, key=_FRAME_KEY
        )
        computed = _compute(replay)
        frame._check_original_base_prefix_v1(state, replay, key=_FRAME_KEY)
        owner = object.__new__(OwnedCurrentOriginalBasePrefixV1)
        _PREFIXES[owner] = {"frame": state, "receipt_json": computed.receipt_json}
        issued = True
        return owner
    except (CurrentOriginalPrefixError, asyncio.CancelledError):
        raise
    except Exception:  # noqa: BLE001 -- do not expose raw/native/private failures
        raise CurrentOriginalPrefixError(
            "current_original_prefix_unavailable"
        ) from None
    finally:
        if state is not None and not issued:
            frame._release_original_base_prefix_v1(state, key=_FRAME_KEY)


def consume_owned_current_original_base_prefix_v1(owner):
    """Burn/replay the private prefix; returned DTO cannot recreate this owner."""
    state = (
        _PREFIXES.pop(owner, None)
        if type(owner) is OwnedCurrentOriginalBasePrefixV1
        else None
    )
    if state is None:
        _deny()
    try:
        replay = frame._replay_original_base_prefix_v1(state["frame"], key=_FRAME_KEY)
        computed = _compute(replay)
        if computed.receipt_json != state["receipt_json"]:
            _deny("original_base_prefix_replay_changed")
        frame._check_original_base_prefix_v1(state["frame"], replay, key=_FRAME_KEY)
        return computed
    except (CurrentOriginalPrefixError, asyncio.CancelledError):
        raise
    except Exception:  # noqa: BLE001 -- consumed owner is never restored for retry
        raise CurrentOriginalPrefixError(
            "current_original_prefix_unavailable"
        ) from None
    finally:
        frame._release_original_base_prefix_v1(state["frame"], key=_FRAME_KEY)
