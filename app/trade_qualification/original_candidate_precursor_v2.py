"""Raw full-public V2 original intent diagnostics; never trading authority.

All strategy, history, trigger and zone mathematics are the existing evaluators.
This additive adapter fixes source-derived operands without selecting a bracket,
sizing exposure, assuming an empty event ledger or claiming native ownership.
"""

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Context, Decimal, localcontext
from fractions import Fraction
from gc import get_referents
from types import MappingProxyType

from app.domain.analysis import MultiTimeframeAnalysis
from app.domain.market import MarketSnapshot
from app.domain.source_primitives import decode
from app.market.quality.candles import BAR_SECONDS
from app.strategies.base import StrategyContext
from app.strategies.conditions import assess_conditions
from app.strategies.regime import RouteDecision, route_regime
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import candle_collector as candles
from app.trade_qualification import data, data_v2
from app.trade_qualification import public_market_collector_v2 as public
from app.trade_qualification.captured_instrument_rules import (
    derive_captured_instrument_rules,
)
from app.trade_qualification.events import extract_trigger
from app.trade_qualification.history_prefix import expansion_htf_permission
from app.trade_qualification.location import (
    RANGE_ANCHOR_POLICY,
    build_entry_zone,
    build_original_range_anchor_zone,
)
from app.trade_qualification.original_candidate_policy import BASE_ENGINE_CONTRACT
from app.trade_qualification.original_history_candidate_policy import ENGINE_CONTRACTS
from app.trade_qualification.range_policy import (
    RANGE_PROTECTION_POLICY,
    range_current_permission,
    replay_range_permission,
)
from app.trade_qualification.regime_admission import (
    evaluate_regime_admission,
    verify_regime_admission,
)
from app.trade_qualification.reversal_policy import (
    REVERSAL_POLICY,
    reversal_current_permission,
)
from app.trade_qualification.service import QualificationIntent
from app.trade_qualification.sweep_history_permission import (
    POLICY_ID as SWEEP_HISTORY_POLICY,
)
from app.trade_qualification.sweep_history_permission import (
    evaluate_sweep_history_permission,
    verify_sweep_history_permission,
)
from app.trade_qualification.timing import TIMING_POLICIES, event_identity

STRATEGY_CATALOG = (
    "trend_pullback",
    "breakout_continuation",
    "liquidity_sweep_reversal",
    "fvg_return",
    "order_block_return",
    "range_reversal",
    "structure_reversal",
    "volatility_expansion",
)
_BASE_STRATEGIES = frozenset(
    {"trend_pullback", "breakout_continuation", "fvg_return", "order_block_return"}
)
_HISTORY_STRATEGIES = frozenset({"structure_reversal", "volatility_expansion"})
_SWEEP = "liquidity_sweep_reversal"
_MAX_RECEIPT = 16 * 1024 * 1024
_DIGEST = re.compile(r"[a-f0-9]{64}")
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
POLICY_BYTES = journal.canonical(
    {
        "version": "ctcc.original_candidate_precursor_policy.v2",
        "public_schema": "ctcc.collected_public_market.v2",
        "g1_schema": "ctcc.data_qualification.v2",
        "public_stage": "initial_public",
        "environment": "demo",
        "g1": "actual_full_raw_evaluate_and_verify_shared_existing_math",
        "instrument": "actual_captured_demo_account_rules_exact_raw_replay",
        "strategy_catalog": list(STRATEGY_CATALOG),
        "minimum_predicate_score": 85,
        "base_context_minimum_rr": "2",
        "history_context_minimum_rr": "1",
        "zone_drift_bps": "30",
        "entry": "original_actual_v2_ask_long_bid_short_exact_tick_no_rounding",
        "expiry": "minimum_original_event_fixed_TIMING_POLICIES_preexisting_service_deadline",
        "event_replacement": False,
        "caller_candidate_operands": False,
        "caller_engine_callback": False,
        "history": "existing_source_recomputed_events_permissions_raw_page_prefix_witness",
        "expansion_htf_policy": "ctcc-expansion-htf-permission-v1",
        "reversal_policy": REVERSAL_POLICY,
        "range_protection_policy": RANGE_PROTECTION_POLICY,
        "range_anchor_policy": RANGE_ANCHOR_POLICY,
        "sweep_history_policy": SWEEP_HISTORY_POLICY,
        "sweep_stage_C_integrated": False,
        "sweep_regime_relabel": False,
        "funding": "actual_forecast_rate_upcoming_fundingTime_pair_generation_unknown",
        "historical_first_availability": "unknown_not_predictive_PIT",
        "quantity_sizing": False,
        "protection_selection": False,
        "event_ledger_assumed_empty": False,
        "full_qualification": False,
        "successful_derivation_action": "WAIT",
        "admission": "DENY",
        "source_authenticity_verified": False,
        "original_source_verified": False,
        "execution_authority": False,
    }
)
POLICY_SHA256 = journal.digest(POLICY_BYTES)


