"""Versioned raw-history candidate precursor; no qualification or authority."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Context, Decimal, localcontext
from fractions import Fraction
from types import MappingProxyType

from app.domain.analysis import MultiTimeframeAnalysis
from app.domain.market import MarketSnapshot
from app.market.quality.candles import BAR_SECONDS
from app.strategies.base import StrategyContext
from app.strategies.conditions import assess_conditions
from app.strategies.regime import RouteDecision, route_regime
from app.trade_qualification import account_capture as account
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import candle_collector as candles
from app.trade_qualification import data as data_gate
from app.trade_qualification import market_bridge
from app.trade_qualification.captured_instrument_rules import (
    derive_captured_instrument_rules,
)
from app.trade_qualification.events import STRATEGIES, extract_trigger
from app.trade_qualification.history_prefix import expansion_htf_permission
from app.trade_qualification.location import (
    RANGE_ANCHOR_POLICY,
    build_entry_zone,
    build_original_range_anchor_zone,
)
from app.trade_qualification.original_candidate_policy import data_policy_sha256
from app.trade_qualification.range_policy import (
    RANGE_PROTECTION_POLICY,
    range_current_permission,
    replay_range_permission,
)
from app.trade_qualification.regime_admission import evaluate_regime_admission
from app.trade_qualification.reversal_policy import (
    REVERSAL_POLICY,
    reversal_current_permission,
)
from app.trade_qualification.service import QualificationIntent, _copy
from app.trade_qualification.timing import TIMING_POLICIES, event_identity

ENGINE_CONTRACTS = MappingProxyType(
    {
        "structure_reversal": "ctcc-history-pre-evidence-v3",
        "volatility_expansion": "ctcc-history-pre-evidence-v2",
        "range_reversal": "ctcc-history-pre-evidence-v5",
        "liquidity_sweep_reversal": "ctcc-history-pre-evidence-v1",
    }
)
POLICY_BYTES = journal.canonical(
    {
        "version": "ctcc.original_history_candidate_policy.v1",
        "engine_contracts": dict(ENGINE_CONTRACTS),
        "minimum_predicate_score": 85,
        "context_minimum_rr": "1",
        "zone_drift_bps": "30",
        "entry": "initial_captured_ask_for_long_bid_for_short_exact_tick_no_rounding",
        "expiry": "min_original_event_fixed_TIMING_POLICIES_service_deadline",
        "history": "existing_closed_prefix_events_and_regime_admission_v1",
        "expansion_htf_policy": "ctcc-expansion-htf-permission-v1",
        "reversal_policy": REVERSAL_POLICY,
        "range_protection_policy": RANGE_PROTECTION_POLICY,
        "range_anchor_policy": RANGE_ANCHOR_POLICY,
        "sweep_policy": "unspecified_WAIT",
        "past_first_availability": "unknown_not_predictive_PIT_proof",
        "full_qualification": False,
        "source_authenticity_verified": False,
        "execution_authority": False,
    }
)
POLICY_SHA256 = journal.digest(POLICY_BYTES)


class OriginalHistoryCandidatePolicyError(ValueError):
    """Redacted static errors; never include private source fields."""


@dataclass(frozen=True, slots=True, repr=False)
class OriginalHistoryCandidatePrecursor:
    intent: QualificationIntent | None
    receipt_json: bytes

    @property
    def receipt_sha256(self):
        return journal.digest(self.receipt_json)

    @property
    def execution_authority(self):
        return False

    @property
    def original_source_verified(self):
        return False


def _deny(code):
    raise OriginalHistoryCandidatePolicyError(code)


def _timeline(public, cutoff):
    """Join every raw row to its retained page; do not invent past receipts.

    The collector already replays ordering, duplicates, gaps and exact counts.
    This witness records that those rows are available at THIS declared cutoff.
    It does not attest when an exchange row first became available in the past.
    """
    output = []
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    for frame in public.candles.frames:
        seconds = BAR_SECONDS[frame.timeframe]
        expected = epoch + timedelta(
            seconds=((cutoff - epoch) // timedelta(seconds=seconds)) * seconds
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
    """Closed-row prefix indices only; old availability remains unknown."""
    basis = dict(detection.setup_basis)
    trigger = detection.trigger
    if trigger is None or detection.setup_time is None:
        return None
    native = "1H" if detection.strategy == "structure_reversal" else "15m"
    trigger_open = trigger.trigger_time - timedelta(seconds=BAR_SECONDS["5m"])
    if (
        basis.get("setup_timeframe") != native
        or account._utc(datetime.fromisoformat(basis["source_closed_at"]))
        != detection.setup_time
        or detection.setup_time > trigger_open
    ):
        _deny("history_event_chronology_mismatch")
    cutoffs = [
        ("setup", native, detection.setup_time),
        ("trigger", "5m", trigger.trigger_time),
    ]
    if "pivot_known_at" in basis:
        pivot = account._utc(datetime.fromisoformat(basis["pivot_known_at"]))
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


def derive_history_candidate_precursor(
    public_packet,
    account_packet,
    *,
    strategy,
    engine_contract,
    expected_public_bundle_sha256,
    expected_account_plan_sha256,
    expected_account_packet_sha256,
    data_policy,
    expected_data_policy_sha256,
    created_at,
    service_deadline,
    expected_policy_sha256=POLICY_SHA256,
):
    """Derive original geometry from raw history, never from caller decisions.

    Cutoffs/profile pins remain declared inputs, not an owned runtime producer.
    The complete selected engine, event ledger, sizing and protection must still
    run inside the future same-invocation owner before G12 and fresh recheck.
    """
    try:
        return _derive(
            public_packet,
            account_packet,
            strategy,
            engine_contract,
            expected_public_bundle_sha256,
            expected_account_plan_sha256,
            expected_account_packet_sha256,
            data_policy,
            expected_data_policy_sha256,
            created_at,
            service_deadline,
            expected_policy_sha256,
        )
    except OriginalHistoryCandidatePolicyError:
        raise
    except Exception:  # noqa: BLE001 -- redact source and analysis failures
        raise OriginalHistoryCandidatePolicyError(
            "history_candidate_source_invalid"
        ) from None


def _derive(
    public_packet,
    account_packet,
    strategy,
    contract,
    public_pin,
    plan_pin,
    account_pin,
    data_policy,
    data_pin,
    created_at,
    service_deadline,
    policy_pin,
):
    if (
        type(strategy) is not str
        or strategy not in STRATEGIES
        or type(contract) is not str
        or len(contract) > 80
    ):
        _deny("history_candidate_profile_invalid")
    if type(policy_pin) is not str or policy_pin != POLICY_SHA256:
        _deny("history_candidate_policy_mismatch")
    policy = _copy(data_policy, data_gate.DataQualificationPolicy)
    if type(data_pin) is not str or data_policy_sha256(policy) != data_pin:
        _deny("history_candidate_data_policy_mismatch")
    created, deadline = account._utc(created_at), account._utc(service_deadline)
    public = market_bridge._checked(public_packet, public_pin)
    rules = derive_captured_instrument_rules(
        account_packet,
        instrument_id=public.instrument_id,
        expected_plan_sha256=plan_pin,
        expected_packet_sha256=account_pin,
    )
    metadata = json.loads(rules.receipt_json)
    if created < max(
        public.completed_at,
        account_packet.completed_at,
        datetime.fromisoformat(metadata["measured_receipt"]["body_completed_at"]),
    ):
        _deny("history_candidate_creation_precedes_sources")
    value = {
        "schema_version": "ctcc.original_history_candidate_precursor.v1",
        "policy_sha256": POLICY_SHA256,
        "selected_engine_contract": contract,
        "strategy": strategy,
        "report_id": public.report_id,
        "instrument_id": public.instrument_id,
        "public_bundle_sha256": public.bundle_sha256,
        "account_plan_sha256": plan_pin,
        "account_packet_sha256": account_pin,
        "instrument_rules_sha256": rules.receipt_sha256,
        "data_policy_sha256": data_pin,
        "created_at": created.isoformat(),
        "service_deadline": deadline.isoformat(),
        "time_semantics": "declared_replay_cutoffs_not_native_clock_proof",
        "timeline": None,
        "timeline_sha256": None,
        "event_prefix_witness": None,
        "historical_first_availability_verified": False,
        "predictive_point_in_time_verified": False,
        "g1": None,
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
        "quantity": None,
        "leverage": None,
        "stop_loss": None,
        "take_profit": None,
        "pre_evidence_complete": False,
        "account_complete": False,
        "original_source_verified": False,
        "source_authenticity_verified": False,
        "execution_authority": False,
        "unverified": [
            "same_invocation_native_source_ownership",
            "current_owned_account_completeness",
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
            admission="DENY",
        )
        return OriginalHistoryCandidatePrecursor(intent, journal.canonical(value))

    if strategy not in ENGINE_CONTRACTS or contract != ENGINE_CONTRACTS[strategy]:
        return finish("NO_TRADE", "selected_strategy_version_not_integrated")
    if deadline <= created:
        return finish("CANCEL", "service_deadline_expired")
    timeline = _timeline(public, created)
    if timeline is None:
        return finish("WAIT", "confirmed_tail_missing")
    value["timeline"] = timeline
    value["timeline_sha256"] = journal.digest(journal.canonical(timeline))
    g1 = market_bridge.qualify_public_market(
        public,
        expected_bundle_sha256=public.bundle_sha256,
        policy=policy,
        evaluated_at=created,
    )
    value["g1"] = {
        "evaluation_sha256": g1.evaluation_sha256,
        "source_sha256": g1.source_sha256,
        "code": g1.gate.code,
        "passed": g1.passed,
    }
    if not g1.passed:
        return finish("NO_TRADE", "initial_G1_rejected")
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
            "source_sha256": route.snapshot_sha256,
        }
        assessed = assess_conditions(
            StrategyContext(analysis, market, 85, Decimal(1)), strategy
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
        if strategy != "range_reversal":
            history = evaluate_regime_admission(
                market,
                report_id=public.report_id,
                strategy=strategy,
                direction=direction,
                observed_at=created,
                analysis_version=policy.analysis_version,
            )
            value["history_admission"] = history.model_dump(mode="json")
            value["detection"] = (
                history.detection.model_dump(mode="json") if history.detection else None
            )
            value["event_key"] = history.event_key
            if history.source_sha256 not in (None, g1.source_sha256):
                _deny("history_candidate_source_binding_mismatch")
            if not history.admitted:
                return finish("WAIT", history.code)
        elif (
            route.decision != RouteDecision.ALLOW_SCORING
            or strategy not in route.allowed_strategies
        ):
            return finish("NO_TRADE", "source_regime_strategy_not_allowed")
        permission = (
            expansion_htf_permission(analysis, route, direction)
            if strategy == "volatility_expansion"
            else reversal_current_permission(analysis, market, route, direction)
            if strategy == "structure_reversal"
            else range_current_permission(analysis, market, route, direction)
        )
        value["current_permission"] = permission
        if not permission:
            return finish("WAIT", "source_htf_permission_missing")
        if assessed.required_failures or assessed.veto_failures or assessed.score < 85:
            return finish("WAIT", "source_setup_predicates_rejected")
        quote = public.quote.quote
        if (
            quote.instrument_id != public.instrument_id
            or quote.report_id != public.report_id
            or public.quote.bundle_sha256 != g1.quote_bundle_sha256
        ):
            _deny("history_candidate_quote_binding_mismatch")
        side = "ask" if direction == "long" else "bid"
        entry = getattr(quote, side)
        value.update(initial_entry=str(entry), entry_quote_side=side)
        if (Fraction(entry) / Fraction(rules.tick_size)).denominator != 1:
            return finish("CANCEL", "initial_entry_off_tick")
        timing = TIMING_POLICIES[strategy]
        detection = extract_trigger(
            market,
            analysis,
            report_id=public.report_id,
            strategy=strategy,
            direction=direction,
            observed_at=created,
            trigger_ttl_seconds=timing.trigger_ttl_seconds,
        )
        value.update(
            detection=detection.model_dump(mode="json"),
            event_key=event_identity(detection),
        )
        if detection.source_sha256 != g1.source_sha256 or (
            history is not None and detection != history.detection
        ):
            _deny("history_candidate_event_binding_mismatch")
        trigger = detection.trigger
        if detection.fail_codes or trigger is None or detection.setup_time is None:
            return finish("WAIT", "original_event_missing_or_rejected")
        if trigger.invalidation_reason is not None:
            return finish("CANCEL", "original_event_invalidated")
        if created < trigger.trigger_time:
            _deny("history_candidate_creation_precedes_event")
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
            report_id=public.report_id,
            instrument_id=public.instrument_id,
            strategy=strategy,
            direction=direction,
            candidate_entry=entry,
            created_at=created,
            expires_at=expiry,
        )
    # No empty consumed-event set, account claim, bracket or sizing is invented.
    return finish("WAIT", "precursor_derived_remaining_dependencies_unverified", intent)
