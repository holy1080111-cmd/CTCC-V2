"""Synthetic timing checks; no source verification, ledger, or trading IO."""

from datetime import UTC, datetime, timedelta, timezone, tzinfo
from decimal import ROUND_DOWN, Context, Decimal, Inexact, Rounded, localcontext

import pytest
from pydantic import ValidationError

from app.trade_qualification.event_models import TriggerDetection
from app.trade_qualification.models import EntryTrigger
from app.trade_qualification.timing import (
    TIMING_POLICIES,
    TimingPolicy,
    TimingResult,
    evaluate_timing,
    event_identity,
)

D = Decimal
T0 = datetime(2026, 1, 5, 12, tzinfo=UTC)
STRATEGY_LIMITS = (
    ("trend_pullback", 2, 3600),
    ("breakout_continuation", 1, 1800),
    ("liquidity_sweep_reversal", 1, 1800),
    ("fvg_return", 2, 14400),
    ("order_block_return", 2, 14400),
    ("range_reversal", 1, 1800),
    ("structure_reversal", 2, 7200),
    ("volatility_expansion", 1, 3600),
)


def detection(*, trigger_updates=None, **updates) -> TriggerDetection:
    report_id = updates.get("report_id", "timing_report")
    direction = updates.get("direction", "long")
    trigger_fields = {
        "report_id": report_id,
        "trigger_type": "synthetic_closed_break",
        "trigger_time": T0,
        "trigger_price": D("100"),
        "expires_at": T0 + timedelta(hours=1),
    }
    trigger_fields.update(trigger_updates or {})
    fields = {
        "report_id": report_id,
        "symbol": "BTC/USDT",
        "instrument_id": "BTC-USDT-SWAP",
        "strategy": "trend_pullback",
        "direction": direction,
        "observed_at": T0,
        "source_sha256": "a" * 64,
        "source_timeframe": "5m",
        "setup_time": T0 - timedelta(minutes=5),
        "setup_type": "synthetic_setup",
        "trigger": EntryTrigger(**trigger_fields),
        "invalidation_price": D("90") if direction == "long" else D("110"),
    }
    fields.update(updates)
    return TriggerDetection(**fields)


def evaluate(event=None, **updates) -> TimingResult:
    fields = {
        "current_time": T0 + timedelta(seconds=1),
        "candidate_created_at": T0,
        "candidate_expires_at": T0 + timedelta(hours=1),
        "reference_price": D("100"),
    }
    fields.update(updates)
    return evaluate_timing(event or detection(), **fields)


def assert_decision(result, action, code):
    assert result.action == action
    assert result.code == code
    assert result.timing_valid is (action == "CONTINUE" and code == "passed")
    assert (
        result.late_entry_risk
        == {"CONTINUE": "low", "WAIT": "unknown", "CANCEL": "high"}[action]
    )
    assert result.reason.strip()


@pytest.mark.parametrize("strategy,bars,setup_age", STRATEGY_LIMITS)
@pytest.mark.parametrize("direction", ["long", "short"])
def test_all_eight_strategy_policies_are_closed_and_direction_symmetric(
    strategy, bars, setup_age, direction
):
    policy = TIMING_POLICIES[strategy]
    assert policy.policy_id == f"closed-v1:{strategy}"
    assert policy.bar_seconds == 300
    assert policy.maximum_trigger_bars == bars
    assert policy.trigger_ttl_seconds == bars * 300
    assert policy.max_setup_to_trigger_seconds == setup_age
    assert policy.candle_close_only is True
    assert policy.intrabar_allowed is False
    assert policy.retracement_reentry_allowed is False
    result = evaluate(detection(strategy=strategy, direction=direction))
    assert_decision(result, "CONTINUE", "passed")
    assert result.latest_valid_entry_time == T0 + timedelta(seconds=bars * 300)
    assert result.seconds_since_trigger == 1
    assert result.candles_since_trigger == 0
    assert result.event_key == event_identity(
        detection(strategy=strategy, direction=direction)
    )


def test_strategy_policy_catalog_covers_exactly_the_supported_eight():
    assert set(TIMING_POLICIES) == {row[0] for row in STRATEGY_LIMITS}