class OriginalCandidatePrecursorV2Error(ValueError):
    """Static, redacted denials; private source fields never enter error text."""


def _deny(code):
    raise OriginalCandidatePrecursorV2Error(code)


def _native_mapping(value):
    """Inspect a proxy referent before a foreign Mapping can execute code."""
    if type(value) is MappingProxyType:
        referents = get_referents(value)
        if len(referents) != 1 or type(referents[0]) is not dict:
            _deny("precursor_v2_native_mapping_required")
        return referents[0]
    if type(value) is not dict:
        _deny("precursor_v2_native_mapping_required")
    return value


def _model_fields(value, expected):
    if type(value) is not expected:
        _deny("precursor_v2_exact_model_required")
    fields = _native_mapping(object.__getattribute__(value, "__dict__"))
    if len(fields) != len(expected.model_fields) or any(
        type(key) is not str or key not in expected.model_fields for key in fields
    ):
        _deny("precursor_v2_exact_model_fields_required")
    for name in ("__pydantic_extra__", "__pydantic_private__"):
        item = object.__getattribute__(value, name)
        if item is not None and len(_native_mapping(item)) != 0:
            _deny("precursor_v2_hidden_model_fields")
    selected = object.__getattribute__(value, "__pydantic_fields_set__")
    if (
        type(selected) is not set
        or len(selected) > len(expected.model_fields)
        or any(
            type(key) is not str or key not in expected.model_fields for key in selected
        )
    ):
        _deny("precursor_v2_model_field_set_invalid")
    return fields


def _exact_utc(value):
    # No utcoffset/astimezone call can reach a caller-defined tzinfo callback.
    if type(value) is not datetime or value.tzinfo is not UTC:
        _deny("precursor_v2_exact_utc_required")
    return value


def _bounded_decimal(value):
    if type(value) is not Decimal:
        _deny("precursor_v2_exact_decimal_required")
    if (
        not value.is_finite()
        or len(value.as_tuple().digits) > 128
        or abs(value.as_tuple().exponent) > 128
        or len(str(value)) > 128
    ):
        _deny("precursor_v2_decimal_bound")
    return value


def _intent_tree(value):
    """Exact bounded primitives before serialization, equality or replay."""
    if value is None:
        return None
    fields = _model_fields(value, QualificationIntent)
    for name in ("report_id", "instrument_id", "strategy", "direction"):
        item = fields[name]
        if type(item) is not str or not 1 <= len(item) <= 128:
            _deny("precursor_v2_intent_scalar_bound")
    price = _bounded_decimal(fields["candidate_entry"])
    created = _exact_utc(fields["created_at"])
    expiry = _exact_utc(fields["expires_at"])
    if (
        price <= 0
        or price >= Decimal("1e20")
        or fields["strategy"] not in STRATEGY_CATALOG
        or fields["direction"] not in {"long", "short"}
        or expiry <= created
    ):
        _deny("precursor_v2_intent_identity_or_lifetime_invalid")
    return {
        **{
            name: fields[name]
            for name in fields
            if name not in {"candidate_entry", "created_at", "expires_at"}
        },
        "candidate_entry": str(price),
        "created_at": created.isoformat(),
        "expires_at": expiry.isoformat(),
    }


@dataclass(frozen=True, slots=True, repr=False)
class OriginalCandidatePrecursorV2:
    intent: QualificationIntent | None
    receipt_json: bytes

    @property
    def receipt_sha256(self):
        raw, _ = _preflight_result(self)
        return journal.digest(raw)

    @property
    def execution_authority(self):
        return False

    @property
    def original_source_verified(self):
        return False


