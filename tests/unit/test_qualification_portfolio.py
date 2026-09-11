"""Synthetic portfolio decisions; no account, market, settings, or execution IO."""

from datetime import UTC, datetime, timedelta, timezone
from decimal import ROUND_DOWN, Context, Decimal, Inexact, Rounded, localcontext

import pytest
from pydantic import ValidationError

from app.trade_qualification.portfolio import (
    ContractRiskSpec,
    DemoRiskAuthority,
    EvidenceStamp,
    PendingReservation,
    PortfolioRiskPolicy,
    PortfolioRiskResult,
    PortfolioRiskSnapshot,
    PositionExposure,
    RealizedOutcome,
    evaluate_portfolio,
)

D = Decimal
NOW = datetime(2026, 1, 12, 12, tzinfo=UTC)
DAY_START = NOW.replace(hour=0)
WEEK_START = NOW - timedelta(days=7)
REPORT = "synthetic-portfolio-report"
ACCOUNT = "synthetic-demo-account"
INSTRUMENT = "BTC-USDT-SWAP"
STAMP_FIELDS = (
    "balance_stamp",
    "positions_stamp",
    "history_stamp",
    "reservations_stamp",
)


def stamp(**updates):
    fields = {
        "environment": "demo",
        "account_id": ACCOUNT,
        "source_sha256": "a" * 64,
        "observed_at": NOW,
        "received_at": NOW,
        "complete": True,
    }
    return EvidenceStamp(**(fields | updates))


def instrument(**updates):
    fields = {
        "instrument_id": INSTRUMENT,
        "base_currency": "BTC",
        "settlement_currency": "USDT",
        "contract_kind": "linear_base",
        "contract_value_currency": "BTC",
        "contract_value": D("0.01"),
        "lot_size": D("0.25"),
        "min_contracts": D("0.25"),
        "max_contracts": D("1000"),
        "max_leverage": 20,
        "correlation_group": "synthetic-crypto-group",
        "source_sha256": "b" * 64,
        "observed_at": NOW,
        "received_at": NOW,
    }
    return ContractRiskSpec(**(fields | updates))


def position(**updates):
    fields = {
        "position_id": "synthetic-position-1",
        "instrument_id": INSTRUMENT,
        "direction": "long",
        "settlement_currency": "USDT",
        "notional": D("100"),
        "margin": D("10"),
        "risk_amount": D("1"),
        "correlation_group": "synthetic-crypto-group",
    }
    return PositionExposure(**(fields | updates))


def reservation(**updates):
    fields = {
        "reservation_id": "synthetic-reservation-1",
        "instrument_id": INSTRUMENT,
        "direction": "long",
        "settlement_currency": "USDT",
        "notional": D("100"),
        "margin": D("10"),
        "risk_amount": D("1"),
        "correlation_group": "synthetic-crypto-group",
    }
    return PendingReservation(**(fields | updates))


def outcome(**updates):
    fields = {
        "outcome_id": "synthetic-outcome-1",
        "sequence": 0,
        "instrument_id": INSTRUMENT,
        "closed_at": NOW - timedelta(hours=1),
        "realized_pnl": D("-1"),
    }
    return RealizedOutcome(**(fields | updates))


def account(*, positions=(), pending_reservations=(), loss_history=(), **updates):
    fields = {
        "account_id": ACCOUNT,
        "settlement_currency": "USDT",
        **{name: stamp() for name in STAMP_FIELDS},
        "equity": D("1000"),
        "available_margin": D("1000"),
        "peak_equity": D("1000"),
        "peak_observed_at": WEEK_START,
        "peak_window_started_at": NOW - timedelta(days=30),
        "history_start": WEEK_START,
        "history_end": NOW,
        "loss_streak_at_history_start": 0,
        "positions": positions,
        "pending_reservations": pending_reservations,
        "loss_history": loss_history,
        "position_count": len(positions),
        "pending_reservation_count": len(pending_reservations),
    }
    return PortfolioRiskSnapshot(**(fields | updates))


def policy(**updates):
    fields = {
        "max_age_seconds": 30,
        "risk_per_trade_pct": D("0.02"),
        "max_daily_loss_pct": D("0.1"),
        "max_weekly_loss_pct": D("0.2"),
        "max_drawdown_pct": D("0.2"),
        "drawdown_window_started_at": NOW - timedelta(days=30),
        "max_consecutive_losses": 3,
        "max_open_positions": 10,
        "max_same_direction_positions": 10,
        "max_correlated_positions": 10,
        "max_order_notional": D("10000"),
        "max_order_contracts": D("1000"),
        "max_portfolio_notional": D("100000"),
        "max_same_direction_notional": D("100000"),
        "max_correlated_notional": D("100000"),
        "max_portfolio_risk_pct": D("0.2"),
        "max_portfolio_margin_pct": D("0.8"),
        "max_leverage": 20,
    }
    return PortfolioRiskPolicy(**(fields | updates))


def authority(**updates):
    fields = {
        "stamp": stamp(),
        "environment": "demo",
        "demo_enabled": True,
        "order_writes_allowed": True,
        "simulated_trading_header": "1",
        "emergency_stop": False,
        "armed": True,
        "live_trading": False,
        "live_order_writes": False,
        "live_auto_execution": False,
    }
    return DemoRiskAuthority(**(fields | updates))


