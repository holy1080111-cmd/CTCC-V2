"""One-use current-frame ORIGINAL base G1--G5, never candidate/order authority.

Only a live original frame supplies raw operands. Existing gate arithmetic is
reused under the unchanged uncalibrated inspection assumptions. A saved result,
DTO or receipt is not a source owner; complete event history is still unknown.
"""

import asyncio
import json
import sys as _local_sys_v13
from collections.abc import Coroutine
from dataclasses import dataclass, field
from decimal import Context, Decimal, localcontext
from typing import Literal
from weakref import WeakKeyDictionary

from pydantic import Field, model_validator

from app.config.settings import get_settings
from app.database.repositories.qualification_ledger import QualificationLedgerRepository
from app.domain.analysis import MultiTimeframeAnalysis
from app.domain.market import MarketSnapshot
from app.domain.native_clock import native_stamp
from app.domain.source_primitives import canonical, decode, sha
from app.strategies import structural_protection as _local_protection_v13
from app.strategies.structural_protection import select_structural_protection
from app.trade_qualification import current_conditions as _owned_conditions_v2
from app.trade_qualification import current_economics_v2 as _local_current_economics_v2
from app.trade_qualification import data, data_v2
from app.trade_qualification import economics as _local_economics_v13
from app.trade_qualification import executable_quote_v2 as _local_quote_v2
from app.trade_qualification import location as _local_location_v13
from app.trade_qualification import market_bridge_v2 as _local_bridge_v2
from app.trade_qualification import native_original_bracket_policy_v1 as bracket_policy
from app.trade_qualification import original_candidate_precursor_v2 as precursor
from app.trade_qualification import owned_current_original_frame_v1 as frame
from app.trade_qualification import public_market_collector_v2 as public_v2
from app.trade_qualification import timing as _local_timing_v13
from app.trade_qualification.captured_instrument_rules import (
    derive_captured_instrument_rules,
)
from app.trade_qualification.current_conditions import (
    _BASE_STRATEGIES,
    _evaluate_base_g2_g4,
)
from app.trade_qualification.economics import EconomicsPolicy
from app.trade_qualification.engine import ProtectionPolicy
from app.trade_qualification.events import TriggerDetection, extract_trigger
from app.trade_qualification.models import (
    EntryQualificationResult,
    EntryZone,
    GateAssessment,
    MarketRegime,
    QualificationGate,
)
from app.trade_qualification.native_original_g1_policy_v1 import (
    DATA_POLICY_SHA256,
    fixed_native_original_g1_policy,
)
from app.trade_qualification.reservations import LedgerScope
from app.trade_qualification.service import (
    QualificationIntent,
    QualificationPrefixPolicy,
)
from app.trade_qualification.timing import (
    TIMING_POLICIES,
    TimingPolicy,
    evaluate_timing,
    event_identity,
)

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


# V13 is a new native-held purpose. V1 diagnostic receipt semantics stay exact.
_LOCAL_KEY_V13 = object()
_LOCAL_HOLDS_V13 = {}
_LOCAL_OWNERS_V13 = {}
_LOCAL_UNKNOWN_V13 = {}
_LOCAL_STORAGE_V13 = (_LOCAL_HOLDS_V13, _LOCAL_OWNERS_V13, _LOCAL_UNKNOWN_V13)
_LOCAL_MAX_V13 = 16


def _freeze_local_gate_policy_v13(_timing=TIMING_POLICIES):
    """Freeze only existing numeric source/settings assumptions, never secrets."""
    if TIMING_POLICIES is not _timing:
        _deny("original_local_gate_timing_policy_changed")
    settings = get_settings()
    numeric = bracket_policy.fixed_native_original_bracket_policy()
    fees = settings.okx_demo_structural_round_trip_fee_bps
    slippage = settings.okx_demo_structural_round_trip_slippage_bps
    funding = settings.okx_demo_structural_funding_buffer_bps
    minimum = max(
        numeric["min_net_rr"], settings.okx_demo_structural_min_net_risk_reward
    )
    with localcontext(Context(prec=100)):
        bracket_cost = max(numeric["cost_bps"], fees + slippage + funding)
    protection = ProtectionPolicy(
        policy_id="ctcc-owned-original-local-protection-v13",
        **{**numeric, "cost_bps": bracket_cost, "min_net_rr": minimum},
    )
    economics = EconomicsPolicy(
        policy_id="ctcc-owned-original-local-economics-v13",
        round_trip_fee_bps=fees,
        round_trip_slippage_bps=slippage,
        funding_buffer_bps=funding,
        funding_periods=1,
        minimum_net_rr=minimum,
        maximum_spread_bps=Decimal(3),
        maximum_funding_bps=Decimal(5),
        maximum_quote_age_seconds=fixed_native_original_g1_policy().maximum_quote_age_seconds,
    )
    return canonical(
        {
            "schema_version": "ctcc.owned_original_local_gate_policy.v13",
            "minimum_score": max(85, settings.strategy_min_score),
            "timing": {
                name: value.model_dump(mode="json", round_trip=True)
                for name, value in _timing.items()
            },
            "protection": protection.model_dump(mode="json", round_trip=True),
            "economics": economics.model_dump(mode="json", round_trip=True),
        }
    )


def _replay_local_quote_v13(public_packet, original_quote, computed, *, evaluated_at):
    """Replay the full raw packet and bind its original V2 domain quote."""
    try:
        if type(original_quote) is not _local_quote_v2.ExecutableQuoteV2:
            _deny("original_local_gate_quote_changed")
        # The frame supplies an ExecutableQuoteV2 value. Its raw subbundle is
        # inside the full original public packet; never reconstruct raw source
        # from that value or pass it into the strict CollectedQuoteV2 API.
        checked, (_policy, quote, _provenance, _candles, _auxiliary, _ws) = (
            public_v2._parts(public_packet)
        )
        document = decode(checked.packet_json, public_v2.MAX_PACKET_BYTES)
        context = _local_bridge_v2.public_market_context_v2(
            checked,
            expected_bundle_sha256=computed.g1.public_bundle_sha256,
            evaluated_at=evaluated_at,
        )
        if (
            type(quote) is not _local_quote_v2.ExecutableQuoteV2
            or original_quote != quote
            or document["quote_sha256"] != computed.g1.quote_bundle_sha256
            or context.quote != quote
            or context.packet_sha256 != computed.g1.public_bundle_sha256
            or (quote.report_id, quote.instrument_id)
            != (computed.intent.report_id, computed.intent.instrument_id)
        ):
            _deny("original_local_gate_quote_changed")
        return context
    except CurrentOriginalPrefixError:
        raise
    except Exception:  # noqa: BLE001 -- sanitize original private raw/source failures.
        _deny("original_local_gate_v2_quote_replay_failed")