def _preflight_result(value):
    if type(value) is not OriginalCandidatePrecursorV2:
        _deny("precursor_v2_exact_result_required")
    raw = object.__getattribute__(value, "receipt_json")
    if type(raw) is not bytes or not 1 <= len(raw) <= _MAX_RECEIPT:
        _deny("precursor_v2_receipt_bytes_bound")
    intent = _intent_tree(object.__getattribute__(value, "intent"))
    # Parsing native bounded bytes cannot invoke caller iterators/serializers.
    document = decode(raw, _MAX_RECEIPT)
    if journal.canonical(document) != raw:
        _deny("precursor_v2_receipt_not_canonical")
    return raw, intent


def _checked_data_policy(value, expected):
    fields = _model_fields(value, data.DataQualificationPolicy)
    text_fields = {"policy_id", "analysis_version"}
    integer_fields = {
        "minimum_confirmed_bars",
        "maximum_snapshot_age_seconds",
        "maximum_quote_age_seconds",
        "maximum_reference_age_seconds",
        "maximum_candle_age_intervals",
    }
    for name, item in fields.items():
        if name in text_fields:
            if type(item) is not str or not 1 <= len(item) <= 512:
                _deny("precursor_v2_data_policy_scalar_invalid")
        elif name in integer_fields:
            if type(item) is not int or abs(item) > 10**40:
                _deny("precursor_v2_data_policy_scalar_invalid")
        else:
            _bounded_decimal(item)
    policy = data._bounded_scalars(value, data.DataQualificationPolicy)
    if type(expected) is not str or data._sha(data._canonical(policy)) != expected:
        _deny("precursor_v2_data_policy_mismatch")
    return policy


