"""Real G1–G11 evaluator proofs from synthetic raw OHLC and fictional Demo claims.

No evaluator is mocked, no precomputed gate/analysis/event/geometry is supplied,
and a passing computation still has no source authentication or order authority.
The independent timeframe examples do not claim one aggregated exchange tape.
"""

import hashlib
import json
from decimal import ROUND_DOWN, Context, Decimal, Inexact, Rounded, localcontext

import pytest

from app.market.quality.candles import candle_closed_at
from app.strategies.structural_protection import select_structural_protection
from app.trade_qualification.engine import (
    evaluate_pre_evidence,
    verify_pre_evidence,
)
from app.trade_qualification.models import QualificationGate, QualificationState
from tests.unit.qualification_engine_fixtures import (
    PROTECTION_PARAMETERS,
    REQUESTED_CONTRACTS,
    REQUESTED_LEVERAGE,
    engine_inputs,
    engine_source,
    portfolio_claims,
    run_engine_source,
)
from tests.unit.qualification_prefix_fixtures import (
    prefix_source,
    qualify_data,
    run_prefix_source,
)
from tests.unit.test_qualification_portfolio import STAMP_FIELDS

D = Decimal


@pytest.fixture(scope="module", params=["long", "short"])
def trace(request):
    return run_engine_source(engine_source(request.param))


@pytest.fixture(scope="module")
def run(trace):
    source = trace.prefix.source
    return evaluate_pre_evidence(source.market, **engine_inputs(source))


def _select(source):
    prefix = run_prefix_source(source)
    return prefix, select_structural_protection(
        prefix.detection,
        prefix.market,
        prefix.analysis,
        observed_at=source.evaluated_at,
        entry=source.market.ticker.last,
        tick_size=source.tick_size,
        **PROTECTION_PARAMETERS,
    )


def test_enrichment_changes_only_twelve_fixed_historical_raw_wicks(trace):
    source = trace.prefix.source
    baseline = prefix_source(source.direction)
    assert source.market.quality == baseline.market.quality == {}
    assert source.market.ticker == baseline.market.ticker
    assert source.market.order_book == baseline.market.order_book
    assert source.quote == baseline.quote and source.reference == baseline.reference
    changed = []
    for timeframe, rows in source.market.candles.items():
        originals = baseline.market.candles[timeframe]
        assert len(rows) == len(originals) == 240
        for index, (candle, original) in enumerate(zip(rows, originals, strict=True)):
            left, right = candle.model_dump(), original.model_dump()
            for key in left:
                if left[key] != right[key]:
                    changed.append((timeframe, index - 240, key))
            assert candle.open == candle.close == original.close
            assert candle.timestamp == original.timestamp
            assert candle_closed_at(candle, timeframe) <= source.evaluated_at
    high, low = ("high", "low") if source.direction == "long" else ("low", "high")
    assert set(changed) == {
        (timeframe, offset, field)
        for timeframe in ("15m", "1H", "4H")
        for offset, field in ((-30, high), (-25, low), (-20, low), (-15, high))
    }
    assert len(changed) == 12
    assert qualify_data(baseline).source_sha256 != trace.prefix.data.source_sha256


def test_original_trigger_and_zone_are_not_repriced_to_make_structure_pass(trace):
    prefix = trace.prefix
    original = run_prefix_source(prefix_source(prefix.source.direction))
    assert prefix.strategy.score == original.strategy.score == 100
    assert prefix.strategy.required_failures == prefix.strategy.veto_failures == ()
    assert prefix.detection.trigger == original.detection.trigger
    # Historical wicks legitimately change ATR; event boundaries and times do not.
    enriched_basis = dict(prefix.detection.setup_basis)
    original_basis = dict(original.detection.setup_basis)
    assert enriched_basis.pop("atr") != original_basis.pop("atr")
    assert enriched_basis == original_basis
    assert prefix.detection.source_sha256 != original.detection.source_sha256
    assert (prefix.zone.zone_low, prefix.zone.zone_high) == (
        original.zone.zone_low,
        original.zone.zone_high,
    )
    assert prefix.location.passed and prefix.timing.timing_valid
    assert trace.protection.reference_entry == prefix.source.market.ticker.last
    assert trace.economics.candidate_entry == prefix.detection.trigger.trigger_price


