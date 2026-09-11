"""Notion H/Q protection checks on synthetic source OHLC, never traded fills.

The source contains actual confirmed wick pivots. Analysis support/resistance
lists are not supplied as truth; the selector must reconstruct its own anchors.
"""

import ast
import inspect
import json
from copy import deepcopy
from dataclasses import FrozenInstanceError
from datetime import timedelta, timezone
from decimal import ROUND_DOWN, Context, Decimal, localcontext

import pytest

from app.strategies.structural_protection import (
    StructuralProtectionSelection,
    select_structural_protection,
)
from app.trade_qualification.events import extract_trigger
from app.trade_qualification.timing import TIMING_POLICIES, evaluate_timing
from tests.unit.test_qualification_entry_chain import (
    _extract,
    _location,
    _quote,
    _source_in_zone,
    _zone,
)
from tests.unit.test_qualification_events import OBSERVED, _snapshot

D = Decimal
TIMEFRAMES = ("15m", "1H", "4H")
POLICY = {
    "tick_size": D("0.01"),
    "expected_slippage_bps": D("1"),
    "cost_bps": D("10"),
    "min_net_rr": D("2"),
    "min_stop_distance_atr": D("1"),
    "atr_buffer_multiplier": D("0.25"),
    "minimum_buffer_bps": D("5"),
}


def _wick(rows, offset, price, *, low, direction):
    field = "low" if low else "high"
    if direction == "short":
        field = "high" if low else "low"
        price = D(200) - price
    rows[offset] = rows[offset].model_copy(update={field: price})


def _finish(frames, direction):
    market, analysis = _snapshot(frames)
    event = _extract(market, analysis, "fvg_return", direction)
    assert event.fail_codes == (), event
    zone, code = _zone(event)
    assert code == "passed"
    quote = _quote(event, event.trigger.trigger_price)
    assert _location(event, zone, quote).passed
    assert evaluate_timing(
        event,
        current_time=OBSERVED,
        candidate_created_at=OBSERVED,
        candidate_expires_at=event.trigger.expires_at,
        reference_price=event.trigger.trigger_price,
    ).timing_valid
    return market, analysis, event, zone


def _inputs(direction="long", *, near_obstacle=False, equal_pool=False):
    market, _, _, _ = _source_in_zone("fvg_return", direction)
    frames = deepcopy(market.candles)
    for timeframe in TIMEFRAMES:
        rows = frames[timeframe]
        _wick(rows, -30, D("110.17"), low=False, direction=direction)
        _wick(rows, -25, D("98.50"), low=True, direction=direction)
        _wick(rows, -20, D("97.20"), low=True, direction=direction)
        _wick(rows, -15, D("107.23"), low=False, direction=direction)
    if near_obstacle:
        # A near 1H opposing target remains binding even when its stops are
        # absent/wrong-side. No paired-stop requirement may erase this barrier.
        frames["1H"] = deepcopy(market.candles["1H"])
        _wick(frames["1H"], -15, D("101.09"), low=False, direction=direction)
    if equal_pool:
        # Distinct separated, right-confirmed equal pivots create the pool.
        _wick(frames["1H"], -25, D("98.50"), low=True, direction=direction)
        _wick(frames["1H"], -20, D("98.50"), low=True, direction=direction)
    return _finish(frames, direction)


def _select(inputs, **updates):
    market, analysis, event, _ = inputs
    values = {
        "observed_at": OBSERVED,
        "entry": event.trigger.trigger_price,
        **POLICY,
        **updates,
    }
    return select_structural_protection(event, market, analysis, **values)


def _prices(selection):
    return (
        tuple((item.anchor.anchor_id, item.final_stop) for item in selection.stops),
        tuple((item.anchor.anchor_id, item.final_target) for item in selection.targets),
    )


@pytest.mark.parametrize("direction", ["long", "short"])
def test_source_chain_enumerates_all_timeframes_and_every_stop_target_pair(direction):
    inputs = _inputs(direction)
    result = _select(inputs)
    assert result.protection_valid, result.to_audit_json()
    assert result.evidence == "confirmed_ohlc"
    assert result.report_id == inputs[2].report_id
    assert result.source_sha256 == inputs[2].source_sha256
    assert {item.anchor.timeframe for item in result.stops} == set(TIMEFRAMES)
    assert {item.anchor.timeframe for item in result.targets} == set(TIMEFRAMES)
    assert len(result.alternatives) == len(result.stops) * len(result.targets)
    assert {
        (item.stop.anchor.anchor_id, item.target.anchor.anchor_id)
        for item in result.alternatives
    } == {
        (stop.anchor.anchor_id, target.anchor.anchor_id)
        for stop in result.stops
        for target in result.targets
    }
    assert result.selected in result.alternatives
    assert result.selected.valid
    assert result.selected.net_rr >= POLICY["min_net_rr"]
    assert result.selection_reason and result.selected.ranking_reason
    for item in (*result.stops, *result.targets):
        assert item.anchor.known_at <= item.anchor.source_closed_at <= OBSERVED
    assert result.execution_authority is False
    assert result.economics_validated is False
    assert result.policy_calibrated is False


