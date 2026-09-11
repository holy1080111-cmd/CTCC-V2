"""Current-quote cost scenarios only; never runtime or historical gate proof."""

from datetime import timedelta, timezone
from decimal import ROUND_DOWN, Context, Decimal, Inexact, Rounded, localcontext

import pytest
from pydantic import ValidationError

from app.trade_qualification.economics import evaluate_economics
from app.trade_qualification.executable_economics import (
    ExecutableEconomicsResult,
    evaluate_executable_economics,
)
from tests.unit.test_qualification_economics import NOW, policy, quote

D = Decimal


def inputs(direction="long", **updates):
    return {
        "report_id": "synthetic-economics",
        "instrument_id": "BTC-USDT-SWAP",
        "direction": direction,
        "candidate_entry": D(100),
        "stop_loss": D(95) if direction == "long" else D(105),
        "take_profit": D(115) if direction == "long" else D(85),
        "quote": quote(),
        "policy": policy(),
        "current_time": NOW,
    } | updates


def evaluate(direction="long", **updates):
    return evaluate_executable_economics(**inputs(direction, **updates))


def zero_cost_policy(**updates):
    return policy(
        **{
            "round_trip_fee_bps": D(0),
            "round_trip_slippage_bps": D(0),
            "funding_buffer_bps": D(0),
            "maximum_spread_bps": D(1000),
        }
        | updates
    )


@pytest.mark.parametrize("direction", ["long", "short"])
def test_both_results_reuse_existing_formula_and_preserve_candidate_structure(
    direction,
):
    arguments = inputs(direction)
    source_before = arguments["quote"].model_dump_json(round_trip=True)
    policy_before = arguments["policy"].model_dump_json(round_trip=True)
    result = evaluate_executable_economics(**arguments)
    reference = (
        arguments["quote"].ask if direction == "long" else arguments["quote"].bid
    )
    assert result.passed and result.code == "passed" and result.failure_stage is None
    assert result.candidate_result == evaluate_economics(**arguments)
    assert result.execution_result == evaluate_economics(
        **(arguments | {"candidate_entry": reference})
    )
    assert (
        result.original_candidate_entry
        == result.candidate_result.candidate_entry
        == 100
    )
    assert (
        result.executable_reference
        == result.execution_result.candidate_entry
        == reference
    )
    assert (
        result.stop_loss
        == result.candidate_result.stop_loss
        == result.execution_result.stop_loss
        == arguments["stop_loss"]
    )
    assert (
        result.take_profit
        == result.candidate_result.take_profit
        == result.execution_result.take_profit
        == arguments["take_profit"]
    )
    assert result.candidate_result.quote_sha256 == result.execution_result.quote_sha256
    assert (
        result.candidate_result.policy_sha256 == result.execution_result.policy_sha256
    )
    assert arguments["quote"].model_dump_json(round_trip=True) == source_before
    assert arguments["policy"].model_dump_json(round_trip=True) == policy_before


@pytest.mark.parametrize(
    "direction,bid,ask", [("long", "102", "103"), ("short", "97", "98")]
)
def test_original_candidate_pass_cannot_hide_worse_executable_rr(direction, bid, ask):
    result = evaluate(
        direction,
        quote=quote(bid=D(bid), ask=D(ask), funding_rate=D(0)),
        policy=zero_cost_policy(),
    )
    assert result.candidate_result.passed
    assert not result.execution_result.passed
    assert not result.passed and result.failure_stage == "execution"
    assert result.code == "net_rr_below_minimum"
    with localcontext(Context(prec=100)):
        assert result.candidate_result.net_rr == D(14) / D(6)
        assert result.execution_result.net_rr == result.worst_net_rr == D(11) / D(9)
    assert result.candidate_cost_adjusted_risk_per_base == 6
    assert (
        result.execution_cost_adjusted_risk_per_base
        == result.worst_cost_adjusted_risk_per_base
        == 9
    )
    assert result.entry_delta_per_base == (3 if direction == "long" else -3)
    assert result.adverse_entry_delta_per_base == 3
    assert result.adverse_entry_delta_bps == 300