def test_all_raw_native_timeframe_candidates_are_evaluated_and_audited(trace):
    selection = trace.protection
    assert selection.evidence == "confirmed_ohlc" and selection.protection_valid
    assert len(selection.stops) == 10 and len(selection.targets) == 6
    assert len(selection.alternatives) == 60
    assert {
        (bracket.stop.anchor.anchor_id, bracket.target.anchor.anchor_id)
        for bracket in selection.alternatives
    } == {
        (stop.anchor.anchor_id, target.anchor.anchor_id)
        for stop in selection.stops
        for target in selection.targets
    }
    assert {target.anchor.timeframe for target in selection.targets} == {
        "15m",
        "1H",
        "4H",
    }
    for target in selection.targets:
        field = "high" if trace.prefix.source.direction == "long" else "low"
        rows = trace.prefix.market.candles[target.anchor.timeframe]
        witnesses = [
            row
            for row in rows[:-2]
            if getattr(row, field) == target.anchor.anchor_price
        ]
        assert witnesses, "a target needs an actual historical raw-price witness"
        assert target.anchor.known_at <= trace.prefix.source.evaluated_at
    assert selection.selected in selection.alternatives
    assert not selection.selected.rejection_codes
    assert any(bracket.rejection_codes for bracket in selection.alternatives)
    assert "noise_clearance_atr" in selection.selected.ranking_reason
    assert selection.execution_authority is selection.policy_calibrated is False


def test_selected_bracket_clears_original_thesis_and_unchanged_rr_threshold(trace):
    source = trace.prefix.source
    selection = trace.protection
    stop, target = selection.selected.stop, selection.selected.target
    expected = (
        (D("98.32"), D("107.23"))
        if source.direction == "long"
        else (D("101.68"), D("92.77"))
    )
    assert (stop.final_stop, target.final_target) == expected
    invalidation = trace.prefix.detection.invalidation_price
    assert (
        (stop.final_stop < invalidation)
        if source.direction == "long"
        else (stop.final_stop > invalidation)
    )
    assert stop.stop_distance_atr >= PROTECTION_PARAMETERS["min_stop_distance_atr"] == 1
    assert dict(selection.policy_inputs)["min_net_rr"] == D(2)
    assert selection.selected.net_rr >= D(2)
    assert trace.economics.policy.minimum_net_rr == D(2)
    assert D(2) < trace.economics.net_rr < selection.selected.net_rr


def test_risk_claims_are_explicit_complete_demo_only_and_fictional(trace):
    source = trace.prefix.source
    claims = portfolio_claims(source)
    account, authority = claims["account"], claims["authority"]
    assert account.account_id == "synthetic-demo-account"
    assert (
        account.positions == account.pending_reservations == account.loss_history == ()
    )
    assert account.position_count == account.pending_reservation_count == 0
    assert account.history_end == source.evaluated_at
    assert account.loss_streak_at_history_start == 0
    stamps = [getattr(account, name) for name in STAMP_FIELDS] + [authority.stamp]
    assert len({stamp.source_sha256 for stamp in stamps}) == 5
    assert all(stamp.complete and stamp.environment == "demo" for stamp in stamps)
    assert all(stamp.account_id == account.account_id for stamp in stamps)
    assert all(
        stamp.observed_at == stamp.received_at == source.evaluated_at
        for stamp in stamps
    )
    assert authority.environment == "demo" and authority.simulated_trading_header == "1"
    assert authority.armed and authority.order_writes_allowed
    assert not (
        authority.live_trading
        or authority.live_order_writes
        or authority.live_auto_execution
    )
    assert claims["requested_contracts"] == REQUESTED_CONTRACTS == 10
    assert claims["requested_leverage"] == REQUESTED_LEVERAGE == 10
    assert trace.portfolio.execution_authority is False


