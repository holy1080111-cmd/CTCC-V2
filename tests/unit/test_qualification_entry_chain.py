"""Synthetic OHLC → event → timing → location integration, never Demo fills."""

from datetime import timedelta
from decimal import Decimal

import pytest

from app.trade_qualification.events import STRATEGIES, extract_trigger
from app.trade_qualification.location import (
    ExecutableQuote,
    build_entry_zone,
    evaluate_location,
)
from app.trade_qualification.timing import (
    TIMING_POLICIES,
    evaluate_timing,
    event_identity,
)
from tests.unit.test_qualification_events import OBSERVED, _snapshot, source

D = Decimal


def _extract(market, analysis, strategy, direction, report_id="source_entry_chain"):
    return extract_trigger(
        market,
        analysis,
        report_id=report_id,
        strategy=strategy,
        direction=direction,
        observed_at=OBSERVED,
        trigger_ttl_seconds=TIMING_POLICIES[strategy].trigger_ttl_seconds,
    )


def _zone(event):
    return build_entry_zone(
        event,
        tick_size=D("0.01"),
        max_allowed_drift_bps=D("30"),
        expires_at=event.trigger.expires_at,
    )


def _source_in_zone(strategy, direction):
    market, analysis = source(strategy, direction)
    initial = _extract(market, analysis, strategy, direction)
    zone, code = _zone(initial)
    assert code == "passed", (strategy, direction, code)
    desired = (
        zone.zone_high - D("0.01") if direction == "long" else zone.zone_low + D("0.01")
    )
    shift = desired - market.candles["5m"][-1].close
    # Construct an independent OHLC fixture with the same indicator transition.
    # No event, trigger, invalidation or source-derived zone is hand-overridden.
    frames = dict(market.candles)
    frames["5m"] = [
        row.model_copy(
            update={
                name: getattr(row, name) + shift
                for name in ("open", "high", "low", "close")
            }
        )
        for row in frames["5m"]
    ]
    market, analysis = _snapshot(frames)
    event = _extract(market, analysis, strategy, direction)
    assert not event.fail_codes, (strategy, direction, event.fail_codes)
    zone, code = _zone(event)
    assert code == "passed", code
    return market, analysis, event, zone


def _quote(event, price, at=OBSERVED):
    return ExecutableQuote(
        report_id=event.report_id,
        instrument_id=event.instrument_id,
        source="rest",
        bid=price - D("0.01") if event.direction == "long" else price,
        ask=price if event.direction == "long" else price + D("0.01"),
        mark_price=price,
        bid_size=D(1),
        ask_size=D(1),
        funding_rate=D(0),
        quote_time=at,
        mark_time=at,
        funding_time=at,
        received_at=at,
        request_started_at=at,
    )


def _location(event, zone, quote, at=OBSERVED):
    return evaluate_location(
        report_id=event.report_id,
        instrument_id=event.instrument_id,
        direction=event.direction,
        zone=zone,
        candidate_entry=event.trigger.trigger_price,
        quote=quote,
        current_time=at,
    )


@pytest.mark.parametrize("strategy", sorted(STRATEGIES))
@pytest.mark.parametrize("direction", ["long", "short"])
def test_source_event_flows_into_timing_and_real_tick_zone(strategy, direction):
    _, _, event, zone = _source_in_zone(strategy, direction)
    price = event.trigger.trigger_price
    timing = evaluate_timing(
        event,
        current_time=OBSERVED,
        candidate_created_at=OBSERVED,
        candidate_expires_at=event.trigger.expires_at,
        reference_price=price,
    )
    location = _location(event, zone, _quote(event, price))
    assert timing.timing_valid and location.passed
    assert event.source_sha256 in zone.zone_source
    assert zone.zone_low % D("0.01") == zone.zone_high % D("0.01") == 0
    assert location.execution_authority is False


@pytest.mark.parametrize("strategy", sorted(STRATEGIES))
@pytest.mark.parametrize("direction", ["long", "short"])
def test_fresh_same_event_cannot_renew_consumed_trigger_with_new_report(
    strategy, direction
):
    market, analysis, event, _ = _source_in_zone(strategy, direction)
    renamed = _extract(market, analysis, strategy, direction, "renamed_report")
    assert event_identity(event) == event_identity(renamed)
    timing = evaluate_timing(
        renamed,
        current_time=OBSERVED,
        candidate_created_at=OBSERVED,
        candidate_expires_at=OBSERVED + timedelta(minutes=20),
        reference_price=renamed.trigger.trigger_price,
        consumed_event_keys=frozenset({event_identity(event)}),
    )
    assert timing.action == "CANCEL" and timing.code == "stale_candidate"


@pytest.mark.parametrize("strategy", sorted(STRATEGIES))
@pytest.mark.parametrize("direction", ["long", "short"])
def test_price_leaving_source_zone_fails_without_repricing_candidate(
    strategy, direction
):
    _, _, event, zone = _source_in_zone(strategy, direction)
    original = event.model_dump_json(round_trip=True)
    outside = (
        zone.zone_high + D("0.01") if direction == "long" else zone.zone_low - D("0.01")
    )
    result = _location(event, zone, _quote(event, outside))
    assert not result.passed and not result.execution_authority
    assert result.code in {"reference_outside_entry_zone", "entry_zone_missed"}
    assert event.model_dump_json(round_trip=True) == original


@pytest.mark.parametrize("strategy", sorted(STRATEGIES))
@pytest.mark.parametrize("direction", ["long", "short"])
def test_trigger_deadline_cannot_be_extended_by_fresh_quote_or_legacy_20_minutes(
    strategy, direction
):
    _, _, event, zone = _source_in_zone(strategy, direction)
    now = event.trigger.expires_at
    price = event.trigger.trigger_price
    timing = evaluate_timing(
        event,
        current_time=now,
        candidate_created_at=OBSERVED,
        candidate_expires_at=OBSERVED + timedelta(minutes=20),
        reference_price=price,
    )
    location = _location(event, zone, _quote(event, price, now), now)
    assert timing.action == "CANCEL" and timing.code == "trigger_expired"
    assert not location.passed and location.code == "entry_zone_expired"
