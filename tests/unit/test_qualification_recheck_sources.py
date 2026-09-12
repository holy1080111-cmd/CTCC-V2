"""Real collector/G1 proofs for fictional post-barrier source fixtures only.

These tests do not claim a G12 publication, authenticated OHLC/WS/account
capture, continuous intrabar coverage, R2/R3 success or execution permission.
"""

import hashlib
import json
from datetime import timedelta
from decimal import Context, Decimal, Inexact, Rounded, localcontext
from itertools import pairwise, product

import pytest

from app.domain.market import Candle
from app.market.quality.candles import BAR_SECONDS, candle_closed_at
from app.strategies.base import StrategyContext
from app.strategies.conditions import assess_conditions
from app.strategies.regime import RouteDecision, route_regime
from app.trade_qualification.engine import verify_pre_evidence
from app.trade_qualification.events import extract_trigger
from app.trade_qualification.executable_economics import evaluate_executable_economics
from app.trade_qualification.location import evaluate_location
from app.trade_qualification.portfolio import evaluate_portfolio
from app.trade_qualification.quote_collector import ENDPOINTS, validate_collected_quote
from app.trade_qualification.timing import evaluate_timing, event_identity
from tests.unit.qualification_prefix_fixtures import qualify_data, restore_source
from tests.unit.qualification_recheck_fixtures import recheck_source
from tests.unit.test_qualification_portfolio import STAMP_FIELDS

D = Decimal


@pytest.fixture(
    scope="module",
    params=list(product(["long", "short"], ["same_interval", "next_boundary"])),
)
def source(request):
    direction, scenario = request.param
    return recheck_source(direction, scenario=scenario)


@pytest.fixture(scope="module")
def data(source):
    return qualify_data(source.latest_source)


def test_original_is_a_real_eleven_gate_run_created_before_the_fixture_barrier(source):
    original = source.original_run
    assert original.pre_evidence_complete and len(original.result.gates) == 11
    assert (
        verify_pre_evidence(
            original, source.original_source.market, **source.original_inputs
        )
        == original
    )
    assert (
        original.prefix.intent.expires_at
        == original.prefix.intent.created_at + timedelta(minutes=10)
    )
    assert original.result.evaluated_at < source.barrier_completed_at
    assert (
        source.barrier_completed_at
        < source.latest_source.quote.quote.request_started_at
    )
    assert not original.execution_authority and not original.result.qualified
    assert source.original_inputs["policy"].prefix.data == source.latest_source.policy
    assert source.original_source.report_id == source.latest_source.report_id


def test_all_original_confirmed_ohlc_fields_are_retained_in_order(source):
    old, new = source.original_source.market, source.latest_source.market
    assert old is not new and old.quality == new.quality == {}
    for timeframe, old_rows in old.candles.items():
        new_rows = new.candles[timeframe]
        expected_added = int(
            source.scenario == "next_boundary" and timeframe in {"5m", "15m"}
        )
        assert len(old_rows) == 240
        assert len(new_rows) == 240 + expected_added
        assert new_rows[:240] == old_rows
        assert [row.model_dump() for row in new_rows[:240]] == [
            row.model_dump() for row in old_rows
        ]
        assert all(row.confirmed is True for row in new_rows)
        assert all(
            candle_closed_at(left, timeframe) == right.timestamp
            for left, right in pairwise(new_rows)
        )
        assert all(
            row.timestamp < candle_closed_at(row, timeframe) <= new.received_at
            for row in new_rows
        )


def test_fictional_ohlc_capture_bytes_exactly_reconstruct_the_supplied_raw_rows(source):
    assert tuple(item.timeframe for item in source.ohlc_captures) == (
        "4H",
        "1H",
        "15m",
        "5m",
    )
    for item in source.ohlc_captures:
        assert (
            source.barrier_completed_at
            < item.request_started_at
            <= item.received_at
            <= item.completed_at
            < source.latest_source.market.received_at
        )
        assert item.instrument_id == source.original_source.market.instrument_id
        assert hashlib.sha256(item.response_body).hexdigest() == item.body_sha256
        body = json.loads(item.response_body)
        assert body["schema"] == "synthetic_ohlc_capture_v1"
        assert (
            body["instrument_id"] == item.instrument_id
            and body["timeframe"] == item.timeframe
        )
        rows = [
            Candle.model_validate_json(json.dumps(row), strict=True)
            for row in body["candles"]
        ]
        assert rows == source.latest_source.market.candles[item.timeframe]
        closed = candle_closed_at(rows[-1], item.timeframe)
        assert (
            closed
            <= item.received_at
            < closed + timedelta(seconds=BAR_SECONDS[item.timeframe])
        )
        assert len(item.response_body) < 1024 * 1024