def _location_local_quote_v13(computed, quote_context, *, observed_at):
    """Existing G7 numeric tail after original raw V2 replay and strict zone."""
    quote = quote_context.quote
    audit = {}
    zone = _local_location_v13._revalidate(computed.original_zone, EntryZone)
    digest = decode(quote_context.quote_inspection_json)["quote_sha256"]
    reference = (
        quote.ticker.ask if computed.intent.direction == "long" else quote.ticker.bid
    )
    code, reason = "passed", "Original V2 source quote and unchanged zone are valid."
    if (
        zone.report_id != computed.intent.report_id
        or zone.instrument_id != computed.intent.instrument_id
        or zone.direction != computed.intent.direction
        or zone.source_sha256 != computed.g1.source_sha256
    ):
        code, reason = (
            "identity_mismatch",
            "The unchanged original zone identity differs.",
        )
    elif observed_at < zone.created_at:
        code, reason = "entry_zone_not_open", "The source entry zone has not opened."
    elif observed_at >= zone.expires_at:
        code, reason = "entry_zone_expired", "The source entry zone has expired."
    else:
        try:
            with localcontext(Context(prec=100)):
                code, reason = _local_location_v13._evaluate_location_numeric_tail(
                    entry=computed.intent.candidate_entry,
                    direction=computed.intent.direction,
                    zone=zone,
                    reference=reference,
                    bid=quote.ticker.bid,
                    ask=quote.ticker.ask,
                    mark_price=quote.mark.price,
                    audit=audit,
                )
        except _local_location_v13._INVALID:
            code, reason = (
                "candidate_entry_invalid",
                "Entry-location arithmetic failed validation.",
            )
    return _local_location_v13.LocationResult(
        report_id=computed.intent.report_id,
        instrument_id=computed.intent.instrument_id,
        passed=code == "passed",
        code=code,
        reason=reason,
        reference_price=reference,
        drift_bps=audit.get("drift_bps"),
        quote_sha256=digest,
    )


def _economics_local_quote_v13(
    record, quote_context, *, stop, target, policy, observed_at
):
    """Existing G10 numeric tail on V2 forecast/rate, with original cost age."""
    computed, quote = record["computed"], quote_context.quote
    checked_policy = _local_economics_v13._revalidate(policy, EconomicsPolicy)
    entry = computed.intent.candidate_entry
    identity = {
        "report_id": computed.intent.report_id,
        "instrument_id": computed.intent.instrument_id,
        "direction": computed.intent.direction,
        "evaluated_at": observed_at,
    }
    evidence = {
        "candidate_entry": entry,
        "stop_loss": stop,
        "take_profit": target,
        "policy": checked_policy,
        "policy_sha256": sha(checked_policy.model_dump_json(round_trip=True).encode()),
        "quote_sha256": decode(quote_context.quote_inspection_json)["quote_sha256"],
    }

    def result(code, reason):
        return _local_economics_v13.EconomicsResult(
            **identity, **evidence, passed=code == "passed", code=code, reason=reason
        )

    if not (
        stop < entry < target
        if computed.intent.direction == "long"
        else target < entry < stop
    ):
        return result(
            "economics_geometry_invalid",
            "The fixed original bracket has invalid geometry.",
        )
    # This is the existing additional cost-policy constraint. It measures the
    # actual ticker generation/mark return/funding return/capture completion;
    # fundingTime remains the upcoming settlement, never a generated-at clock.
    if not _local_current_economics_v2.quote_within_economics_policy_age_v2(
        record["public_packet"], checked_policy, observed_at
    ):
        return result(
            "projected_quote_source_stale",
            "Original V2 quote exceeds the unchanged cost-policy age.",
        )
    try:
        with localcontext(Context(prec=100)):
            code, reason = _local_economics_v13._evaluate_economics_numeric_tail(
                entry=entry,
                direction=computed.intent.direction,
                stop=stop,
                target=target,
                bid=quote.ticker.bid,
                ask=quote.ticker.ask,
                funding_rate=quote.funding.forecast.rate,
                checked_policy=checked_policy,
                evidence=evidence,
            )
    except _local_economics_v13._INVALID:
        for key in ("cost_per_base", "gross_rr", "net_rr"):
            evidence.pop(key, None)
        code, reason = (
            "economics_arithmetic_invalid",
            "Bounded cost arithmetic could not be validated.",
        )
    return result(code, reason)


class OwnedCurrentOriginalLocalGatesV13(OwnedCurrentOriginalBasePrefixV1):
    """Live original G1–G10 state, deliberately not a qualified candidate."""

    __slots__ = ()

    def close(self):
        binding = _LOCAL_OWNERS_V13.pop(self, None)
        if binding is not None:
            _release_local_gate_v13(binding)


def _local_gate_clock_v13():
    from app.domain.source_primitives import utc_from_ns

    return utc_from_ns(native_stamp()["utc_ns"])


def _local_gate_record_v13(binding):
    record = _LOCAL_HOLDS_V13.get(binding)
    if type(record) is not dict:
        _deny("original_local_gate_binding_unavailable")
    guard = record["guard"]
    if _check_local_storage_v13 is not guard or any(
        actual is not expected
        for actual, expected in zip(
            (
                guard.__code__,
                guard.__globals__,
                guard.__defaults__,
                guard.__kwdefaults__,
            ),
            record["guard_pin"],
            strict=True,
        )
    ):
        _deny("original_local_gate_function_changed")
    guard()  # The original held checker, including its captured immutable table.
    replay = frame._replay_original_base_prefix_v1(
        record["prefix"]["frame"], key=_FRAME_KEY
    )
    computed = _compute(replay)
    if (
        computed.receipt_json != record["prefix"]["receipt_json"]
        or computed.receipt_json != record["computed"].receipt_json
        or record["policy_raw"] != record["prefix"]["frame"]["local_gate_policy_v13"]
        or _freeze_local_gate_policy_v13() != record["policy_raw"]
    ):
        _deny("original_local_gate_original_changed")
    original_quote = _replay_local_quote_v13(
        replay[0], replay[2].quote, computed, evaluated_at=record["created_at"]
    )
    if (
        record["public_packet"].packet_json != replay[0].packet_json
        or type(record["quote_value"]) is not _local_quote_v2.ExecutableQuoteV2
        or record["quote_value"] != replay[2].quote
        or type(record["quote_context"]) is not _local_bridge_v2.PublicMarketContextV2
        or record["quote_context"] != original_quote
    ):
        _deny("original_local_gate_quote_changed")
    frame._check_original_base_prefix_v1(
        record["prefix"]["frame"], replay, key=_FRAME_KEY
    )
    return record, replay