def evaluate(**updates):
    fields = {
        "report_id": REPORT,
        "instrument_id": INSTRUMENT,
        "direction": "long",
        "candidate_entry": D("100"),
        "stop_loss": D("95"),
        "round_trip_cost_per_base": D("0.1"),
        "requested_contracts": D("10"),
        "requested_leverage": 10,
        "instrument": instrument(),
        "account": account(),
        "policy": policy(),
        "authority": authority(),
        "current_time": NOW,
    }
    return evaluate_portfolio(**(fields | updates))


def assert_passed(result):
    assert result.report_id == REPORT
    assert result.instrument_id == INSTRUMENT
    assert result.code == "passed"
    assert result.passed is True
    assert result.causes == ()
    assert result.reason.strip()
    assert result.execution_authority is False


def assert_denied(result, cause=None):
    assert result.report_id == REPORT
    assert result.instrument_id == INSTRUMENT
    assert result.code == "risk_authority_denied"
    assert result.passed is False
    assert result.causes
    assert result.causes == tuple(sorted(set(result.causes)))
    if cause is not None:
        assert cause in result.causes
    assert result.reason.strip()
    assert result.execution_authority is False


def test_complete_empty_portfolio_is_valid_not_missing_data():
    result = evaluate()
    assert_passed(result)
    assert result.requested_contracts == D("10")
    assert result.requested_leverage == 10
    assert type(result.requested_leverage) is int
    assert result.base_quantity == D("0.1")
    assert result.notional == D("10")
    assert result.required_margin == D("1")
    assert result.max_loss_amount == D("0.51")
    assert result.risk_pct == D("0.00051")
    assert result.daily_loss_pct == result.weekly_loss_pct == result.drawdown_pct == 0
    assert len(result.evidence_sha256) == 64


@pytest.mark.parametrize("direction,stop", [("long", D("95")), ("short", D("105"))])
def test_linear_contract_arithmetic_is_direction_symmetric(direction, stop):
    result = evaluate(direction=direction, stop_loss=stop)
    assert_passed(result)
    assert result.max_loss_amount == D("0.51")


@pytest.mark.parametrize("name", ["instrument", "account", "policy", "authority"])
def test_each_required_evidence_bundle_is_required(name):
    assert_denied(evaluate(**{name: None}), f"{name}_missing")


@pytest.mark.parametrize(
    "factory",
    [stamp, instrument, position, reservation, outcome, account, policy, authority],
)
def test_domain_records_are_frozen_strict_and_json_roundtrip(factory):
    model = factory()
    assert type(model).model_validate_json(model.model_dump_json()) == model
    field = next(iter(type(model).model_fields))
    with pytest.raises(ValidationError):
        setattr(model, field, getattr(model, field))
    with pytest.raises(ValidationError):
        factory(unexpected_field="not-authority")


@pytest.mark.parametrize("field", ["complete"])
@pytest.mark.parametrize("value", [1, 0, "true", "false", None])
def test_complete_flag_requires_an_exact_boolean(field, value):
    with pytest.raises(ValidationError):
        stamp(**{field: value})


@pytest.mark.parametrize(
    "field",
    [
        "demo_enabled",
        "order_writes_allowed",
        "emergency_stop",
        "armed",
        "live_trading",
        "live_order_writes",
        "live_auto_execution",
    ],
)
@pytest.mark.parametrize("value", [1, 0, "true", "false", None])
def test_authority_flags_require_exact_booleans(field, value):
    with pytest.raises(ValidationError):
        authority(**{field: value})


@pytest.mark.parametrize("field", ["equity", "available_margin", "peak_equity"])
@pytest.mark.parametrize(
    "value",
    [1000, 1000.0, "1000", True, D("NaN"), D("Infinity"), D("1E-21"), D("1E40")],
)
def test_account_money_requires_finite_bounded_decimal(field, value):
    with pytest.raises(ValidationError):
        account(**{field: value})


@pytest.mark.parametrize(
    "field",
    [
        "candidate_entry",
        "stop_loss",
        "round_trip_cost_per_base",
        "requested_contracts",
    ],
)
@pytest.mark.parametrize(
    "value",
    [None, 1, 1.0, "1", True, D("NaN"), D("Infinity"), D("-1"), D("1E-21"), D("1E40")],
)
def test_candidate_inputs_fail_closed_for_invalid_decimal_values(field, value):
    assert_denied(evaluate(**{field: value}), "candidate_invalid")


@pytest.mark.parametrize("value", [None, 0, -1, 1.0, D("1"), "1", True, 126])
def test_leverage_requires_an_exact_bounded_positive_integer(value):
    assert_denied(evaluate(requested_leverage=value), "candidate_invalid")


@pytest.mark.parametrize(
    "field",
    ["candidate_entry", "stop_loss", "requested_contracts", "requested_leverage"],
)
def test_zero_positive_candidate_input_is_denied(field):
    assert_denied(evaluate(**{field: D("0")}), "candidate_invalid")


def test_explicit_zero_cost_is_not_missing():
    result = evaluate(round_trip_cost_per_base=D("0"))
    assert_passed(result)
    assert result.max_loss_amount == D("0.5")


@pytest.mark.parametrize("direction", [None, "LONG", "neutral", True, 1, [], {}])
def test_invalid_direction_does_not_throw_or_grant_authority(direction):
    assert_denied(evaluate(direction=direction), "direction_invalid")


@pytest.mark.parametrize(
    "current_time", [None, NOW.replace(tzinfo=None), NOW.isoformat(), 123, True]
)
def test_invalid_current_time_fails_closed(current_time):
    assert_denied(evaluate(current_time=current_time), "current_time_invalid")


