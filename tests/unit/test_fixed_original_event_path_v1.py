"""Original event policy calculations, synthetic sources; no native acceptance."""

import json
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from app.domain.analysis import MultiTimeframeAnalysis
from app.domain.market import MarketSnapshot
from app.trade_qualification.fixed_original_event_path_v1 import (
    evaluate_fixed_original_event_path_v1,
    join_confirmed_original_path_v1,
    verify_fixed_original_event_path_v1,
)
from tests.unit.qualification_recheck_fixtures import recheck_source


@pytest.fixture(scope="module", params=["long", "short"])
def source(request):
    return recheck_source(request.param, scenario="next_boundary")


def parts(source):
    raw = json.loads(source.original_run.prefix.data_result.source_json)
    return (
        MarketSnapshot.model_validate_json(json.dumps(raw["market"]), strict=True),
        MultiTimeframeAnalysis.model_validate_json(
            json.dumps(raw["analysis"]), strict=True
        ),
        source.latest_source.market.model_copy(deep=True),
    )


def evaluate(source, values=None, **updates):
    inputs = {
        "detection": source.original_run.prefix.detection,
        "observed_at": source.latest_source.evaluated_at,
    } | updates
    return evaluate_fixed_original_event_path_v1(*(values or parts(source)), **inputs)


def test_same_original_event_required_path_can_pass(source):
    values = parts(source)
    before = tuple(value.model_dump_json() for value in values)
    result = evaluate(source, values)
    assert result.required_event_path_complete, result.code
    assert result.continuation.original_event_key == source.original_event_key
    assert not result.continuous_tick_path_claimed
    assert not result.execution_authority
    assert before == tuple(value.model_dump_json() for value in values)
    assert (
        verify_fixed_original_event_path_v1(
            result,
            *values,
            detection=source.original_run.prefix.detection,
            observed_at=source.latest_source.evaluated_at,
        )
        == result
    )


@pytest.mark.parametrize("timeframe", ["4H", "1H", "15m", "5m"])
def test_rewritten_original_prefix_cannot_become_positive_path(source, timeframe):
    values = parts(source)
    row = values[2].candles[timeframe][100]
    values[2].candles[timeframe][100] = row.model_copy(
        update={"volume_quote": row.volume_quote + Decimal(1)}
    )
    result = evaluate(source, values)
    assert not result.required_event_path_complete
    assert result.code == "history_rewritten"


@pytest.mark.parametrize("timeframe", ["15m", "5m"])
def test_required_newly_closed_interval_cannot_be_omitted(source, timeframe):
    values = parts(source)
    values[2].candles[timeframe].pop()
    result = evaluate(source, values)
    assert not result.required_event_path_complete
    assert result.code == "closed_tail_missing"


@pytest.mark.parametrize("timeframe", ["4H", "1H", "15m", "5m"])
def test_matching_latest_close_cannot_hide_a_missing_middle_interval(source, timeframe):
    values = parts(source)
    final_close = values[2].candles[timeframe][-1]
    values[2].candles[timeframe].pop(100)
    assert values[2].candles[timeframe][-1] == final_close
    result = evaluate(source, values)
    assert not result.required_event_path_complete
    assert result.code == "current_source_invalid"


def test_unconfirmed_row_cannot_claim_required_confirmed_path(source):
    values = parts(source)
    rows = values[2].candles["5m"]
    rows[-1] = rows[-1].model_copy(update={"confirmed": False})
    result = evaluate(source, values)
    assert not result.required_event_path_complete
    assert result.code == "current_source_invalid"


def test_later_cutoff_crossing_close_requires_actual_new_interval(source):
    now = source.latest_source.evaluated_at
    later = now + timedelta(minutes=5)
    result = evaluate(source, observed_at=later)
    assert not result.required_event_path_complete
    assert result.code in {
        "required_confirmed_tail_missing_at_cutoff",
        "original_event_expired",
    }


def test_touch_and_return_in_fully_post_setup_bar_still_invalidates(source):
    values = parts(source)
    detection = source.original_run.prefix.detection
    rows = values[2].candles["5m"]
    index = len(values[0].candles["5m"])
    assert index < len(rows) and rows[index].timestamp >= detection.setup_time
    row = rows[index]
    key = "low" if detection.direction == "long" else "high"
    rows[index] = row.model_copy(update={key: detection.invalidation_price})
    result = evaluate(source, values)
    # Close/quote remain on the surviving side; touching cannot be erased.
    assert not result.required_event_path_complete
    assert result.code == "trigger_invalidated"


def test_constructor_or_changed_record_cannot_replace_recomputation(source):
    values = parts(source)
    result = evaluate(source, values)
    forged = replace(result, code="invented")
    with pytest.raises(ValueError, match="^fixed_original_event_path_replay_mismatch$"):
        verify_fixed_original_event_path_v1(
            forged,
            *values,
            detection=source.original_run.prefix.detection,
            observed_at=source.latest_source.evaluated_at,
        )


def test_rolling_window_can_retain_only_unrequired_pre_setup_original_rows(source):
    values = parts(source)
    raw_before = values[2].model_dump_json()
    for timeframe, rows in values[2].candles.items():
        while len(rows) > len(values[0].candles[timeframe]):
            rows.pop(0)
    rolling_before = values[2].model_dump_json()
    joined = join_confirmed_original_path_v1(
        values[0], values[2], detection=source.original_run.prefix.detection
    )
    result = evaluate(source, (values[0], values[1], joined))
    assert result.required_event_path_complete
    assert values[2].model_dump_json() == rolling_before
    assert rolling_before != raw_before


def test_rolling_adapter_cannot_hide_a_revised_overlap(source):
    values = parts(source)
    rows = values[2].candles["5m"]
    rows.pop(0)
    row = rows[100]
    rows[100] = row.model_copy(update={"volume_quote": row.volume_quote + Decimal(1)})
    with pytest.raises(ValueError, match="^fixed_path_overlap_changed_or_missing$"):
        join_confirmed_original_path_v1(
            values[0], values[2], detection=source.original_run.prefix.detection
        )