@pytest.mark.parametrize(
    "direction,bid,ask", [("long", "96", "97"), ("short", "103", "104")]
)
def test_favorable_executable_price_never_repairs_failed_original_candidate(
    direction, bid, ask
):
    result = evaluate(
        direction,
        quote=quote(bid=D(bid), ask=D(ask), funding_rate=D(0)),
        policy=zero_cost_policy(minimum_net_rr=D(3)),
    )
    assert not result.candidate_result.passed and result.execution_result.passed
    assert not result.passed and result.failure_stage == "candidate"
    assert result.code == "net_rr_below_minimum"
    assert result.worst_net_rr == result.candidate_result.net_rr
    assert (
        result.worst_cost_adjusted_risk_per_base
        == result.candidate_cost_adjusted_risk_per_base
        == 6
    )
    assert result.execution_cost_adjusted_risk_per_base == 3
    assert result.adverse_entry_delta_per_base == result.adverse_entry_delta_bps == 0
    assert result.entry_delta_per_base == (-3 if direction == "long" else 3)
    assert result.original_candidate_entry == 100


@pytest.mark.parametrize("direction", ["long", "short"])
def test_exact_same_reference_has_no_double_spread_or_entry_shift_charge(direction):
    result = evaluate(direction, quote=quote(bid=D(100), ask=D(100)))
    assert result.passed and result.candidate_result == result.execution_result
    assert (
        result.entry_delta_per_base
        == result.adverse_entry_delta_per_base
        == result.net_rr_delta
        == 0
    )
    assert result.candidate_result.cost_per_base == D("0.16")
    assert result.worst_cost_adjusted_risk_per_base == D("5.16")


@pytest.mark.parametrize(
    "direction,reference",
    [
        ("long", "95"),
        ("long", "94"),
        ("long", "115"),
        ("long", "116"),
        ("short", "105"),
        ("short", "106"),
        ("short", "85"),
        ("short", "84"),
    ],
)
def test_execution_at_or_across_stop_or_target_rejects_without_fake_worst_summary(
    direction, reference
):
    result = evaluate(direction, quote=quote(bid=D(reference), ask=D(reference)))
    assert result.candidate_result.passed
    assert (
        result.code == "economics_geometry_invalid"
        and result.failure_stage == "execution"
    )
    assert not result.passed
    assert result.executable_reference == D(reference)
    assert (
        result.execution_result.net_rr is result.execution_result.cost_per_base is None
    )
    assert result.execution_cost_adjusted_risk_per_base is None
    assert (
        result.worst_cost_adjusted_risk_per_base
        is result.worst_net_rr
        is result.net_rr_delta
        is None
    )
    assert result.candidate_cost_adjusted_risk_per_base is not None


@pytest.mark.parametrize("direction", ["long", "short"])
def test_exact_rr_boundary_uses_existing_unrounded_cost_engine(direction):
    # Both references cost zero. Candidate RR exceeds two; reference RR is two.
    reference = D(101) if direction == "long" else D(99)
    target = D(113) if direction == "long" else D(87)
    result = evaluate(
        direction,
        take_profit=target,
        quote=quote(bid=reference, ask=reference, funding_rate=D(0)),
        policy=zero_cost_policy(),
    )
    assert result.passed and result.execution_result.net_rr == result.worst_net_rr == 2
    worse = reference + (D("1e-20") if direction == "long" else -D("1e-20"))
    denied = evaluate(
        direction,
        take_profit=target,
        quote=quote(bid=worse, ask=worse, funding_rate=D(0)),
        policy=zero_cost_policy(),
    )
    assert not denied.passed and denied.code == "net_rr_below_minimum"


@pytest.mark.parametrize("direction,reference", [("long", "115"), ("short", "85")])
def test_candidate_failure_has_priority_when_execution_has_a_different_failure(
    direction, reference
):
    result = evaluate(
        direction,
        quote=quote(bid=D(reference), ask=D(reference)),
        policy=policy(minimum_net_rr=D(3)),
    )
    assert result.candidate_result.code == "net_rr_below_minimum"
    assert result.execution_result.code == "economics_geometry_invalid"
    assert result.code == "net_rr_below_minimum" and result.failure_stage == "candidate"
    assert result.worst_net_rr is result.worst_cost_adjusted_risk_per_base is None