def test_clock_fold_uses_elapsed_utc_time_not_repeated_local_wall_clock():
    class FoldOffset(tzinfo):
        def utcoffset(self, value):
            return timedelta(hours=-5 if value.fold else -4)

        def dst(self, value):
            return timedelta(0)

    zone = FoldOffset()
    trigger_time = datetime(2026, 11, 1, 1, 59, tzinfo=zone)
    current = datetime(2026, 11, 1, 1, 4, tzinfo=zone, fold=1)
    expires = datetime(2026, 11, 1, 2, 30, tzinfo=zone, fold=1)
    event = detection(
        trigger_updates={"trigger_time": trigger_time, "expires_at": expires},
        observed_at=trigger_time,
        setup_time=trigger_time - timedelta(minutes=5),
    )
    result = evaluate(
        event,
        current_time=current,
        candidate_created_at=trigger_time,
        candidate_expires_at=expires,
    )
    assert_decision(result, "CONTINUE", "passed")
    assert result.seconds_since_trigger == 300
    assert result.candles_since_trigger == 1
    assert result.latest_valid_entry_time == trigger_time.astimezone(UTC) + timedelta(
        minutes=10
    )


@pytest.mark.parametrize("strategy,bars,setup_age", STRATEGY_LIMITS)
def test_strategy_ttl_is_exclusive_and_reports_elapsed_bars(strategy, bars, setup_age):
    event = detection(strategy=strategy)
    deadline = T0 + timedelta(seconds=300 * bars)
    before = evaluate(event, current_time=deadline - timedelta(microseconds=1))
    assert_decision(before, "CONTINUE", "passed")
    assert before.candles_since_trigger == bars - 1
    assert before.seconds_since_trigger == 300 * bars - 1
    expired = evaluate(event, current_time=deadline)
    assert_decision(expired, "CANCEL", "entry_window_expired")
    assert expired.candles_since_trigger == bars


@pytest.mark.parametrize("strategy,bars,setup_age", STRATEGY_LIMITS)
def test_setup_maximum_age_is_inclusive_but_next_microsecond_is_late(
    strategy, bars, setup_age
):
    at_limit = detection(
        strategy=strategy, setup_time=T0 - timedelta(seconds=setup_age)
    )
    assert_decision(evaluate(at_limit), "CONTINUE", "passed")
    too_old = detection(
        strategy=strategy,
        setup_time=T0 - timedelta(seconds=setup_age, microseconds=1),
    )
    assert_decision(evaluate(too_old), "CANCEL", "late_entry")


def test_custom_bar_and_wait_policy_uses_explicit_clock():
    policy = TimingPolicy(
        policy_id="synthetic-15m-window",
        bar_seconds=900,
        maximum_trigger_bars=2,
        max_setup_to_trigger_seconds=600,
        minimum_wait_seconds=10,
    )
    event = detection(source_timeframe="15m")
    assert_decision(
        evaluate(
            event,
            policy=policy,
            current_time=T0 + timedelta(seconds=10, microseconds=-1),
        ),
        "WAIT",
        "entry_window_not_open",
    )
    at_open = evaluate(event, policy=policy, current_time=T0 + timedelta(seconds=10))
    assert_decision(at_open, "CONTINUE", "passed")
    assert at_open.latest_valid_entry_time == T0 + timedelta(seconds=1800)
    assert at_open.timing_window_type == policy.policy_id
    assert_decision(
        evaluate(event, policy=policy, current_time=T0 + timedelta(seconds=1800)),
        "CANCEL",
        "entry_window_expired",
    )


@pytest.mark.parametrize(
    "timeframe,seconds", [("5m", 300), ("15m", 900), ("1H", 3600), ("4H", 14400)]
)
def test_policy_bar_duration_must_match_its_source_timeframe(timeframe, seconds):
    event = detection(source_timeframe=timeframe)
    matching = TimingPolicy(
        policy_id="synthetic-matched",
        bar_seconds=seconds,
        maximum_trigger_bars=1,
        max_setup_to_trigger_seconds=600,
    )
    assert_decision(evaluate(event, policy=matching), "CONTINUE", "passed")
    mismatched = TimingPolicy(
        policy_id="synthetic-mismatched",
        bar_seconds=seconds - 1,
        maximum_trigger_bars=1,
        max_setup_to_trigger_seconds=600,
    )
    assert_decision(
        evaluate(event, policy=mismatched), "CANCEL", "timing_policy_mismatch"
    )


