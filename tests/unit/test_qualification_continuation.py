"""Original-event survival from synthetic raw OHLC, not a complete recheck."""

import ast
import inspect
import json
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from decimal import Context, Decimal, Inexact, Rounded, localcontext

import pytest
from pydantic import BaseModel, ValidationError

from app.domain.analysis import MultiTimeframeAnalysis
from app.domain.market import Candle, MarketSnapshot
from app.market.quality.candles import BAR_SECONDS, candle_closed_at
from app.trade_qualification import continuation
from app.trade_qualification.continuation import (
    ContinuationResult,
    evaluate_continuation,
    verify_continuation,
)
from tests.unit.qualification_recheck_fixtures import recheck_source

D = Decimal
FRAMES = ("4H", "1H", "15m", "5m")


@pytest.fixture(scope="module", params=["long", "short"])
def source(request):
    return recheck_source(request.param, scenario="next_boundary")


def _parts(source):
    raw = json.loads(source.original_run.prefix.data_result.source_json)
    return (
        MarketSnapshot.model_validate_json(json.dumps(raw["market"]), strict=True),
        MultiTimeframeAnalysis.model_validate_json(
            json.dumps(raw["analysis"]), strict=True
        ),
        source.latest_source.market.model_copy(deep=True),
    )


def _evaluate(source, *, parts=None, **updates):
    values = {
        "detection": source.original_run.prefix.detection,
        "observed_at": source.latest_source.evaluated_at,
    } | updates
    return evaluate_continuation(*(parts or _parts(source)), **values)


def _denied(result, code=None):
    assert not result.passed and result.code != "passed"
    assert not result.execution_authority
    assert not result.execution_recheck_performed
    assert not result.complete_path_verified
    if code:
        assert result.code == code