@pytest.mark.parametrize("direction", ["long", "short"])
def test_nearer_opposing_obstacle_cannot_be_skipped_for_better_rr(direction):
    inputs = _inputs(direction, near_obstacle=True)
    result = _select(inputs)
    assert not result.protection_valid and result.selected is None
    assert not any(item.anchor.timeframe == "1H" for item in result.stops)
    near = D("101.09") if direction == "long" else D("98.91")
    assert any(
        item.anchor.timeframe == "1H" and item.anchor.anchor_price == near
        for item in result.targets
    )
    farther = (
        [
            item
            for item in result.targets
            if item.anchor.anchor_price > near
            if direction == "long"
        ]
        if direction == "long"
        else [item for item in result.targets if item.anchor.anchor_price < near]
    )
    assert farther
    assert all(
        "intervening_structural_target" in item.rejection_codes for item in farther
    )
    assert all(not item.valid for item in result.alternatives)


@pytest.mark.parametrize("direction", ["long", "short"])
def test_tick_alignment_is_outward_for_stops_and_inward_for_targets(direction):
    inputs = _inputs(direction)
    result = _select(inputs, tick_size=D("0.03"))
    assert result.stops and result.targets
    entry = result.reference_entry
    friction = (
        result.spread + entry * POLICY["expected_slippage_bps"] / D(10000) + D("0.03")
    )
    for stop in result.stops:
        if stop.final_stop is None or stop.anchor.atr is None:
            continue
        noise = max(
            entry * POLICY["minimum_buffer_bps"] / D(10000),
            stop.anchor.atr * POLICY["atr_buffer_multiplier"],
        )
        raw = (
            stop.anchor.anchor_price - noise - friction
            if direction == "long"
            else stop.anchor.anchor_price + noise + friction
        )
        assert stop.final_stop % D("0.03") == 0
        assert stop.final_stop <= raw if direction == "long" else stop.final_stop >= raw
        assert stop.buffer == abs(stop.final_stop - stop.anchor.anchor_price)
        assert stop.buffer_atr_multiple > 0
    for target in result.targets:
        assert target.final_target % D("0.03") == 0
        assert (
            target.final_target <= target.anchor.anchor_price
            if direction == "long"
            else target.final_target >= target.anchor.anchor_price
        )
    assert result.reference_entry == inputs[2].trigger.trigger_price


@pytest.mark.parametrize("direction", ["long", "short"])
def test_cost_adjusted_rr_is_measured_without_shrinking_any_stop(direction):
    inputs = _inputs(direction)
    baseline = _select(inputs)
    expensive = _select(inputs, cost_bps=D("1000"))
    demanding = _select(inputs, min_net_rr=D("1000"))
    assert baseline.protection_valid
    assert not expensive.protection_valid and not demanding.protection_valid
    assert _prices(baseline) == _prices(expensive) == _prices(demanding)
    assert all(
        result.reference_entry == inputs[2].trigger.trigger_price
        for result in (baseline, expensive, demanding)
    )
    chosen = baseline.selected
    entry = baseline.reference_entry
    risk = abs(entry - chosen.stop.final_stop)
    reward = abs(chosen.target.final_target - entry)
    cost = entry * POLICY["cost_bps"] / D(10000)
    with localcontext(Context(prec=100)):
        assert chosen.gross_rr == reward / risk
        assert chosen.net_rr == (reward - cost) / (risk + cost)
    assert chosen.net_rr < chosen.gross_rr
    assert any(
        "net_rr_below_minimum" in item.rejection_codes
        for item in expensive.alternatives
    )


def test_cost_scenario_cannot_erase_observed_spread_and_slippage():
    result = _select(_inputs(), cost_bps=D("1"))
    assert result.fail_codes == ("cost_below_observed_friction",)
    assert not result.protection_valid