@pytest.mark.parametrize("name", STAMP_FIELDS)
def test_each_account_source_requires_completeness(name):
    assert_denied(
        evaluate(account=account(**{name: stamp(complete=False)})),
        "evidence_incomplete",
    )


@pytest.mark.parametrize("name", STAMP_FIELDS)
def test_every_account_stamp_is_bound_to_the_same_account_scope(name):
    assert_denied(
        evaluate(account=account(**{name: stamp(account_id="another-account")})),
        "scope_mismatch",
    )


def test_demo_authority_is_bound_to_the_account_scope():
    assert_denied(
        evaluate(authority=authority(stamp=stamp(account_id="another-account"))),
        "scope_mismatch",
    )


@pytest.mark.parametrize("value", ["live", "paper", "Demo", True, False, 1, 0, None])
def test_account_evidence_environment_is_explicit_and_demo_only(value):
    with pytest.raises(ValidationError):
        stamp(environment=value)
    values = stamp().model_dump(mode="python", round_trip=True)
    del values["environment"]
    with pytest.raises(ValidationError):
        EvidenceStamp.model_validate(values, strict=True)


@pytest.mark.parametrize("source", [*STAMP_FIELDS, "authority"])
@pytest.mark.parametrize("defect", ["live", "missing", "false"])
def test_same_account_id_cannot_mix_live_or_unscoped_source_receipts(source, defect):
    # An unchanged shared account ID, digest, freshness and complete=True do not
    # identify the source environment. model_copy cannot bypass that boundary.
    changed = stamp().model_copy(update={"environment": "live"})
    if defect == "missing":
        changed = stamp().model_copy(deep=True)
        del changed.__dict__["environment"]
    elif defect == "false":
        changed = stamp().model_copy(update={"environment": False})
    if source == "authority":
        result = evaluate(authority=authority().model_copy(update={"stamp": changed}))
        assert_denied(result, "authority_invalid")
    else:
        result = evaluate(account=account().model_copy(update={source: changed}))
        assert_denied(result, "account_invalid")


def test_instrument_identity_must_match_requested_instrument():
    assert_denied(
        evaluate(instrument=instrument(instrument_id="ETH-USDT-SWAP")),
        "instrument_mismatch",
    )


@pytest.mark.parametrize(
    "updates",
    [
        {"account": account(settlement_currency="USDC")},
        {"account": account(positions=(position(settlement_currency="USDC"),))},
        {
            "account": account(
                pending_reservations=(reservation(settlement_currency="USDC"),)
            )
        },
    ],
)
def test_every_amount_is_in_the_same_settlement_currency(updates):
    assert_denied(evaluate(**updates), "settlement_currency_mismatch")


@pytest.mark.parametrize(
    "updates",
    [
        {"contract_kind": "inverse"},
        {"contract_kind": "spot"},
        {"contract_value_currency": "USDT"},
    ],
)
def test_non_linear_or_non_base_contract_units_are_not_guessed(updates):
    assert_denied(
        evaluate(instrument=instrument(**updates)), "instrument_contract_unsupported"
    )


@pytest.mark.parametrize("name", STAMP_FIELDS)
def test_each_account_stamp_is_fresh_at_exact_age_limit(name):
    older = NOW - timedelta(seconds=30)
    updates = {name: stamp(observed_at=older, received_at=older)}
    if name == "history_stamp":
        updates["history_end"] = older
    assert_passed(evaluate(account=account(**updates)))


@pytest.mark.parametrize("name", STAMP_FIELDS)
def test_each_stale_account_source_is_denied_even_with_a_fresh_receipt(name):
    older = NOW - timedelta(seconds=30, microseconds=1)
    updates = {name: stamp(observed_at=older)}
    if name == "history_stamp":
        updates["history_end"] = older
    assert_denied(evaluate(account=account(**updates)), "evidence_stale")


@pytest.mark.parametrize("name", STAMP_FIELDS)
@pytest.mark.parametrize("field", ["observed_at", "received_at"])
def test_each_future_account_component_is_denied(name, field):
    future = NOW + timedelta(microseconds=1)
    assert_denied(
        evaluate(account=account(**{name: stamp(**{field: future})})),
        "evidence_timestamp_invalid",
    )


@pytest.mark.parametrize("name", STAMP_FIELDS)
def test_observation_cannot_follow_its_receipt(name):
    bad = stamp(observed_at=NOW, received_at=NOW - timedelta(microseconds=1))
    assert_denied(
        evaluate(account=account(**{name: bad})), "evidence_timestamp_invalid"
    )


@pytest.mark.parametrize("name", ["instrument", "authority"])
def test_instrument_and_authority_have_their_own_freshness(name):
    older = NOW - timedelta(seconds=30, microseconds=1)
    value = (
        instrument(observed_at=older)
        if name == "instrument"
        else authority(stamp=stamp(observed_at=older))
    )
    assert_denied(evaluate(**{name: value}), "evidence_stale")


@pytest.mark.parametrize("name", ["instrument", "authority"])
@pytest.mark.parametrize("field", ["observed_at", "received_at"])
def test_instrument_and_authority_reject_future_component_times(name, field):
    values = {field: NOW + timedelta(microseconds=1)}
    value = (
        instrument(**values)
        if name == "instrument"
        else authority(stamp=stamp(**values))
    )
    assert_denied(evaluate(**{name: value}), "evidence_timestamp_invalid")