def test_actual_economics_and_base_contract_units_flow_to_real_risk(trace):
    prefix, costs, risk = trace.prefix, trace.economics, trace.portfolio
    assert costs.passed and risk.passed
    assert costs.quote_sha256 == prefix.location.quote_sha256
    assert costs.report_id == risk.report_id == prefix.source.report_id
    assert (
        costs.instrument_id == risk.instrument_id == prefix.source.market.instrument_id
    )
    assert costs.direction == risk.direction == prefix.source.direction
    with localcontext(Context(prec=100)):
        assert risk.base_quantity == D("0.1")
        assert risk.notional == risk.base_quantity * costs.candidate_entry
        assert risk.required_margin == risk.notional / REQUESTED_LEVERAGE
        assert risk.max_loss_amount == risk.base_quantity * (
            abs(costs.candidate_entry - costs.stop_loss) + costs.cost_per_base
        )
    expected = D("0.2841584") if prefix.source.direction == "long" else D("0.2838416")
    assert risk.max_loss_amount == expected
    assert risk.causes == () and risk.evidence_sha256


def test_real_full_engine_matches_independent_chain_without_granting_g12_or_orders(
    trace, run
):
    assert run.pre_evidence_complete
    assert (
        tuple(gate.gate for gate in run.result.gates) == tuple(QualificationGate)[:11]
    )
    assert all(gate.passed for gate in run.result.gates)
    assert run.result.gates[:7] == run.prefix.result.gates
    assert run.prefix.data_result == trace.prefix.data
    assert run.prefix.detection == trace.prefix.detection
    assert run.protection_audit_json == trace.protection.to_audit_json()
    assert (
        run.protection_sha256
        == hashlib.sha256(run.protection_audit_json.encode()).hexdigest()
    )
    assert run.economics == trace.economics and run.portfolio == trace.portfolio
    assert (
        run.result.candidate_entry
        == run.prefix.intent.candidate_entry
        == trace.protection.reference_entry
    )
    assert run.result.stop_loss == trace.economics.stop_loss
    assert run.result.take_profit == trace.economics.take_profit
    assert run.result.state == QualificationState.RISK_VALID
    assert not run.result.qualified and not run.result.execution_recheck_passed
    assert not run.execution_authority and not run.source_authenticity_verified
    assert not run.account_evidence_authenticated and not run.atomic_risk_reserved
    assert not run.execution_recheck_performed


def test_full_engine_is_readonly_and_replayable_under_hostile_decimal_context(
    trace, run
):
    source = trace.prefix.source
    args = engine_inputs(source)
    raw_before = source.market.model_dump_json()
    risk_before = args["risk_inputs"].model_dump_json()
    policy_before = args["policy"].model_dump_json()
    with localcontext(Context(prec=6, rounding=ROUND_DOWN, traps=[Inexact, Rounded])):
        repeated = evaluate_pre_evidence(source.market, **args)
        replayed = verify_pre_evidence(run, source.market, **args)
        assert repeated.evaluation_sha256 == run.evaluation_sha256
    assert repeated == replayed == run
    assert source.market.model_dump_json() == raw_before
    assert args["risk_inputs"].model_dump_json() == risk_before
    assert args["policy"].model_dump_json() == policy_before


@pytest.mark.parametrize("direction", ["long", "short"])
def test_original_prefix_without_historical_targets_stops_at_real_g8(direction):
    source = prefix_source(direction)
    prefix, selection = _select(source)
    assert prefix.data.passed and prefix.location.passed
    assert not selection.protection_valid
    assert "structural_target_missing" in selection.fail_codes
    result = evaluate_pre_evidence(source.market, **engine_inputs(source))
    assert all(g.passed for g in result.result.gates[:7])
    assert len(result.result.gates) == 8 and not result.result.gates[-1].passed
    assert result.result.stop_loss is result.result.take_profit is None
    assert result.economics is result.portfolio is None