def _local_journal_context_v13(binding, *, key):
    """Only the registered held purpose can supply a native DB context."""
    if key is not _LOCAL_KEY_V13:
        _deny("original_local_gate_fixed_purpose_required")
    record, _ = _local_gate_record_v13(binding)
    original = record["prefix"]["frame"]
    if (
        record["scope"].environment != original["plans"][1].environment
        or record["scope"].account_id != original["plans"][1].expected_uid
        or record["scope"].settlement_currency
        != original["plans"][1].settlement_currency
    ):
        _deny("original_local_gate_account_changed")
    return (
        original["factory"],
        record["scope"],
        record["checkpoint_sha256"],
        min(original["deadline"], record["computed"].intent.expires_at),
    )


def _release_local_gate_v13(binding):
    if binding in _LOCAL_UNKNOWN_V13:
        _deny("original_local_gate_release_unknown")
    record = _LOCAL_HOLDS_V13.pop(binding, None)
    if record is None:
        return
    owner = record.get("owner")
    if owner is not None:
        _LOCAL_OWNERS_V13.pop(owner, None)
    try:
        frame._release_original_base_prefix_v1(
            record["prefix"]["frame"], key=_FRAME_KEY
        )
    except BaseException as error:  # noqa: BLE001 -- preserve exact original state and never retry an uncertain release.
        _LOCAL_UNKNOWN_V13[binding] = record
        if isinstance(error, asyncio.CancelledError):
            raise asyncio.CancelledError() from None
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise type(error)() from None
        _deny("original_local_gate_release_unknown")
    record.clear()


class _LocalGateAttemptV13(Coroutine):
    def __init__(self, inner, binding):
        self._inner, self._binding, self._closed = inner, binding, False

    def __await__(self):
        return self

    def __iter__(self):
        return self

    def __next__(self):
        return self.send(None)

    def send(self, value):
        try:
            return self._inner.send(value)
        except BaseException as error:  # Settle native MOVE for every failed coroutine exit, then preserve control flow.
            self._closed = True
            _settle_local_attempt_v13(self._binding, error)
            if isinstance(error, asyncio.CancelledError):
                raise asyncio.CancelledError() from None
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise type(error)() from None
            raise

    def throw(self, typ, val=None, tb=None):
        try:
            return self._inner.throw(typ, val, tb)
        except (
            BaseException
        ) as error:  # An injected pre-await cancellation must burn the held MOVE.
            self._closed = True
            _settle_local_attempt_v13(self._binding, error)
            if isinstance(error, asyncio.CancelledError):
                raise asyncio.CancelledError() from None
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise type(error)() from None
            raise

    def close(self):
        if not self._closed:
            self._closed = True
            try:
                self._inner.close()
            finally:
                _release_local_gate_v13(self._binding)

    def __del__(self):
        try:
            self.close()
        except BaseException:  # noqa: BLE001, S110 -- finalizers cannot rethrow; close already releases once or strongly retains unknown source, never deleting its journal.
            pass


def _settle_local_attempt_v13(binding, original_error):
    record = _LOCAL_HOLDS_V13.get(binding)
    if record is None:
        return
    owner = record.get("owner")
    if owner is not None and _LOCAL_OWNERS_V13.get(owner) is binding:
        return
    try:
        _release_local_gate_v13(binding)
    except BaseException:  # Retain unknown release and preserve the original cancellation/control-flow priority.
        if isinstance(original_error, asyncio.CancelledError):
            raise asyncio.CancelledError() from None
        if isinstance(original_error, (KeyboardInterrupt, SystemExit)):
            raise type(original_error)() from None
        raise