def test_empty_history_does_not_hide_incomplete_seven_day_coverage():
    assert_denied(
        evaluate(account=account(history_start=WEEK_START + timedelta(microseconds=1))),
        "history_coverage_incomplete",
    )


def test_history_end_must_match_its_own_observation():
    assert_denied(
        evaluate(account=account(history_end=NOW - timedelta(microseconds=1))),
        "history_coverage_incomplete",
    )


def test_history_end_must_cover_the_current_utc_day():
    before_day = DAY_START - timedelta(microseconds=1)
    snapshot = account(
        history_end=before_day,
        history_stamp=stamp(observed_at=before_day, received_at=before_day),
    )
    assert_denied(evaluate(account=snapshot), "history_coverage_incomplete")


@pytest.mark.parametrize(
    "closed_at",
    [WEEK_START - timedelta(microseconds=1), NOW + timedelta(microseconds=1)],
)
def test_history_rows_outside_declared_coverage_are_not_accepted(closed_at):
    assert_denied(
        evaluate(account=account(loss_history=(outcome(closed_at=closed_at),))),
        "history_invalid",
    )


def test_weekly_loss_interval_excludes_its_exact_left_endpoint():
    result = evaluate(
        account=account(
            loss_history=(outcome(closed_at=WEEK_START, realized_pnl=D("-300")),)
        )
    )
    assert_passed(result)
    assert result.weekly_loss_pct == 0
    included = evaluate(
        account=account(
            loss_history=(
                outcome(
                    closed_at=WEEK_START + timedelta(microseconds=1),
                    realized_pnl=D("-300"),
                ),
            )
        )
    )
    assert_denied(included, "weekly_loss_limit_reached")


def test_daily_loss_interval_includes_utc_midnight():
    before = evaluate(
        account=account(
            loss_history=(
                outcome(
                    closed_at=DAY_START - timedelta(microseconds=1),
                    realized_pnl=D("-100"),
                ),
            )
        )
    )
    assert_passed(before)
    assert before.daily_loss_pct == 0
    at_start = evaluate(
        account=account(
            loss_history=(outcome(closed_at=DAY_START, realized_pnl=D("-100")),)
        )
    )
    assert_denied(at_start, "daily_loss_limit_reached")


def test_realized_outcome_at_history_end_is_included():
    result = evaluate(
        account=account(loss_history=(outcome(closed_at=NOW, realized_pnl=D("-100")),))
    )
    assert_denied(result, "daily_loss_limit_reached")


@pytest.mark.parametrize(
    "amount,passed", [(D("99.99999999999999999999"), True), (D("100"), False)]
)
def test_daily_loss_cap_is_exclusive(amount, passed):
    result = evaluate(account=account(loss_history=(outcome(realized_pnl=-amount),)))
    if passed:
        assert_passed(result)
    else:
        assert_denied(result, "daily_loss_limit_reached")


@pytest.mark.parametrize(
    "amount,passed", [(D("199.99999999999999999999"), True), (D("200"), False)]
)
def test_weekly_loss_cap_is_exclusive(amount, passed):
    row = outcome(closed_at=DAY_START - timedelta(hours=1), realized_pnl=-amount)
    result = evaluate(account=account(loss_history=(row,)))
    if passed:
        assert_passed(result)
    else:
        assert_denied(result, "weekly_loss_limit_reached")


@pytest.mark.parametrize(
    "rows",
    [
        (outcome(sequence=0), outcome(outcome_id="second", sequence=2, closed_at=NOW)),
        (outcome(sequence=1), outcome(outcome_id="second", sequence=1, closed_at=NOW)),
        (outcome(sequence=1), outcome(outcome_id="second", sequence=0, closed_at=NOW)),
        (outcome(sequence=0), outcome(sequence=1, closed_at=NOW)),
        (outcome(sequence=0, closed_at=NOW), outcome(outcome_id="second", sequence=1)),
    ],
)
def test_gapped_duplicate_or_time_reversed_history_fails(rows):
    assert_denied(evaluate(account=account(loss_history=rows)), "history_invalid")


def test_history_sequence_need_not_start_at_zero_and_equal_times_are_ordered_by_sequence():
    rows = (outcome(sequence=40), outcome(outcome_id="second", sequence=41))
    assert_passed(evaluate(account=account(loss_history=rows)))


@pytest.mark.parametrize(
    "last_pnl,expected", [(D("1"), True), (D("0"), False), (D("-1"), False)]
)
def test_prior_loss_streak_is_carried_and_only_a_win_resets_it(last_pnl, expected):
    snapshot = account(
        loss_streak_at_history_start=3, loss_history=(outcome(realized_pnl=last_pnl),)
    )
    result = evaluate(account=snapshot)
    if expected:
        assert_passed(result)
    else:
        assert_denied(result, "consecutive_loss_limit_reached")


def test_loss_streak_reaching_exact_cap_is_denied():
    snapshot = account(loss_streak_at_history_start=2, loss_history=(outcome(),))
    assert_denied(evaluate(account=snapshot), "consecutive_loss_limit_reached")


@pytest.mark.parametrize(
    "equity,passed", [(D("800.00000000000000000001"), True), (D("800"), False)]
)
def test_drawdown_uses_peak_equity_and_reaching_cap_is_denied(equity, passed):
    result = evaluate(account=account(equity=equity, available_margin=D("800")))
    if passed:
        assert_passed(result)
    else:
        assert_denied(result, "drawdown_limit_reached")
        assert result.drawdown_pct == D("0.2")