def test_mocktransport_uses_exact_three_unauthenticated_public_requests(source):
    quote = source.latest_source.quote
    assert validate_collected_quote(quote) == quote
    assert len(source.requests) == len(quote.provenance) == 3
    for request, observation, (role, endpoint) in zip(
        source.requests, quote.provenance, ENDPOINTS, strict=True
    ):
        assert request.method == "GET" and request.url.startswith(
            "https://www.okx.com" + endpoint
        )
        assert request.body == b""
        assert set(dict(request.headers)) == {
            "host",
            "accept",
            "accept-encoding",
            "user-agent",
        }
        assert dict(request.headers)["accept-encoding"] == "identity"
        assert tuple(sorted(request.parameters)) == observation.parameters
        assert observation.role == role and observation.endpoint == endpoint
        assert observation.request_started_at > source.barrier_completed_at
        assert (
            observation.request_started_at
            <= observation.received_at
            <= observation.completed_at
            <= quote.completed_at
        )
        assert observation.source_time <= observation.received_at
        assert (
            observation.body_sha256
            == hashlib.sha256(observation.response_body).hexdigest()
        )
        assert observation.body_size_bytes == len(observation.response_body)
        row = json.loads(observation.response_body)["data"][0]
        assert row["ts"] == observation.ts_raw
        assert row["instId"] == source.original_source.market.instrument_id
    assert quote.barrier_completed_at == source.barrier_completed_at
    assert len({observation.source_time for observation in quote.provenance}) == 3
    assert (
        len({observation.request_started_at for observation in quote.provenance}) == 3
    )
    assert not quote.execution_authority and not quote.source_authenticity_verified


def test_funding_observation_is_not_the_future_settlement_schedule(source):
    captured = source.latest_source.quote
    funding = captured.provenance[2]
    row = json.loads(funding.response_body)["data"][0]
    assert captured.quote.funding_time == funding.source_time
    assert funding.ts_raw == row["ts"]
    assert int(row["fundingTime"]) > int(row["ts"])
    assert int(row["nextFundingTime"]) > int(row["fundingTime"])
    assert captured.quote.funding_time <= captured.quote.received_at
    assert captured.quote.funding_rate == D(0)


def test_fictional_ws_frame_is_separate_from_rest_bytes_and_has_its_own_clocks(source):
    reference = source.latest_source.reference
    frame = json.loads(source.ws_frame_bytes)
    assert hashlib.sha256(source.ws_frame_bytes).hexdigest() == source.ws_frame_sha256
    assert frame["arg"] == {"channel": "tickers", "instId": reference.instrument_id}
    row = frame["data"][0]
    assert D(row["bidPx"]) == reference.bid and D(row["askPx"]) == reference.ask
    assert reference.source == "ws" and reference.channel == "tickers"
    assert (
        source.barrier_completed_at
        < reference.source_time
        <= reference.received_at
        <= source.latest_source.evaluated_at
    )
    assert reference.source_time != source.latest_source.quote.quote.quote_time
    assert all(
        source.ws_frame_bytes != item.response_body
        for item in source.latest_source.quote.provenance
    )


def test_both_original_and_latest_g1_rebuild_raw_sources_without_supplied_passes(
    source, data
):
    original = source.original_run.prefix.data_result
    assert original.passed and data.passed
    assert (
        source.original_source.market.quality
        == source.latest_source.market.quality
        == {}
    )
    assert original.source_sha256 != data.source_sha256
    assert original.quote_bundle_sha256 != data.quote_bundle_sha256
    assert data.quote_bundle_sha256 == source.latest_source.quote.bundle_sha256
    assert data.evaluated_at == source.latest_source.evaluated_at
    market, analysis = restore_source(data)
    assert analysis.generated_at == data.evaluated_at
    assert all(report.ok and report.issues == [] for report in market.quality.values())
    route = route_regime(analysis)
    assert route.decision == RouteDecision.ALLOW_SCORING
    assert source.original_source.strategy in route.allowed_strategies
    conditions = assess_conditions(
        StrategyContext(analysis, market, 85, D(2)), source.original_source.strategy
    )
    assert conditions.score >= 85
    assert conditions.required_failures == conditions.veto_failures == ()
    assert not data.execution_authority and not data.source_authenticity_verified