def test_original_trigger_expiry_is_never_extended_by_candidate_or_policy():
    event = detection(trigger_updates={"expires_at": T0 + timedelta(seconds=20)})
    before = evaluate(event, current_time=T0 + timedelta(seconds=20, microseconds=-1))
    assert_decision(before, "CONTINUE", "passed")
    assert before.latest_valid_entry_time == T0 + timedelta(seconds=20)
    assert_decision(
        evaluate(event, current_time=T0 + timedelta(seconds=20)),
        "CANCEL",
        "trigger_expired",
    )


def test_candidate_expiry_is_the_earliest_deadline_and_is_exclusive():
    deadline = T0 + timedelta(seconds=15)
    before = evaluate(
        candidate_expires_at=deadline, current_time=deadline - timedelta(microseconds=1)
    )
    assert_decision(before, "CONTINUE", "passed")
    assert before.latest_valid_entry_time == deadline
    assert_decision(
        evaluate(candidate_expires_at=deadline, current_time=deadline),
        "CANCEL",
        "stale_candidate",
    )


@pytest.mark.parametrize("expiry", [T0, T0 - timedelta(seconds=1)])
def test_empty_or_reversed_candidate_lifetime_is_cancelled(expiry):
    assert_decision(evaluate(candidate_expires_at=expiry), "CANCEL", "stale_candidate")


@pytest.mark.parametrize("future_field", ["candidate", "observation"])
def test_evaluation_before_evidence_or_candidate_creation_waits(future_field):
    event = (
        detection(observed_at=T0 + timedelta(seconds=10))
        if future_field == "observation"
        else detection()
    )
    overrides = (
        {"candidate_created_at": T0 + timedelta(seconds=10)}
        if future_field == "candidate"
        else {}
    )
    assert_decision(evaluate(event, **overrides), "WAIT", "entry_window_not_open")


@pytest.mark.parametrize("lead", [timedelta(seconds=1), timedelta(microseconds=1)])
def test_future_trigger_waits_without_reporting_a_completed_candle(lead):
    result = evaluate(current_time=T0 - lead)
    assert_decision(result, "WAIT", "entry_window_not_open")
    assert result.seconds_since_trigger == -1
    assert result.candles_since_trigger is None


@pytest.mark.parametrize("missing_setup", [False, True])
def test_missing_trigger_or_setup_does_not_pass(missing_setup):
    event = detection(
        trigger=None, setup_time=None if missing_setup else T0, invalidation_price=None
    )
    result = evaluate(event)
    assert_decision(result, "WAIT", "too_early_no_trigger")
    assert result.event_key is None
    assert result.trigger_time is None
    assert result.seconds_since_trigger is None
    assert result.candles_since_trigger is None
    assert result.latest_valid_entry_time is None
    assert event_identity(event) is None


@pytest.mark.parametrize(
    "codes,action",
    [
        (("setup_missing",), "WAIT"),
        (("trigger_missing",), "WAIT"),
        (("setup_missing", "trigger_missing"), "WAIT"),
        (("source_invalid",), "CANCEL"),
        (("trigger_missing", "source_invalid"), "CANCEL"),
    ],
)
def test_detection_failure_codes_fail_closed_without_promoting_a_trigger(codes, action):
    assert_decision(evaluate(detection(fail_codes=codes)), action, codes[0])


def test_original_invalidation_reason_cancels_before_price_checks():
    result = evaluate(
        detection(
            trigger_updates={"invalidation_reason": "setup structurally invalidated"}
        )
    )
    assert_decision(result, "CANCEL", "trigger_invalidated")
    assert result.reason == "setup structurally invalidated"


@pytest.mark.parametrize(
    "direction,anchor,outside", [("long", "90", "89.99"), ("short", "110", "110.01")]
)
def test_reference_touching_or_crossing_invalidation_cancels(
    direction, anchor, outside
):
    for price in (anchor, outside):
        assert_decision(
            evaluate(detection(direction=direction), reference_price=D(price)),
            "CANCEL",
            "trigger_invalidated",
        )


