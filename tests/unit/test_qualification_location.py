"""Synthetic location checks; no market client, settings, or execution IO."""

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta, timezone
from decimal import ROUND_DOWN, Context, Decimal, Inexact, Rounded, localcontext

import pytest
from pydantic import ValidationError

from app.trade_qualification.event_models import TriggerDetection
from app.trade_qualification.location import (
    ExecutableQuote,
    LocationResult,
    build_entry_zone,
    evaluate_location,
    quote_fingerprint,
)
from app.trade_qualification.models import EntryTrigger, EntryZone

D = Decimal
T0 = datetime(2026, 1, 5, 12, tzinfo=UTC)
REPORT = "synthetic-location-report"
INSTRUMENT = "BTC-USDT-SWAP"
QUOTE_PRICES = ("bid", "ask", "mark_price", "bid_size", "ask_size")
QUOTE_NUMBERS = (*QUOTE_PRICES, "funding_rate")
QUOTE_TIMES = ("quote_time", "mark_time", "funding_time", "received_at")


def executable_quote(**updates):
    fields = {
        "report_id": REPORT,
        "instrument_id": INSTRUMENT,
        "source": "ws",
        "bid": D("99.9"),
        "ask": D("100.1"),
        "mark_price": D("100"),
        "bid_size": D("2"),
        "ask_size": D("3"),
        "funding_rate": D("0.0001"),
        "quote_time": T0,
        "mark_time": T0,
        "funding_time": T0,
        "received_at": T0,
    }
    return ExecutableQuote(**(fields | updates))


def entry_zone(*, direction="long", **updates):
    fields = {
        "report_id": REPORT,
        "instrument_id": INSTRUMENT,
        "direction": direction,
        "source_sha256": "a" * 64,
        "zone_low": D("99"),
        "zone_high": D("101"),
        "zone_type": "synthetic_closed_retest",
        "zone_source": "synthetic source pin",
        "created_at": T0 - timedelta(seconds=1),
        "expires_at": T0 + timedelta(minutes=5),
        "max_allowed_drift_bps": D("20"),
        "invalidation_price": D("95") if direction == "long" else D("105"),
    }
    return EntryZone(**(fields | updates))


def evaluate(**updates):
    direction = updates.get("direction", "long")
    zone_direction = (
        direction
        if type(direction) is str and direction in {"long", "short"}
        else "long"
    )
    fields = {
        "report_id": REPORT,
        "instrument_id": INSTRUMENT,
        "direction": direction,
        "zone": entry_zone(direction=zone_direction),
        "candidate_entry": D("100"),
        "quote": executable_quote(),
        "current_time": T0 + timedelta(seconds=1),
        "max_quote_age_seconds": 30,
    }
    return evaluate_location(**(fields | updates))


def event(*, basis_updates=None, basis_remove=(), trigger_updates=None, **updates):
    direction = updates.get("direction", "long")
    setup_time = T0 - timedelta(minutes=5)
    basis = {
        "zone_low": "99.01",
        "zone_high": "101.09",
        "setup_close": "100",
        "source_closed_at": setup_time.isoformat(),
        "setup_timeframe": "15m",
        "ohlc_ordering": "close_confirmed_only",
        "atr": "2",
    }
    basis.update(basis_updates or {})
    for name in basis_remove:
        basis.pop(name, None)
    trigger_fields = {
        "report_id": REPORT,
        "trigger_type": "synthetic_momentum_transition",
        "trigger_time": T0,
        "trigger_price": D("100"),
        "expires_at": T0 + timedelta(minutes=5),
    }
    trigger_fields.update(trigger_updates or {})
    fields = {
        "report_id": REPORT,
        "symbol": "BTC/USDT",
        "instrument_id": INSTRUMENT,
        "strategy": "trend_pullback",
        "direction": direction,
        "observed_at": T0 + timedelta(seconds=1),
        "source_sha256": "a" * 64,
        "source_timeframe": "5m",
        "setup_time": setup_time,
        "setup_type": "synthetic_closed_retest",
        "trigger": EntryTrigger(**trigger_fields),
        "invalidation_price": D("95") if direction == "long" else D("105"),
        "setup_basis": tuple(basis.items()),
    }
    return TriggerDetection(**(fields | updates))


def build(detection=None, **updates):
    fields = {
        "tick_size": D("0.25"),
        "max_allowed_drift_bps": D("20"),
        "expires_at": T0 + timedelta(minutes=3),
    }
    return build_entry_zone(
        event() if detection is None else detection, **(fields | updates)
    )


def assert_result(result, code):
    assert result.report_id == REPORT
    assert result.instrument_id == INSTRUMENT
    assert result.code == code
    assert result.passed is (code == "passed")
    assert result.reason.strip()