@pytest.mark.parametrize("direction", ["long", "short"])
def test_summary_and_json_ignore_hostile_decimal_rounding_and_traps(direction):
    arguments = inputs(direction)
    expected = evaluate_executable_economics(**arguments)
    with localcontext(Context(prec=6, rounding=ROUND_DOWN)) as context:
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        actual = evaluate_executable_economics(**arguments)
        encoded = actual.model_dump_json(round_trip=True)
        rebuilt = ExecutableEconomicsResult.model_validate_json(encoded)
    assert actual == rebuilt == expected
    assert encoded == expected.model_dump_json(round_trip=True)
    assert actual.entry_delta_per_base == (
        D("0.01") if direction == "long" else D("-0.01")
    )
    assert actual.worst_cost_adjusted_risk_per_base == (
        D("5.190016") if direction == "long" else D("5.189984")
    )


@pytest.mark.parametrize("source", ["rest", "ws"])
def test_quote_source_channel_and_optional_request_start_do_not_claim_post_render_barrier(
    source,
):
    result = evaluate(quote=quote(source=source, request_started_at=None))
    assert result.passed
    assert result.post_render_barrier_verified is False
    assert result.historical_gates_verified is False
    assert result.source_authenticity_verified is False


@pytest.mark.parametrize(
    "field", ["quote_time", "mark_time", "funding_time", "received_at"]
)
@pytest.mark.parametrize("micros,expected", [(0, "passed"), (1, "quote_stale")])
def test_each_component_exact_freshness_boundary_is_independent(
    field, micros, expected
):
    old = NOW - timedelta(seconds=30, microseconds=micros)
    changes = {field: old}
    if field == "received_at":
        changes = {
            name: old
            for name in (
                "quote_time",
                "mark_time",
                "funding_time",
                "received_at",
                "request_started_at",
            )
        }
    result = evaluate(quote=quote(**changes))
    assert result.code == expected
    if expected != "passed":
        assert result.execution_result is result.executable_reference is None
        assert result.failure_stage == "candidate"


@pytest.mark.parametrize(
    "field",
    ["quote_time", "mark_time", "funding_time", "received_at", "request_started_at"],
)
def test_future_component_cannot_pass_second_scenario(field):
    result = evaluate(quote=quote(**{field: NOW + timedelta(microseconds=1)}))
    assert result.code == "quote_timestamp_invalid" and not result.passed
    assert result.execution_result is result.executable_reference is None


@pytest.mark.parametrize(
    "field,value", [("report_id", "other-report"), ("instrument_id", "ETH-USDT-SWAP")]
)
def test_quote_identity_mismatch_fails_before_execution_arithmetic(field, value):
    result = evaluate(quote=quote(**{field: value}))
    assert result.code == "identity_mismatch" and result.failure_stage == "candidate"
    assert result.execution_result is result.executable_reference is None


@pytest.mark.parametrize(
    "updates,code",
    [
        ({"quote": None}, "executable_quote_missing"),
        ({"policy": None}, "cost_policy_missing"),
        ({"quote": quote(bid=D(101), ask=D(99))}, "quote_geometry_invalid"),
    ],
)
def test_missing_source_costs_or_crossed_book_never_default_to_free_execution(
    updates, code
):
    result = evaluate(**updates)
    assert result.code == code and not result.passed
    assert (
        result.execution_result
        is result.worst_net_rr
        is result.worst_cost_adjusted_risk_per_base
        is None
    )
    assert (
        result.original_candidate_entry == 100
        and result.stop_loss == 95
        and result.take_profit == 115
    )


@pytest.mark.parametrize("field", ["candidate_entry", "stop_loss", "take_profit"])
@pytest.mark.parametrize(
    "value",
    [None, True, "100", 100.0, D("NaN"), D("Infinity"), D(0), D(-1), D("1e-21")],
)
def test_invalid_geometry_is_not_coerced_or_repaired_by_valid_reference(field, value):
    result = evaluate(**{field: value})
    assert not result.passed and result.code == "economics_geometry_invalid"
    assert result.execution_result is result.executable_reference is None
    output_name = "original_candidate_entry" if field == "candidate_entry" else field
    assert getattr(result, output_name) is None


@pytest.mark.parametrize("direction", ["long", "short"])
def test_candidate_invalid_geometry_remains_first_failure_without_reference_rescue(
    direction,
):
    result = evaluate(direction, stop_loss=D(100))
    assert not result.passed and result.code == "economics_geometry_invalid"
    assert result.failure_stage == "candidate" and result.execution_result is None
    assert result.original_candidate_entry == result.stop_loss == 100