@pytest.mark.parametrize("direction", ["long", "short"])
def test_thesis_inside_stops_and_atr_noise_are_explicit_rejections(direction):
    inputs = _inputs(direction)
    market = inputs[0]
    frames = deepcopy(market.candles)
    # A tighter swing is a real observed pivot, but lies inside FVG thesis.
    rows = frames["1H"]
    for index in range(len(rows)):
        row = rows[index]
        change = D("0.5") if direction == "long" else D("-0.5")
        rows[index] = row.model_copy(
            update={
                name: getattr(row, name) + change
                for name in ("open", "high", "low", "close")
            }
        )
    _wick(rows, -8, D("100.10"), low=True, direction=direction)
    result = _select(_finish(frames, direction), min_stop_distance_atr=D("1000"))
    assert not result.protection_valid
    assert any("thesis_anchor_inside" in item.rejection_codes for item in result.stops)
    assert any("stop_inside_atr_noise" in item.rejection_codes for item in result.stops)
    assert all(not item.valid for item in result.alternatives)


@pytest.mark.parametrize("direction", ["long", "short"])
def test_equal_pivot_liquidity_pools_are_retained_in_source_audit(direction):
    result = _select(_inputs(direction, equal_pool=True))
    pools = [item for item in result.stops if item.anchor.source.startswith("equal_")]
    assert pools
    assert all(item.anchor.timeframe == "1H" for item in pools)
    assert "equal_pivot_pool_not_observed" not in result.missing_evidence
    assert all(item.buffer is not None and item.buffer > 0 for item in pools)


@pytest.mark.parametrize(
    "defect,code",
    [
        ("hash", "source_sha256_mismatch"),
        ("direction", "source_event_reconstruction_mismatch"),
        ("thesis", "source_event_reconstruction_mismatch"),
        ("event_failed", "source_event_invalid"),
        ("event_expired", "source_event_time_invalid"),
        ("event_future", "source_event_time_invalid"),
        ("source_gap", "structural_source_invalid"),
        ("source_missing", "structural_source_invalid"),
        ("source_quality", "structural_source_invalid"),
    ],
)
def test_invalid_source_event_cannot_acquire_structural_qualification(defect, code):
    market, analysis, event, zone = _inputs()
    overrides = {}
    if defect == "hash":
        event = event.model_copy(update={"source_sha256": "f" * 64})
    elif defect == "direction":
        event = event.model_copy(update={"direction": "short"})
    elif defect == "thesis":
        event = event.model_copy(update={"invalidation_price": D("99")})
    elif defect == "event_failed":
        event = event.model_copy(update={"fail_codes": ("trigger_invalidated",)})
    elif defect == "event_expired":
        overrides["observed_at"] = event.trigger.expires_at
    elif defect == "event_future":
        overrides["observed_at"] = OBSERVED - timedelta(seconds=1)
    elif defect == "source_gap":
        del market.candles["1H"][-3]
    elif defect == "source_missing":
        del market.candles["4H"]
    else:
        market.quality["15m"].ok = False
    result = _select((market, analysis, event, zone), **overrides)
    assert result.fail_codes == (code,)
    assert result.selected is None and not result.protection_valid
    assert result.execution_authority is False


@pytest.mark.parametrize("field", list(POLICY) + ["entry"])
@pytest.mark.parametrize("value", [True, 1.0, "1", D("NaN"), D("Infinity"), D("-1")])
def test_policy_inputs_remain_strict_finite_bounded_decimals(field, value):
    result = _select(_inputs(), **{field: value})
    assert result.fail_codes == ("protection_input_invalid",)
    assert not result.protection_valid


def test_reconstructed_sources_ignore_forged_analysis_structure_price_lists():
    inputs = _inputs()
    baseline = _select(inputs)
    market, analysis, _, _ = inputs
    for view in analysis.timeframe_analyses.values():
        # Deliberate conflicting claims, not fixture truth. Hash changes, but
        # these claimed levels must never replace raw confirmed OHLC pivots.
        view.structure.support_levels = [D("1")]
        view.structure.resistance_levels = [D("999999")]
    event = _extract(market, analysis, "fvg_return", "long")
    zone, code = _zone(event)
    assert code == "passed"
    rebuilt = _select((market, analysis, event, zone))
    assert rebuilt.source_sha256 != baseline.source_sha256
    assert _prices(rebuilt) == _prices(baseline)
    assert rebuilt.selected == baseline.selected