def test_closed_bar_extension_does_not_pretend_to_cover_intrabar_path(source):
    latest = source.latest_source
    for timeframe, rows in latest.market.candles.items():
        verified_through = candle_closed_at(rows[-1], timeframe)
        assert verified_through < latest.evaluated_at
        assert latest.evaluated_at - verified_through > timedelta(0)
    if source.scenario == "same_interval":
        assert latest.market.candles == source.original_source.market.candles
        assert latest.evaluated_at - candle_closed_at(
            latest.market.candles["5m"][-1], "5m"
        ) > timedelta(seconds=1)
    else:
        assert candle_closed_at(
            latest.market.candles["5m"][-1], "5m"
        ) == candle_closed_at(latest.market.candles["15m"][-1], "15m")
        assert latest.evaluated_at - candle_closed_at(
            latest.market.candles["5m"][-1], "5m"
        ) == timedelta(milliseconds=22)


def test_fresh_latest_event_never_overwrites_original_identity_prices_or_deadline(
    source, data
):
    original = source.original_run
    before = original.model_dump_json(round_trip=True)
    market, analysis = restore_source(data)
    extracted = extract_trigger(
        market,
        analysis,
        report_id=source.latest_source.report_id,
        strategy=source.latest_source.strategy,
        direction=source.latest_source.direction,
        observed_at=source.latest_source.evaluated_at,
        trigger_ttl_seconds=600,
    )
    assert (
        extracted.source_sha256
        == data.source_sha256
        != original.prefix.detection.source_sha256
    )
    assert extracted.observed_at > original.prefix.detection.observed_at
    assert extracted is not original.prefix.detection
    # In these controlled continuations the latest scan still finds the old
    # event. A different result in another fixture must not be installed here.
    assert event_identity(extracted) == source.original_event_key
    assert extracted.trigger == original.prefix.detection.trigger
    assert original.model_dump_json(round_trip=True) == before
    assert (
        original.result.candidate_entry
        == source.original_inputs["intent"].candidate_entry
    )
    if source.scenario == "next_boundary":
        reference = (
            source.latest_source.quote.quote.ask
            if source.latest_source.direction == "long"
            else source.latest_source.quote.quote.bid
        )
        assert reference != original.result.candidate_entry


def test_original_zone_and_timing_are_used_directly_without_rebuilding_them(source):
    original, latest = source.original_run, source.latest_source
    timing = evaluate_timing(
        original.prefix.detection,
        current_time=latest.evaluated_at,
        candidate_created_at=original.prefix.intent.created_at,
        candidate_expires_at=original.prefix.intent.expires_at,
        reference_price=latest.quote.quote.ask
        if latest.direction == "long"
        else latest.quote.quote.bid,
        consumed_event_keys=frozenset(),
        policy=original.prefix.timing_policy,
    )
    location = evaluate_location(
        report_id=latest.report_id,
        instrument_id=latest.market.instrument_id,
        direction=latest.direction,
        zone=original.result.entry_zone,
        candidate_entry=original.result.candidate_entry,
        quote=latest.quote.quote,
        current_time=latest.evaluated_at,
        max_quote_age_seconds=latest.policy.maximum_quote_age_seconds,
    )
    assert timing.timing_valid and location.passed
    assert timing.event_key == source.original_event_key
    assert (
        timing.latest_valid_entry_time == original.prefix.timing.latest_valid_entry_time
    )
    assert not location.execution_authority