@pytest.mark.parametrize("source", ["rest", "ws"])
def test_quote_json_roundtrip_and_optional_request_metadata(source):
    quote = executable_quote(
        source=source, request_started_at=T0 - timedelta(seconds=1)
    )
    assert ExecutableQuote.model_validate_json(quote.model_dump_json()) == quote
    assert executable_quote().request_started_at is None
    assert re.fullmatch("[a-f0-9]{64}", quote_fingerprint(quote))


@pytest.mark.parametrize("field", QUOTE_NUMBERS)
@pytest.mark.parametrize(
    "value", [1, 1.0, "1", True, D("NaN"), D("Infinity"), D("-Infinity")]
)
def test_quote_python_numbers_require_finite_decimal(field, value):
    with pytest.raises(ValidationError):
        executable_quote(**{field: value})


@pytest.mark.parametrize("field", QUOTE_PRICES)
@pytest.mark.parametrize("value", [D("0"), D("-1")])
def test_prices_and_sizes_must_be_positive(field, value):
    with pytest.raises(ValidationError):
        executable_quote(**{field: value})


@pytest.mark.parametrize("field", QUOTE_NUMBERS)
@pytest.mark.parametrize("value", [D("1E-21"), D("1E40")])
def test_quote_decimals_are_bounded(field, value):
    with pytest.raises(ValidationError):
        executable_quote(**{field: value})


@pytest.mark.parametrize("rate", [D("-0.01"), D("0"), D("0.01")])
def test_funding_can_be_negative_zero_or_positive(rate):
    assert executable_quote(funding_rate=rate).funding_rate == rate


@pytest.mark.parametrize("field", (*QUOTE_TIMES, "request_started_at"))
@pytest.mark.parametrize("value", [T0.replace(tzinfo=None), T0.isoformat(), 123, True])
def test_quote_python_times_require_aware_datetime(field, value):
    with pytest.raises(ValidationError):
        executable_quote(**{field: value})


@pytest.mark.parametrize("field", (*QUOTE_TIMES, "request_started_at"))
def test_quote_normalizes_every_time_to_utc(field):
    taipei = T0.astimezone(timezone(timedelta(hours=8)))
    quote = executable_quote(**{field: taipei})
    assert getattr(quote, field).tzinfo is UTC
    assert getattr(quote, field) == T0


def test_quote_is_strict_frozen_and_rejects_extras():
    quote = executable_quote()
    with pytest.raises(ValidationError):
        quote.bid = D("1")
    with pytest.raises(ValidationError):
        executable_quote(last_price=D("100"))
    with pytest.raises(ValidationError):
        executable_quote(source="cached")


def test_fingerprint_is_numeric_and_timezone_canonical_not_repr_dependent():
    base = executable_quote(request_started_at=T0)
    alternate = executable_quote(
        bid=D("99.9000"),
        ask=D("100.100"),
        mark_price=D("1E2"),
        bid_size=D("2.00"),
        ask_size=D("3.0000"),
        funding_rate=D("0.0001000"),
        **{
            name: T0.astimezone(timezone(timedelta(hours=8)))
            for name in (*QUOTE_TIMES, "request_started_at")
        },
    )
    assert quote_fingerprint(base) == quote_fingerprint(alternate)
    assert quote_fingerprint(
        executable_quote(funding_rate=D("0"))
    ) == quote_fingerprint(executable_quote(funding_rate=D("-0.00")))


@pytest.mark.parametrize(
    "field,value",
    [
        ("report_id", "another-report"),
        ("instrument_id", "ETH-USDT-SWAP"),
        ("source", "rest"),
        ("bid", D("99.8")),
        ("ask", D("100.2")),
        ("mark_price", D("100.3")),
        ("bid_size", D("4")),
        ("ask_size", D("4")),
        ("funding_rate", D("-0.0001")),
        *((name, T0 - timedelta(microseconds=1)) for name in QUOTE_TIMES),
        ("request_started_at", T0 - timedelta(seconds=1)),
    ],
)
def test_fingerprint_pins_every_field(field, value):
    assert quote_fingerprint(executable_quote()) != quote_fingerprint(
        executable_quote(**{field: value})
    )