@pytest.mark.parametrize(
    "direction,anchor",
    [("long", "100"), ("long", "101"), ("short", "100"), ("short", "99")],
)
def test_zero_or_reversed_trigger_risk_cannot_pass(direction, anchor):
    assert_decision(
        evaluate(detection(direction=direction, invalidation_price=D(anchor))),
        "CANCEL",
        "trigger_invalidated",
    )


@pytest.mark.parametrize(
    "direction,inside,boundary,adverse",
    [("long", "104.999999", "105", "99"), ("short", "95.000001", "95", "101")],
)
def test_favorable_impulse_boundary_is_exclusive_in_both_directions(
    direction, inside, boundary, adverse
):
    event = detection(direction=direction)
    assert_decision(evaluate(event, reference_price=D(inside)), "CONTINUE", "passed")
    assert_decision(
        evaluate(event, reference_price=D(boundary)),
        "CANCEL",
        "impulse_already_extended",
    )
    assert_decision(evaluate(event, reference_price=D(adverse)), "CONTINUE", "passed")


@pytest.mark.parametrize(
    "price",
    [
        None,
        True,
        100,
        100.0,
        "100",
        D("0"),
        D("-1"),
        D("NaN"),
        D("sNaN"),
        D("Infinity"),
        D("-Infinity"),
    ],
)
def test_invalid_reference_prices_cancel_without_arithmetic_errors(price):
    assert_decision(
        evaluate(reference_price=price), "CANCEL", "reference_price_invalid"
    )


def test_candidate_created_before_trigger_is_stale_even_when_price_and_age_pass():
    assert_decision(
        evaluate(candidate_created_at=T0 - timedelta(microseconds=1)),
        "CANCEL",
        "stale_candidate",
    )


def test_consumed_event_cannot_be_reentered_with_a_new_report_or_snapshot():
    original = detection()
    refreshed = detection(
        report_id="renamed_report",
        source_sha256="b" * 64,
        observed_at=T0 + timedelta(seconds=2),
    )
    key = event_identity(original)
    assert event_identity(refreshed) == key
    result = evaluate(
        refreshed,
        current_time=T0 + timedelta(seconds=3),
        candidate_created_at=T0 + timedelta(seconds=2),
        consumed_event_keys=frozenset({key}),
    )
    assert_decision(result, "CANCEL", "stale_candidate")
    assert result.report_id == "renamed_report"


def test_unrelated_consumed_event_does_not_block_a_new_event():
    consumed = frozenset(
        {event_identity(detection(trigger_updates={"trigger_type": "different_event"}))}
    )
    assert_decision(evaluate(consumed_event_keys=consumed), "CONTINUE", "passed")


@pytest.mark.parametrize("spelling", ["100", "100.0", "100.000000", "1E+2"])
def test_event_identity_ignores_decimal_trailing_zeros_and_exponent_spelling(spelling):
    assert event_identity(
        detection(trigger_updates={"trigger_price": D(spelling)})
    ) == event_identity(detection())


@pytest.mark.parametrize("offset_hours", [-7, 0, 8, 14])
def test_equivalent_timezone_instants_have_the_same_event_identity(offset_hours):
    shifted = T0.astimezone(timezone(timedelta(hours=offset_hours)))
    event = detection(trigger_updates={"trigger_time": shifted})
    assert event_identity(event) == event_identity(detection())


def test_consumed_event_cannot_be_replayed_by_changing_utc_offset():
    original = detection()
    equivalent = detection(
        trigger_updates={"trigger_time": T0.astimezone(timezone(timedelta(hours=8)))}
    )
    assert_decision(
        evaluate(equivalent, consumed_event_keys=frozenset({event_identity(original)})),
        "CANCEL",
        "stale_candidate",
    )


@pytest.mark.parametrize(
    "updates,trigger_updates",
    [
        ({"instrument_id": "ETH-USDT-SWAP"}, {}),
        ({"strategy": "fvg_return"}, {}),
        ({"direction": "short"}, {}),
        ({}, {"trigger_type": "different_event"}),
        ({}, {"trigger_price": D("100.01")}),
        ({}, {"trigger_time": T0 - timedelta(seconds=1)}),
    ],
)
def test_material_event_fields_change_identity(updates, trigger_updates):
    assert event_identity(
        detection(**updates, trigger_updates=trigger_updates)
    ) != event_identity(detection())


