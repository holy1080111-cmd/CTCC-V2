"""Synthetic deterministic aggregation and causal gates; no OOS data or claims."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from hashlib import sha256
from warnings import catch_warnings, simplefilter

import pytest
from pydantic import ValidationError

from app.mie.features import FeatureBar
from app.mie.validation.availability import AvailabilityBasis, AvailabilityProvenance
from app.mie.validation.batch_replay import (
    BoundMinute,
    MinuteAggregationError,
    MinuteAggregationPlan,
    aggregate_point_in_time_minutes,
    archive_batch_minutes,
    minute_source_sha256,
    replay_aggregated_features_at,
)
from app.mie.validation.contracts import DatasetPartition, PartitionWindow
from app.mie.validation.replay import PointInTimeBar, ReplayValidationError
from app.mie.validation.splits import purged_walk_forward_folds

START = datetime(2024, 1, 1, tzinfo=UTC)


def inputs(basis=AvailabilityBasis.MEASURED_ROW_RECEIPT, *, count=480):
    plan = MinuteAggregationPlan(
        source_manifest_sha256="a" * 64,
        window=PartitionWindow(
            partition=DatasetPartition.DEVELOPMENT,
            start_at=START,
            end_at=START + timedelta(minutes=count),
        ),
        instrument_id="BTC-USDT-SWAP",
        expected_minute_rows=count,
        volume_unit="source_unspecified",
    )
    minutes = []
    for ordinal in range(1, count + 1):
        closed = START + timedelta(minutes=ordinal)
        digest = sha256(f"synthetic-minute:{ordinal}".encode()).hexdigest()
        observed = closed + timedelta(seconds=1)
        retrieved = closed + timedelta(seconds=2)
        available = observed
        if basis == AvailabilityBasis.ARCHIVE_OBSERVATION:
            observed = START + timedelta(days=2)
            retrieved = START + timedelta(days=3)
            available = retrieved
        elif basis == AvailabilityBasis.ASSUMED_BAR_CLOSE:
            available = closed
        price = Decimal(100) + Decimal(ordinal) / 100
        row = PointInTimeBar(
            source_row_id=f"synthetic:{ordinal}",
            source_row_sha256=digest,
            instrument_id=plan.instrument_id,
            available_at=available,
            bar=FeatureBar(
                closed_at=closed,
                open=price,
                high=price + 1,
                low=price - 1,
                close=price + Decimal("0.25"),
                volume=Decimal("0.01234567890123456789"),
            ),
        )
        minutes.append(
            BoundMinute(
                row=row,
                availability=AvailabilityProvenance(
                    basis=basis,
                    source_row_sha256=digest,
                    receipt_sha256="b" * 64,
                    bar_closed_at=closed,
                    observed_at=observed,
                    retrieved_at=retrieved,
                    available_at=available,
                    assumption="confirmed_at_bar_close"
                    if basis == AvailabilityBasis.ASSUMED_BAR_CLOSE
                    else "none",
                ),
            )
        )
    return plan, tuple(minutes)


def build(plan, minutes):
    return aggregate_point_in_time_minutes(
        minutes,
        plan=plan,
        expected_plan_sha256=plan.canonical_sha256(),
        expected_source_sha256=minute_source_sha256(minutes),
    )


@pytest.mark.parametrize("basis", list(AvailabilityBasis))
def test_deterministic_aggregation_preserves_basis_and_receipts_without_promotion(
    basis,
):
    plan, minutes = inputs(basis)
    first = build(plan, minutes)
    with localcontext() as context:
        context.prec = 4
        second = build(plan, minutes)
    assert first.canonical_json_bytes() == second.canonical_json_bytes()
    assert [len(series.bars) for series in first.timeframes] == [32, 8, 2]
    for series in first.timeframes:
        aggregate = series.bars[0]
        width = series.horizon.seconds // 60
        assert aggregate.row.bar.open == minutes[0].row.bar.open
        assert aggregate.row.bar.close == minutes[width - 1].row.bar.close
        assert aggregate.row.bar.high == max(
            item.row.bar.high for item in minutes[:width]
        )
        assert aggregate.row.bar.low == min(
            item.row.bar.low for item in minutes[:width]
        )
        assert aggregate.row.available_at == max(
            item.row.available_at for item in minutes[:width]
        )
        assert aggregate.availability_bases == (basis,) * width
        assert aggregate.receipt_sha256s == ("b" * 64,) * width
    assert first.predictive_oos_eligible is False
    assert first.execution_authority is False
    assert first.runtime_consumers == 0


@pytest.mark.parametrize("damage", ["missing", "duplicate", "reordered", "instrument"])
def test_rejects_malformed_source_even_with_recomputed_source_pin(damage):
    plan, minutes = inputs()
    rows = list(minutes)
    if damage == "missing":
        rows.pop(10)
    elif damage == "duplicate":
        rows[10] = rows[9]
    elif damage == "reordered":
        rows[9], rows[10] = rows[10], rows[9]
    else:
        payload = rows[10].model_dump(mode="python")
        payload["row"]["instrument_id"] = "ETH-USDT-SWAP"
        rows[10] = BoundMinute.model_validate(payload)
    with pytest.raises(MinuteAggregationError):
        build(plan, tuple(rows))


def test_pinned_plan_source_and_assumption_link_cannot_be_replaced():
    plan, minutes = inputs()
    with pytest.raises(MinuteAggregationError, match="plan pin"):
        aggregate_point_in_time_minutes(
            minutes,
            plan=plan,
            expected_plan_sha256="0" * 64,
            expected_source_sha256=minute_source_sha256(minutes),
        )
    with pytest.raises(MinuteAggregationError, match="source pin"):
        aggregate_point_in_time_minutes(
            minutes,
            plan=plan,
            expected_plan_sha256=plan.canonical_sha256(),
            expected_source_sha256="0" * 64,
        )
    forged = minutes[0].model_copy(
        update={
            "availability": minutes[0].availability.model_copy(
                update={"available_at": START}
            )
        }
    )
    with pytest.raises(ValidationError):
        build(plan, (forged,) + minutes[1:])


def test_source_pin_and_aggregation_consume_the_same_validated_nested_values():
    plan, minutes = inputs(count=240)
    forged = tuple(
        minute.model_copy(
            update={
                "row": minute.row.model_copy(
                    update={
                        "bar": minute.row.bar.model_copy(
                            update={
                                "open": Decimal(5),
                                "close": Decimal(5),
                                "low": Decimal(1),
                                "high": "10" if index == 0 else "9",
                            }
                        )
                    }
                )
            }
        )
        for index, minute in enumerate(minutes)
    )
    with catch_warnings():
        simplefilter("ignore", UserWarning)
        checked = tuple(
            BoundMinute.model_validate(minute.model_dump(mode="python"))
            for minute in forged
        )
        assert minute_source_sha256(forged) == minute_source_sha256(checked)
        actual = build(plan, forged)
        expected = build(plan, checked)
    assert actual.timeframes[0].bars[0].row.bar.high == Decimal(10)
    assert actual.canonical_json_bytes() == expected.canonical_json_bytes()


def test_holdout_partial_windows_and_cross_partition_rows_rejected():
    plan, minutes = inputs()
    payload = plan.model_dump(mode="python")
    payload["window"]["partition"] = DatasetPartition.RETROSPECTIVE_HOLDOUT
    with pytest.raises(ValidationError, match="excludes holdout"):
        MinuteAggregationPlan.model_validate(payload)
    payload = plan.model_dump(mode="python")
    payload["window"]["start_at"] += timedelta(minutes=1)
    with pytest.raises(ValidationError, match="4H boundaries"):
        MinuteAggregationPlan.model_validate(payload)
    payload = plan.model_dump(mode="python")
    payload["window"]["start_at"] += timedelta(days=2)
    payload["window"]["end_at"] += timedelta(days=2)
    validation = MinuteAggregationPlan.model_validate(payload)
    with pytest.raises(MinuteAggregationError, match="out-of-partition"):
        build(validation, minutes)


def test_current_feature_replay_rejects_late_archive_and_future_receipt():
    plan, minutes = inputs()
    batch = build(plan, minutes)
    params = {
        "minutes": minutes,
        "expected_plan_sha256": plan.canonical_sha256(),
        "expected_source_sha256": minute_source_sha256(minutes),
        "horizon_seconds": 900,
        "history_bars": 32,
    }
    with pytest.raises(ReplayValidationError, match="not available"):
        replay_aggregated_features_at(batch, as_of=plan.window.end_at, **params)
    first = replay_aggregated_features_at(
        batch, as_of=plan.window.end_at + timedelta(seconds=1), **params
    )
    second = replay_aggregated_features_at(
        batch, as_of=plan.window.end_at + timedelta(seconds=1), **params
    )
    assert first.replay_sha256 == second.replay_sha256
    archive_plan, archive_minutes = inputs(AvailabilityBasis.ARCHIVE_OBSERVATION)
    archive_batch = build(archive_plan, archive_minutes)
    with pytest.raises(ReplayValidationError, match="not available"):
        replay_aggregated_features_at(
            archive_batch,
            minutes=archive_minutes,
            expected_plan_sha256=archive_plan.canonical_sha256(),
            expected_source_sha256=minute_source_sha256(archive_minutes),
            horizon_seconds=900,
            as_of=archive_plan.window.end_at,
            history_bars=32,
        )


def test_tampered_aggregate_cannot_bypass_original_source_replay():
    plan, minutes = inputs()
    batch = build(plan, minutes)
    forged = batch.model_copy(update={"source_minutes_sha256": "f" * 64})
    with pytest.raises(MinuteAggregationError, match="original-source"):
        replay_aggregated_features_at(
            forged,
            minutes=minutes,
            expected_plan_sha256=plan.canonical_sha256(),
            expected_source_sha256=minute_source_sha256(minutes),
            horizon_seconds=900,
            as_of=plan.window.end_at + timedelta(seconds=1),
        )


def test_aggregate_timestamps_feed_existing_purge_and_embargo_splitter():
    plan, minutes = inputs(count=1440)
    batch = build(plan, minutes)
    times = tuple(item.row.bar.closed_at for item in batch.timeframes[0].bars)
    params = {
        "minimum_training_observations": 21,
        "validation_observations": 5,
        "feature_dependency_seconds": 900,
        "label_dependency_seconds": 900,
        "purge_seconds": 900,
        "embargo_seconds": 1800,
    }
    folds = purged_walk_forward_folds(times, **params)
    assert folds == purged_walk_forward_folds(times, **params)
    assert len(folds) > 1
    for fold in folds:
        assert times[fold.training_indices[-1]] < fold.validation_start_at - timedelta(
            seconds=900
        )
        assert not set(fold.training_indices) & set(fold.prior_embargoed_indices)
    with pytest.raises(ValueError, match="purge"):
        purged_walk_forward_folds(times, **{**params, "purge_seconds": 1})


def test_archive_bridge_rebuilds_original_bytes_and_refuses_holdout():
    from test_gate3_archive_batch_artifacts import synthetic_source
    from test_gate3_archive_batch_plan import valid_plan

    from app.mie.validation.archive_batch import load_archive_batch_rehearsal

    source_plan = valid_plan()
    sources = tuple(
        synthetic_source(day_offset=day, partition=partition, symbol="BTCUSDT")
        for partition, days in (
            (DatasetPartition.DEVELOPMENT, (0, 1)),
            (DatasetPartition.VALIDATION, (3,)),
        )
        for day in days
    )
    manifest = load_archive_batch_rehearsal(
        sources, plan=source_plan, expected_plan_sha256=source_plan.canonical_sha256()
    )
    plan, minutes = archive_batch_minutes(
        manifest,
        archive_bytes=tuple(item.archive_bytes for item in sources),
        expected_plan_sha256=manifest.plan_sha256,
        partition=DatasetPartition.DEVELOPMENT,
        instrument_id="BTC-USDT-SWAP",
    )
    assert len(minutes) == 2880
    assert plan.source_manifest_sha256 == manifest.canonical_sha256()
    assert all(
        item.availability.basis == AvailabilityBasis.ARCHIVE_OBSERVATION
        for item in minutes
    )
    assert build(plan, minutes).predictive_oos_eligible is False
    with pytest.raises(ValueError, match="development or validation"):
        archive_batch_minutes(
            manifest,
            archive_bytes=tuple(item.archive_bytes for item in sources),
            expected_plan_sha256=manifest.plan_sha256,
            partition=DatasetPartition.RETROSPECTIVE_HOLDOUT,
            instrument_id="BTC-USDT-SWAP",
        )
    corrupted = tuple(item.archive_bytes for item in sources[:-1]) + (
        b"corrupt validation",
    )
    with pytest.raises(ValueError):
        archive_batch_minutes(
            manifest,
            archive_bytes=corrupted,
            expected_plan_sha256=manifest.plan_sha256,
            partition=DatasetPartition.DEVELOPMENT,
            instrument_id="BTC-USDT-SWAP",
        )