def _prepare_owned_original_local_gates_v13(owner, *, guard):
    """MOVE before returning an awaitable; no source/clock/policy DTO accepted."""
    if guard is not _check_local_storage_v13:
        _deny("original_local_gate_function_changed")
    guard()
    if len(_LOCAL_HOLDS_V13) + len(_LOCAL_UNKNOWN_V13) >= _LOCAL_MAX_V13:
        _deny("original_local_gate_binding_bound")
    state = (
        _PREFIXES.pop(owner, None)
        if type(owner) is OwnedCurrentOriginalBasePrefixV1
        else None
    )
    if state is None:
        _deny("original_local_gate_owner_unavailable")
    binding = object()
    record = {
        "prefix": state,
        "guard": guard,
        "guard_pin": (
            guard.__code__,
            guard.__globals__,
            guard.__defaults__,
            guard.__kwdefaults__,
        ),
    }
    _LOCAL_HOLDS_V13[binding] = record
    try:
        replay = frame._replay_original_base_prefix_v1(state["frame"], key=_FRAME_KEY)
        computed = _compute(replay)
        if computed.receipt_json != state["receipt_json"]:
            _deny("original_local_gate_prefix_changed")
        policy_raw = state["frame"]["local_gate_policy_v13"]
        if policy_raw != _freeze_local_gate_policy_v13():
            _deny("original_local_gate_policy_changed")
        policy = decode(policy_raw)
        if computed.result.raw_score < policy["minimum_score"]:
            _deny("original_local_gate_score_below_current_policy")
        _public, private, context, _, _, created, _ = replay
        document = decode(
            state["frame"]["account_state"]["bundle_raw"],
            frame.custody.MAX_BUNDLE_BYTES,
        )
        chain = frame.account._chain_replay(document["chain"])
        from app.trade_qualification import account_capture_journal as journal

        initial = journal.checked_event(chain[0].event)
        checkpoint = initial["data"]["local_checkpoint_sha256"]
        journal._sha(checkpoint)
        scope = LedgerScope(
            account_id=private.packet.plan.expected_uid, settlement_currency="USDT"
        )
        rules = derive_captured_instrument_rules(
            private.packet,
            instrument_id=computed.intent.instrument_id,
            expected_plan_sha256=private.packet.plan_sha256,
            expected_packet_sha256=private.reference.packet_sha256,
        )
        source = decode(computed.g1.source_json.encode(), data.MAX_SOURCE_BYTES)
        market = MarketSnapshot.model_validate_json(
            json.dumps(source["market"]), strict=True
        )
        analysis = MultiTimeframeAnalysis.model_validate_json(
            json.dumps(source["analysis"]), strict=True
        )
        protection = ProtectionPolicy.model_validate_json(
            json.dumps(policy["protection"]), strict=True
        )
        selected = select_structural_protection(
            computed.detection,
            market,
            analysis,
            observed_at=created,
            entry=computed.intent.candidate_entry,
            tick_size=rules.tick_size,
            **{
                name: value
                for name, value in protection.model_dump().items()
                if name != "policy_id"
            },
        )
        quote_context = _replay_local_quote_v13(
            _public, context.quote, computed, evaluated_at=created
        )
        selection_raw = selected.to_audit_json()
        if (
            not 0 < len(selection_raw.encode("ascii")) <= 8 * 1024 * 1024
            or selected.reference_entry != computed.intent.candidate_entry
            or selected.report_id != computed.intent.report_id
            or selected.instrument_id != computed.intent.instrument_id
            or selected.direction != computed.intent.direction
            or selected.strategy != computed.intent.strategy
            or selected.source_sha256 != computed.g1.source_sha256
        ):
            _deny("original_local_gate_selection_binding_changed")
        record.update(
            computed=computed,
            policy_raw=policy_raw,
            scope=scope,
            checkpoint_sha256=checkpoint,
            created_at=created,
            public_packet=_public,
            quote_value=context.quote,
            quote_context=quote_context,
            selection=selected,
            selection_raw=selection_raw,
            market=market,
            analysis=analysis,
            protection=protection,
            tick_size=rules.tick_size,
        )
        _local_gate_record_v13(binding)
        return _LocalGateAttemptV13(_continue_local_gates_v13(binding), binding)
    except (
        BaseException
    ) as error:  # Settle every post-MOVE preparation failure before sanitized rethrow.
        _settle_local_attempt_v13(binding, error)
        if isinstance(error, asyncio.CancelledError):
            raise asyncio.CancelledError() from None
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise type(error)() from None
        if isinstance(error, CurrentOriginalPrefixError):
            raise
        _deny("original_local_gate_prepare_failed")


def _evaluate_original_local_math_v13(record, journal, *, observed_at):
    """Pure shared gate arithmetic; supplied values cannot mint a native owner."""
    b = decode(journal.document_json)
    computed = record["computed"]
    values = {
        name: getattr(computed.result, name)
        for name in type(computed.result).model_fields
    }
    gates = list(computed.result.gates)

    def gate(kind, code, reason, measured):
        gates.append(
            GateAssessment(
                report_id=computed.intent.report_id,
                gate=kind,
                passed=code == "passed",
                code=code,
                reason=reason,
                measured_values=measured,
            )
        )
        values["gates"] = tuple(gates)
        return code == "passed"

    now = observed_at
    quote = record["quote_context"].quote
    reference = (
        quote.ticker.ask if computed.intent.direction == "long" else quote.ticker.bid
    )
    policy = decode(record["policy_raw"])
    timing_policy = TimingPolicy.model_validate_json(
        json.dumps(policy["timing"][computed.intent.strategy]), strict=True
    )
    timing = evaluate_timing(
        computed.detection,
        current_time=now,
        candidate_created_at=computed.intent.created_at,
        candidate_expires_at=computed.intent.expires_at,
        reference_price=reference,
        consumed_event_keys=journal.consumed_event_keys,
        policy=timing_policy,
    )
    values["entry_timing_state"] = (
        "valid"
        if timing.timing_valid
        else "wait"
        if timing.action == "WAIT"
        else "cancel"
    )
    values["reference_price"] = reference
    if not gate(
        QualificationGate.TIMING,
        timing.code,
        timing.reason,
        {
            "event_key": timing.event_key,
            "journal_sha256": journal.sha256,
            "observed_at": now.isoformat(),
            "original_expires_at": computed.intent.expires_at.isoformat(),
            "timing_policy_sha256": sha(
                canonical(timing_policy.model_dump(mode="json", round_trip=True))
            ),
            "local_ledger_revision": b["ledger_revision"],
            "scope": "finite_local_CTCC_uid_event_only",
            "reservation_performed": False,
        },
    ):
        return EntryQualificationResult(**values)
    values["entry_zone"] = computed.original_zone
    quote_context = _replay_local_quote_v13(
        record["public_packet"], record["quote_value"], computed, evaluated_at=now
    )
    location = _location_local_quote_v13(computed, quote_context, observed_at=now)
    if not gate(
        QualificationGate.LOCATION,
        location.code,
        location.reason,
        {
            "original_zone_sha256": sha(
                canonical(computed.original_zone.model_dump(mode="json"))
            ),
            "entry_unchanged": True,
            "quote_sha256": location.quote_sha256,
        },
    ):
        return EntryQualificationResult(**values)
    # Recompute ONLY on the same original source/time/policy. This proves
    # the retained audit, not selection of another current bracket.
    selected = select_structural_protection(
        computed.detection,
        record["market"],
        record["analysis"],
        observed_at=record["created_at"],
        entry=computed.intent.candidate_entry,
        tick_size=record["tick_size"],
        **{
            name: value
            for name, value in record["protection"].model_dump().items()
            if name != "policy_id"
        },
    )
    if selected.to_audit_json() != record["selection_raw"]:
        _deny("original_local_gate_bracket_changed")
    bracket = selected.selected if selected.protection_valid else None
    if bracket is not None:
        values["stop_loss"] = bracket.stop.final_stop
    if not gate(
        QualificationGate.STOP,
        "passed"
        if selected.protection_valid
        else selected.fail_codes[0]
        if selected.fail_codes
        else "no_valid_structural_bracket",
        "The same original source-selected joint bracket is required.",
        {
            "original_selection_sha256": sha(record["selection_raw"].encode()),
            "entry_unchanged": True,
        },
    ):
        return EntryQualificationResult(**values)
    values["take_profit"] = bracket.target.final_target
    if not gate(
        QualificationGate.TARGET,
        "passed"
        if bracket.valid and not bracket.target.rejection_codes
        else "structural_target_invalid",
        "The target remains from the same fixed original bracket.",
        {"stop_unchanged": True, "target_unchanged": True},
    ):
        return EntryQualificationResult(**values)
    economics_policy = EconomicsPolicy.model_validate_json(
        json.dumps(policy["economics"]), strict=True
    )
    costs = _economics_local_quote_v13(
        record,
        quote_context,
        stop=values["stop_loss"],
        target=values["take_profit"],
        policy=economics_policy,
        observed_at=now,
    )
    if costs.passed:
        with localcontext(Context(prec=100)):
            values["gross_rr"] = costs.gross_rr.quantize(Decimal("1e-30"))
            values["net_rr"] = costs.net_rr.quantize(Decimal("1e-30"))
    if not gate(
        QualificationGate.ECONOMICS,
        costs.code,
        costs.reason,
        {
            "cost_policy_sha256": costs.policy_sha256,
            "quote_sha256": costs.quote_sha256,
            "net_rr": costs.net_rr,
            "entry_unchanged": True,
            "stop_unchanged": True,
            "target_unchanged": True,
        },
    ):
        return EntryQualificationResult(**values)
    return EntryQualificationResult(**values)