def test_original_trigger_expiration_takes_precedence_over_minimum_wait():
    policy = TimingPolicy(
        policy_id="synthetic-wait",
        maximum_trigger_bars=1,
        max_setup_to_trigger_seconds=600,
        minimum_wait_seconds=120,
    )
    event = detection(trigger_updates={"expires_at": T0 + timedelta(seconds=60)})
    assert_decision(
        evaluate(event, policy=policy, current_time=T0 + timedelta(seconds=60)),
        "CANCEL",
        "trigger_expired",
    )


@pytest.mark.parametrize("minimum_wait", [300, 301, 600])
def test_policy_wait_must_leave_a_nonempty_entry_window(minimum_wait):
    with pytest.raises(ValidationError, match="minimum wait"):
        TimingPolicy(
            policy_id="synthetic-empty",
            maximum_trigger_bars=1,
            max_setup_to_trigger_seconds=600,
            minimum_wait_seconds=minimum_wait,
        )


@pytest.mark.parametrize(
    "codes",
    [("trigger_missing",), ("setup_missing",), ("trigger_missing", "setup_missing")],
)
def test_expired_present_trigger_cannot_be_disguised_as_waiting_missing_evidence(codes):
    event = detection(
        fail_codes=codes, trigger_updates={"expires_at": T0 + timedelta(seconds=60)}
    )
    assert_decision(
        evaluate(event, current_time=T0 + timedelta(seconds=60)),
        "CANCEL",
        "trigger_expired",
    )


def test_expired_and_invalidated_event_is_always_cancelled():
    event = detection(
        trigger_updates={
            "expires_at": T0 + timedelta(seconds=60),
            "invalidation_reason": "structure invalidated",
        }
    )
    result = evaluate(event, current_time=T0 + timedelta(seconds=60))
    assert result.action == "CANCEL"
    assert result.code in {"trigger_expired", "trigger_invalidated"}
    assert result.timing_valid is False


def test_expired_trigger_cannot_wait_on_a_new_candidate_or_refresh_observation():
    event = detection(
        observed_at=T0 + timedelta(seconds=120),
        trigger_updates={"expires_at": T0 + timedelta(seconds=60)},
    )
    assert_decision(
        evaluate(
            event,
            current_time=T0 + timedelta(seconds=60),
            candidate_created_at=T0 + timedelta(seconds=120),
        ),
        "CANCEL",
        "trigger_expired",
    )


@pytest.mark.parametrize(
    "field", ["current_time", "candidate_created_at", "candidate_expires_at"]
)
def test_naive_evaluation_timestamps_are_rejected(field):
    with pytest.raises(ValueError, match="timezone-aware"):
        evaluate(**{field: T0.replace(tzinfo=None)})


@pytest.mark.parametrize("field", ["observed_at", "setup_time"])
def test_naive_detection_timestamps_are_rejected(field):
    with pytest.raises(ValidationError):
        detection(**{field: T0.replace(tzinfo=None)})


@pytest.mark.parametrize("field", ["trigger_time", "expires_at"])
def test_naive_trigger_timestamps_are_rejected(field):
    with pytest.raises(ValidationError):
        detection(trigger_updates={field: T0.replace(tzinfo=None)})


@pytest.mark.parametrize(
    "updates",
    [
        {"source_sha256": None},
        {"invalidation_price": None},
        {"setup_time": None},
        {"observed_at": T0 - timedelta(seconds=1)},
        {"setup_time": T0 + timedelta(seconds=1)},
        {"strategy": "unknown"},
        {"execution_authority": True},
    ],
)
def test_detection_requires_consistent_bounded_source_bound_records(updates):
    with pytest.raises(ValidationError):
        detection(**updates)


def test_nested_trigger_report_id_must_match_detection():
    with pytest.raises(ValidationError, match="report mismatch"):
        detection(trigger_updates={"report_id": "other_report"})


@pytest.mark.parametrize(
    "updates",
    [
        {"trigger_price": 100.0},
        {"trigger_time": T0.replace(tzinfo=None)},
        {"expires_at": T0},
        {"report_id": "other_report"},
    ],
)
def test_evaluator_revalidates_nested_model_copy_tampering(updates):
    original = detection()
    poisoned = original.model_copy(
        update={"trigger": original.trigger.model_copy(update=updates)}
    )
    with pytest.raises(ValidationError):
        evaluate(poisoned)


