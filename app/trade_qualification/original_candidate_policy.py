"""Fixed original-entry/event precursor from exact raw sources, never authority."""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Context, Decimal, localcontext
from fractions import Fraction

from app.domain.analysis import MultiTimeframeAnalysis
from app.domain.market import MarketSnapshot
from app.strategies.base import StrategyContext
from app.strategies.conditions import assess_conditions
from app.strategies.regime import RouteDecision, route_regime
from app.trade_qualification import account_capture as account
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import data as data_gate
from app.trade_qualification import market_bridge
from app.trade_qualification.captured_instrument_rules import (
    derive_captured_instrument_rules,
)
from app.trade_qualification.events import STRATEGIES, extract_trigger
from app.trade_qualification.location import build_entry_zone
from app.trade_qualification.service import QualificationIntent, _copy
from app.trade_qualification.timing import TIMING_POLICIES, event_identity

BASE_ENGINE_CONTRACT = "ctcc-original-base-precursor-v1"
SUPPORTED_STRATEGIES = frozenset(
    {"trend_pullback", "breakout_continuation", "fvg_return", "order_block_return"}
)
POLICY_BYTES = journal.canonical(
    {
        "version": "ctcc.original_candidate_policy.v1",
        "engine_adapter": BASE_ENGINE_CONTRACT,
        "mapped_engine": "app.trade_qualification.engine.PreEvidenceRun",
        "event_adapter": "app.trade_qualification.events.extract_trigger:closed_base_v1",
        "supported_strategies": sorted(SUPPORTED_STRATEGIES),
        "entry": "initial_captured_ask_for_long_bid_for_short_exact_tick_no_rounding",
        "minimum_predicate_score": 85,
        "context_minimum_rr": "2",
        "zone_drift_bps": "30",
        "expiry": "minimum_original_event_TIMING_POLICIES_and_preexisting_service_deadline",
        "history_fallback": False,
        "quantity_sizing": False,
        "protection_selection": False,
        "source_authenticity_verified": False,
        "execution_authority": False,
    }
)
POLICY_SHA256 = journal.digest(POLICY_BYTES)


class OriginalCandidatePolicyError(ValueError):
    """Static errors only; original market/account rows never enter error text."""


@dataclass(frozen=True, slots=True, repr=False)
class OriginalCandidatePrecursor:
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


def data_policy_sha256(policy):
    checked = _copy(policy, data_gate.DataQualificationPolicy)
    return data_gate._sha(data_gate._canonical(checked))


def _deny(code):
    raise OriginalCandidatePolicyError(code)


def derive_original_candidate_precursor(
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
    """No direction, entry, event, expiry, score, quantity or protection input.

    Supplied timestamps are explicit replay cutoffs, never a native-clock claim.
    The future owner must pin profile/strategy/deadline before acquisition and
    retain both real source owners, then independently run the complete engine.
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
    except OriginalCandidatePolicyError:
        raise
    except Exception:  # noqa: BLE001 -- redacted source/analysis errors
        raise OriginalCandidatePolicyError(
            "original_candidate_source_invalid"
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
        _deny("original_candidate_profile_invalid")
    if type(policy_pin) is not str or policy_pin != POLICY_SHA256:
        _deny("original_candidate_policy_mismatch")
    policy = _copy(data_policy, data_gate.DataQualificationPolicy)
    if type(data_pin) is not str or data_policy_sha256(policy) != data_pin:
        _deny("original_candidate_data_policy_mismatch")
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
        _deny("original_candidate_creation_precedes_sources")
    value = {
        "schema_version": "ctcc.original_candidate_precursor.v1",
        "policy_sha256": POLICY_SHA256,
        "selected_engine_adapter": contract,
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
        "g1": None,
        "route": None,
        "conditions": None,
        "direction": None,
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
        return OriginalCandidatePrecursor(intent, journal.canonical(value))

    if contract != BASE_ENGINE_CONTRACT or strategy not in SUPPORTED_STRATEGIES:
        return finish("NO_TRADE", "selected_strategy_version_not_integrated")
    if deadline <= created:
        return finish("CANCEL", "service_deadline_expired")
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
        if (
            route.decision != RouteDecision.ALLOW_SCORING
            or strategy not in route.allowed_strategies
        ):
            return finish("NO_TRADE", "source_regime_strategy_not_allowed")
        # These are existing predicate context fields, not sizing or a bracket.
        # No legacy candidate-builder or ATR/RR protection generator is called.
        assessed = assess_conditions(
            StrategyContext(analysis, market, 85, Decimal(2)), strategy
        )
        value["direction"] = assessed.direction
        value["conditions"] = {
            "score": assessed.score,
            "required_failures": list(assessed.required_failures),
            "veto_failures": list(assessed.veto_failures),
        }
        if assessed.direction not in {"long", "short"}:
            return finish("WAIT", "source_direction_neutral")
        htf = assessed.for_group("htf")
        if not htf or any(
            (item.required or item.veto) and not item.passed for item in htf
        ):
            return finish("WAIT", "source_htf_permission_missing")
        if assessed.required_failures or assessed.veto_failures or assessed.score < 85:
            return finish("WAIT", "source_setup_predicates_rejected")
        quote = public.quote.quote
        if (
            quote.instrument_id != public.instrument_id
            or quote.report_id != public.report_id
            or public.quote.bundle_sha256 != g1.quote_bundle_sha256
        ):
            _deny("original_candidate_quote_binding_mismatch")
        side = "ask" if assessed.direction == "long" else "bid"
        entry = getattr(quote, side)
        value["initial_entry"], value["entry_quote_side"] = str(entry), side
        if (Fraction(entry) / Fraction(rules.tick_size)).denominator != 1:
            return finish("CANCEL", "initial_entry_off_tick")
        timing = TIMING_POLICIES[strategy]
        detection = extract_trigger(
            market,
            analysis,
            report_id=public.report_id,
            strategy=strategy,
            direction=assessed.direction,
            observed_at=created,
            trigger_ttl_seconds=timing.trigger_ttl_seconds,
        )
        value["detection"] = detection.model_dump(mode="json")
        value["event_key"] = event_identity(detection)
        if detection.source_sha256 != g1.source_sha256:
            return finish("NO_TRADE", "original_event_source_mismatch")
        trigger = detection.trigger
        if detection.fail_codes or trigger is None:
            return finish("WAIT", "original_event_missing_or_rejected")
        if trigger.invalidation_reason is not None:
            return finish("CANCEL", "original_event_invalidated")
        if created < trigger.trigger_time:
            _deny("original_candidate_creation_precedes_event")
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
        zone, code = build_entry_zone(
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
            direction=assessed.direction,
            candidate_entry=entry,
            created_at=created,
            expires_at=expiry,
        )
    # Full timing still requires the owned all-state event ledger. An empty
    # caller/default consumed-event set is never used to manufacture G6 PASS.
    return finish("WAIT", "precursor_derived_remaining_dependencies_unverified", intent)