@pytest.mark.parametrize("direction", ["long", "short"])
def test_nearer_raw_one_hour_barrier_cannot_be_skipped_for_high_rr(direction):
    source = engine_source(direction)
    field, value = ("high", D("101.09")) if direction == "long" else ("low", D("98.91"))
    rows = source.market.candles["1H"]
    rows[-15] = rows[-15].model_copy(update={field: value})
    prefix, selection = _select(source)
    assert prefix.location.passed and prefix.strategy.score == 100
    assert not selection.protection_valid and selection.selected is None
    assert any(target.final_target == value for target in selection.targets)
    far = [
        bracket
        for bracket in selection.alternatives
        if (
            bracket.target.final_target > value
            if direction == "long"
            else bracket.target.final_target < value
        )
    ]
    assert far and all(
        "intervening_structural_target" in bracket.rejection_codes for bracket in far
    )
    assert all(not bracket.valid for bracket in selection.alternatives)
    result = evaluate_pre_evidence(source.market, **engine_inputs(source))
    assert len(result.result.gates) == 8 and not result.result.gates[-1].passed
    assert all(g.passed for g in result.result.gates[:7])
    assert result.result.stop_loss is result.result.take_profit is None
    assert result.economics is result.portfolio is None


@pytest.mark.parametrize("direction", ["long", "short"])
def test_higher_explicit_cost_fails_g10_without_reselecting_or_tightening_geometry(
    direction,
):
    source = engine_source(direction)
    args = engine_inputs(source)
    original = evaluate_pre_evidence(source.market, **args)
    policy = args["policy"]
    args["policy"] = policy.model_copy(
        update={
            "economics": policy.economics.model_copy(
                update={"round_trip_fee_bps": D(50)}
            )
        }
    )
    result = evaluate_pre_evidence(source.market, **args)
    assert all(g.passed for g in result.result.gates[:9])
    assert len(result.result.gates) == 10 and not result.result.gates[-1].passed
    assert result.economics.net_rr < D(2) and result.portfolio is None
    assert result.protection_audit_json == original.protection_audit_json
    assert (
        result.result.candidate_entry,
        result.result.stop_loss,
        result.result.take_profit,
    ) == (
        original.result.candidate_entry,
        original.result.stop_loss,
        original.result.take_profit,
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("armed", False),
        ("order_writes_allowed", False),
        ("emergency_stop", True),
        ("live_trading", True),
        ("live_order_writes", True),
        ("live_auto_execution", True),
    ],
)
@pytest.mark.parametrize("direction", ["long", "short"])
def test_real_demo_guard_denial_does_not_discard_passing_earlier_evaluations(
    direction, field, value
):
    source = engine_source(direction)
    args = engine_inputs(source)
    claims = args["risk_inputs"]
    args["risk_inputs"] = claims.model_copy(
        update={"authority": claims.authority.model_copy(update={field: value})}
    )
    result = evaluate_pre_evidence(source.market, **args)
    assert len(result.result.gates) == 11 and all(
        g.passed for g in result.result.gates[:10]
    )
    assert result.economics.passed and not result.portfolio.passed
    assert result.result.gates[-1].code == "risk_authority_denied"
    assert not result.pre_evidence_complete and not result.execution_authority
    assert result.portfolio.execution_authority is False


@pytest.mark.parametrize("direction", ["long", "short"])
def test_altered_historical_source_cannot_replay_an_earlier_complete_run(direction):
    source = engine_source(direction)
    args = engine_inputs(source)
    complete = evaluate_pre_evidence(source.market, **args)
    rows = source.market.candles["4H"]
    field, value = ("high", D("110.18")) if direction == "long" else ("low", D("89.82"))
    rows[-30] = rows[-30].model_copy(update={field: value})
    with pytest.raises(ValueError, match="pre_evidence_replay_mismatch"):
        verify_pre_evidence(complete, source.market, **args)
    rerun = evaluate_pre_evidence(source.market, **args)
    assert rerun.pre_evidence_complete
    assert (
        rerun.prefix.data_result.source_sha256
        != complete.prefix.data_result.source_sha256
    )
    assert (
        json.loads(rerun.protection_audit_json)["source_sha256"]
        == rerun.prefix.data_result.source_sha256
    )
