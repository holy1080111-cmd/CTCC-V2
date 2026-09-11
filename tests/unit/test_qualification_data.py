"""Synthetic G1 source contracts only; no network, authenticated WS or trades."""

import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta, timezone
from decimal import Context, Decimal, Inexact, localcontext

import pytest
from pydantic import ValidationError

from app.domain.analysis import MultiTimeframeAnalysis
from app.domain.market import Candle, MarketSnapshot, OrderBook, OrderBookLevel, Ticker
from app.market.quality.candles import BAR_SECONDS
from app.trade_qualification import data as module
from app.trade_qualification.data import (
    DataQualificationPolicy,
    DataQualificationResult,
    WSReferenceObservation,
    evaluate_data,
)
from app.trade_qualification.events import _copy_source
from tests.unit.test_qualification_quote_collector import NOW, REPORT, capture

D = Decimal
AT = NOW + timedelta(milliseconds=10)
INSTRUMENT = "BTC-USDT-SWAP"
POLICY = DataQualificationPolicy(
    policy_id="synthetic-g1-policy",
    analysis_version="synthetic-analysis-v1",
    minimum_confirmed_bars=200,
    maximum_snapshot_age_seconds=5,
    maximum_quote_age_seconds=10,
    maximum_reference_age_seconds=10,
    maximum_candle_age_intervals=2,
    maximum_reference_conflict_bps=D(10),
    maximum_mark_dislocation_bps=D(10),
    maximum_spread_bps=D(5),
    maximum_absolute_funding_bps=D(10),
)


@pytest.fixture(scope="module")
def collected():
    return asyncio.run(capture())[0]