def _append_to(market, at, *, price):
    market.received_at = at
    market.ticker = market.ticker.model_copy(update={"timestamp": at})
    market.order_book = market.order_book.model_copy(update={"timestamp": at})
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    for frame in FRAMES:
        interval = timedelta(seconds=BAR_SECONDS[frame])
        expected = epoch + ((at - epoch) // interval) * interval
        rows = market.candles[frame]
        while candle_closed_at(rows[-1], frame) < expected:
            rows.append(
                Candle(
                    timestamp=candle_closed_at(rows[-1], frame),
                    open=price,
                    close=price,
                    low=price - D("0.001"),
                    high=price + D("0.001"),
                    volume_contracts=D(100),
                    volume_currency=D(100),
                    volume_quote=D(10000),
                    confirmed=True,
                )
            )


def test_actual_next_boundary_preserves_original_event_and_exposes_unknown_path(source):
    original, analysis, current = _parts(source)
    before = tuple(item.model_dump_json() for item in (original, analysis, current))
    result = _evaluate(source, parts=(original, analysis, current))
    assert result.passed, result
    assert (
        result.original_source_sha256
        == source.original_run.prefix.detection.source_sha256
    )
    assert result.original_event_key == source.original_event_key
    assert (
        result.original_trigger_expires_at
        == source.original_run.prefix.detection.trigger.expires_at
    )
    assert result.current_market_sha256 != result.original_market_sha256
    assert tuple(item.timeframe for item in result.coverage) == FRAMES
    assert tuple(item.appended_count for item in result.coverage) == (0, 0, 1, 1)
    for item in result.coverage:
        assert item.verified_through == candle_closed_at(
            current.candles[item.timeframe][-1], item.timeframe
        )
        assert item.blind_from == item.verified_through
        assert item.blind_to == source.latest_source.evaluated_at
        assert item.intrabar_status == "unknown"
    assert all(
        not getattr(result, name)
        for name in (
            "execution_authority",
            "source_authenticity_verified",
            "execution_recheck_performed",
            "complete_path_verified",
        )
    )
    assert before == tuple(
        item.model_dump_json() for item in (original, analysis, current)
    )
    assert (
        ContinuationResult.model_validate_json(result.model_dump_json(round_trip=True))
        == result
    )
    assert (
        verify_continuation(
            result,
            original,
            analysis,
            current,
            detection=source.original_run.prefix.detection,
            observed_at=source.latest_source.evaluated_at,
        )
        == result
    )


def test_same_interval_requires_no_invented_new_candle(source):
    original, analysis, current = _parts(source)
    current.candles = original.model_copy(deep=True).candles
    now = original.received_at + timedelta(seconds=1)
    current.received_at = now
    current.ticker = current.ticker.model_copy(update={"timestamp": now})
    current.order_book = current.order_book.model_copy(update={"timestamp": now})
    result = _evaluate(source, parts=(original, analysis, current), observed_at=now)
    assert result.passed
    assert all(item.appended_count == 0 for item in result.coverage)
    assert all(item.intrabar_status == "unknown" for item in result.coverage)


@pytest.mark.parametrize("frame", FRAMES)
@pytest.mark.parametrize(
    "field",
    [
        "open",
        "high",
        "low",
        "close",
        "volume_contracts",
        "volume_currency",
        "volume_quote",
    ],
)
def test_each_original_confirmed_field_must_remain_unchanged(source, frame, field):
    original, analysis, current = _parts(source)
    rows = current.candles[frame]
    row = rows[100]
    # Alter within the original OHLC envelope; no invalid-price shortcut.
    delta = D("0.00001") if field != "low" else D("-0.00001")
    rows[100] = row.model_copy(update={field: getattr(row, field) + delta})
    _denied(_evaluate(source, parts=(original, analysis, current)), "history_rewritten")


@pytest.mark.parametrize("frame", FRAMES)
def test_history_cannot_roll_drop_or_replace_the_original_start(source, frame):
    original, analysis, current = _parts(source)
    current.candles[frame].pop(0)
    result = _evaluate(source, parts=(original, analysis, current))
    assert result.code in {"history_truncated", "history_rewritten"}
    _denied(result)


@pytest.mark.parametrize("frame", ["15m", "5m"])
def test_newly_closed_tail_is_mandatory_even_with_otherwise_fresh_quality(
    source, frame
):
    original, analysis, current = _parts(source)
    current.candles[frame].pop()
    _denied(
        _evaluate(source, parts=(original, analysis, current)), "closed_tail_missing"
    )


@pytest.mark.parametrize(
    "kind",
    [
        "gap",
        "duplicate",
        "reversed",
        "off_grid",
        "unconfirmed",
        "future",
        "nan",
        "float",
        "extra_frame",
        "missing_frame",
        "oversize",
    ],
)
def test_malformed_current_source_is_not_repaired_or_coerced(source, kind):
    original, analysis, current = _parts(source)
    rows = current.candles["5m"]
    if kind == "gap":
        rows.pop(100)
    elif kind == "duplicate":
        rows.insert(100, rows[100])
    elif kind == "reversed":
        rows[100], rows[101] = rows[101], rows[100]
    elif kind == "off_grid":
        rows[-1] = rows[-1].model_copy(
            update={"timestamp": rows[-1].timestamp + timedelta(microseconds=1)}
        )
    elif kind == "unconfirmed":
        rows[-1] = rows[-1].model_copy(update={"confirmed": False})
    elif kind == "future":
        rows.append(
            rows[-1].model_copy(update={"timestamp": candle_closed_at(rows[-1], "5m")})
        )
    elif kind in {"nan", "float"}:
        rows[-1] = rows[-1].model_copy(
            update={"high": D("NaN") if kind == "nan" else 101.0}
        )
    elif kind == "extra_frame":
        current.candles["1m"] = rows
    elif kind == "missing_frame":
        current.candles.pop("4H")
    else:
        current.candles["5m"] = [rows[0]] * 1025
    _denied(
        _evaluate(source, parts=(original, analysis, current)), "current_source_invalid"
    )


@pytest.mark.parametrize("frame", FRAMES)
def test_any_fully_post_setup_frame_touch_cancels_even_after_recovery(source, frame):
    original, analysis, current = _parts(source)
    detection = source.original_run.prefix.detection
    # For HTF examples the event will also be expired. Invalidation evidence
    # still takes precedence and cannot be hidden by a later recovered close.
    interval = timedelta(seconds=BAR_SECONDS[frame])
    setup = detection.setup_time
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    first_open = epoch + (-(-(setup - epoch) // interval)) * interval
    first_open = max(first_open, candle_closed_at(original.candles[frame][-1], frame))
    at = max(current.received_at, first_open + interval) + timedelta(milliseconds=1)
    _append_to(current, at, price=original.ticker.last)
    row = next(r for r in current.candles[frame] if r.timestamp == first_open)
    field = "low" if source.original_source.direction == "long" else "high"
    index = current.candles[frame].index(row)
    current.candles[frame][index] = row.model_copy(
        update={field: detection.invalidation_price}
    )
    result = _evaluate(source, parts=(original, analysis, current), observed_at=at)
    _denied(result, "trigger_invalidated")
    assert result.invalidation_timeframe == frame
    assert result.invalidation_known_at == candle_closed_at(row, frame)
    assert row.close != detection.invalidation_price


def test_earliest_known_invalidation_is_preserved_deterministically(source):
    original, analysis, current = _parts(source)
    detection = source.original_run.prefix.detection
    field = "low" if detection.direction == "long" else "high"
    for frame in ("15m", "5m"):
        rows = current.candles[frame]
        rows[-1] = rows[-1].model_copy(update={field: detection.invalidation_price})
    result = _evaluate(source, parts=(original, analysis, current))
    _denied(result, "trigger_invalidated")
    # Same close -> shorter independently observed interval first, as extractor.
    assert result.invalidation_timeframe == "5m"


@pytest.mark.parametrize(
    "field", ["version", "price", "indicators", "extra", "subclass"]
)
def test_original_analysis_is_recomputed_not_a_trusted_boolean(source, field):
    original, analysis, current = _parts(source)
    if field == "version":
        # Even a recomputable version changes the source pin and event replay.
        analysis = analysis.model_copy(update={"version": "other-version"})
    elif field == "price":
        analysis = analysis.model_copy(update={"price": analysis.price + D(1)})
    elif field == "indicators":
        view = analysis.timeframe_analyses["5m"]
        view.indicators = view.indicators.model_copy(update={"atr14": D("0.00001")})
    elif field == "extra":
        analysis = analysis.model_copy(update={"untrusted": True})
    else:

        class OtherAnalysis(MultiTimeframeAnalysis):
            pass

        analysis = OtherAnalysis.model_validate(analysis.model_dump())
    result = _evaluate(source, parts=(original, analysis, current))
    assert result.code in {
        "original_source_invalid",
        "original_analysis_mismatch",
        "original_event_mismatch",
    }
    _denied(result)


@pytest.mark.parametrize(
    "field",
    ["anchor", "basis", "source", "expiry", "trigger_time", "failed", "missing"],
)
def test_original_event_cannot_be_forged_or_renewed(source, field):
    detection = source.original_run.prefix.detection
    if field == "anchor":
        detection = detection.model_copy(
            update={"invalidation_price": detection.invalidation_price - D("0.001")}
        )
    elif field == "basis":
        detection = detection.model_copy(
            update={"setup_basis": detection.setup_basis + (("fake", "evidence"),)}
        )
    elif field == "source":
        detection = detection.model_copy(update={"source_sha256": "0" * 64})
    elif field == "expiry":
        detection = detection.model_copy(
            update={
                "trigger": detection.trigger.model_copy(
                    update={
                        "expires_at": detection.trigger.expires_at
                        + timedelta(seconds=1)
                    }
                )
            }
        )
    elif field == "trigger_time":
        detection = detection.model_copy(
            update={
                "trigger": detection.trigger.model_copy(
                    update={
                        "trigger_time": detection.trigger.trigger_time
                        - timedelta(seconds=1)
                    }
                )
            }
        )
    elif field == "failed":
        detection = detection.model_copy(update={"fail_codes": ("trigger_missing",)})
    else:
        detection = detection.model_copy(update={"trigger": None})
    result = _evaluate(source, detection=detection)
    assert result.code in {"original_event_invalid", "original_event_mismatch"}
    _denied(result)


def test_only_original_source_is_used_for_event_extraction(source, monkeypatch):
    actual = continuation.extract_trigger
    calls = []

    def observe(market, analysis, **kwargs):
        calls.append((market.received_at, kwargs["observed_at"]))
        return actual(market, analysis, **kwargs)

    monkeypatch.setattr(continuation, "extract_trigger", observe)
    result = _evaluate(source)
    assert result.passed
    assert calls == [
        (
            source.original_source.market.received_at,
            source.original_run.prefix.detection.observed_at,
        )
    ]


def test_event_expiry_is_not_extended_by_successful_new_closed_candles(source):
    original, analysis, current = _parts(source)
    at = source.original_run.prefix.detection.trigger.expires_at
    _append_to(current, at, price=original.ticker.last)
    _denied(
        _evaluate(source, parts=(original, analysis, current), observed_at=at),
        "original_event_expired",
    )


def test_large_bar_spanning_setup_does_not_invent_touch_order(source):
    original, analysis, current = _parts(source)
    detection = source.original_run.prefix.detection
    opened = candle_closed_at(original.candles["4H"][-1], "4H")
    at = opened + timedelta(hours=4)
    assert opened < detection.setup_time < at
    _append_to(current, at, price=original.ticker.last)
    field = "low" if detection.direction == "long" else "high"
    current.candles["4H"][-1] = current.candles["4H"][-1].model_copy(
        update={field: detection.invalidation_price}
    )
    result = _evaluate(source, parts=(original, analysis, current), observed_at=at)
    # Expiry independently forbids entry, but the spanning wick cannot assert
    # a post-setup touch happened. No fabricated intrabar chronology is stored.
    _denied(result, "original_event_expired")
    assert result.invalidation_timeframe is result.invalidation_known_at is None


@pytest.mark.parametrize("after_close", [False, True])
def test_exact_closed_boundary_needs_new_bar_but_never_proves_full_path(
    source, after_close
):
    original, analysis, current = _parts(source)
    closed = candle_closed_at(current.candles["5m"][-1], "5m")
    at = closed if after_close else closed - timedelta(microseconds=1)
    if not after_close:
        current.candles["5m"].pop()
        current.candles["15m"].pop()
    current.received_at = at
    current.ticker = current.ticker.model_copy(update={"timestamp": at})
    current.order_book = current.order_book.model_copy(update={"timestamp": at})
    result = _evaluate(source, parts=(original, analysis, current), observed_at=at)
    assert result.passed
    frame = result.coverage[-1]
    assert frame.appended_count == int(after_close)
    assert (frame.blind_from == frame.blind_to) is after_close
    assert frame.intrabar_status == "unknown" and not result.complete_path_verified


def test_caller_quality_is_not_traversed_or_used_as_source_truth(source):
    original, analysis, current = _parts(source)

    class UntrustedQuality:
        def __iter__(self):
            raise AssertionError("caller quality must not be traversed")

    current.quality = UntrustedQuality()
    result = _evaluate(source, parts=(original, analysis, current))
    assert result.passed
    assert result.current_market_sha256 == _evaluate(source).current_market_sha256


@pytest.mark.parametrize(
    "field", ["instrument", "future_capture", "future_ticker", "original_candle"]
)
def test_identity_and_source_causality_cannot_hide_behind_complete_ohlc(source, field):
    original, analysis, current = _parts(source)
    if field == "instrument":
        current.instrument_id = "ETH-USDT-SWAP"
    elif field == "future_capture":
        current.received_at = source.latest_source.evaluated_at + timedelta(
            microseconds=1
        )
    elif field == "future_ticker":
        current.ticker = current.ticker.model_copy(
            update={"timestamp": current.received_at + timedelta(microseconds=1)}
        )
    else:
        original.candles["5m"][0] = original.candles["5m"][0].model_copy(
            update={"open": D("NaN")}
        )
    result = _evaluate(source, parts=(original, analysis, current))
    _denied(
        result,
        "original_source_invalid"
        if field == "original_candle"
        else "current_source_invalid",
    )


@pytest.mark.parametrize(
    "clock",
    [datetime(2026, 9, 12, tzinfo=UTC).replace(tzinfo=None), "2026-09-12T01:15:00Z", 1],
)
def test_observation_clock_must_be_exact_aware_datetime(source, clock):
    with pytest.raises(ValueError):
        _evaluate(source, observed_at=clock)


def _timezone_tree(value, target):
    if isinstance(value, BaseModel):
        return value.model_copy(
            update={
                key: _timezone_tree(item, target)
                for key, item in value.__dict__.items()
            }
        )
    if type(value) is datetime:
        return value.astimezone(target)
    if type(value) is list:
        return [_timezone_tree(item, target) for item in value]
    if type(value) is dict:
        return {key: _timezone_tree(item, target) for key, item in value.items()}
    return value


def test_utc_equivalence_and_hostile_decimal_context_preserve_replay_hash(source):
    parts = _parts(source)
    baseline = _evaluate(source, parts=parts)
    local = timezone(timedelta(hours=8))
    shifted = tuple(_timezone_tree(value, local) for value in parts)
    with localcontext(Context(prec=6, traps=[Inexact, Rounded])):
        repeated = _evaluate(
            source,
            parts=shifted,
            observed_at=source.latest_source.evaluated_at.astimezone(local),
        )
        assert repeated == baseline
        assert repeated.evaluation_sha256 == baseline.evaluation_sha256


class FoldOffset(tzinfo):
    def utcoffset(self, dt):
        return timedelta(hours=1 if dt.fold == 0 else 0)

    def dst(self, dt):
        return timedelta(0)


def test_fold_clock_is_compared_as_utc_instant_not_same_tz_wall_time(source):
    local = source.latest_source.evaluated_at.replace(tzinfo=FoldOffset(), fold=0)
    assert local.astimezone(UTC) < source.original_run.prefix.detection.observed_at
    _denied(_evaluate(source, observed_at=local), "capture_order_invalid")


@pytest.mark.parametrize(
    "kind", ["candle_subclass", "candle_extra", "generator", "nested_iterator"]
)
def test_raw_model_tampering_never_invokes_untrusted_serializer_or_iterator(
    source, kind
):
    original, analysis, current = _parts(source)
    rows = current.candles["5m"]
    if kind == "candle_subclass":

        class ForeignCandle(Candle):
            pass

        rows[0] = ForeignCandle.model_validate(rows[0].model_dump())
    elif kind == "candle_extra":
        rows[0] = rows[0].model_copy(update={"hidden": True})
    else:

        def forbidden():
            raise AssertionError("untrusted iterator must not be consumed")
            yield None

        if kind == "generator":
            current.candles["5m"] = forbidden()
        else:
            current.order_book.bids = forbidden()
    _denied(
        _evaluate(source, parts=(original, analysis, current)), "current_source_invalid"
    )


@pytest.mark.parametrize(
    "field",
    [
        "execution_authority",
        "source_authenticity_verified",
        "execution_recheck_performed",
        "complete_path_verified",
    ],
)
@pytest.mark.parametrize("value", [True, 0, 1, "false"])
def test_output_cannot_grant_or_coerce_any_authority(source, field, value):
    result = _evaluate(source)
    raw = json.loads(result.model_dump_json(round_trip=True))
    raw[field] = value
    with pytest.raises(ValidationError):
        ContinuationResult.model_validate_json(json.dumps(raw))


def test_copy_tampering_and_changed_source_cannot_pass_replay(source):
    original, analysis, current = _parts(source)
    result = _evaluate(source, parts=(original, analysis, current))
    for dirty in (
        result.model_copy(update={"hidden": True}),
        result.model_copy(update={"coverage": iter(result.coverage)}),
        result.model_copy(update={"reason": analysis}),
    ):
        with pytest.raises(ValueError):
            verify_continuation(
                dirty,
                original,
                analysis,
                current,
                detection=source.original_run.prefix.detection,
                observed_at=source.latest_source.evaluated_at,
            )
    current.open_interest_contracts += D(1)
    with pytest.raises(ValueError, match="continuation_replay_mismatch"):
        verify_continuation(
            result,
            original,
            analysis,
            current,
            detection=source.original_run.prefix.detection,
            observed_at=source.latest_source.evaluated_at,
        )


def test_module_has_no_clock_lookup_network_order_or_new_candidate_builder():
    tree = ast.parse(inspect.getsource(continuation))
    banned = {
        "now",
        "utcnow",
        "get_settings",
        "build_candidate",
        "place_order",
        "create_order",
        "open",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else node.func.id
                if isinstance(node.func, ast.Name)
                else ""
            )
            assert name not in banned