@pytest.mark.parametrize(
    "updates",
    [
        {"source_sha256": None},
        {"invalidation_price": 90.0},
        {"strategy": "not_a_strategy"},
    ],
)
def test_evaluator_revalidates_top_level_model_copy_tampering(updates):
    with pytest.raises(ValidationError):
        evaluate(detection().model_copy(update=updates))


@pytest.mark.parametrize(
    "updates",
    [
        {"maximum_trigger_bars": 0},
        {"maximum_trigger_bars": 13},
        {"bar_seconds": 0},
        {"bar_seconds": 14401},
        {"max_setup_to_trigger_seconds": 0},
        {"minimum_wait_seconds": -1},
        {"minimum_wait_seconds": 3601},
        {"max_favorable_move_r": D("0")},
        {"max_favorable_move_r": D("2.01")},
        {"max_favorable_move_r": D("NaN")},
        {"intrabar_allowed": True},
        {"retracement_reentry_allowed": True},
        {"candle_close_only": False},
        {"maximum_trigger_bars": True},
        {"max_favorable_move_r": 0.5},
    ],
)
def test_invalid_policy_and_unvalidated_policy_copies_are_rejected(updates):
    policy = TIMING_POLICIES["trend_pullback"]
    with pytest.raises(ValidationError):
        TimingPolicy.model_validate({**policy.model_dump(round_trip=True), **updates})
    with pytest.raises(ValidationError):
        evaluate(policy=policy.model_copy(update=updates))


@pytest.mark.parametrize(
    "field,value",
    [
        ("candle_close_only", 1),
        ("intrabar_allowed", 0),
        ("retracement_reentry_allowed", 0),
    ],
)
def test_policy_authority_flags_require_exact_booleans(field, value):
    with pytest.raises(ValidationError):
        TimingPolicy.model_validate(
            {
                **TIMING_POLICIES["trend_pullback"].model_dump(round_trip=True),
                field: value,
            }
        )


def test_policy_catalog_and_domain_records_are_immutable():
    with pytest.raises(TypeError):
        TIMING_POLICIES["new_strategy"] = TIMING_POLICIES["trend_pullback"]
    for model, field, value in [
        (TIMING_POLICIES["trend_pullback"], "bar_seconds", 1),
        (detection(), "source_sha256", "b" * 64),
        (detection().trigger, "trigger_price", D("101")),
        (evaluate(), "action", "CANCEL"),
    ]:
        with pytest.raises(ValidationError, match="frozen"):
            setattr(model, field, value)


def test_json_input_round_trips_keep_timing_result_and_event_identity():
    event = detection(trigger_updates={"trigger_price": D("100.000")})
    restored = TriggerDetection.model_validate_json(
        event.model_dump_json(round_trip=True)
    )
    policy = TIMING_POLICIES["trend_pullback"]
    restored_policy = TimingPolicy.model_validate_json(
        policy.model_dump_json(round_trip=True)
    )
    expected = evaluate(event, policy=policy)
    actual = evaluate(restored, policy=restored_policy)
    assert actual == expected
    assert event_identity(restored) == event_identity(event)
    assert (
        TimingResult.model_validate_json(actual.model_dump_json(round_trip=True))
        == actual
    )
    with pytest.raises(ValidationError, match="extra_forbidden"):
        TimingResult.model_validate_json(actual.model_dump_json())


@pytest.mark.parametrize("direction", ["long", "short"])
def test_timing_and_identity_are_independent_of_callers_decimal_context(direction):
    event = detection(
        direction=direction,
        trigger_updates={"trigger_price": D("100.001")},
        invalidation_price=D("90.001") if direction == "long" else D("110.001"),
    )
    reference = D("104.001") if direction == "long" else D("96.001")
    expected = evaluate(event, reference_price=reference)
    expected_key = event_identity(event)
    context = Context(prec=3, rounding=ROUND_DOWN)
    context.traps[Inexact] = True
    context.traps[Rounded] = True
    with localcontext(context):
        actual = evaluate(event, reference_price=reference)
        assert actual == expected
        assert event_identity(event) == expected_key
    assert_decision(actual, "CONTINUE", "passed")