async def _continue_local_gates_v13(binding):
    retained = False
    pending_failure = None
    try:
        record, _ = _local_gate_record_v13(binding)
        original_deadline = min(
            record["prefix"]["frame"]["deadline"], record["computed"].intent.expires_at
        )
        # Sample the loop clock first. Time spent sampling native UTC then
        # subtracts from this one conservative absolute client deadline.
        loop_started = asyncio.get_running_loop().time()
        remaining = (original_deadline - _local_gate_clock_v13()).total_seconds()
        if remaining <= 0:
            _deny("original_local_gate_original_expiry")
        client_deadline = loop_started + remaining
        async with asyncio.timeout_at(client_deadline):
            repository = QualificationLedgerRepository(
                record["prefix"]["frame"]["factory"], clock=_local_gate_clock_v13
            )
            first, control = await repository._inspect_owned_original_local_events_v13(
                binding, key=_LOCAL_KEY_V13
            )
            record, _ = _local_gate_record_v13(binding)
            (
                second,
                current_control,
            ) = await repository._inspect_owned_original_local_events_v13(
                binding, key=_LOCAL_KEY_V13
            )
            record, _ = _local_gate_record_v13(binding)
            stable = (
                "scope_sha256",
                "account_revision",
                "ledger_revision",
                "claims_sha256",
                "event_count",
                "transition_count",
                "state_counts",
                "event_keys_sha256",
                "rows_sha256",
                "transitions_sha256",
            )
            a, b = decode(first.document_json), decode(second.document_json)
            if (
                first.received_at > second.observed_at
                or first.consumed_event_keys != second.consumed_event_keys
                or any(a[name] != b[name] for name in stable)
                or control != current_control
            ):
                _deny("original_local_gate_journal_changed")
            local_evaluated_at = _local_gate_clock_v13()
            result = _evaluate_original_local_math_v13(
                record, second, observed_at=local_evaluated_at
            )
            if len(result.gates) != 10 or not all(gate.passed for gate in result.gates):
                return result
            _local_gate_record_v13(
                binding
            )  # Final original-source fence after the last await/math.
            owner = object.__new__(OwnedCurrentOriginalLocalGatesV13)
            record.update(
                owner=owner,
                result=result,
                local_evaluated_at=local_evaluated_at,
                result_sha256=sha(canonical(result.model_dump(mode="json"))),
                local_journal=second,
                control=current_control,
            )
            _LOCAL_OWNERS_V13[owner] = binding
            retained = True
            return owner
    except (
        BaseException
    ) as error:  # Release the original source while retaining sanitized control flow.
        pending_failure = error
        if isinstance(error, asyncio.CancelledError):
            raise asyncio.CancelledError() from None
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise type(error)() from None
        if isinstance(error, CurrentOriginalPrefixError):
            raise
        _deny("original_local_gate_evaluation_failed")
    finally:
        if not retained:
            _settle_local_attempt_v13(binding, pending_failure)


def consume_owned_original_local_gates_v13(owner):
    """Read/burn the actual G1–G10 result. Unknown owned G11 never qualifies."""
    binding = (
        _LOCAL_OWNERS_V13.pop(owner, None)
        if type(owner) is OwnedCurrentOriginalLocalGatesV13
        else None
    )
    if binding is None:
        _deny("original_local_gate_owner_unavailable")
    try:
        record, _ = _local_gate_record_v13(binding)
        result = record["result"]
        values = {name: getattr(result, name) for name in type(result).model_fields}
        values["gates"] = (
            *result.gates,
            GateAssessment(
                report_id=result.report_id,
                gate=QualificationGate.RISK,
                passed=False,
                code="owned_account_risk_inputs_unknown",
                reason="G11 requires the genuine finite owned account/risk producer.",
                measured_values={
                    "account_inputs_available": False,
                    "candidate_owner_minted": False,
                },
            ),
        )
        return EntryQualificationResult(**values)
    finally:
        _release_local_gate_v13(binding)


def _local_pin_v13(owner, name):
    function = getattr(owner, name)
    return (
        owner,
        name,
        function,
        getattr(function, "__code__", None),
        getattr(function, "__globals__", None),
        getattr(function, "__defaults__", None),
        getattr(function, "__kwdefaults__", None),
    )