def test_fingerprint_matches_the_full_canonical_record():
    payload = {
        "report_id": REPORT,
        "instrument_id": INSTRUMENT,
        "source": "ws",
        "bid": "99.9",
        "ask": "100.1",
        "mark_price": "100",
        "bid_size": "2",
        "ask_size": "3",
        "funding_rate": "0.0001",
        "quote_time": T0.isoformat(),
        "mark_time": T0.isoformat(),
        "funding_time": T0.isoformat(),
        "received_at": T0.isoformat(),
        "request_started_at": None,
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    assert (
        quote_fingerprint(executable_quote()) == hashlib.sha256(canonical).hexdigest()
    )


@pytest.mark.parametrize(
    "direction,reference", [("long", D("100.1")), ("short", D("99.9"))]
)
def test_long_uses_ask_short_uses_bid_and_not_mark(direction, reference):
    quote = executable_quote(mark_price=D("100.5"))
    result = evaluate(direction=direction, quote=quote)
    assert_result(result, "passed")
    assert result.reference_price == reference
    assert result.drift_bps == D("10")
    assert result.quote_sha256 == quote_fingerprint(quote)
    assert LocationResult.model_validate_json(result.model_dump_json()) == result


@pytest.mark.parametrize("direction,mark", [("long", D("102")), ("short", D("98"))])
def test_mark_need_not_be_in_zone_when_still_before_directional_invalidation(
    direction, mark
):
    assert_result(
        evaluate(direction=direction, quote=executable_quote(mark_price=mark)), "passed"
    )


def test_locked_positive_book_is_valid():
    assert_result(
        evaluate(quote=executable_quote(bid=D("100"), ask=D("100"))), "passed"
    )


@pytest.mark.parametrize("direction", ["long", "short"])
@pytest.mark.parametrize("price", [D("99"), D("101")])
def test_entry_zone_price_boundaries_are_inclusive(direction, price):
    quote = executable_quote(bid=price, ask=price, mark_price=price)
    result = evaluate(direction=direction, quote=quote, candidate_entry=price)
    assert_result(result, "passed")
    assert result.drift_bps == 0


@pytest.mark.parametrize(
    "direction,price", [("long", D("100.2")), ("short", D("99.8"))]
)
def test_drift_limit_is_inclusive(direction, price):
    quote = executable_quote(bid=price, ask=price, mark_price=price)
    result = evaluate(direction=direction, quote=quote)
    assert_result(result, "passed")
    assert result.drift_bps == D("20")


@pytest.mark.parametrize(
    "direction,price",
    [("long", D("100.20000000000000000001")), ("short", D("99.79999999999999999999"))],
)
def test_drift_above_boundary_does_not_round_to_a_pass(direction, price):
    assert_result(
        evaluate(
            direction=direction,
            quote=executable_quote(bid=price, ask=price, mark_price=price),
        ),
        "entry_drift_exceeds_limit",
    )


def test_zero_drift_budget_allows_only_exact_reference():
    zone = entry_zone(max_allowed_drift_bps=D("0"))
    assert_result(
        evaluate(zone=zone, quote=executable_quote(bid=D("100"), ask=D("100"))),
        "passed",
    )
    assert_result(evaluate(zone=zone), "entry_drift_exceeds_limit")


@pytest.mark.parametrize(
    "field,code",
    [("zone", "entry_zone_missing"), ("quote", "executable_quote_missing")],
)
def test_missing_inputs_fail_closed(field, code):
    assert_result(evaluate(**{field: None}), code)


@pytest.mark.parametrize(
    "updates",
    [
        {"zone": entry_zone(report_id="other")},
        {"quote": executable_quote(report_id="other")},
        {"quote": executable_quote(instrument_id="ETH-USDT-SWAP")},
    ],
)
def test_cross_report_and_instrument_inputs_are_rejected(updates):
    assert_result(evaluate(**updates), "identity_mismatch")


@pytest.mark.parametrize("direction", ["LONG", "neutral", None, True, 1, [], {}])
def test_unknown_direction_fails_closed(direction):
    assert_result(evaluate(direction=direction, zone=entry_zone()), "direction_invalid")


@pytest.mark.parametrize(
    "value",
    [
        None,
        100,
        100.0,
        "100",
        True,
        D("0"),
        D("-1"),
        D("NaN"),
        D("Infinity"),
        D("1E-21"),
        D("1E40"),
    ],
)
def test_candidate_entry_is_a_strict_positive_bounded_decimal(value):
    assert_result(evaluate(candidate_entry=value), "candidate_entry_invalid")


@pytest.mark.parametrize("value", [None, -1, 0, 86401, 1.0, "30", True])
def test_age_limit_requires_a_positive_exact_integer(value):
    assert_result(evaluate(max_quote_age_seconds=value), "quote_age_limit_invalid")


@pytest.mark.parametrize(
    "value", [None, T0.replace(tzinfo=None), T0.isoformat(), 123, True]
)
def test_current_time_requires_an_aware_datetime(value):
    assert_result(evaluate(current_time=value), "current_time_invalid")


@pytest.mark.parametrize("field", QUOTE_TIMES)
def test_each_component_is_fresh_at_exact_maximum_age(field):
    quote = executable_quote(**{field: T0 - timedelta(seconds=30)})
    if field == "received_at":
        quote = executable_quote(
            **{name: T0 - timedelta(seconds=30) for name in QUOTE_TIMES}
        )
    assert_result(evaluate(quote=quote, current_time=T0), "passed")


@pytest.mark.parametrize("field", QUOTE_TIMES)
def test_each_component_stale_by_one_microsecond_fails(field):
    old = T0 - timedelta(seconds=30, microseconds=1)
    quote = executable_quote(**{field: old})
    if field == "received_at":
        quote = executable_quote(**{name: old for name in QUOTE_TIMES})
    assert_result(evaluate(quote=quote, current_time=T0), "quote_stale")


@pytest.mark.parametrize("field", QUOTE_TIMES)
def test_each_future_timestamp_fails_even_by_one_microsecond(field):
    quote = executable_quote(**{field: T0 + timedelta(microseconds=1)})
    assert_result(evaluate(quote=quote, current_time=T0), "quote_timestamp_invalid")


@pytest.mark.parametrize(
    "field", ["quote_time", "mark_time", "funding_time", "request_started_at"]
)
def test_no_component_can_follow_received_time_even_if_not_future_now(field):
    quote = executable_quote(**{field: T0 + timedelta(microseconds=1)})
    assert_result(
        evaluate(quote=quote, current_time=T0 + timedelta(seconds=1)),
        "quote_timestamp_invalid",
    )


def test_future_request_start_is_invalid():
    assert_result(
        evaluate(quote=executable_quote(request_started_at=T0 + timedelta(seconds=2))),
        "quote_timestamp_invalid",
    )


def test_received_timestamp_does_not_refresh_old_quote_mark_or_funding():
    fresh = T0 + timedelta(minutes=1)
    quote = executable_quote(received_at=fresh)
    assert_result(evaluate(quote=quote, current_time=fresh), "quote_stale")


def test_crossed_book_is_rejected():
    assert_result(
        evaluate(quote=executable_quote(bid=D("101"), ask=D("99"))),
        "quote_geometry_invalid",
    )


def test_zone_open_is_inclusive_and_expiry_exclusive():
    zone = entry_zone(created_at=T0, expires_at=T0 + timedelta(seconds=30))
    assert_result(evaluate(zone=zone, current_time=T0), "passed")
    assert_result(
        evaluate(zone=zone, current_time=T0 + timedelta(seconds=30, microseconds=-1)),
        "passed",
    )
    assert_result(
        evaluate(zone=zone, current_time=T0 + timedelta(seconds=30)),
        "entry_zone_expired",
    )


def test_unopened_zone_fails_without_waiting_or_moving_its_clock():
    zone = entry_zone(created_at=T0 + timedelta(seconds=2))
    assert_result(evaluate(zone=zone), "entry_zone_not_open")


@pytest.mark.parametrize(
    "price", [D("98.99999999999999999999"), D("101.00000000000000000001")]
)
def test_candidate_outside_zone_fails_even_when_reference_is_inside(price):
    assert_result(evaluate(candidate_entry=price), "candidate_outside_entry_zone")


@pytest.mark.parametrize(
    "direction,price",
    [("long", D("101.00000000000000000001")), ("short", D("98.99999999999999999999"))],
)
def test_executable_reference_outside_zone_fails(direction, price):
    assert_result(
        evaluate(direction=direction, quote=executable_quote(bid=price, ask=price)),
        "reference_outside_entry_zone",
    )


@pytest.mark.parametrize(
    "direction,mark",
    [
        ("long", D("95")),
        ("long", D("94.99")),
        ("short", D("105")),
        ("short", D("105.01")),
    ],
)
def test_touching_or_crossing_invalidation_with_mark_fails(direction, mark):
    assert_result(
        evaluate(direction=direction, quote=executable_quote(mark_price=mark)),
        "invalidation_crossed",
    )


@pytest.mark.parametrize(
    "direction,invalidation", [("long", D("105")), ("short", D("95"))]
)
def test_wrong_side_invalidation_fails(direction, invalidation):
    assert_result(
        evaluate(
            direction=direction,
            zone=entry_zone(direction=direction, invalidation_price=invalidation),
        ),
        "invalidation_crossed",
    )


@pytest.mark.parametrize(
    "updates",
    [
        {"zone_low": 99.0},
        {"zone_high": D("NaN")},
        {"zone_low": D("102")},
        {"invalidation_price": D("100")},
        {"max_allowed_drift_bps": D("-1")},
        {"created_at": T0.replace(tzinfo=None)},
        {"expires_at": T0 - timedelta(minutes=1)},
        {"report_id": "../bad"},
    ],
)
def test_copied_zone_is_revalidated_before_use(updates):
    assert_result(
        evaluate(zone=entry_zone().model_copy(update=updates)), "entry_zone_invalid"
    )


@pytest.mark.parametrize(
    "updates",
    [
        {"bid": 99.0},
        {"ask": "100"},
        {"mark_price": D("NaN")},
        {"bid_size": D("0")},
        {"funding_rate": float("nan")},
        {"quote_time": T0.replace(tzinfo=None)},
        {"source": "cache"},
        {"report_id": "../bad"},
    ],
)
def test_copied_quote_is_revalidated_before_use(updates):
    assert_result(
        evaluate(quote=executable_quote().model_copy(update=updates)),
        "executable_quote_invalid",
    )
    with pytest.raises((ValueError, TypeError)):
        quote_fingerprint(executable_quote().model_copy(update=updates))


@pytest.mark.parametrize("field", ["zone", "quote"])
def test_undeclared_model_copy_fields_are_not_silently_discarded(field):
    original = entry_zone() if field == "zone" else executable_quote()
    copied = original.model_copy(update={"execution_authority": True})
    code = "entry_zone_invalid" if field == "zone" else "executable_quote_invalid"
    assert_result(evaluate(**{field: copied}), code)


@pytest.mark.parametrize("field", ["zone", "quote"])
def test_raw_mapping_is_not_a_trusted_domain_instance(field):
    value = entry_zone() if field == "zone" else executable_quote()
    code = "entry_zone_invalid" if field == "zone" else "executable_quote_invalid"
    assert_result(evaluate(**{field: value.model_dump()}), code)


@pytest.mark.parametrize("direction", ["long", "short"])
def test_builder_snaps_inward_to_real_non_power_of_ten_ticks(direction):
    detection = event(direction=direction)
    zone, code = build(detection)
    assert code == "passed"
    assert zone is not None
    assert zone.zone_low == D("99.25")
    assert zone.zone_high == D("101.00")
    assert zone.invalidation_price == detection.invalidation_price
    assert zone.zone_type == detection.setup_type
    assert zone.report_id == detection.report_id
    assert zone.instrument_id == detection.instrument_id
    assert zone.direction == detection.direction
    assert zone.source_sha256 == detection.source_sha256
    assert zone.max_allowed_drift_bps == D("20")
    assert detection.source_sha256 in zone.zone_source
    assert "15m" in zone.zone_source
    assert zone.created_at == detection.setup_time
    assert zone.created_at <= detection.observed_at < zone.expires_at
    assert zone.expires_at <= T0 + timedelta(minutes=3)
    assert EntryZone.model_validate_json(zone.model_dump_json()) == zone


def test_builder_source_clock_and_identity_change_its_provenance():
    original, _ = build()
    alternate, _ = build(event(source_sha256="b" * 64))
    setup = T0 - timedelta(minutes=10)
    earlier, _ = build(
        event(setup_time=setup, basis_updates={"source_closed_at": setup.isoformat()})
    )
    assert len({original.zone_source, alternate.zone_source, earlier.zone_source}) == 3


def test_builder_accepts_native_cross_timeframe_setup_and_trigger():
    zone, code = build(
        event(source_timeframe="5m", basis_updates={"setup_timeframe": "1H"})
    )
    assert code == "passed"
    assert "1H" in zone.zone_source


def test_builder_missing_optional_setup_timeframe_is_explicitly_unknown():
    zone, code = build(event(basis_remove=("setup_timeframe", "ohlc_ordering")))
    assert code == "passed"
    assert "unknown_setup_timeframe" in zone.zone_source


def test_irrelevant_basis_metadata_cannot_rewrite_the_price_band():
    original, code = build()
    alternate, other_code = build(
        event(
            basis_updates={
                "atr": "garbage",
                "candidate_entry": "999999",
                "renderer_note": "synthetic",
            }
        )
    )
    assert code == other_code == "passed"
    assert original == alternate


@pytest.mark.parametrize(
    "name", ["zone_low", "zone_high", "source_closed_at", "setup_close"]
)
def test_each_required_source_basis_field_is_required(name):
    assert build(event(basis_remove=(name,))) == (None, "source_basis_missing")


@pytest.mark.parametrize(
    "name,value",
    [
        ("zone_low", "NaN"),
        ("zone_high", "Infinity"),
        ("zone_low", "0"),
        ("zone_high", "-1"),
        ("zone_low", "102"),
        ("setup_close", "NaN"),
        ("setup_close", "0"),
        ("setup_close", "not-a-number"),
        ("source_closed_at", "not-a-time"),
        ("source_closed_at", T0.replace(tzinfo=None).isoformat()),
        ("source_closed_at", (T0 - timedelta(minutes=4)).isoformat()),
        ("setup_timeframe", "1m"),
        ("ohlc_ordering", "intrabar_known"),
    ],
)
def test_invalid_or_inconsistent_source_basis_is_rejected(name, value):
    assert build(event(basis_updates={name: value})) == (None, "source_basis_invalid")


def test_equivalent_source_time_timezone_is_not_a_mismatch():
    source_time = (T0 - timedelta(minutes=5)).astimezone(timezone(timedelta(hours=8)))
    assert (
        build(event(basis_updates={"source_closed_at": source_time.isoformat()}))[1]
        == "passed"
    )


@pytest.mark.parametrize(
    "updates",
    [
        {"trigger": None},
        {"setup_time": None},
        {"setup_type": None},
        {"source_sha256": None},
        {"invalidation_price": None},
        {"fail_codes": ("source_rejected",)},
        {"direction": "neutral"},
        {"source_sha256": "bad"},
        {"invalidation_price": 95.0},
    ],
)
def test_builder_revalidates_missing_or_tampered_event_fields(updates):
    assert build(event().model_copy(update=updates)) == (None, "source_event_invalid")


def test_builder_revalidates_nested_trigger_and_invalidation_reason():
    original = event()
    for trigger in (
        original.trigger.model_copy(update={"report_id": "other"}),
        original.trigger.model_copy(update={"trigger_price": 100.0}),
        original.trigger.model_copy(update={"invalidation_reason": "cancelled"}),
    ):
        assert build(original.model_copy(update={"trigger": trigger})) == (
            None,
            "source_event_invalid",
        )


def test_builder_rejects_same_time_setup_and_trigger():
    detection = event(setup_time=T0, basis_updates={"source_closed_at": T0.isoformat()})
    assert build(detection) == (None, "source_event_invalid")


def test_builder_requires_event_instance_not_raw_mapping():
    assert build(event().model_dump()) == (None, "source_event_invalid")


@pytest.mark.parametrize(
    "value",
    [
        None,
        1,
        0.25,
        "0.25",
        True,
        D("0"),
        D("-0.25"),
        D("NaN"),
        D("Infinity"),
        D("1E-21"),
        D("1E40"),
    ],
)
def test_tick_requires_strict_positive_finite_bounded_decimal(value):
    assert build(tick_size=value) == (None, "instrument_tick_invalid")


@pytest.mark.parametrize(
    "value",
    [
        None,
        20,
        20.0,
        "20",
        True,
        D("-1"),
        D("10000.01"),
        D("NaN"),
        D("Infinity"),
        D("1E-21"),
    ],
)
def test_builder_drift_limit_requires_strict_nonnegative_bounded_decimal(value):
    assert build(max_allowed_drift_bps=value) == (None, "drift_limit_invalid")


@pytest.mark.parametrize("value", [D("0"), D("10000")])
def test_builder_drift_budget_boundaries_are_inclusive(value):
    zone, code = build(max_allowed_drift_bps=value)
    assert code == "passed"
    assert zone.max_allowed_drift_bps == value


@pytest.mark.parametrize(
    "expiry", [T0 - timedelta(seconds=1), T0, T0 + timedelta(seconds=1)]
)
def test_builder_rejects_expiry_at_or_before_observation(expiry):
    assert build(expires_at=expiry) == (None, "entry_zone_expired")


def test_builder_does_not_extend_trigger_expiry():
    zone, code = build(expires_at=T0 + timedelta(hours=1))
    assert code == "passed"
    assert zone.expires_at <= event().trigger.expires_at


@pytest.mark.parametrize(
    "direction,close,expected_low,expected_high",
    [
        ("long", "100.74", "100.25", "100.50"),
        ("short", "99.26", "99.50", "99.75"),
    ],
)
def test_zero_width_source_expands_only_between_level_and_actual_setup_close(
    direction, close, expected_low, expected_high
):
    detection = event(
        direction=direction,
        invalidation_price=D("100"),
        basis_updates={"zone_low": "100", "zone_high": "100", "setup_close": close},
    )
    zone, code = build(detection)
    assert code == "passed"
    assert zone.zone_low == D(expected_low)
    assert zone.zone_high == D(expected_high)
    assert zone.invalidation_price == D("100")


@pytest.mark.parametrize("direction", ["long", "short"])
def test_level_equal_to_setup_close_cannot_manufacture_a_band(direction):
    detection = event(
        direction=direction,
        basis_updates={"zone_low": "100", "zone_high": "100", "setup_close": "100"},
    )
    assert build(detection) == (None, "no_executable_zone")


@pytest.mark.parametrize("direction,close", [("long", "100.24"), ("short", "99.76")])
def test_no_executable_tick_between_level_and_close_fails(direction, close):
    detection = event(
        direction=direction,
        invalidation_price=D("100"),
        basis_updates={"zone_low": "100", "zone_high": "100", "setup_close": close},
    )
    assert build(detection) == (None, "no_executable_zone")


@pytest.mark.parametrize(
    "direction,close,price",
    [("long", "100.25", D("100.25")), ("short", "99.75", D("99.75"))],
)
def test_one_noninvalid_tick_is_an_executable_final_zone(direction, close, price):
    detection = event(
        direction=direction,
        invalidation_price=D("100"),
        basis_updates={"zone_low": "100", "zone_high": "100", "setup_close": close},
    )
    zone, code = build(detection)
    assert code == "passed"
    assert zone.zone_low == zone.zone_high == price
    assert zone.invalidation_price == D("100")


@pytest.mark.parametrize("direction,close", [("long", "99"), ("short", "101")])
def test_zero_width_break_requires_close_on_the_executable_side(direction, close):
    detection = event(
        direction=direction,
        invalidation_price=D("100"),
        basis_updates={"zone_low": "100", "zone_high": "100", "setup_close": close},
    )
    assert build(detection) == (None, "source_basis_invalid")


@pytest.mark.parametrize(
    "direction,invalidation,low,high",
    [
        ("long", "99.25", "99.50", "101.00"),
        ("short", "101.00", "99.25", "100.75"),
    ],
)
def test_invalidation_boundary_is_excluded_without_moving_the_anchor(
    direction, invalidation, low, high
):
    boundary = "zone_low" if direction == "long" else "zone_high"
    detection = event(
        direction=direction,
        invalidation_price=D(invalidation),
        basis_updates={boundary: invalidation},
    )
    zone, code = build(detection)
    assert code == "passed"
    assert zone.zone_low == D(low)
    assert zone.zone_high == D(high)
    assert zone.invalidation_price == D(invalidation)


def test_positive_source_band_without_any_inward_tick_fails():
    assert build(
        event(basis_updates={"zone_low": "100.01", "zone_high": "100.24"})
    ) == (None, "no_executable_zone")


@pytest.mark.parametrize("direction", ["long", "short"])
def test_fifty_digit_source_basis_is_not_pre_rounded_before_tick_alignment(direction):
    low = "99." + "0" * 47 + "1"
    high = "101." + "0" * 46 + "9"
    detection = event(
        direction=direction, basis_updates={"zone_low": low, "zone_high": high}
    )
    zone, code = build(detection)
    assert code == "passed"
    assert zone.zone_low == D("99.25")
    assert zone.zone_high == D("101")
    assert zone.zone_low >= D(low)
    assert zone.zone_high <= D(high)


@pytest.mark.parametrize(
    "direction,raw,anchor,boundary,low,high",
    [
        (
            "long",
            "99.249999999999999999999999999999999999999999999999",
            "99.25",
            "zone_low",
            "99.5",
            "101",
        ),
        (
            "short",
            "101.00000000000000000000000000000000000000000000001",
            "101",
            "zone_high",
            "99.25",
            "100.75",
        ),
    ],
)
def test_verified_toward_close_encoding_can_exclude_its_rounded_anchor(
    direction, raw, anchor, boundary, low, high
):
    detection = event(
        direction=direction,
        invalidation_price=D(anchor),
        basis_updates={
            boundary: raw,
            "invalidation_unrounded": raw,
            "invalidation_rounding": "decimal20_toward_setup_close",
        },
    )
    zone, code = build(detection)
    assert code == "passed"
    assert zone.zone_low == D(low)
    assert zone.zone_high == D(high)
    assert zone.invalidation_price == D(anchor)


@pytest.mark.parametrize(
    "direction,raw,anchor,boundary",
    [
        ("long", "99.249999999999999999999", "99.25", "zone_low"),
        ("short", "101.000000000000000000001", "101", "zone_high"),
    ],
)
@pytest.mark.parametrize("missing", ["invalidation_unrounded", "invalidation_rounding"])
def test_rounding_metadata_requires_both_fields(
    direction, raw, anchor, boundary, missing
):
    detection = event(
        direction=direction,
        invalidation_price=D(anchor),
        basis_updates={
            boundary: raw,
            "invalidation_unrounded": raw,
            "invalidation_rounding": "decimal20_toward_setup_close",
        },
        basis_remove=(missing,),
    )
    assert build(detection) == (None, "source_basis_invalid")


@pytest.mark.parametrize(
    "basis_update,anchor",
    [
        ({"invalidation_rounding": "nearest_tick"}, "99.25"),
        ({"invalidation_unrounded": "99.25"}, "99.25"),
        ({"invalidation_unrounded": "NaN"}, "99.25"),
        ({"invalidation_unrounded": "100.000000000000000000001"}, "99.25"),
        ({"invalidation_unrounded": "99.260000000000000000001"}, "99.25"),
        ({}, "99.24999999999999999999"),
        ({}, "99.25000000000000000001"),
    ],
)
def test_unknown_wrong_side_or_inexact_rounding_metadata_is_rejected(
    basis_update, anchor
):
    raw = "99.249999999999999999999"
    basis = {
        "zone_low": raw,
        "invalidation_unrounded": raw,
        "invalidation_rounding": "decimal20_toward_setup_close",
    } | basis_update
    assert build(event(invalidation_price=D(anchor), basis_updates=basis)) == (
        None,
        "source_basis_invalid",
    )


def test_partial_rounding_metadata_is_not_ignored_even_without_boundary_intrusion():
    assert build(event(basis_updates={"invalidation_rounding": "unknown"})) == (
        None,
        "source_basis_invalid",
    )


@pytest.mark.parametrize(
    "value",
    [
        "1e101",
        "1e-101",
        "1e20",
        "1" + "0" * 80 + "e-78",
        "1" * 129,
        "1_00",
        "0x64",
        "１００",
        "1 00",
        "+100",
    ],
)
def test_source_decimal_text_has_a_bounded_exact_syntax(value):
    assert build(event(basis_updates={"setup_close": value})) == (
        None,
        "source_basis_invalid",
    )


def test_hostile_decimal_context_does_not_change_zone_drift_or_fingerprint():
    detection = event()
    quote = executable_quote()
    expected_zone = build(detection)
    expected_result = evaluate(quote=quote)
    expected_hash = quote_fingerprint(quote)
    context = Context(prec=2, rounding=ROUND_DOWN)
    context.traps[Inexact] = True
    context.traps[Rounded] = True
    with localcontext(context):
        assert build(detection) == expected_zone
        assert evaluate(quote=quote) == expected_result
        assert quote_fingerprint(quote) == expected_hash


def test_failed_and_passed_results_are_immutable_and_json_roundtrip():
    for result in (evaluate(), evaluate(quote=None)):
        assert LocationResult.model_validate_json(result.model_dump_json()) == result
        assert result.execution_authority is False
        with pytest.raises(ValidationError):
            result.passed = not result.passed


@pytest.mark.parametrize("authority", [True, 0, 1, "false", None])
def test_location_result_cannot_grant_or_coerce_execution_authority(authority):
    fields = evaluate().model_dump(round_trip=True)
    fields["execution_authority"] = authority
    with pytest.raises(ValidationError):
        LocationResult.model_validate(fields, strict=True)


def test_equivalent_evaluation_time_offset_preserves_the_result():
    now = T0 + timedelta(seconds=1)
    assert evaluate(current_time=now) == evaluate(
        current_time=now.astimezone(timezone(timedelta(hours=8)))
    )


def test_evaluating_does_not_rewrite_the_zone_quote_or_candidate():
    zone = entry_zone()
    quote = executable_quote()
    candidate = D("100.00")
    before = (zone.model_dump_json(), quote.model_dump_json(), candidate.as_tuple())
    assert_result(evaluate(zone=zone, quote=quote, candidate_entry=candidate), "passed")
    assert (
        zone.model_dump_json(),
        quote.model_dump_json(),
        candidate.as_tuple(),
    ) == before


@pytest.mark.parametrize(
    "direction,field,value",
    [
        ("long", "bid", D("95")),
        ("long", "bid", D("94.99")),
        ("short", "ask", D("105")),
        ("short", "ask", D("105.01")),
    ],
)
def test_either_book_side_touching_or_crossing_invalidation_fails(
    direction, field, value
):
    assert_result(
        evaluate(direction=direction, quote=executable_quote(**{field: value})),
        "invalidation_crossed",
    )


@pytest.mark.parametrize(
    "direction,field,value", [("long", "bid", D("98")), ("short", "ask", D("102"))]
)
def test_nonexecutable_book_side_can_be_outside_zone_if_invalidation_is_intact(
    direction, field, value
):
    assert_result(
        evaluate(direction=direction, quote=executable_quote(**{field: value})),
        "passed",
    )


@pytest.mark.parametrize("field", ["instrument_id", "direction", "source_sha256"])
def test_each_typed_zone_provenance_field_is_required_to_evaluate(field):
    zone = entry_zone(**{field: None})
    assert_result(evaluate(zone=zone), "zone_provenance_missing")


def test_a_zone_from_another_instrument_cannot_pass_with_matching_report_and_quote():
    assert_result(
        evaluate(zone=entry_zone(instrument_id="ETH-USDT-SWAP")), "identity_mismatch"
    )


@pytest.mark.parametrize(
    "direction,other_direction", [("long", "short"), ("short", "long")]
)
def test_a_zone_from_the_opposite_direction_is_not_relabelled(
    direction, other_direction
):
    zone = entry_zone(direction=other_direction)
    assert_result(evaluate(direction=direction, zone=zone), "identity_mismatch")


@pytest.mark.parametrize("digest", ["bad", "A" * 64, "a" * 63, 123, True])
def test_copied_zone_with_malformed_source_digest_is_invalid(digest):
    zone = entry_zone().model_copy(update={"source_sha256": digest})
    assert_result(evaluate(zone=zone), "entry_zone_invalid")