def test_audit_is_complete_immutable_and_not_execution_authority():
    inputs = _inputs()
    before = deepcopy(inputs)
    result = _select(inputs)
    payload = json.loads(result.to_audit_json())
    assert payload["schema"] == "ctcc_structural_selection_v1"
    assert payload["record_kind"] == "audit_not_authority"
    assert payload["source_sha256"] == inputs[2].source_sha256
    assert len(payload["alternatives"]) == len(result.alternatives)
    for field in ("execution_authority", "economics_validated", "policy_calibrated"):
        assert payload[field] is False
        function = getattr(StructuralProtectionSelection, field).fget
        tree = ast.parse(inspect.cleandoc(inspect.getsource(function)))
        returns = [node for node in ast.walk(tree) if isinstance(node, ast.Return)]
        assert len(returns) == 1 and isinstance(returns[0].value, ast.Constant)
        assert returns[0].value.value is False
    with pytest.raises(FrozenInstanceError):
        result.selected = None
    assert inputs == before


def test_selection_is_deterministic_under_source_map_order_and_decimal_context():
    inputs = _inputs()
    before = _select(inputs)
    market, analysis, event, zone = inputs
    market.candles = dict(reversed(tuple(market.candles.items())))
    analysis.timeframe_analyses = dict(
        reversed(tuple(analysis.timeframe_analyses.items()))
    )
    with localcontext(Context(prec=7, rounding=ROUND_DOWN)):
        after = _select((market, analysis, event, zone))
    assert after == before
    assert after.to_audit_json() == before.to_audit_json()


@pytest.mark.parametrize("direction", ["long", "short"])
def test_stop_inside_confirmed_liquidity_pool_buffer_is_never_selected(direction):
    market, _, _, _ = _inputs(direction)
    frames = deepcopy(market.candles)
    for offset in (-25, -20):
        _wick(frames["1H"], offset, D("99.70"), low=True, direction=direction)
    result = _select(_finish(frames, direction))
    rejected = [
        item
        for item in result.stops
        if "stop_inside_liquidity_pool_buffer" in item.rejection_codes
    ]
    assert rejected
    assert all(
        item.liquidity_warnings and not item.structure_validity for item in rejected
    )
    assert all(
        not bracket.valid for bracket in result.alternatives if bracket.stop in rejected
    )
    assert result.selected is None or result.selected.stop not in rejected


@pytest.mark.parametrize("direction", ["long", "short"])
def test_missing_observed_target_cannot_be_replaced_by_synthetic_rr_multiple(direction):
    result = _select(_source_in_zone("fvg_return", direction))
    assert result.stops and not result.targets
    assert result.fail_codes == ("structural_target_missing",)
    assert result.selected is None and not result.protection_valid
    assert "measured_objective_not_defined" in result.missing_evidence


@pytest.mark.parametrize("direction", ["long", "short"])
def test_unrelated_fvg_cannot_supply_a_valid_stop_for_this_setup(direction):
    result = _select(_inputs(direction))
    unrelated = [
        item for item in result.stops if "fvg_setup_mismatch" in item.rejection_codes
    ]
    assert unrelated
    assert all(
        item.anchor.source == "fvg" and not item.structure_validity
        for item in unrelated
    )
    assert result.selected is None or result.selected.stop not in unrelated


@pytest.mark.parametrize("direction", ["long", "short"])
def test_unconfirmed_pivot_wick_cannot_be_used_as_an_observed_target(direction):
    market, _, _, _ = _inputs(direction)
    frames = deepcopy(market.candles)
    _wick(frames["1H"], -1, D("115.43"), low=False, direction=direction)
    result = _select(_finish(frames, direction))
    unsupported = D("115.43") if direction == "long" else D("84.57")
    assert result.targets
    assert not any(
        item.anchor.timeframe == "1H" and item.anchor.anchor_price == unsupported
        for item in result.targets
    )


@pytest.mark.parametrize("direction", ["long", "short"])
def test_valid_first_fifteen_minute_bracket_does_not_end_comparison(direction):
    result = _select(_inputs(direction))
    assert any(
        item.valid and item.stop.anchor.timeframe == "15m"
        for item in result.alternatives
    )
    assert result.protection_valid
    assert result.selected.stop.anchor.timeframe == "1H"
    assert result.selected.stop.noise_clearance_atr > max(
        item.stop.noise_clearance_atr
        for item in result.alternatives
        if item.valid and item.stop.anchor.timeframe == "15m"
    )