_LOCAL_PINS_V13 = tuple(
    _local_pin_v13(owner, name)
    for owner, names in (
        (
            _local_sys_v13.modules[__name__],
            (
                "_freeze_local_gate_policy_v13",
                "_local_gate_clock_v13",
                "_local_gate_record_v13",
                "_local_journal_context_v13",
                "_release_local_gate_v13",
                "_settle_local_attempt_v13",
                "get_settings",
                "native_stamp",
                "canonical",
                "decode",
                "sha",
                "_replay_local_quote_v13",
                "derive_captured_instrument_rules",
                "evaluate_timing",
                "event_identity",
                "_location_local_quote_v13",
                "_economics_local_quote_v13",
                "select_structural_protection",
                "fixed_native_original_g1_policy",
                "_prepare_owned_original_local_gates_v13",
                "_evaluate_original_local_math_v13",
                "_continue_local_gates_v13",
                "consume_owned_original_local_gates_v13",
                "_compute",
            ),
        ),
        (
            frame,
            (
                "_replay_original_base_prefix_v1",
                "_check_original_base_prefix_v1",
                "_release_original_base_prefix_v1",
            ),
        ),
        (
            QualificationLedgerRepository,
            (
                "_inspect_owned_original_local_events_v13",
                "_inspect_prospective_event_journal_locked",
                "_inspect_event_journal_rows_locked",
                "_bootstrap_checkpoint",
                "_locked",
            ),
        ),
        (
            _LocalGateAttemptV13,
            (
                "__init__",
                "__await__",
                "__iter__",
                "__next__",
                "send",
                "throw",
                "close",
                "__del__",
            ),
        ),
        (OwnedCurrentOriginalLocalGatesV13, ("close",)),
        (_local_timing_v13, ("evaluate_timing", "event_identity")),
        (_local_location_v13, ("_revalidate", "_evaluate_location_numeric_tail")),
        (_local_economics_v13, ("_revalidate", "_evaluate_economics_numeric_tail")),
        (_local_bridge_v2, ("public_market_context_v2", "inspect_executable_quote_v2")),
        (
            _local_quote_v2,
            (
                "inspect_executable_quote_v2",
                "_inspect",
                "_component",
                "_utc",
                "_price",
                "_exact",
            ),
        ),
        (_local_current_economics_v2, ("quote_within_economics_policy_age_v2",)),
        (public_v2, ("_parts", "_quote_parts", "_replay")),
        (_local_protection_v13, ("select_structural_protection",)),
        (bracket_policy, ("fixed_native_original_bracket_policy",)),
    )
    for name in names
)


def _check_local_storage_v13(
    _storage=_LOCAL_STORAGE_V13,
    _key=_LOCAL_KEY_V13,
    _pins=_LOCAL_PINS_V13,
    _namespace=_local_gate_record_v13.__globals__,
    _aliases=(
        ("_local_bridge_v2", _local_bridge_v2),
        ("_local_current_economics_v2", _local_current_economics_v2),
        ("_local_quote_v2", _local_quote_v2),
        ("_local_location_v13", _local_location_v13),
        ("_local_economics_v13", _local_economics_v13),
        ("public_v2", public_v2),
        ("frame", frame),
        ("QualificationLedgerRepository", QualificationLedgerRepository),
    ),
):
    # Capture actual original owners directly in this checker default, not in
    # a rebindable global mirror. Entry and held-record snapshots already bind
    # this function's original defaults. No caller can pass this checker a
    # replacement table through the public one-argument MOVE boundary.
    if (
        type(_namespace) is not dict
        or type(_aliases) is not tuple
        or len(_aliases) != 8
        or any(_namespace.get(name) is not original for name, original in _aliases)
    ):
        _deny("original_local_gate_module_alias_changed")
    # Original immutable tuple is captured directly, without a second mutable
    # alias or a caller-provided table. The entry/held-record snapshots protect
    # this checker itself independently of the table it checks.
    if (
        type(_LOCAL_PINS_V13) is not tuple
        or _LOCAL_PINS_V13 is not _pins
        or len(_pins) != 61
    ):
        _deny("original_local_gate_pin_table_changed")
    if (
        type(_LOCAL_MAX_V13) is not int
        or _LOCAL_MAX_V13 != 16
        or _LOCAL_STORAGE_V13 is not _storage
        or _LOCAL_KEY_V13 is not _key
        or any(
            type(value) is not dict or value is not expected
            for value, expected in zip(
                (_LOCAL_HOLDS_V13, _LOCAL_OWNERS_V13, _LOCAL_UNKNOWN_V13),
                _LOCAL_STORAGE_V13,
                strict=True,
            )
        )
    ):
        _deny("original_local_gate_storage_changed")
    for owner, name, function, code, namespace, defaults, kwdefaults in _pins:
        actual = getattr(owner, name)
        if (
            actual is not function
            or getattr(actual, "__code__", None) is not code
            or getattr(actual, "__globals__", None) is not namespace
            or getattr(actual, "__defaults__", None) is not defaults
            or getattr(actual, "__kwdefaults__", None) is not kwdefaults
        ):
            _deny("original_local_gate_function_changed")


def _bind_local_move_entry_v13(prepare, checker):
    """Bind only this MOVE entry to its real checker; no caller callback API."""
    snapshots = tuple(
        (
            value,
            value.__code__,
            value.__globals__,
            value.__defaults__,
            value.__kwdefaults__,
        )
        for value in (prepare, checker)
    )

    def bound(owner):
        if (
            continue_owned_original_local_gates_v13 is not bound
            or _prepare_owned_original_local_gates_v13 is not prepare
            or _check_local_storage_v13 is not checker
        ):
            _deny("original_local_gate_function_changed")
        for value, code, namespace, defaults, kwdefaults in snapshots:
            if (
                value.__code__ is not code
                or value.__globals__ is not namespace
                or value.__defaults__ is not defaults
                or value.__kwdefaults__ is not kwdefaults
            ):
                _deny("original_local_gate_function_changed")
        checker()
        return prepare(owner, guard=checker)

    return bound


continue_owned_original_local_gates_v13 = _bind_local_move_entry_v13(
    _prepare_owned_original_local_gates_v13, _check_local_storage_v13
)


def _original_g11_purpose_v1(key):
    from app.trade_qualification import owned_native_candidate_v1 as candidate

    if key is not candidate._G11_KEY_V1:
        _deny("original_g11_fixed_purpose_required")


def _take_original_local_for_g11_v1(owner, *, key):
    """MOVE the real G1–G10 hold; the old diagnostic consumer stays denying."""
    _original_g11_purpose_v1(key)
    binding = (
        _LOCAL_OWNERS_V13.pop(owner, None)
        if type(owner) is OwnedCurrentOriginalLocalGatesV13
        else None
    )
    if binding is None:
        _deny("original_local_gate_owner_unavailable")
    try:
        _replay_original_local_for_g11_v1(binding, key=key)
        return binding
    except BaseException:
        _release_local_gate_v13(binding)
        raise


