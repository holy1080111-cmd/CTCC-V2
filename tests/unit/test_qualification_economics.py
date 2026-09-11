"""Explicit synthetic cost scenarios; no claimed fee verification or forecasts."""

from datetime import UTC, datetime, timedelta, timezone
from decimal import ROUND_DOWN, Context, Decimal, Inexact, Rounded, localcontext

import pytest
from pydantic import ValidationError

from app.trade_qualification.economics import (
    EconomicsPolicy,
    EconomicsResult,
    evaluate_economics,
)
from app.trade_qualification.location import ExecutableQuote, inspect_executable_quote

D = Decimal
NOW = datetime(2026, 9, 12, tzinfo=UTC)


def policy(**updates):
    return EconomicsPolicy(
        **(
            {
                "policy_id": "synthetic-cost-policy",
                "round_trip_fee_bps": D(10),
                "round_trip_slippage_bps": D(4),
                "funding_buffer_bps": D(2),
                "funding_periods": 1,
                "minimum_net_rr": D(2),
                "maximum_spread_bps": D(8),
                "maximum_funding_bps": D(30),
                "maximum_quote_age_seconds": 30,
            }
            | updates
        )
    )


def quote(**updates):
    return ExecutableQuote(
        **(
            {
                "report_id": "synthetic-economics",
                "instrument_id": "BTC-USDT-SWAP",
                "source": "rest",
                "bid": D("99.99"),
                "ask": D("100.01"),
                "mark_price": D(100),
                "bid_size": D(1),
                "ask_size": D(1),
                "funding_rate": D("0.0001"),
                "quote_time": NOW,
                "mark_time": NOW,
                "funding_time": NOW,
                "received_at": NOW,
                "request_started_at": NOW,
            }
            | updates
        )
    )


def evaluate(direction="long", **updates):
    return evaluate_economics(
        **(
            {
                "report_id": "synthetic-economics",
                "instrument_id": "BTC-USDT-SWAP",
                "direction": direction,
                "candidate_entry": D(100),
                "stop_loss": D(95) if direction == "long" else D(105),
                "take_profit": D(115) if direction == "long" else D(85),
                "quote": quote(),
                "policy": policy(),
                "current_time": NOW,
            }
            | updates
        )
    )


@pytest.mark.parametrize("direction", ["long", "short"])
def test_cost_components_and_conservative_net_rr_are_explicit(direction):
    result = evaluate(direction)
    assert result.passed and result.code == "passed"
    assert result.execution_authority is False
    assert result.spread_bps == 2 and result.funding_bps == 2
    assert result.total_cost_bps == 18 and result.cost_per_base == D("0.18")
    assert result.gross_rr == 3
    with localcontext(Context(prec=100)):
        assert result.net_rr == D("14.82") / D("5.18")
    assert result.quote_sha256 and result.policy_sha256


@pytest.mark.parametrize("direction,rate", [("long", "-0.005"), ("short", "0.005")])
def test_favorable_funding_is_not_a_credit(direction, rate):
    result = evaluate(
        direction,
        quote=quote(funding_rate=D(rate)),
        policy=policy(funding_buffer_bps=D(0)),
    )
    assert result.passed and result.funding_bps == 0
    assert result.cost_per_base == D("0.16")
    assert result.net_rr < result.gross_rr


@pytest.mark.parametrize("direction,rate", [("long", "0.0005"), ("short", "-0.0005")])
def test_projected_adverse_funding_respects_explicit_holding_horizon(direction, rate):
    result = evaluate(
        direction, quote=quote(funding_rate=D(rate)), policy=policy(funding_periods=3)
    )
    assert result.passed and result.funding_bps == 15
    assert result.cost_per_base == D("0.31")
    denied = evaluate(
        direction,
        quote=quote(funding_rate=D(rate)),
        policy=policy(funding_periods=3, maximum_funding_bps=D(14)),
    )
    assert not denied.passed and denied.code == "funding_above_limit"
    assert denied.cost_per_base is None


@pytest.mark.parametrize(
    "field", ["round_trip_fee_bps", "round_trip_slippage_bps", "funding_buffer_bps"]
)
def test_raising_any_cost_cannot_improve_net_rr(field):
    baseline = evaluate()
    higher = evaluate(policy=policy(**{field: D(20)}))
    assert higher.cost_per_base > baseline.cost_per_base
    assert higher.net_rr < baseline.net_rr