@pytest.fixture
def market():
    frames = {}
    for tf, seconds in BAR_SECONDS.items():
        end = datetime.fromtimestamp(int(NOW.timestamp()) // seconds * seconds, UTC)
        rows = []
        for i in range(240):
            close = D(100) + D(i % 11 - 5) / D(100)
            rows.append(
                Candle(
                    timestamp=end - timedelta(seconds=seconds * (240 - i)),
                    open=close,
                    high=close + D("0.1"),
                    low=close - D("0.1"),
                    close=close,
                    volume_contracts=D(100 + i % 7),
                    volume_currency=D(100 + i % 7),
                    volume_quote=D(10000 + i % 7),
                    confirmed=True,
                )
            )
        frames[tf] = rows
    return MarketSnapshot(
        symbol="BTC/USDT:USDT",
        instrument_id=INSTRUMENT,
        ticker=Ticker(
            instrument_id=INSTRUMENT,
            last=D("100.005"),
            bid=D(100),
            ask=D("100.01"),
            bid_size=D(2),
            ask_size=D(3),
            open_24h=D(100),
            high_24h=D(101),
            low_24h=D(99),
            volume_24h=D(100),
            volume_quote_24h=D(10000),
            timestamp=NOW - timedelta(seconds=1),
        ),
        order_book=OrderBook(
            instrument_id=INSTRUMENT,
            bids=[OrderBookLevel(price=D(100), size=D(2))],
            asks=[OrderBookLevel(price=D("100.01"), size=D(3))],
            timestamp=NOW - timedelta(seconds=1),
        ),
        mark_price=D(100),
        funding_rate=D(0),
        next_funding_time=NOW + timedelta(hours=8),
        open_interest_contracts=D(100),
        open_interest_currency=D(100),
        candles=frames,
        quality={},
        received_at=NOW,
    )


def reference(**updates):
    args = {
        "report_id": REPORT,
        "instrument_id": INSTRUMENT,
        "bid": D(100),
        "ask": D("100.01"),
        "source_time": NOW - timedelta(seconds=1),
        "received_at": NOW,
    }
    args.update(updates)
    return WSReferenceObservation(**args)


def check(market, collected, **updates):
    args = {
        "report_id": REPORT,
        "instrument_id": INSTRUMENT,
        "quote": collected,
        "reference": reference(),
        "policy": POLICY,
        "evaluated_at": AT,
    }
    args.update(updates)
    return evaluate_data(market, **args)


def test_rebuilds_quality_analysis_and_source_used_by_existing_event_engine(
    market, collected
):
    before = market.model_dump_json(round_trip=True)
    result = check(market, collected)
    assert result.passed, result.gate
    assert result.gate.gate.value == "G1"
    assert result.gate.measured_values["quality_recomputed"]
    assert result.gate.measured_values["analysis_recomputed"]
    assert not result.execution_authority and not result.source_authenticity_verified
    assert result.quote_bundle_sha256 == collected.bundle_sha256
    assert (
        hashlib.sha256(result.source_json.encode()).hexdigest() == result.source_sha256
    )
    source = json.loads(result.source_json)
    rebuilt = MarketSnapshot.model_validate_json(json.dumps(source["market"]))
    analysis = MultiTimeframeAnalysis.model_validate_json(
        json.dumps(source["analysis"])
    )
    assert all(item.ok for item in rebuilt.quality.values())
    assert analysis.generated_at == AT and analysis.version == POLICY.analysis_version
    assert (
        analysis.timeframe_analyses["4H"].last_closed_at
        != analysis.timeframe_analyses["5m"].last_closed_at
    )
    assert _copy_source(rebuilt, analysis, AT)[3] == result.source_sha256
    assert market.model_dump_json(round_trip=True) == before
    restored = DataQualificationResult.model_validate_json(
        result.model_dump_json(round_trip=True)
    )
    assert restored == result and restored.evaluation_sha256 == result.evaluation_sha256


def test_does_not_consume_or_trust_supplied_quality(market, collected):
    class Unreadable:
        def __iter__(self):
            pytest.fail("caller quality must not be traversed")

    assert check(market.model_copy(update={"quality": Unreadable()}), collected).passed


@pytest.mark.parametrize("tf", tuple(BAR_SECONDS))
def test_missing_timeframe_rejected(market, collected, tf):
    market.candles.pop(tf)
    assert check(market, collected).gate.code == "missing_timeframe"


@pytest.mark.parametrize("count", [0, 1, 30, 199])
def test_insufficient_history_does_not_fake_ema200(market, collected, count):
    market.candles["5m"] = market.candles["5m"][-count:] if count else []
    assert check(market, collected).gate.code == "indicator_invalid"


@pytest.mark.parametrize(
    "defect",
    [
        "duplicate",
        "reverse",
        "gap",
        "microsecond",
        "negative",
        "ohlc",
        "unconfirmed",
        "future",
    ],
)
def test_false_quality_cannot_repair_candle_defects(market, collected, defect):
    rows = market.candles["5m"]
    if defect == "duplicate":
        rows[-1] = rows[-2]
    elif defect == "reverse":
        rows.reverse()
    elif defect == "gap":
        rows.pop(-2)
    else:
        updates = {
            "microsecond": {
                "timestamp": rows[-1].timestamp + timedelta(microseconds=1)
            },
            "negative": {"volume_contracts": D(-1)},
            "ohlc": {"high": rows[-1].low},
            "unconfirmed": {"confirmed": False},
            "future": {"timestamp": rows[-1].timestamp + timedelta(seconds=300)},
        }[defect]
        rows[-1] = rows[-1].model_copy(update=updates)
    result = check(market, collected)
    assert not result.passed and result.gate.code in {
        "ohlc_quality_invalid",
        "future_market_data",
    }


@pytest.mark.parametrize("target", ["market", "ticker", "book", "candle", "level"])
def test_hidden_fields_cannot_disappear_in_serialization(market, collected, target):
    if target == "market":
        market = market.model_copy(update={"hidden": True})
    elif target == "ticker":
        market.ticker = market.ticker.model_copy(update={"hidden": True})
    elif target == "book":
        market.order_book = market.order_book.model_copy(update={"hidden": True})
    elif target == "candle":
        market.candles["5m"][-1] = market.candles["5m"][-1].model_copy(
            update={"hidden": True}
        )
    else:
        market.order_book.bids[0] = market.order_book.bids[0].model_copy(
            update={"hidden": True}
        )
    assert check(market, collected).gate.code == "market_source_invalid"


def test_generator_rejected_before_iteration(market, collected):
    def rows():
        pytest.fail("generator must not be consumed")
        yield

    market.candles["5m"] = rows()
    assert check(market, collected).gate.code == "market_source_invalid"


@pytest.mark.parametrize(
    "value", [D("NaN"), D("Infinity"), D("1e999999"), D("1e-999999"), 100.0]
)
def test_bad_raw_decimal_rejected(market, collected, value):
    market.candles["5m"][-1] = market.candles["5m"][-1].model_copy(
        update={"close": value}
    )
    assert check(market, collected).gate.code == "market_source_invalid"


@pytest.mark.parametrize("target", ["ticker", "order_book"])
def test_independent_market_source_clock_cannot_be_refreshed_by_receipt(
    market, collected, target
):
    setattr(
        market,
        target,
        getattr(market, target).model_copy(
            update={"timestamp": NOW - timedelta(seconds=11)}
        ),
    )
    assert check(market, collected).gate.code == "stale_market_data"


def test_future_component_before_evaluation_but_after_capture_is_rejected(
    market, collected
):
    market.ticker.timestamp = NOW + timedelta(microseconds=1)
    assert check(market, collected).gate.code == "future_market_data"


def test_quote_revalidated_at_gate_time_not_collection_time(market, collected):
    market.received_at = NOW + timedelta(seconds=10)
    market.ticker.timestamp = market.order_book.timestamp = market.received_at
    now = market.received_at + timedelta(microseconds=1)
    assert check(market, collected, evaluated_at=now).gate.code == "stale_market_data"


@pytest.mark.parametrize(
    "updates",
    [{"bundle_sha256": "0" * 64}, {"execution_authority": True}, {"provenance": ()}],
)
def test_dirty_public_quote_rejected(market, collected, updates):
    assert (
        check(market, collected.model_copy(update=updates)).gate.code
        == "quote_provenance_invalid"
    )


def test_missing_independent_ws_is_not_assumed_agreement(market, collected):
    assert (
        check(market, collected, reference=None).gate.code == "reference_source_missing"
    )


@pytest.mark.parametrize(
    "updates,code",
    [
        ({"instrument_id": "ETH-USDT-SWAP"}, "identity_mismatch"),
        ({"source_time": NOW - timedelta(seconds=11)}, "stale_market_data"),
        ({"received_at": AT + timedelta(microseconds=1)}, "stale_market_data"),
        ({"bid": D(101)}, "reference_source_invalid"),
        ({"source": "rest"}, "reference_source_invalid"),
        ({"channel": "mark-price"}, "reference_source_invalid"),
        ({"hidden": True}, "reference_source_invalid"),
    ],
)
def test_invalid_reference_contract(market, collected, updates, code):
    assert (
        check(
            market, collected, reference=reference().model_copy(update=updates)
        ).gate.code
        == code
    )


@pytest.mark.parametrize("side", ["bid", "ask"])
def test_reference_conflict_compares_both_sides_at_exact_boundary(
    market, collected, side
):
    # Explicit rational denominator: REST midpoint 100.005; 10 bps = .100005.
    delta = D("0.100005")
    updates = {side: D(100) - delta if side == "bid" else D("100.01") + delta}
    policy = POLICY.model_copy(update={"maximum_spread_bps": D(100)})
    assert check(
        market, collected, reference=reference(**updates), policy=policy
    ).passed
    updates[side] += (
        D("-0.00000000000000000001") if side == "bid" else D("0.00000000000000000001")
    )
    assert (
        check(
            market, collected, reference=reference(**updates), policy=policy
        ).gate.code
        == "reference_price_conflict"
    )


def test_same_midpoint_does_not_hide_wide_reference(market, collected):
    ref = reference(bid=D(99), ask=D("101.01"))
    assert (
        check(market, collected, reference=ref).gate.code == "reference_price_conflict"
    )


@pytest.mark.parametrize(
    "field", ["ema200", "atr14", "causal_state", "return_interval"]
)
def test_required_recomputed_indicator_missing_rejected(
    market, collected, monkeypatch, field
):
    original = module.analyze_snapshot_at

    def corrupt(*args, **kwargs):
        result = original(*args, **kwargs)
        setattr(result.timeframe_analyses["1H"].indicators, field, None)
        return result

    monkeypatch.setattr(module, "analyze_snapshot_at", corrupt)
    assert check(market, collected).gate.code == "indicator_invalid"


def test_nested_nonfinite_analysis_rejected(market, collected, monkeypatch):
    original = module.analyze_snapshot_at

    def corrupt(*args, **kwargs):
        result = original(*args, **kwargs)
        result.timeframe_analyses[
            "1H"
        ].indicators.causal_trend.log_velocity_per_bar = D("NaN")
        return result

    monkeypatch.setattr(module, "analyze_snapshot_at", corrupt)
    assert check(market, collected).gate.code == "indicator_invalid"


def test_result_is_pinned_to_clock_policy_raw_sources_and_reference(market, collected):
    first = check(market, collected)
    assert first.passed
    changes = [
        check(market, collected, evaluated_at=AT + timedelta(microseconds=1)),
        check(
            market,
            collected,
            policy=POLICY.model_copy(update={"policy_id": "another-policy"}),
        ),
        check(market, collected, reference=reference(bid=D("99.999"))),
    ]
    market.candles["5m"][0].volume_quote += 1
    changes.append(check(market, collected))
    assert all(
        result.passed and result.evaluation_sha256 != first.evaluation_sha256
        for result in changes
    )


def test_hostile_decimal_context_and_equivalent_timezone_do_not_change_result(
    market, collected
):
    expected = check(market, collected)
    context = Context(prec=6)
    context.traps[Inexact] = True
    with localcontext(context):
        actual = check(
            market, collected, evaluated_at=AT.astimezone(timezone(timedelta(hours=8)))
        )
    assert actual == expected


@pytest.mark.parametrize(
    "field", ["execution_authority", "source_authenticity_verified"]
)
def test_consistency_record_never_grants_authority(market, collected, field):
    result = check(market, collected)
    raw = result.model_dump(mode="python", round_trip=True)
    raw[field] = True
    with pytest.raises(ValidationError):
        DataQualificationResult.model_validate(raw)


@pytest.mark.parametrize("price", [D("100.030000000000000000000000000001"), D("1e20")])
def test_g1_price_contract_cannot_exceed_downstream_event_engine(
    market, collected, price
):
    market.candles["5m"][-1].close = price
    assert check(market, collected).gate.code == "market_source_invalid"


def test_max_datetime_source_is_a_failure_not_an_uncaught_overflow(market, collected):
    market.candles["5m"][-1].timestamp = datetime.max.replace(tzinfo=UTC)
    result = check(market, collected)
    assert not result.passed and result.gate.code in {
        "future_market_data",
        "ohlc_quality_invalid",
    }


@pytest.mark.parametrize(
    "updates",
    [
        {"analysis_version": "x" * 65},
        {"minimum_confirmed_bars": 199},
        {"maximum_snapshot_age_seconds": 0},
        {"maximum_quote_age_seconds": 61},
        {"maximum_reference_age_seconds": True},
        {"maximum_candle_age_intervals": 4},
        {"maximum_reference_conflict_bps": D(-1)},
        {"maximum_spread_bps": 1.0},
        {"hidden": True},
    ],
)
def test_invalid_policy_is_not_mislabeled_an_indicator_failure(
    market, collected, updates
):
    assert (
        check(market, collected, policy=POLICY.model_copy(update=updates)).gate.code
        == "data_policy_invalid"
    )


def test_exact_200_confirmed_bars_support_required_indicator_contract(
    market, collected
):
    for tf in BAR_SECONDS:
        market.candles[tf] = market.candles[tf][-200:]
    assert check(market, collected).passed


def test_source_list_and_order_book_are_bounded(market, collected):
    oversized = market.model_copy(deep=True)
    oversized.candles["5m"] = [market.candles["5m"][0]] * 1025
    assert check(oversized, collected).gate.code == "market_source_invalid"
    market.order_book.bids *= 51
    assert check(market, collected).gate.code == "market_source_invalid"


def test_snapshot_age_exact_and_plus_one_microsecond(market, collected):
    market.received_at = AT - timedelta(seconds=5)
    market.ticker.timestamp = market.order_book.timestamp = market.received_at
    for tf, rows in market.candles.items():
        interval = timedelta(seconds=BAR_SECONDS[tf])
        if rows[-1].timestamp + interval > market.received_at:
            for row in rows:
                row.timestamp -= interval
    result = check(market, collected)
    assert result.passed, result.gate
    market.received_at -= timedelta(microseconds=1)
    market.ticker.timestamp = market.order_book.timestamp = market.received_at
    assert check(market, collected).gate.code == "stale_market_data"


def test_ws_source_age_exact_and_plus_one_microsecond(market, collected):
    ref = reference(source_time=AT - timedelta(seconds=10))
    assert check(market, collected, reference=ref).passed
    ref = ref.model_copy(
        update={"source_time": ref.source_time - timedelta(microseconds=1)}
    )
    assert check(market, collected, reference=ref).gate.code == "stale_market_data"


def test_old_merged_funding_and_settlement_never_replace_collected_observations(
    market, collected
):
    original = check(market, collected)
    market.mark_price = D(999)
    market.funding_rate = D(999)
    market.next_funding_time += timedelta(days=1)
    result = check(market, collected)
    assert result.passed
    assert (
        result.gate.measured_values["absolute_funding_bps"]
        == original.gate.measured_values["absolute_funding_bps"]
    )
    assert (
        result.gate.measured_values["mark_dislocation_bps"]
        == original.gate.measured_values["mark_dislocation_bps"]
    )
    assert result.source_sha256 != original.source_sha256


@pytest.mark.parametrize("field", ["source_json", "source_sha256", "policy_sha256"])
def test_receipt_digest_tampering_is_rejected(market, collected, field):
    result = check(market, collected)
    raw = result.model_dump(mode="python", round_trip=True)
    raw[field] = "{}" if field == "source_json" else "0" * 64
    with pytest.raises(ValidationError):
        DataQualificationResult.model_validate(raw)


@pytest.mark.parametrize("target", ["result", "gate", "policy"])
def test_fingerprint_rejects_hidden_fields_at_every_record_layer(
    market, collected, target
):
    result = check(market, collected)
    if target == "result":
        changed = result.model_copy(update={"hidden": True})
    else:
        changed = result.model_copy(
            update={target: getattr(result, target).model_copy(update={"hidden": True})}
        )
    with pytest.raises(ValueError):
        _ = changed.evaluation_sha256


def test_self_signed_result_is_not_a_substitute_for_replaying_inputs(market, collected):
    args = {
        "report_id": REPORT,
        "instrument_id": INSTRUMENT,
        "quote": collected,
        "reference": reference(),
        "policy": POLICY,
        "evaluated_at": AT,
    }
    result = check(market, collected)
    assert module.verify_data_result(result, market, **args) == result
    raw = result.model_dump(mode="python", round_trip=True)
    raw.update(source_json="{}", source_sha256=hashlib.sha256(b"{}").hexdigest())
    forged = DataQualificationResult.model_validate(raw)
    with pytest.raises(ValueError, match="data_replay_mismatch"):
        module.verify_data_result(forged, market, **args)
    market.candles["5m"][0].volume_quote += 1
    with pytest.raises(ValueError, match="data_replay_mismatch"):
        module.verify_data_result(result, market, **args)


@pytest.mark.parametrize("target", ["policy", "ws", "public_quote"])
def test_trailing_zero_decimal_representation_is_bounded_before_serialization(
    market, collected, target
):
    giant = D("100." + "0" * 10000)
    if target == "policy":
        result = check(
            market,
            collected,
            policy=POLICY.model_copy(update={"maximum_spread_bps": giant}),
        )
        assert result.gate.code == "data_policy_invalid"
    elif target == "ws":
        result = check(
            market, collected, reference=reference().model_copy(update={"bid": giant})
        )
        assert result.gate.code == "reference_source_invalid"
    else:
        changed = collected.model_copy(
            update={"quote": collected.quote.model_copy(update={"bid": giant})}
        )
        assert check(market, changed).gate.code == "quote_provenance_invalid"
