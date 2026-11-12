"""Synthetic-only holdout aggregation controls; never opens a protected path."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.mie.features import FeatureBar
from app.mie.validation import fitted_holdout_aggregation_v1 as subject
from app.mie.validation.blind_window_dataset import BlindWindowDatasetRow
from app.mie.validation.contracts import DatasetPartition, PartitionWindow
from app.mie.validation.fitted_holdout_materializer_v1 import (
    FittedHoldoutMinuteTapeV1,
    FittedHoldoutMinuteV1,
    _minutes_sha256,
)
from app.mie.validation.prospective_capture_schedule import (
    ProspectiveCoordinatePlanV1,
)
from app.mie.validation.replay import PointInTimeBar

START = datetime(2026, 9, 1, tzinfo=UTC)
MINUTE = timedelta(minutes=1)
SYMBOLS = ("BTC-USDT-SWAP", "ETH-USDT-SWAP")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def _case(*, start: datetime = START, minute_count: int = 240):
    coordinates = ProspectiveCoordinatePlanV1(
        holdout_id="synthetic-holdout",
        origin="https://openapi.okx.com",
        instrument_ids=SYMBOLS,
        start_at=start,
        end_at=start + minute_count * MINUTE,
        expected_rows=minute_count * len(SYMBOLS),
    )
    minutes = []
    for ordinal in range(coordinates.expected_rows):
        minute_index, symbol_index = divmod(ordinal, 2)
        opened = start + minute_index * MINUTE
        closed = opened + MINUTE
        symbol = SYMBOLS[symbol_index]
        identity = _digest(f"identity:{ordinal}")
        content = _digest(f"content:{ordinal}")
        price = Decimal(100 + symbol_index) + Decimal(minute_index) / Decimal(100)
        evidence = BlindWindowDatasetRow(
            ordinal=ordinal,
            journal_index=0,
            journal_checkpoint_sha256=_digest("checkpoint"),
            capture_id=f"{ordinal + 1:032x}",
            capture_plan_sha256=_digest(f"plan:{ordinal}"),
            capture_receipt_sha256=_digest(f"receipt:{ordinal}"),
            attempt_sha256=_digest(f"attempt:{ordinal}"),
            instrument_id=symbol,
            source_row_identity=identity,
            source_row_sha256=content,
            raw_page_sha256=_digest(f"raw:{ordinal}"),
            opened_at=opened,
            closed_at=closed,
            request_started_at=closed,
            observed_at=closed + timedelta(seconds=1),
            retained_at=closed + timedelta(seconds=2),
            decision_available_at=closed + timedelta(seconds=2),
        )
        minutes.append(
            FittedHoldoutMinuteV1(
                row=PointInTimeBar(
                    source_row_id="okx-public-minute:" + identity,
                    source_row_sha256=content,
                    instrument_id=symbol,
                    available_at=evidence.retained_at,
                    bar=FeatureBar(
                        closed_at=closed,
                        open=price,
                        high=price + Decimal("0.1"),
                        low=price - Decimal("0.1"),
                        close=price,
                        volume=Decimal(1),
                    ),
                ),
                evidence=evidence,
            )
        )
    original = tuple(minutes)
    tape = FittedHoldoutMinuteTapeV1(
        seal_sha256=_digest("seal"),
        binding_sha256=_digest("binding"),
        receipt_sha256=_digest("receipt"),
        reservation_sha256=_digest("reservation"),
        coordinate_plan_sha256=coordinates.canonical_sha256(),
        manifest_sha256=_digest("manifest"),
        dataset_sha256=_digest("dataset"),
        minutes_sha256=_minutes_sha256(original),
        minutes=original,
    )
    return tape, coordinates


def _arguments(tape, coordinates):
    return {
        "tape_bytes": tape.canonical_json_bytes(),
        "coordinate_plan_bytes": coordinates.canonical_json_bytes(),
        "expected_tape_sha256": tape.canonical_sha256(),
        "expected_coordinate_plan_sha256": coordinates.canonical_sha256(),
    }


def test_two_symbol_full_grid_replays_15m_1h_4h_without_authority():
    tape, coordinates = _case()
    args = _arguments(tape, coordinates)
    first = subject.aggregate_fitted_holdout_v1(**args)
    report = first.contract
    assert report.instrument_ids == SYMBOLS
    assert report.tape_sha256 == tape.canonical_sha256()
    assert report.reservation_sha256 == tape.reservation_sha256
    assert not report.independent_source_custody_verified
    assert not report.independent_first_access_verified
    assert not report.predictive_oos_eligible
    assert not report.gate4_eligible and not report.execution_authority
    for symbol_index, series in enumerate(report.series):
        assert series.instrument_id == SYMBOLS[symbol_index]
        assert tuple(len(frame.bars) for frame in series.timeframes) == (16, 4, 1)
        assert all(frame.excluded_leading_minutes == 0 for frame in series.timeframes)
        assert all(frame.excluded_trailing_minutes == 0 for frame in series.timeframes)
        assert all(
            frame.bucket_status == "complete_buckets" for frame in series.timeframes
        )
        for frame, expected_volume in zip(
            series.timeframes, (15, 60, 240), strict=True
        ):
            bar = frame.bars[0]
            assert bar.row.bar.volume == Decimal(expected_volume)
            assert (
                bar.row.available_at
                == tape.minutes[
                    (expected_volume - 1) * 2 + symbol_index
                ].row.available_at
            )
            assert bar.constituent_count == expected_volume
            assert bar.ordinal_stride == 2
    second = subject.verify_fitted_holdout_aggregation_v1(
        first.payload, expected_report_sha256=first.sha256, **args
    )
    assert first.payload == second.payload


def test_utc_complete_buckets_expose_both_partial_edges_and_short_window():
    start = START + timedelta(hours=3, minutes=45)
    tape, coordinates = _case(start=start, minute_count=270)
    report = subject.aggregate_fitted_holdout_v1(
        **_arguments(tape, coordinates)
    ).contract
    for series in report.series:
        assert tuple(len(frame.bars) for frame in series.timeframes) == (18, 4, 1)
        assert tuple(frame.excluded_leading_minutes for frame in series.timeframes) == (
            0,
            15,
            15,
        )
        assert tuple(
            frame.excluded_trailing_minutes for frame in series.timeframes
        ) == (0, 15, 15)
        assert series.timeframes[2].bars[0].row.bar.closed_at == START + timedelta(
            hours=8
        )
    short, short_coordinates = _case(
        start=START + timedelta(minutes=10), minute_count=4
    )
    short_report = subject.aggregate_fitted_holdout_v1(
        **_arguments(short, short_coordinates)
    ).contract
    for series in short_report.series:
        assert all(frame.bars == () for frame in series.timeframes)
        assert all(
            frame.bucket_status == "no_complete_bucket" for frame in series.timeframes
        )
        assert all(frame.excluded_leading_minutes == 4 for frame in series.timeframes)
        assert all(frame.excluded_trailing_minutes == 0 for frame in series.timeframes)


def test_changed_pin_coordinate_and_missing_or_reordered_minute_refuse():
    tape, coordinates = _case()
    args = _arguments(tape, coordinates)
    with pytest.raises(subject.ArtifactVerificationError):
        subject.aggregate_fitted_holdout_v1(
            **{**args, "expected_tape_sha256": _digest("wrong")}
        )
    shifted = coordinates.model_copy(
        update={
            "end_at": coordinates.end_at + MINUTE,
            "expected_rows": coordinates.expected_rows + len(SYMBOLS),
        }
    )
    with pytest.raises(subject.FittedHoldoutAggregationError):
        subject.aggregate_fitted_holdout_v1(
            **{
                **args,
                "coordinate_plan_bytes": shifted.canonical_json_bytes(),
                "expected_coordinate_plan_sha256": shifted.canonical_sha256(),
            }
        )
    for changed in (
        tape.minutes[:-1],
        tape.minutes[:100] + tape.minutes[101:],
        tape.minutes[:100] + (tape.minutes[100],) + tape.minutes[100:],
        (tape.minutes[1], tape.minutes[0], *tape.minutes[2:]),
    ):
        corrupt = tape.model_copy(update={"minutes": changed})
        with pytest.raises(subject.ArtifactVerificationError):
            subject.aggregate_fitted_holdout_v1(**_arguments(corrupt, coordinates))


def test_original_development_validation_plan_still_refuses_holdout():
    from app.mie.validation.batch_replay import MinuteAggregationPlan

    with pytest.raises(ValueError, match="minute aggregation excludes holdout"):
        MinuteAggregationPlan(
            source_manifest_sha256=_digest("manifest"),
            window=PartitionWindow(
                partition=DatasetPartition.RETROSPECTIVE_HOLDOUT,
                start_at=START,
                end_at=START + timedelta(hours=4),
            ),
            instrument_id=SYMBOLS[0],
            expected_minute_rows=240,
            volume_unit="contracts",
        )


def test_fresh_self_hash_cannot_hide_a_reassigned_instrument():
    tape, coordinates = _case(minute_count=15)
    first = tape.minutes[0]
    reassigned = FittedHoldoutMinuteV1(
        row=first.row.model_copy(update={"instrument_id": SYMBOLS[1]}),
        evidence=first.evidence.model_copy(update={"instrument_id": SYMBOLS[1]}),
    )
    minutes = (reassigned, *tape.minutes[1:])
    changed = tape.model_copy(
        update={"minutes": minutes, "minutes_sha256": _minutes_sha256(minutes)}
    )
    with pytest.raises(subject.FittedHoldoutAggregationError, match="grid_changed"):
        subject.aggregate_fitted_holdout_v1(**_arguments(changed, coordinates))


def test_aggregate_availability_includes_a_delayed_early_minute():
    tape, coordinates = _case(minute_count=15)
    first = tape.minutes[0]
    delayed = coordinates.end_at + timedelta(seconds=20)
    revised = FittedHoldoutMinuteV1(
        row=first.row.model_copy(update={"available_at": delayed}),
        evidence=first.evidence.model_copy(
            update={"retained_at": delayed, "decision_available_at": delayed}
        ),
    )
    minutes = (revised, *tape.minutes[1:])
    changed = tape.model_copy(
        update={"minutes": minutes, "minutes_sha256": _minutes_sha256(minutes)}
    )
    result = subject.aggregate_fitted_holdout_v1(**_arguments(changed, coordinates))
    assert result.contract.series[0].timeframes[0].bars[0].row.available_at == delayed