def _replay_original_local_for_g11_v1(binding, *, key):
    """Re-read every native source and recompute the captured fixed local gates."""
    _original_g11_purpose_v1(key)
    record, replay = _local_gate_record_v13(binding)
    actual = _evaluate_original_local_math_v13(
        record, record["local_journal"], observed_at=record["local_evaluated_at"]
    )
    if (
        actual != record["result"]
        or sha(canonical(actual.model_dump(mode="json"))) != record["result_sha256"]
        or len(actual.gates) != 10
        or not all(gate.passed for gate in actual.gates)
    ):
        _deny("original_local_gate_result_changed")
    return record, replay


def _release_original_local_for_g11_v1(binding, *, key):
    _original_g11_purpose_v1(key)
    _release_local_gate_v13(binding)


# New genuine native-owned family purpose. Historical V1/V13 bodies stay exact.
_OWNED_HISTORY_FAMILIES_V2 = frozenset(
    (
        "volatility_expansion",
        "structure_reversal",
        "range_reversal",
        "liquidity_sweep_reversal",
    )
)


class OwnedFamilyEntryQualificationResultV2(EntryQualificationResult):
    """Computed family DATA, never a source owner or independent permission."""

    contract_version: Literal["ctcc-owned-original-family-result-v2"] = (
        "ctcc-owned-original-family-result-v2"
    )
    market_regime: (
        MarketRegime | Literal["History Verified Reversal", "History Verified Sweep"]
    ) = MarketRegime.UNKNOWN
    family_policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    history_admission_sha256: str | None = Field(
        default=None, pattern=r"^[a-f0-9]{64}$"
    )
    execution_authority: Literal[False] = False

    @model_validator(mode="after")
    def exact_family_basis(self):
        if self.strategy not in _OWNED_HISTORY_FAMILIES_V2:
            raise ValueError("owned_family_result_strategy_invalid")
        expected = sha(
            bracket_policy.fixed_native_owned_family_policy_v2(self.strategy)
        )
        if self.family_policy_sha256 != expected:
            raise ValueError("owned_family_result_policy_changed")
        if self._passed(QualificationGate.REGIME):
            measured = self.gates[1].measured_values
            if (
                self.history_admission_sha256 is None
                or measured.get("history_evaluation_sha256")
                != self.history_admission_sha256
                or measured.get("family_policy_sha256") != self.family_policy_sha256
                or measured.get("router_relabelled") is not False
            ):
                raise ValueError("owned_family_result_history_basis_missing")
        if self.market_regime == "History Verified Reversal" and (
            self.strategy != "structure_reversal"
            or not self._passed(QualificationGate.REGIME)
            or self.gates[1].measured_values.get("actual_router_regime")
            != MarketRegime.UNKNOWN.value
        ):
            raise ValueError("owned_family_reversal_classification_invalid")
        if self.market_regime == "History Verified Sweep" and (
            self.strategy != "liquidity_sweep_reversal"
            or not self._passed(QualificationGate.REGIME)
            or self.gates[1].measured_values.get("actual_router_regime")
            != MarketRegime.UNKNOWN.value
        ):
            raise ValueError("owned_family_sweep_classification_invalid")
        return self


@dataclass(frozen=True, slots=True, repr=False)
class CurrentOriginalFamilyPrefixInputsV2(CurrentOriginalBasePrefixInputsV1):
    family_history_json: bytes


def _owned_result_from_values_v2(original, values):
    if type(original) is EntryQualificationResult:
        return EntryQualificationResult(**values)
    if type(original) is OwnedFamilyEntryQualificationResultV2:
        return OwnedFamilyEntryQualificationResultV2(**values)
    _deny("owned_family_exact_result_required")


def decode_owned_family_result_v2(document):
    """Historical DATA decode only; it cannot recreate a native registry owner."""
    if type(document) is not dict:
        _deny("owned_family_result_document_invalid")
    if document.get("strategy") in _BASE_STRATEGIES:
        return EntryQualificationResult.model_validate_json(
            canonical(document), strict=True
        )
    if (
        document.get("strategy") not in _OWNED_HISTORY_FAMILIES_V2
        or document.get("contract_version") != "ctcc-owned-original-family-result-v2"
    ):
        _deny("owned_family_result_document_invalid")
    return OwnedFamilyEntryQualificationResultV2.model_validate_json(
        canonical(document), strict=True
    )


def _select_owned_family_protection_v2(detection, market, analysis, **inputs):
    if type(detection) is not TriggerDetection:
        _deny("owned_family_exact_detection_required")
    if (
        detection.strategy in _BASE_STRATEGIES
        or detection.strategy == "volatility_expansion"
    ):
        return select_structural_protection(detection, market, analysis, **inputs)
    if detection.strategy == "structure_reversal":
        return _local_protection_v13.select_reversal_structural_protection(
            detection, market, analysis, **inputs
        )
    if detection.strategy == "range_reversal":
        return _local_protection_v13.select_range_structural_protection(
            detection, market, analysis, **inputs
        )
    if detection.strategy == "liquidity_sweep_reversal":
        return _local_protection_v13.select_sweep_structural_protection_v2(
            detection, market, analysis, **inputs
        )
    _deny("owned_family_selection_strategy_invalid")


def _freeze_owned_family_policy_v2(strategy):
    """Capture actual existing numeric/settings policy and exact family before capture."""
    family = bracket_policy.fixed_native_owned_family_policy_v2(strategy)
    if strategy in _BASE_STRATEGIES:
        return _freeze_local_gate_policy_v13()
    if strategy not in _OWNED_HISTORY_FAMILIES_V2:
        _deny("owned_family_policy_strategy_invalid")
    value = decode(_freeze_local_gate_policy_v13())
    value.update(
        schema_version="ctcc.owned_original_family_gate_policy.v2",
        selected_family=decode(family),
        selected_family_sha256=sha(family),
    )
    return canonical(value)