@pytest.mark.parametrize("direction", ["long", "short"])
def test_rr_failure_never_reprices_entry_or_tightens_stop(direction):
    source = quote()
    before = source.model_dump_json(round_trip=True)
    result = evaluate(direction, quote=source, policy=policy(minimum_net_rr=D(3)))
    assert not result.passed and result.code == "net_rr_below_minimum"
    assert result.candidate_entry == 100
    assert result.stop_loss == (95 if direction == "long" else 105)
    assert result.take_profit == (115 if direction == "long" else 85)
    assert source.model_dump_json(round_trip=True) == before


def test_exact_minimum_net_rr_is_inclusive_without_report_rounding():
    # Cost=0 and structural gross RR=2 are exact, with every zero explicit.
    free = policy(
        round_trip_fee_bps=D(0), round_trip_slippage_bps=D(0), funding_buffer_bps=D(0)
    )
    result = evaluate(
        policy=free,
        quote=quote(bid=D(100), ask=D(100), funding_rate=D(0)),
        take_profit=D(110),
    )
    assert result.passed and result.net_rr == 2 and result.cost_per_base == 0
    below = evaluate(
        policy=free,
        quote=quote(bid=D(100), ask=D(100), funding_rate=D(0)),
        take_profit=D("109.99999999999999999999"),
    )
    assert not below.passed and below.code == "net_rr_below_minimum"


def test_cost_is_rounded_up_at_contract_precision_before_rr_and_risk():
    result = evaluate(
        candidate_entry=D("2e-20"),
        stop_loss=D("1e-20"),
        take_profit=D("9e-20"),
        quote=quote(bid=D("2e-20"), ask=D("2e-20"), mark_price=D("2e-20")),
    )
    assert result.passed and result.cost_per_base == D("1e-20")
    assert result.net_rr == 3