@pytest.mark.parametrize(
    "updates",
    [
        {"peak_equity": D("999")},
        {"peak_observed_at": NOW + timedelta(microseconds=1)},
        {"peak_window_started_at": WEEK_START + timedelta(microseconds=1)},
    ],
)
def test_peak_equity_and_its_observation_window_must_be_consistent(updates):
    assert_denied(evaluate(account=account(**updates)), "peak_equity_invalid")


@pytest.mark.parametrize("quantity", [D("10.25"), D("0.25"), D("1000")])
def test_quantities_on_the_true_lot_grid_are_accepted_without_resizing(quantity):
    result = evaluate(
        requested_contracts=quantity, policy=policy(risk_per_trade_pct=D("1"))
    )
    assert_passed(result)
    assert result.requested_contracts == quantity


@pytest.mark.parametrize(
    "quantity", [D("10.1"), D("10.01"), D("10.24999999999999999999")]
)
def test_decimal_places_do_not_substitute_for_real_quarter_contract_lots(quantity):
    assert_denied(evaluate(requested_contracts=quantity), "quantity_not_on_lot")


def test_quantity_below_instrument_minimum_is_denied_not_raised():
    assert_denied(
        evaluate(
            requested_contracts=D("0.25"), instrument=instrument(min_contracts=D("0.5"))
        ),
        "quantity_below_minimum",
    )


@pytest.mark.parametrize("limit_source", ["instrument", "policy"])
def test_quantity_above_either_maximum_is_denied(limit_source):
    updates = (
        {"instrument": instrument(max_contracts=D("9.75"))}
        if limit_source == "instrument"
        else {"policy": policy(max_order_contracts=D("9.75"))}
    )
    result = evaluate(**updates)
    assert_denied(result, "quantity_limit_exceeded")
    assert result.requested_contracts == D("10")


@pytest.mark.parametrize("limit_source", ["instrument", "policy"])
def test_leverage_above_either_maximum_is_denied_without_reduction(limit_source):
    updates = (
        {"instrument": instrument(max_leverage=9)}
        if limit_source == "instrument"
        else {"policy": policy(max_leverage=9)}
    )
    result = evaluate(**updates)
    assert_denied(result, "leverage_limit_exceeded")
    assert result.requested_leverage == 10


def test_leverage_equal_to_both_caps_is_allowed():
    assert_passed(evaluate(requested_leverage=20))


@pytest.mark.parametrize(
    "direction,stop",
    [("long", D("100")), ("long", D("101")), ("short", D("100")), ("short", D("99"))],
)
def test_stop_must_be_strictly_on_the_protective_side(direction, stop):
    assert_denied(
        evaluate(direction=direction, stop_loss=stop), "protection_geometry_invalid"
    )


@pytest.mark.parametrize(
    "limit,passed", [(D("10"), True), (D("9.99999999999999999999"), False)]
)
def test_order_notional_limit_is_inclusive(limit, passed):
    result = evaluate(policy=policy(max_order_notional=limit))
    assert_passed(result) if passed else assert_denied(
        result, "order_notional_limit_exceeded"
    )


@pytest.mark.parametrize(
    "limit,passed", [(D("0.00051"), True), (D("0.00050999999999999999"), False)]
)
def test_trade_risk_budget_includes_stop_distance_and_round_trip_cost(limit, passed):
    result = evaluate(policy=policy(risk_per_trade_pct=limit))
    assert_passed(result) if passed else assert_denied(
        result, "trade_risk_limit_exceeded"
    )


@pytest.mark.parametrize(
    "margin,passed", [(D("1"), True), (D("0.99999999999999999999"), False)]
)
def test_available_margin_exact_boundary_is_inclusive(margin, passed):
    result = evaluate(account=account(available_margin=margin))
    assert_passed(result) if passed else assert_denied(
        result, "available_margin_exceeded"
    )


@pytest.mark.parametrize(
    "limit,passed", [(D("210"), True), (D("209.99999999999999999999"), False)]
)
def test_portfolio_notional_includes_positions_reservations_and_new_order(
    limit, passed
):
    snapshot = account(positions=(position(),), pending_reservations=(reservation(),))
    result = evaluate(account=snapshot, policy=policy(max_portfolio_notional=limit))
    assert_passed(result) if passed else assert_denied(
        result, "portfolio_notional_limit_exceeded"
    )


@pytest.mark.parametrize(
    "limit,passed", [(D("0.00251"), True), (D("0.00250999999999999999"), False)]
)
def test_portfolio_risk_includes_positions_reservations_and_new_order(limit, passed):
    snapshot = account(positions=(position(),), pending_reservations=(reservation(),))
    result = evaluate(account=snapshot, policy=policy(max_portfolio_risk_pct=limit))
    assert_passed(result) if passed else assert_denied(
        result, "portfolio_risk_limit_exceeded"
    )


@pytest.mark.parametrize(
    "limit,passed", [(D("0.021"), True), (D("0.02099999999999999999"), False)]
)
def test_portfolio_margin_includes_positions_reservations_and_new_order(limit, passed):
    snapshot = account(positions=(position(),), pending_reservations=(reservation(),))
    result = evaluate(account=snapshot, policy=policy(max_portfolio_margin_pct=limit))
    assert_passed(result) if passed else assert_denied(
        result, "portfolio_margin_limit_exceeded"
    )