def test_fictional_account_refresh_preserves_original_size_leverage_and_loss_windows(
    source,
):
    old, new = source.original_inputs["risk_inputs"], source.current_risk_inputs
    assert new.requested_contracts == old.requested_contracts == D(10)
    assert new.requested_leverage == old.requested_leverage == 10
    for field in (
        "account_id",
        "settlement_currency",
        "equity",
        "available_margin",
        "peak_equity",
        "peak_observed_at",
        "peak_window_started_at",
        "history_start",
        "loss_streak_at_history_start",
        "positions",
        "pending_reservations",
        "loss_history",
    ):
        assert getattr(new.account, field) == getattr(old.account, field)
    stamps = [getattr(new.account, name) for name in STAMP_FIELDS] + [
        new.authority.stamp
    ]
    assert all(stamp.environment == "demo" and stamp.complete for stamp in stamps)
    assert all(
        source.barrier_completed_at
        < stamp.observed_at
        <= stamp.received_at
        <= source.latest_source.evaluated_at
        for stamp in stamps
    )
    assert new.account.history_end == new.account.history_stamp.observed_at
    assert new.account.history_end > old.account.history_end
    assert (
        new.account.peak_window_started_at
        == source.original_run.policy.portfolio.drawdown_window_started_at
    )
    for field in STAMP_FIELDS:
        assert (
            getattr(new.account, field).source_sha256
            != getattr(old.account, field).source_sha256
        )
    assert new.authority.stamp.source_sha256 != old.authority.stamp.source_sha256
    assert new.instrument.source_sha256 != old.instrument.source_sha256
    assert not (
        new.authority.live_trading
        or new.authority.live_order_writes
        or new.authority.live_auto_execution
    )


def test_real_two_reference_costs_and_current_fictional_risk_keep_original_bracket(
    source,
):
    original, latest, risk = (
        source.original_run,
        source.latest_source,
        source.current_risk_inputs,
    )
    economics = evaluate_executable_economics(
        report_id=latest.report_id,
        instrument_id=latest.market.instrument_id,
        direction=latest.direction,
        candidate_entry=original.result.candidate_entry,
        stop_loss=original.result.stop_loss,
        take_profit=original.result.take_profit,
        quote=latest.quote.quote,
        policy=original.policy.economics,
        current_time=latest.evaluated_at,
    )
    assert economics.passed and not economics.execution_authority
    for scenario in (economics.candidate_result, economics.execution_result):
        result = evaluate_portfolio(
            report_id=latest.report_id,
            instrument_id=latest.market.instrument_id,
            direction=latest.direction,
            candidate_entry=scenario.candidate_entry,
            stop_loss=scenario.stop_loss,
            round_trip_cost_per_base=scenario.cost_per_base,
            requested_contracts=risk.requested_contracts,
            requested_leverage=risk.requested_leverage,
            instrument=risk.instrument,
            account=risk.account,
            authority=risk.authority,
            policy=original.policy.portfolio,
            current_time=latest.evaluated_at,
        )
        assert result.passed and result.causes == ()
        assert not result.execution_authority
        assert (
            scenario.stop_loss == original.result.stop_loss
            and scenario.take_profit == original.result.take_profit
        )
    assert economics.original_candidate_entry == original.result.candidate_entry


@pytest.mark.parametrize("direction", ["long", "short"])
def test_five_minute_original_lifetime_naturally_expires_without_refreshing_it(
    direction,
):
    source = recheck_source(
        direction, scenario="next_boundary", original_lifetime_minutes=5
    )
    pre, new = source.original_run, source.latest_source
    assert pre.pre_evidence_complete and qualify_data(new).passed
    assert pre.prefix.intent.expires_at == pre.prefix.intent.created_at + timedelta(
        minutes=5
    )
    assert new.evaluated_at >= pre.prefix.intent.expires_at
    result = evaluate_timing(
        pre.prefix.detection,
        current_time=new.evaluated_at,
        candidate_created_at=pre.prefix.intent.created_at,
        candidate_expires_at=pre.prefix.intent.expires_at,
        reference_price=new.quote.quote.ask
        if direction == "long"
        else new.quote.quote.bid,
        policy=pre.prefix.timing_policy,
    )
    assert not result.timing_valid
    assert result.event_key == source.original_event_key


def test_fixture_construction_and_g1_remain_deterministic_in_hostile_decimal_context():
    expected = recheck_source("short", scenario="next_boundary")
    expected_data = qualify_data(expected.latest_source)
    with localcontext(Context(prec=6, traps=[Inexact, Rounded])):
        actual = recheck_source("short", scenario="next_boundary")
        actual_data = qualify_data(actual.latest_source)
    assert actual == expected
    assert actual_data == expected_data


@pytest.mark.parametrize(
    "kwargs",
    [
        {"scenario": "unknown"},
        {"scenario": None},
        {"scenario": True},
        {"original_lifetime_minutes": 5.0},
        {"original_lifetime_minutes": True},
        {"original_lifetime_minutes": 11},
    ],
)
def test_fixture_rejects_unknown_scenario_or_unreviewed_lifetime(kwargs):
    with pytest.raises(ValueError):
        recheck_source(**kwargs)