@pytest.mark.parametrize("direction", ["long", "short"])
def test_one_hour_has_no_fixed_preference_when_its_ohlc_noise_is_worse(direction):
    market, _, _, _ = _inputs(direction)
    frames = deepcopy(market.candles)
    _wick(frames["1H"], -15, D("130.23"), low=False, direction=direction)
    result = _select(_finish(frames, direction))
    assert result.protection_valid
    assert result.selected.stop.anchor.timeframe == "4H"


@pytest.mark.parametrize("field", ["raw_score", "effective_score", "eligible"])
def test_injected_score_or_eligibility_cannot_override_source_record_contract(field):
    market, analysis, event, zone = _inputs()
    event = event.model_copy(update={field: 95 if field != "eligible" else True})
    result = _select((market, analysis, event, zone))
    assert result.fail_codes == ("source_event_invalid",)
    assert not result.protection_valid and result.execution_authority is False


@pytest.mark.parametrize("extra_seconds", [1, 300])
def test_active_but_overlong_source_event_ttl_cannot_exceed_strategy_policy(
    extra_seconds,
):
    market, analysis, original, zone = _inputs()
    cap = TIMING_POLICIES[original.strategy].trigger_ttl_seconds
    event = extract_trigger(
        market,
        analysis,
        report_id=original.report_id,
        strategy=original.strategy,
        direction=original.direction,
        observed_at=OBSERVED,
        trigger_ttl_seconds=cap + extra_seconds,
    )
    assert event.trigger.expires_at > OBSERVED and not event.fail_codes
    assert event.source_sha256 == original.source_sha256
    result = _select((market, analysis, event, zone))
    assert result.fail_codes == ("source_event_ttl_exceeds_policy",)
    assert not result.protection_valid and result.selected is None


@pytest.mark.parametrize("ttl", [1, 300, 599])
def test_shorter_source_event_ttl_remains_legal_without_changing_geometry(ttl):
    inputs = _inputs()
    market, analysis, original, zone = inputs
    baseline = _select(inputs)
    assert ttl < TIMING_POLICIES[original.strategy].trigger_ttl_seconds
    event = extract_trigger(
        market,
        analysis,
        report_id=original.report_id,
        strategy=original.strategy,
        direction=original.direction,
        observed_at=OBSERVED,
        trigger_ttl_seconds=ttl,
    )
    result = _select((market, analysis, event, zone))
    assert result.protection_valid
    assert _prices(result) == _prices(baseline)
    assert result.selected == baseline.selected
    expired = _select(
        (market, analysis, event, zone), observed_at=event.trigger.expires_at
    )
    assert expired.fail_codes == ("source_event_time_invalid",)


def test_equivalent_observation_utc_offset_preserves_selection_and_audit():
    inputs = _inputs()
    original = _select(inputs)
    taipei = OBSERVED.astimezone(timezone(timedelta(hours=8)))
    shifted = _select(inputs, observed_at=taipei)
    assert shifted == original
    assert shifted.to_audit_json() == original.to_audit_json()


@pytest.mark.parametrize("direction", ["long", "short"])
def test_matching_hash_with_forged_tiny_analysis_atr_cannot_shrink_source_stops(
    direction,
):
    inputs = _inputs(direction)
    baseline = _select(inputs)
    market, analysis, original, _ = inputs
    for view in analysis.timeframe_analyses.values():
        view.indicators.atr14 = D("1e-20")
        view.indicators.atr_pct = D("1e-20")
    event = _extract(market, analysis, original.strategy, direction)
    zone, code = _zone(event)
    assert code == "passed"
    result = _select((market, analysis, event, zone))
    assert result.protection_valid
    assert result.source_sha256 == event.source_sha256 != baseline.source_sha256
    assert _prices(result) == _prices(baseline)
    assert result.selected == baseline.selected
    assert all(stop.anchor.atr > D("1e-20") for stop in result.stops)


def test_audit_preserves_all_seven_explicit_policy_inputs_and_observed_spread():
    inputs = _inputs()
    result = _select(inputs)
    policy = dict(result.policy_inputs)
    expected = POLICY | {"spread": inputs[0].ticker.ask - inputs[0].ticker.bid}
    assert len(result.policy_inputs) == len(policy) == 8
    assert policy == expected
    assert result.strategy == inputs[2].strategy
    assert result.event_setup_basis == inputs[2].setup_basis
    output = json.loads(result.to_audit_json())
    assert dict(output["policy_inputs"]) == {
        name: format(value, "f") for name, value in expected.items()
    }