@pytest.mark.parametrize("direction", ["long", "short"])
@pytest.mark.parametrize("fee_bps", [0, 10])
def test_repeating_spread_bps_does_not_add_a_phantom_cost_quantum(direction, fee_bps):
    # Exact cost is 1u spread + (0u or 6u) fee. The non-terminating
    # spread/entry ratio is for display, never an input to monetary rounding.
    unit = D("1e-20")
    cost_units = 1 + 6 * (fee_bps // 10)
    reward_units = 4 + 3 * cost_units
    sign = 1 if direction == "long" else -1
    result = evaluate(
        direction,
        candidate_entry=6000 * unit,
        stop_loss=(6000 - 2 * sign) * unit,
        take_profit=(6000 + reward_units * sign) * unit,
        quote=quote(
            bid=6000 * unit,
            ask=6001 * unit,
            mark_price=6000 * unit,
            funding_rate=D(0),
        ),
        policy=policy(
            round_trip_fee_bps=D(fee_bps),
            round_trip_slippage_bps=D(0),
            funding_buffer_bps=D(0),
            maximum_spread_bps=D(1000),
        ),
    )
    assert result.cost_per_base == cost_units * unit
    assert result.net_rr == 2 and result.passed


@pytest.mark.parametrize("field", ["candidate_entry", "stop_loss", "take_profit"])
@pytest.mark.parametrize(
    "value", [D(0), D(-1), D("NaN"), D("Infinity"), 100.0, "100", True]
)
def test_invalid_prices_cannot_reach_cost_math(field, value):
    result = evaluate(**{field: value})
    assert not result.passed and result.code == "economics_geometry_invalid"
    assert result.cost_per_base is None


@pytest.mark.parametrize(
    "direction,stop,target",
    [
        ("long", "105", "115"),
        ("short", "95", "85"),
        ("long", "95", "99"),
        ("short", "105", "101"),
    ],
)
def test_wrong_direction_geometry_is_rejected(direction, stop, target):
    assert (
        evaluate(direction, stop_loss=D(stop), take_profit=D(target)).code
        == "economics_geometry_invalid"
    )


@pytest.mark.parametrize(
    "field", ["quote_time", "mark_time", "funding_time", "received_at"]
)
def test_stale_component_cannot_be_hidden_by_other_fresh_components(field):
    updates = {field: NOW - timedelta(seconds=31)}
    if field == "received_at":
        updates.update(
            {
                key: updates[field]
                for key in (
                    "quote_time",
                    "mark_time",
                    "funding_time",
                    "request_started_at",
                )
            }
        )
    result = evaluate(quote=quote(**updates))
    assert result.code == "quote_stale" and result.cost_per_base is None


@pytest.mark.parametrize(
    "field",
    ["quote_time", "mark_time", "funding_time", "received_at", "request_started_at"],
)
def test_future_quote_component_is_rejected(field):
    result = evaluate(quote=quote(**{field: NOW + timedelta(seconds=1)}))
    assert result.code == "quote_timestamp_invalid" and not result.passed


@pytest.mark.parametrize(
    "field,value", [("report_id", "another-report"), ("instrument_id", "ETH-USDT-SWAP")]
)
def test_cost_quote_must_match_report_and_instrument(field, value):
    assert evaluate(quote=quote(**{field: value})).code == "identity_mismatch"


def test_quote_absence_crossing_or_wide_spread_is_not_a_zero_cost_fallback():
    assert evaluate(quote=None).code == "executable_quote_missing"
    assert (
        evaluate(quote=quote(bid=D(101), ask=D(100))).code == "quote_geometry_invalid"
    )
    result = evaluate(quote=quote(bid=D("99.9"), ask=D("100.1")))
    assert result.code == "spread_above_limit" and result.cost_per_base is None


@pytest.mark.parametrize("age", [0, -1, 1.5, True, "30", 86401])
def test_shared_quote_inspector_rejects_invalid_age_policy(age):
    checked, code = inspect_executable_quote(
        quote(), current_time=NOW, max_quote_age_seconds=age
    )
    assert checked is None and code == "quote_age_limit_invalid"


@pytest.mark.parametrize(
    "updates",
    [
        {"round_trip_fee_bps": D(-1)},
        {"round_trip_slippage_bps": D("NaN")},
        {"funding_buffer_bps": D(31)},
        {"funding_periods": 0},
        {"funding_periods": True},
        {"minimum_net_rr": D(0)},
        {"maximum_quote_age_seconds": 61},
        {"maximum_spread_bps": 8.0},
        {"round_trip_fee_bps": None},
        {"raw_score": 95},
        {"risk_permission": True},
        {"continuous": True},
    ],
)
def test_policy_is_complete_strict_and_has_no_score_or_bypass_fields(updates):
    with pytest.raises(ValidationError):
        policy(**updates)
    bypassed = policy().model_copy(update=updates)
    result = evaluate(policy=bypassed)
    assert not result.passed and result.code == "cost_policy_invalid"


def test_missing_policy_does_not_guess_fee_rate_or_holding_horizon():
    result = evaluate(policy=None)
    assert not result.passed and result.code == "cost_policy_missing"
    fields = policy().model_dump(round_trip=True)
    for name in EconomicsPolicy.model_fields:
        with pytest.raises(ValidationError):
            EconomicsPolicy.model_validate(
                {key: value for key, value in fields.items() if key != name}
            )


def test_result_is_frozen_strict_and_serializable_without_granting_authority():
    result = evaluate()
    assert (
        EconomicsResult.model_validate_json(result.model_dump_json(round_trip=True))
        == result
    )
    with pytest.raises(ValidationError):
        result.cost_per_base = D(0)
    for authority in (True, 0, "false"):
        with pytest.raises(ValidationError):
            EconomicsResult.model_validate(
                result.model_dump(round_trip=True) | {"execution_authority": authority}
            )


def test_utc_equivalent_clock_and_hostile_decimal_context_do_not_change_decision():
    baseline = evaluate()
    assert (
        evaluate(current_time=NOW.astimezone(timezone(timedelta(hours=8)))) == baseline
    )
    source, configured = quote(), policy()
    with localcontext(Context(prec=6, rounding=ROUND_DOWN)) as context:
        context.traps[Inexact] = context.traps[Rounded] = True
        under_hostile_context = evaluate(quote=source, policy=configured)
    assert under_hostile_context == baseline