def _timeline(candle_packet, cutoff):
    """Retain raw/page ordering and receipt observations at this cutoff only."""
    output = []
    for frame in candle_packet.frames:
        seconds = BAR_SECONDS[frame.timeframe]
        expected = _EPOCH + timedelta(
            seconds=((cutoff - _EPOCH) // timedelta(seconds=seconds)) * seconds
        )
        if frame.verified_through != expected:
            return None
        rows = []
        for page in frame.pages:
            if page.completed_at > cutoff:
                _deny("history_receipt_after_decision_cutoff")
            raw, _ = candles._parse(page.response_body)
            parsed = candles._rows(raw, frame.timeframe, page.received_at)
            for index, (values, row) in enumerate(zip(raw, parsed, strict=True)):
                closed = row.timestamp + timedelta(seconds=seconds)
                if row.confirmed and closed > min(frame.verified_through, cutoff):
                    _deny("history_closed_row_after_cutoff")
                rows.append(
                    {
                        "row_index": index,
                        "page_index": page.page_index,
                        "page_body_sha256": page.body_sha256,
                        "raw_row_sha256": journal.digest(journal.canonical(values)),
                        "request_started_at": page.request_started_at.isoformat(),
                        "headers_received_at": page.received_at.isoformat(),
                        "body_completed_at": page.completed_at.isoformat(),
                        "open_at": row.timestamp.isoformat(),
                        "closed_at": closed.isoformat(),
                        "confirmed": row.confirmed,
                        "historical_first_available_at": None,
                    }
                )
        output.append(
            {
                "timeframe": frame.timeframe,
                "instrument_id": frame.instrument_id,
                "frame_sha256": frame.frame_sha256,
                "verified_through": frame.verified_through.isoformat(),
                "confirmed_count": len(frame.confirmed),
                "rows_in_original_page_order": rows,
            }
        )
    return output


def _prefix_witness(timeline, detection):
    basis = dict(detection.setup_basis)
    trigger = detection.trigger
    if trigger is None or detection.setup_time is None:
        return None
    native = "1H" if detection.strategy == "structure_reversal" else "15m"
    trigger_open = trigger.trigger_time - timedelta(seconds=BAR_SECONDS["5m"])
    if (
        basis.get("setup_timeframe") != native
        or datetime.fromisoformat(basis["source_closed_at"]) != detection.setup_time
        or detection.setup_time > trigger_open
    ):
        _deny("history_event_chronology_mismatch")
    cutoffs = [
        ("setup", native, detection.setup_time),
        ("trigger", "5m", trigger.trigger_time),
    ]
    if "pivot_known_at" in basis:
        pivot = datetime.fromisoformat(basis["pivot_known_at"])
        if pivot > detection.setup_time:
            _deny("history_pivot_after_setup")
        cutoffs.append(("pivot_confirmation", native, pivot))
    output = []
    for role, timeframe, cutoff in cutoffs:
        frame = next(item for item in timeline if item["timeframe"] == timeframe)
        rows = [
            row
            for row in frame["rows_in_original_page_order"]
            if row["confirmed"] and datetime.fromisoformat(row["closed_at"]) <= cutoff
        ]
        if not rows or not any(
            datetime.fromisoformat(row["closed_at"]) == cutoff for row in rows
        ):
            _deny("history_event_prefix_missing")
        output.append(
            {
                "role": role,
                "timeframe": timeframe,
                "closed_row_cutoff": cutoff.isoformat(),
                "eligible_row_count": len(rows),
                "eligible_rows_sha256": journal.digest(journal.canonical(rows)),
                "past_receipt_availability_verified": False,
            }
        )
    return output


def derive_original_candidate_precursor_v2(
    public_packet,
    account_packet,
    *,
    strategy,
    expected_public_bundle_sha256,
    expected_account_plan_sha256,
    expected_account_packet_sha256,
    data_policy,
    expected_data_policy_sha256,
    created_at,
    service_deadline,
    expected_policy_sha256=POLICY_SHA256,
):
    """Recompute a fixed incomplete original intent from actual raw source."""
    try:
        return _derive(
            public_packet,
            account_packet,
            strategy,
            expected_public_bundle_sha256,
            expected_account_plan_sha256,
            expected_account_packet_sha256,
            data_policy,
            expected_data_policy_sha256,
            created_at,
            service_deadline,
            expected_policy_sha256,
        )
    except OriginalCandidatePrecursorV2Error:
        raise
    except Exception:  # noqa: BLE001 -- raw source/analysis errors stay redacted
        raise OriginalCandidatePrecursorV2Error("precursor_v2_source_invalid") from None


def _derive(
    public_packet,
    account_packet,
    strategy,
    public_pin,
    plan_pin,
    account_pin,
    data_policy,
    data_pin,
    created_at,
    service_deadline,
    policy_pin,
):
    if type(strategy) is not str or strategy not in STRATEGY_CATALOG:
        _deny("precursor_v2_strategy_invalid")
    if type(policy_pin) is not str or policy_pin != POLICY_SHA256:
        _deny("precursor_v2_policy_mismatch")
    for pin in (public_pin, plan_pin, account_pin, data_pin):
        if type(pin) is not str or _DIGEST.fullmatch(pin) is None:
            _deny("precursor_v2_source_pin_invalid")
    created, deadline = _exact_utc(created_at), _exact_utc(service_deadline)
    policy = _checked_data_policy(data_policy, data_pin)
    checked, (_, quote, _, candle_packet, _, _) = public._parts(public_packet)
    document = decode(checked.packet_json, public.MAX_PACKET_BYTES)
    if checked.bundle_sha256 != public_pin:
        _deny("precursor_v2_public_pin_mismatch")
    if (
        document["stage"] != "initial_public"
        or document["barrier_completed_at"] is not None
        or document["environment"] != "demo"
    ):
        _deny("precursor_v2_original_initial_demo_source_required")
    # Always run actual raw G1 and independent verifier. A caller G1/context
    # cannot enter this API, and no V1 funding generation timestamp is invented.
    g1 = data_v2.evaluate_public_market_data_v2(
        checked, expected_bundle_sha256=public_pin, policy=policy, evaluated_at=created
    )
    g1 = data_v2.verify_public_market_data_v2(
        g1,
        checked,
        expected_bundle_sha256=public_pin,
        policy=policy,
        evaluated_at=created,
    )
    rules = derive_captured_instrument_rules(
        account_packet,
        instrument_id=document["instrument_id"],
        expected_plan_sha256=plan_pin,
        expected_packet_sha256=account_pin,
    )
    metadata = json.loads(rules.receipt_json)
    identity = metadata["account_identity"]
    if (
        identity["environment"] != document["environment"]
        or rules.instrument_id != document["instrument_id"]
        or rules.settlement_currency != "USDT"
    ):
        _deny("precursor_v2_account_instrument_identity_mismatch")
    if created < max(
        public._time(document["completed_at"]),
        account_packet.completed_at,
        datetime.fromisoformat(metadata["measured_receipt"]["body_completed_at"]),
    ):
        _deny("precursor_v2_creation_precedes_sources")
    value = {
        "schema_version": "ctcc.original_candidate_precursor.v2",
        "policy_sha256": POLICY_SHA256,
        "strategy": strategy,
        "selected_engine_adapter": (
            BASE_ENGINE_CONTRACT
            if strategy in _BASE_STRATEGIES
            else "ctcc-sweep-history-evidence-v1-stage-C-closed"
            if strategy == _SWEEP
            else ENGINE_CONTRACTS[strategy]
        ),
        "report_id": document["report_id"],
        "instrument_id": document["instrument_id"],
        "environment": "demo",
        "public_stage": "initial_public",
        "public_invocation_id": document["invocation_id"],
        "public_bundle_sha256": public_pin,
        "account_plan_sha256": plan_pin,
        "account_packet_sha256": account_pin,
        "account_identity_sha256": journal.digest(journal.canonical(identity)),
        "instrument_rules_sha256": rules.receipt_sha256,
        "instrument_raw_row_sha256": metadata["source_binding"]["row_sha256"],
        "data_policy_sha256": data_pin,
        "created_at": created.isoformat(),
        "service_deadline": deadline.isoformat(),
        "time_semantics": "declared_replay_cutoffs_not_native_clock_proof",
        "g1": {
            "schema_version": g1.schema_version,
            "evaluation_sha256": g1.evaluation_sha256,
            "source_sha256": g1.source_sha256,
            "policy_sha256": g1.policy_sha256,
            "quote_bundle_sha256": g1.quote_bundle_sha256,
            "quote_profile_sha256": g1.quote_profile_sha256,
            "quote_transport_policy_sha256": g1.quote_transport_policy_sha256,
            "quote_inspection_sha256": g1.quote_inspection_sha256,
            "code": g1.gate.code,
            "passed": g1.passed,
        },
        "funding_pair": decode(g1.funding_pair_json.encode()),
        "funding_pair_sha256": g1.funding_pair_sha256,
        "timeline": None,
        "timeline_sha256": None,
        "event_prefix_witness": None,
        "route": None,
        "conditions": None,
        "direction": None,
        "history_admission": None,
        "current_permission": None,
        "range_permission_replay_sha256": None,
        "initial_entry": None,
        "entry_quote_side": None,
        "detection": None,
        "event_key": None,
        "zone": None,
        "original_event_expires_at": None,
        "timing_deadline": None,
        "expires_at": None,
        "intent": None,
        "quantity": None,
        "leverage": None,
        "stop_loss": None,
        "take_profit": None,
        "actual_rr": None,
        "portfolio_risk": None,
        "event_ledger_revision": None,
        "native_clock_verified": False,
        "metadata_current_owned": False,
        "historical_first_availability_verified": False,
        "predictive_point_in_time_verified": False,
        "pre_evidence_complete": False,
        "account_complete": False,
        "original_source_verified": False,
        "source_authenticity_verified": False,
        "qualification_performed": False,
        "execution_recheck_performed": False,
        "atomic_risk_reserved": False,
        "execution_authority": False,
        "sweep_stage_C_integrated": False,
        "unverified": [
            "same_invocation_native_source_ownership",
            "current_owned_account_completeness",
            "current_owned_instrument_metadata",
            "all_state_event_ledger",
            "full_selected_G1_G11_replay",
            "structural_SL_TP_selection",
            "source_bound_sizing_and_leverage",
            "G12",
            "post_G12_recheck",
            "reservation",
            "durable_intent",
        ],
    }

    def finish(action, code, intent=None):
        value.update(
            action=action,
            code=code,
            precursor_derived=intent is not None,
            intent=_intent_tree(intent),
            admission="DENY",
        )
        encoded = journal.canonical(value)
        if len(encoded) > _MAX_RECEIPT:
            _deny("precursor_v2_receipt_bytes_bound")
        return OriginalCandidatePrecursorV2(intent, encoded)

    if deadline <= created:
        return finish("CANCEL", "service_deadline_expired")
    if not g1.passed:
        return finish("NO_TRADE", "initial_G1_rejected")
    timeline = _timeline(candle_packet, created)
    if timeline is None:
        return finish("WAIT", "confirmed_tail_missing")
    value["timeline"] = timeline
    value["timeline_sha256"] = journal.digest(journal.canonical(timeline))
    source = json.loads(g1.source_json)
    market = MarketSnapshot.model_validate_json(
        json.dumps(source["market"]), strict=True
    )
    analysis = MultiTimeframeAnalysis.model_validate_json(
        json.dumps(source["analysis"]), strict=True
    )
    with localcontext(Context(prec=100)):
        route = route_regime(analysis)
        value["route"] = {
            "regime": route.regime.value,
            "decision": route.decision.value,
            "allowed_strategies": list(route.allowed_strategies),
            "fail_codes": list(route.fail_codes),
            "source_sha256": route.snapshot_sha256,
        }
        if strategy in _BASE_STRATEGIES and (
            route.decision != RouteDecision.ALLOW_SCORING
            or strategy not in route.allowed_strategies
        ):
            return finish("NO_TRADE", "source_regime_strategy_not_allowed")
        assessed = assess_conditions(
            StrategyContext(
                analysis,
                market,
                85,
                Decimal(2) if strategy in _BASE_STRATEGIES else Decimal(1),
            ),
            strategy,
        )
        direction = assessed.direction
        value["direction"] = direction
        value["conditions"] = {
            "score": assessed.score,
            "required_failures": list(assessed.required_failures),
            "veto_failures": list(assessed.veto_failures),
        }
        if direction not in {"long", "short"}:
            return finish("WAIT", "source_direction_neutral")
        history = None
        if strategy in _HISTORY_STRATEGIES:
            inputs = {
                "report_id": document["report_id"],
                "strategy": strategy,
                "direction": direction,
                "observed_at": created,
                "analysis_version": policy.analysis_version,
            }
            history = evaluate_regime_admission(market, **inputs)
            history = verify_regime_admission(history, market, **inputs)
            value["history_admission"] = history.model_dump(mode="json")
            value["detection"] = (
                history.detection.model_dump(mode="json") if history.detection else None
            )
            value["event_key"] = history.event_key
            if history.source_sha256 not in (None, g1.source_sha256):
                _deny("history_candidate_source_binding_mismatch")
            if not history.admitted:
                return finish("WAIT", history.code)
        elif strategy == _SWEEP:
            inputs = {
                "report_id": document["report_id"],
                "direction": direction,
                "observed_at": created,
                "analysis_version": policy.analysis_version,
            }
            sweep = evaluate_sweep_history_permission(market, **inputs)
            sweep = verify_sweep_history_permission(sweep, market, **inputs)
            sweep_record = json.loads(sweep.receipt_json)
            value["history_admission"] = sweep_record
            value["detection"], value["event_key"] = (
                sweep_record["detection"],
                sweep_record["event_key"],
            )
            if sweep_record["source_sha256"] not in (None, g1.source_sha256):
                _deny("history_candidate_source_binding_mismatch")
            if not sweep.admitted:
                return finish("WAIT", sweep.code)
        elif strategy == "range_reversal" and (
            route.decision != RouteDecision.ALLOW_SCORING
            or strategy not in route.allowed_strategies
        ):
            return finish("NO_TRADE", "source_regime_strategy_not_allowed")
        if strategy in _BASE_STRATEGIES:
            htf = assessed.for_group("htf")
            permission = bool(htf) and not any(
                (item.required or item.veto) and not item.passed for item in htf
            )
        elif strategy == "volatility_expansion":
            permission = expansion_htf_permission(analysis, route, direction)
        elif strategy == "structure_reversal":
            permission = reversal_current_permission(analysis, market, route, direction)
        elif strategy == "range_reversal":
            permission = range_current_permission(analysis, market, route, direction)
        else:
            # Actual sweep permission was replayed above, retaining Unknown and
            # all original blockers. Its history scope cannot open Stage C.
            permission = sweep.admitted
        value["current_permission"] = permission
        if not permission:
            return finish("WAIT", "source_htf_permission_missing")
        if assessed.required_failures or assessed.veto_failures or assessed.score < 85:
            return finish("WAIT", "source_setup_predicates_rejected")
        if (
            quote.instrument_id != document["instrument_id"]
            or quote.report_id != document["report_id"]
            or document["quote_sha256"] != g1.quote_bundle_sha256
        ):
            _deny("precursor_v2_quote_binding_mismatch")
        side = "ask" if direction == "long" else "bid"
        entry = getattr(quote.ticker, side)
        value.update(initial_entry=str(entry), entry_quote_side=side)
        if (Fraction(entry) / Fraction(rules.tick_size)).denominator != 1:
            return finish("CANCEL", "initial_entry_off_tick")
        timing = TIMING_POLICIES[strategy]
        detection = extract_trigger(
            market,
            analysis,
            report_id=document["report_id"],
            strategy=strategy,
            direction=direction,
            observed_at=created,
            trigger_ttl_seconds=timing.trigger_ttl_seconds,
        )
        value.update(
            detection=detection.model_dump(mode="json"),
            event_key=event_identity(detection),
        )
        if detection.source_sha256 != g1.source_sha256:
            if strategy in _BASE_STRATEGIES:
                return finish("NO_TRADE", "original_event_source_mismatch")
            _deny("history_candidate_event_binding_mismatch")
        if (history is not None and detection != history.detection) or (
            strategy == _SWEEP
            and (
                value["detection"] != sweep_record["detection"]
                or value["event_key"] != sweep_record["event_key"]
            )
        ):
            _deny("history_candidate_event_binding_mismatch")
        trigger = detection.trigger
        if detection.fail_codes or trigger is None or detection.setup_time is None:
            return finish("WAIT", "original_event_missing_or_rejected")
        if trigger.invalidation_reason is not None:
            return finish("CANCEL", "original_event_invalidated")
        if created < trigger.trigger_time:
            _deny("precursor_v2_creation_precedes_event")
        if trigger.trigger_time - detection.setup_time > timedelta(
            seconds=timing.max_setup_to_trigger_seconds
        ):
            return finish("WAIT", "setup_expired")
        value["event_prefix_witness"] = _prefix_witness(timeline, detection)
        timing_deadline = trigger.trigger_time + timedelta(
            seconds=timing.trigger_ttl_seconds
        )
        expiry = min(trigger.expires_at, timing_deadline, deadline)
        value.update(
            original_event_expires_at=trigger.expires_at.isoformat(),
            timing_deadline=timing_deadline.isoformat(),
            expires_at=expiry.isoformat(),
        )
        if expiry <= created:
            return finish("CANCEL", "original_event_or_service_expired")
        if strategy == "range_reversal":
            value["range_permission_replay_sha256"] = replay_range_permission(
                market, analysis, detection
            )
        builder = (
            build_original_range_anchor_zone
            if strategy == "range_reversal"
            else build_entry_zone
        )
        zone, code = builder(
            detection,
            tick_size=rules.tick_size,
            max_allowed_drift_bps=Decimal(30),
            expires_at=expiry,
        )
        if zone is None:
            return finish("WAIT", "original_zone_" + code)
        value["zone"] = zone.model_dump(mode="json")
        if not zone.zone_low <= entry <= zone.zone_high:
            return finish("CANCEL", "fixed_initial_entry_outside_original_zone")
        intent = QualificationIntent(
            report_id=document["report_id"],
            instrument_id=document["instrument_id"],
            strategy=strategy,
            direction=direction,
            candidate_entry=entry,
            created_at=created,
            expires_at=expiry,
        )
    return finish(
        "WAIT",
        "sweep_stage_C_closed"
        if strategy == _SWEEP
        else "precursor_derived_remaining_dependencies_unverified",
        intent,
    )


def verify_original_candidate_precursor_v2(
    result,
    public_packet,
    account_packet,
    *,
    strategy,
    expected_public_bundle_sha256,
    expected_account_plan_sha256,
    expected_account_packet_sha256,
    data_policy,
    expected_data_policy_sha256,
    created_at,
    service_deadline,
    expected_policy_sha256=POLICY_SHA256,
):
    """Bound the supplied tree first, then replay every actual original input."""
    raw, intent = _preflight_result(result)
    replayed = derive_original_candidate_precursor_v2(
        public_packet,
        account_packet,
        strategy=strategy,
        expected_public_bundle_sha256=expected_public_bundle_sha256,
        expected_account_plan_sha256=expected_account_plan_sha256,
        expected_account_packet_sha256=expected_account_packet_sha256,
        data_policy=data_policy,
        expected_data_policy_sha256=expected_data_policy_sha256,
        created_at=created_at,
        service_deadline=service_deadline,
        expected_policy_sha256=expected_policy_sha256,
    )
    if raw != replayed.receipt_json or intent != _intent_tree(replayed.intent):
        _deny("precursor_v2_original_replay_mismatch")
    return replayed