def _compute_owned_family_v2(replay):
    """Pure arithmetic on already privately replayed originals, not an issuer."""
    packet, private, _, g1, original, created, deadline = replay
    if (
        type(g1) is not data_v2.DataQualificationResultV2
        or not g1.passed
        or type(original) is not precursor.OwnedOriginalCandidatePrecursorV3
        or type(original.intent) is not QualificationIntent
        or original.intent.strategy not in _OWNED_HISTORY_FAMILIES_V2
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
        policy_id="ctcc-native-owned-original-family-g1-g4-v2",
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

    history_json = _owned_conditions_v2._evaluate_owned_history_g2_g4_v2(
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
        detection=TriggerDetection.model_validate_json(
            canonical(document["detection"]), strict=True
        ),
        original_source_sha256=g1.source_sha256,
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
    result = OwnedFamilyEntryQualificationResultV2(**values, gates=tuple(gates))
    receipt = canonical(
        {
            "schema_version": "ctcc.current_original_family_prefix.v2",
            "family_policy_sha256": values["family_policy_sha256"],
            "original_history_record_sha256": sha(history_json),
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
    return CurrentOriginalFamilyPrefixInputsV2(
        intent, g1, result, detection, zone, receipt, history_json
    )


def _evaluate_owned_family_local_math_v2(record, journal, *, observed_at):
    """Pure shared gate arithmetic; supplied values cannot mint a native owner."""
    b = decode(journal.document_json)
    computed = record["computed"]
    values = {
        name: getattr(computed.result, name)
        for name in type(computed.result).model_fields
    }
    gates = list(computed.result.gates)

    def gate(kind, code, reason, measured):
        gates.append(
            GateAssessment(
                report_id=computed.intent.report_id,
                gate=kind,
                passed=code == "passed",
                code=code,
                reason=reason,
                measured_values=measured,
            )
        )
        values["gates"] = tuple(gates)
        return code == "passed"

    now = observed_at
    quote = record["quote_context"].quote
    reference = (
        quote.ticker.ask if computed.intent.direction == "long" else quote.ticker.bid
    )
    policy = decode(record["policy_raw"])
    timing_policy = TimingPolicy.model_validate_json(
        json.dumps(policy["timing"][computed.intent.strategy]), strict=True
    )
    timing = evaluate_timing(
        computed.detection,
        current_time=now,
        candidate_created_at=computed.intent.created_at,
        candidate_expires_at=computed.intent.expires_at,
        reference_price=reference,
        consumed_event_keys=journal.consumed_event_keys,
        policy=timing_policy,
    )
    values["entry_timing_state"] = (
        "valid"
        if timing.timing_valid
        else "wait"
        if timing.action == "WAIT"
        else "cancel"
    )
    values["reference_price"] = reference
    if not gate(
        QualificationGate.TIMING,
        timing.code,
        timing.reason,
        {
            "event_key": timing.event_key,
            "journal_sha256": journal.sha256,
            "observed_at": now.isoformat(),
            "original_expires_at": computed.intent.expires_at.isoformat(),
            "timing_policy_sha256": sha(
                canonical(timing_policy.model_dump(mode="json", round_trip=True))
            ),
            "local_ledger_revision": b["ledger_revision"],
            "scope": "finite_local_CTCC_uid_event_only",
            "reservation_performed": False,
        },
    ):
        return _owned_result_from_values_v2(computed.result, values)
    values["entry_zone"] = computed.original_zone
    quote_context = _replay_local_quote_v13(
        record["public_packet"], record["quote_value"], computed, evaluated_at=now
    )
    location = _location_local_quote_v13(computed, quote_context, observed_at=now)
    if not gate(
        QualificationGate.LOCATION,
        location.code,
        location.reason,
        {
            "original_zone_sha256": sha(
                canonical(computed.original_zone.model_dump(mode="json"))
            ),
            "entry_unchanged": True,
            "quote_sha256": location.quote_sha256,
        },
    ):
        return _owned_result_from_values_v2(computed.result, values)
    # Recompute ONLY on the same original source/time/policy. This proves
    # the retained audit, not selection of another current bracket.
    selected = _select_owned_family_protection_v2(
        computed.detection,
        record["market"],
        record["analysis"],
        observed_at=record["created_at"],
        entry=computed.intent.candidate_entry,
        tick_size=record["tick_size"],
        **{
            name: value
            for name, value in record["protection"].model_dump().items()
            if name != "policy_id"
        },
    )
    if selected.to_audit_json() != record["selection_raw"]:
        _deny("original_local_gate_bracket_changed")
    bracket = selected.selected if selected.protection_valid else None
    if bracket is not None:
        values["stop_loss"] = bracket.stop.final_stop
    if not gate(
        QualificationGate.STOP,
        "passed"
        if selected.protection_valid
        else selected.fail_codes[0]
        if selected.fail_codes
        else "no_valid_structural_bracket",
        "The same original source-selected joint bracket is required.",
        {
            "original_selection_sha256": sha(record["selection_raw"].encode()),
            "entry_unchanged": True,
        },
    ):
        return _owned_result_from_values_v2(computed.result, values)
    values["take_profit"] = bracket.target.final_target
    if not gate(
        QualificationGate.TARGET,
        "passed"
        if bracket.valid and not bracket.target.rejection_codes
        else "structural_target_invalid",
        "The target remains from the same fixed original bracket.",
        {"stop_unchanged": True, "target_unchanged": True},
    ):
        return _owned_result_from_values_v2(computed.result, values)
    economics_policy = EconomicsPolicy.model_validate_json(
        json.dumps(policy["economics"]), strict=True
    )
    costs = _economics_local_quote_v13(
        record,
        quote_context,
        stop=values["stop_loss"],
        target=values["take_profit"],
        policy=economics_policy,
        observed_at=now,
    )
    if costs.passed:
        with localcontext(Context(prec=100)):
            values["gross_rr"] = costs.gross_rr.quantize(Decimal("1e-30"))
            values["net_rr"] = costs.net_rr.quantize(Decimal("1e-30"))
    if not gate(
        QualificationGate.ECONOMICS,
        costs.code,
        costs.reason,
        {
            "cost_policy_sha256": costs.policy_sha256,
            "quote_sha256": costs.quote_sha256,
            "net_rr": costs.net_rr,
            "entry_unchanged": True,
            "stop_unchanged": True,
            "target_unchanged": True,
        },
    ):
        return _owned_result_from_values_v2(computed.result, values)
    return _owned_result_from_values_v2(computed.result, values)