@pytest.mark.parametrize(
    "field,cause",
    [
        ("max_open_positions", "open_position_limit_exceeded"),
        ("max_same_direction_positions", "same_direction_limit_exceeded"),
        ("max_correlated_positions", "correlation_limit_exceeded"),
    ],
)
def test_position_limits_include_pending_and_new_order(field, cause):
    snapshot = account(positions=(position(),), pending_reservations=(reservation(),))
    assert_passed(evaluate(account=snapshot, policy=policy(**{field: 3})))
    assert_denied(evaluate(account=snapshot, policy=policy(**{field: 2})), cause)


def test_opposite_direction_positions_still_count_for_total_and_correlation():
    snapshot = account(positions=(position(direction="short"),))
    assert_passed(
        evaluate(account=snapshot, policy=policy(max_same_direction_positions=1))
    )
    assert_denied(
        evaluate(account=snapshot, policy=policy(max_open_positions=1)),
        "open_position_limit_exceeded",
    )
    assert_denied(
        evaluate(account=snapshot, policy=policy(max_correlated_positions=1)),
        "correlation_limit_exceeded",
    )


def test_other_instrument_in_same_correlation_group_still_counts():
    snapshot = account(positions=(position(instrument_id="ETH-USDT-SWAP"),))
    assert_denied(
        evaluate(account=snapshot, policy=policy(max_correlated_positions=1)),
        "correlation_limit_exceeded",
    )


def test_unrelated_correlation_group_does_not_consume_that_specific_cap():
    snapshot = account(
        positions=(
            position(instrument_id="OTHER-USDT-SWAP", correlation_group="unrelated"),
        )
    )
    assert_passed(evaluate(account=snapshot, policy=policy(max_correlated_positions=1)))


@pytest.mark.parametrize(
    "field,values,cause",
    [
        ("positions", (position(), position()), "duplicate_exposure"),
        ("pending_reservations", (reservation(), reservation()), "duplicate_exposure"),
    ],
)
def test_duplicate_exposure_ids_are_not_double_counted_or_silently_dropped(
    field, values, cause
):
    assert_denied(evaluate(account=account(**{field: values})), cause)


@pytest.mark.parametrize(
    "field,cause",
    [
        ("position_count", "position_count_mismatch"),
        ("pending_reservation_count", "reservation_count_mismatch"),
    ],
)
def test_declared_counts_must_match_the_full_observed_lists(field, cause):
    snapshot = account().model_copy(update={field: 1})
    assert_denied(evaluate(account=snapshot), cause)


@pytest.mark.parametrize(
    "updates",
    [
        {"environment": "live"},
        {"environment": "test"},
        {"demo_enabled": False},
        {"order_writes_allowed": False},
        {"simulated_trading_header": "0"},
        {"armed": False},
        {"live_trading": True},
        {"live_order_writes": True},
        {"live_auto_execution": True},
    ],
)
def test_demo_authority_must_explicitly_match_every_required_switch(updates):
    assert_denied(evaluate(authority=authority(**updates)), "demo_authority_denied")


def test_emergency_stop_overrides_complete_fresh_evidence():
    assert_denied(
        evaluate(authority=authority(emergency_stop=True)), "emergency_stop_active"
    )


def test_incomplete_authority_is_not_permission():
    assert_denied(
        evaluate(authority=authority(stamp=stamp(complete=False))),
        "evidence_incomplete",
    )


@pytest.mark.parametrize(
    "field,factory,updates",
    [
        ("account", account, {"equity": 1000.0}),
        ("account", account, {"positions": []}),
        ("instrument", instrument, {"contract_value": D("NaN")}),
        ("instrument", instrument, {"lot_size": "0.25"}),
        ("policy", policy, {"max_open_positions": True}),
        ("policy", policy, {"risk_per_trade_pct": 0.1}),
        ("authority", authority, {"order_writes_allowed": 1}),
    ],
)
def test_model_copy_tamper_is_revalidated(field, factory, updates):
    assert_denied(
        evaluate(**{field: factory().model_copy(update=updates)}), f"{field}_invalid"
    )


@pytest.mark.parametrize(
    "name,factory",
    [
        ("instrument", instrument),
        ("account", account),
        ("policy", policy),
        ("authority", authority),
    ],
)
def test_undeclared_copy_fields_and_raw_mappings_are_not_trusted(name, factory):
    copied = factory().model_copy(update={"execution_authority": True})
    assert_denied(evaluate(**{name: copied}), f"{name}_invalid")
    assert_denied(evaluate(**{name: factory().model_dump()}), f"{name}_invalid")


def test_nested_stamp_and_position_tamper_is_not_lost_during_serialization():
    copied_stamp = stamp().model_copy(update={"unexpected": True})
    assert_denied(
        evaluate(account=account().model_copy(update={"balance_stamp": copied_stamp})),
        "account_invalid",
    )
    copied_position = position().model_copy(update={"risk_amount": 0.0})
    copied_account = account().model_copy(
        update={"positions": (copied_position,), "position_count": 1}
    )
    assert_denied(evaluate(account=copied_account), "account_invalid")


def test_multiple_denials_are_retained_sorted_and_never_turn_into_a_pass():
    result = evaluate(
        account=account(
            balance_stamp=stamp(complete=False, account_id="another-account")
        ),
        authority=authority(emergency_stop=True),
    )
    assert_denied(result)
    assert {
        "evidence_incomplete",
        "emergency_stop_active",
        "scope_mismatch",
    } <= set(result.causes)