@pytest.mark.parametrize(
    "updates",
    [
        {"source": "fabricated"},
        {"bid": 99.0},
        {"ask": D("NaN")},
        {"funding_rate": D("Infinity")},
        {"funding_time": None},
        {"bid_size": D(0)},
        {"hidden_execution_authority": True},
    ],
)
def test_quote_model_copy_tampering_is_revalidated(updates):
    result = evaluate(quote=quote().model_copy(update=updates))
    assert result.code == "executable_quote_invalid" and not result.passed
    assert result.execution_result is None


@pytest.mark.parametrize(
    "updates",
    [
        {"round_trip_fee_bps": 1.0},
        {"funding_periods": True},
        {"minimum_net_rr": D("NaN")},
        {"maximum_quote_age_seconds": 86400},
        {"execution_authority": True},
    ],
)
def test_cost_policy_model_copy_tampering_is_not_repaired(updates):
    result = evaluate(policy=policy().model_copy(update=updates))
    assert result.code == "cost_policy_invalid" and result.execution_result is None
    assert result.original_candidate_entry == 100


@pytest.mark.parametrize(
    "field,value",
    [
        ("report_id", "bad/id"),
        ("instrument_id", ""),
        ("direction", "both"),
        ("current_time", NOW.replace(tzinfo=None)),
        ("current_time", NOW.isoformat()),
        ("current_time", True),
    ],
)
def test_invalid_result_identity_or_clock_raises_like_existing_engine(field, value):
    with pytest.raises((ValueError, TypeError)):
        evaluate(**{field: value})


def test_equivalent_utc_offsets_preserve_complete_result():
    result = evaluate()
    offset = timezone(timedelta(hours=8))
    source = quote(
        **{
            field: NOW.astimezone(offset)
            for field in (
                "quote_time",
                "mark_time",
                "funding_time",
                "received_at",
                "request_started_at",
            )
        }
    )
    assert evaluate(quote=source, current_time=NOW.astimezone(offset)) == result


@pytest.mark.parametrize(
    "field",
    [
        "execution_authority",
        "market_fill_guaranteed",
        "historical_gates_verified",
        "post_render_barrier_verified",
        "source_authenticity_verified",
    ],
)
@pytest.mark.parametrize("value", [True, 0, "false"])
def test_result_can_never_promote_economics_into_authority_or_guarantees(field, value):
    result = evaluate()
    assert getattr(result, field) is False
    data = result.model_dump(mode="python", round_trip=True)
    data[field] = value
    with pytest.raises(ValidationError):
        ExecutableEconomicsResult.model_validate(data, strict=True)


@pytest.mark.parametrize(
    "field,value",
    [
        ("original_candidate_entry", D(101)),
        ("stop_loss", D(94)),
        ("take_profit", D(116)),
        ("executable_reference", D(102)),
        ("entry_delta_per_base", D(1)),
        ("worst_net_rr", D(99)),
        ("worst_cost_adjusted_risk_per_base", D(1)),
        ("failure_stage", "candidate"),
        ("code", "repaired"),
    ],
)
def test_result_geometry_measurements_and_decision_are_internally_bound(field, value):
    data = evaluate().model_dump(mode="python", round_trip=True)
    data[field] = value
    with pytest.raises(ValidationError):
        ExecutableEconomicsResult.model_validate(data, strict=True)


def test_execution_pass_cannot_flip_candidate_failure_in_serialized_result():
    result = evaluate(
        quote=quote(bid=D(96), ask=D(97), funding_rate=D(0)),
        policy=zero_cost_policy(minimum_net_rr=D(3)),
    )
    assert not result.passed and result.execution_result.passed
    data = result.model_dump(mode="python", round_trip=True)
    data.update(passed=True, code="passed", failure_stage=None)
    with pytest.raises(ValidationError):
        ExecutableEconomicsResult.model_validate(data, strict=True)


def test_result_is_frozen_and_json_roundtrip_retains_all_original_prices():
    result = evaluate()
    with pytest.raises(ValidationError):
        result.original_candidate_entry = D(101)
    assert (
        ExecutableEconomicsResult.model_validate_json(
            result.model_dump_json(round_trip=True)
        )
        == result
    )
    missing = evaluate(policy=None)
    assert (
        ExecutableEconomicsResult.model_validate_json(
            missing.model_dump_json(round_trip=True)
        )
        == missing
    )