def test_hostile_decimal_context_cannot_change_risk_decisions_or_evidence_identity():
    original_account = account(
        positions=(position(),),
        pending_reservations=(reservation(),),
        loss_history=(outcome(realized_pnl=D("-3.1")),),
    )
    original_instrument = instrument()
    original_policy = policy()
    original_authority = authority()
    fields = {
        "account": original_account,
        "instrument": original_instrument,
        "policy": original_policy,
        "authority": original_authority,
    }
    expected = evaluate(**fields)
    assert_passed(expected)
    context = Context(prec=2, rounding=ROUND_DOWN)
    context.traps[Inexact] = True
    context.traps[Rounded] = True
    with localcontext(context):
        assert evaluate(**fields) == expected


def test_result_is_frozen_and_json_roundtrips_for_both_outcomes():
    for result in (evaluate(), evaluate(authority=None)):
        assert (
            PortfolioRiskResult.model_validate_json(result.model_dump_json()) == result
        )
        with pytest.raises(ValidationError):
            result.passed = not result.passed


@pytest.mark.parametrize("authority_value", [True, 0, 1, "false", None])
def test_result_cannot_grant_or_coerce_execution_authority(authority_value):
    fields = evaluate().model_dump(round_trip=True) | {
        "execution_authority": authority_value
    }
    with pytest.raises(ValidationError):
        PortfolioRiskResult.model_validate(fields, strict=True)


def test_utc_equivalent_current_time_preserves_risk_and_evidence_identity():
    assert evaluate() == evaluate(
        current_time=NOW.astimezone(timezone(timedelta(hours=8)))
    )


@pytest.mark.parametrize(
    "field,cause",
    [
        ("max_same_direction_notional", "same_direction_notional_limit_exceeded"),
        ("max_correlated_notional", "correlated_notional_limit_exceeded"),
    ],
)
def test_directional_and_correlated_notional_include_pending_and_new_order(
    field, cause
):
    snapshot = account(positions=(position(),), pending_reservations=(reservation(),))
    assert_passed(evaluate(account=snapshot, policy=policy(**{field: D("210")})))
    assert_denied(
        evaluate(
            account=snapshot, policy=policy(**{field: D("209.99999999999999999999")})
        ),
        cause,
    )


def test_opposite_direction_notional_is_not_netted_out_of_correlated_exposure():
    snapshot = account(
        positions=(position(direction="short"),),
        pending_reservations=(reservation(direction="short"),),
    )
    assert_passed(
        evaluate(account=snapshot, policy=policy(max_same_direction_notional=D("10")))
    )
    assert_denied(
        evaluate(account=snapshot, policy=policy(max_correlated_notional=D("209"))),
        "correlated_notional_limit_exceeded",
    )


@pytest.mark.parametrize(
    "margin,passed", [(D("11"), True), (D("10.99999999999999999999"), False)]
)
def test_available_margin_reserves_pending_but_does_not_rededuct_open_margin(
    margin, passed
):
    snapshot = account(
        available_margin=margin,
        positions=(position(margin=D("100")),),
        pending_reservations=(reservation(),),
    )
    result = evaluate(account=snapshot)
    assert_passed(result) if passed else assert_denied(
        result, "available_margin_exceeded"
    )


@pytest.mark.parametrize("source", ["instrument", "positions", "pending_reservations"])
@pytest.mark.parametrize("group", ["unknown", "UNAVAILABLE", "unclassified"])
def test_unknown_correlation_classification_denies_risk(source, group):
    if source == "instrument":
        result = evaluate(instrument=instrument(correlation_group=group))
    elif source == "positions":
        result = evaluate(
            account=account(positions=(position(correlation_group=group),))
        )
    else:
        result = evaluate(
            account=account(
                pending_reservations=(reservation(correlation_group=group),)
            )
        )
    assert_denied(result, "correlation_unknown")


@pytest.mark.parametrize("source", ["positions", "pending_reservations"])
def test_same_instrument_cannot_claim_another_correlation_group(source):
    exposure = (
        position(correlation_group="unrelated")
        if source == "positions"
        else reservation(correlation_group="unrelated")
    )
    assert_denied(
        evaluate(account=account(**{source: (exposure,)})),
        "correlation_identity_mismatch",
    )


@pytest.mark.parametrize("layout", ["positions", "mixed", "reservations"])
@pytest.mark.parametrize(
    "limits,limit_cause",
    [
        ({"max_correlated_positions": 2}, "correlation_limit_exceeded"),
        ({"max_correlated_notional": D(150)}, "correlated_notional_limit_exceeded"),
    ],
)
def test_other_instrument_cannot_split_its_exposure_across_conflicting_groups(
    layout, limits, limit_cause
):
    group = instrument().correlation_group
    first = (
        reservation(reservation_id="other-pending-1", instrument_id="ETH-USDT-SWAP")
        if layout == "reservations"
        else position(position_id="other-position-1", instrument_id="ETH-USDT-SWAP")
    )
    second = (
        position(
            position_id="other-position-2",
            instrument_id="ETH-USDT-SWAP",
            direction="short",
        )
        if layout == "positions"
        else reservation(
            reservation_id="other-pending-2",
            instrument_id="ETH-USDT-SWAP",
            direction="short",
        )
    )

    def snapshot(first_record, second_record):
        records = (first_record, second_record)
        return account(
            positions=tuple(
                item for item in records if isinstance(item, PositionExposure)
            ),
            pending_reservations=tuple(
                item for item in records if isinstance(item, PendingReservation)
            ),
        )

    configured = policy(**limits)
    assert_denied(
        evaluate(account=snapshot(first, second), policy=configured), limit_cause
    )
    conflict = second.model_copy(update={"correlation_group": "another-group"})
    assert_denied(
        evaluate(account=snapshot(first, conflict), policy=configured),
        "correlation_identity_mismatch",
    )
    # A genuinely separate, internally consistent class remains legal and does
    # not consume the proposed BTC instrument's specific correlation budget.
    independent = first.model_copy(update={"correlation_group": "another-group"})
    assert_passed(evaluate(account=snapshot(independent, conflict), policy=configured))
    assert first.correlation_group == second.correlation_group == group


def test_drawdown_window_is_bound_to_the_explicit_reviewed_policy():
    result = evaluate(
        policy=policy(drawdown_window_started_at=NOW - timedelta(days=29))
    )
    assert_denied(result, "drawdown_window_mismatch")


def test_wins_offset_realized_losses_but_do_not_erase_drawdown():
    rows = (
        outcome(realized_pnl=D("-200")),
        outcome(outcome_id="win", sequence=1, closed_at=NOW, realized_pnl=D("150")),
    )
    result = evaluate(account=account(loss_history=rows))
    assert_passed(result)
    assert result.daily_loss_pct == result.weekly_loss_pct == D("0.05")
    deteriorated = evaluate(
        account=account(equity=D("800"), available_margin=D("800"), loss_history=rows)
    )
    assert_denied(deteriorated, "drawdown_limit_reached")


def test_net_realized_profit_clips_loss_to_zero_not_a_negative_budget():
    rows = (
        outcome(realized_pnl=D("-20")),
        outcome(outcome_id="win", sequence=1, closed_at=NOW, realized_pnl=D("30")),
    )
    result = evaluate(account=account(loss_history=rows))
    assert_passed(result)
    assert result.daily_loss_pct == result.weekly_loss_pct == 0


def test_profit_outside_the_current_day_cannot_offset_todays_loss():
    rows = (
        outcome(closed_at=DAY_START - timedelta(microseconds=1), realized_pnl=D("100")),
        outcome(outcome_id="loss", sequence=1, closed_at=NOW, realized_pnl=D("-100")),
    )
    result = evaluate(account=account(loss_history=rows))
    assert_denied(result, "daily_loss_limit_reached")
    assert result.daily_loss_pct == D("0.1")
    assert result.weekly_loss_pct == 0


@pytest.mark.parametrize(
    "margin,passed",
    [(D("3.33333333333333333333"), False), (D("3.33333333333333333334"), True)],
)
def test_recurring_margin_fraction_is_compared_exactly_before_display_rounding(
    margin, passed
):
    result = evaluate(requested_leverage=3, account=account(available_margin=margin))
    assert_passed(result) if passed else assert_denied(
        result, "available_margin_exceeded"
    )


@pytest.mark.parametrize(
    "limit,passed",
    [(D("0.33333333333333333333"), False), (D("0.33333333333333333334"), True)],
)
def test_recurring_risk_fraction_does_not_round_a_failure_into_a_pass(limit, passed):
    snapshot = account(equity=D("3"), peak_equity=D("3"), available_margin=D("3"))
    result = evaluate(
        account=snapshot,
        stop_loss=D("90"),
        round_trip_cost_per_base=D("0"),
        policy=policy(risk_per_trade_pct=limit, max_portfolio_risk_pct=D("1")),
    )
    assert_passed(result) if passed else assert_denied(
        result, "trade_risk_limit_exceeded"
    )


def test_current_time_too_early_to_form_the_week_window_fails_closed():
    assert_denied(
        evaluate(current_time=datetime.min.replace(tzinfo=UTC)), "current_time_invalid"
    )


@pytest.mark.parametrize(
    "factory",
    [stamp, instrument, position, reservation, outcome, account, policy, authority],
)
def test_input_records_do_not_silently_default_missing_evidence(factory):
    original = factory()
    for name, field in type(original).model_fields.items():
        assert field.is_required(), name
        values = original.model_dump(mode="python", round_trip=True)
        del values[name]
        with pytest.raises(ValidationError):
            type(original).model_validate(values, strict=True)


@pytest.mark.parametrize("factory", [stamp, instrument, outcome, account, policy])
def test_all_direct_observation_times_are_normalized_to_utc(factory):
    original = factory()
    for name in type(original).model_fields:
        value = getattr(original, name)
        if isinstance(value, datetime):
            changed = factory(**{name: value.astimezone(timezone(timedelta(hours=8)))})
            assert getattr(changed, name).tzinfo is UTC
            assert changed == original


def test_numeric_spelling_does_not_change_the_complete_risk_evidence_identity():
    original = evaluate()
    alternate = evaluate(
        candidate_entry=D("100.000"),
        stop_loss=D("95.00"),
        requested_contracts=D("10.00"),
        round_trip_cost_per_base=D("0.100"),
        instrument=instrument(contract_value=D("0.010")),
        account=account(equity=D("1000.00")),
    )
    assert_passed(alternate)
    assert alternate == original


@pytest.mark.parametrize("source", [*STAMP_FIELDS, "instrument", "authority"])
def test_every_source_digest_is_bound_into_the_result_identity(source):
    original = evaluate()
    if source == "instrument":
        changed = evaluate(instrument=instrument(source_sha256="c" * 64))
    elif source == "authority":
        changed = evaluate(authority=authority(stamp=stamp(source_sha256="c" * 64)))
    else:
        changed = evaluate(account=account(**{source: stamp(source_sha256="c" * 64)}))
    assert_passed(changed)
    assert changed.evidence_sha256 != original.evidence_sha256
