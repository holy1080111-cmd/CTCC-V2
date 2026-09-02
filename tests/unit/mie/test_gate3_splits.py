from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from app.mie.validation.splits import (
    assert_no_temporal_leakage,
    purged_walk_forward_folds,
)


START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def hourly_timestamps(count: int) -> tuple[datetime, ...]:
    return tuple(START + timedelta(hours=index) for index in range(count))


def split(timestamps: tuple[datetime, ...], **overrides):
    parameters = {
        "minimum_training_observations": 5,
        "validation_observations": 3,
        "feature_dependency_seconds": 7_200,
        "label_dependency_seconds": 3_600,
        "purge_seconds": 7_200,
        "embargo_seconds": 7_200,
        "step_observations": 3,
    }
    parameters.update(overrides)
    return purged_walk_forward_folds(timestamps, **parameters)


def test_purged_walk_forward_is_deterministic_and_dependency_safe() -> None:
    timestamps = hourly_timestamps(24)

    first = split(timestamps, maximum_folds=2)
    second = split(timestamps, maximum_folds=2)

    assert first == second
    assert len(first) == 2
    assert first[0].fold_index == 0
    assert first[0].training_indices == (0, 1, 2, 3, 4)
    assert first[0].purged_indices == (5, 6)
    assert first[0].validation_indices == (7, 8, 9)
    assert first[0].embargoed_indices == (10, 11)
    assert first[1].validation_indices == (12, 13, 14)
    assert first[1].prior_embargoed_indices == (10, 11)
    assert set(first[1].training_indices).isdisjoint({10, 11})
    assert first[1].validation_start_at > first[0].validation_end_at

    assert_no_temporal_leakage(
        timestamps,
        first,
        purge_seconds=7_200,
        embargo_seconds=7_200,
    )
    for fold in first:
        assert (
            fold.training_end_at + timedelta(seconds=7_200)
            < fold.validation_start_at
        )


def test_embargo_advances_validation_even_when_step_is_smaller() -> None:
    folds = split(
        hourly_timestamps(24),
        step_observations=1,
        maximum_folds=2,
    )

    assert folds[0].validation_indices == (7, 8, 9)
    assert folds[0].embargoed_indices == (10, 11)
    assert folds[1].validation_indices[0] == 12


def test_walk_forward_omits_an_incomplete_trailing_validation_window() -> None:
    folds = split(hourly_timestamps(10))

    assert len(folds) == 1
    assert folds[0].validation_indices == (7, 8, 9)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("purge_seconds", 3_600, "purge"),
        ("embargo_seconds", 3_600, "embargo"),
        ("minimum_training_observations", 0, "positive integer"),
        ("validation_observations", True, "positive integer"),
        ("maximum_folds", 0, "positive integer"),
    ],
)
def test_walk_forward_fails_closed_on_invalid_plan(
    field: str,
    value: object,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        split(hourly_timestamps(24), **{field: value})


def test_walk_forward_rejects_non_utc_or_non_monotonic_timestamps() -> None:
    naive = list(hourly_timestamps(12))
    naive[3] = naive[3].replace(tzinfo=None)
    with pytest.raises(ValueError, match="UTC"):
        split(tuple(naive))

    duplicate = list(hourly_timestamps(12))
    duplicate[4] = duplicate[3]
    with pytest.raises(ValueError, match="strictly increasing"):
        split(tuple(duplicate))

    non_utc = list(hourly_timestamps(12))
    non_utc[3] = non_utc[3].astimezone(timezone(timedelta(hours=8)))
    with pytest.raises(ValueError, match="must use UTC"):
        split(tuple(non_utc))


def test_walk_forward_rejects_data_too_short_for_a_safe_fold() -> None:
    with pytest.raises(ValueError, match="complete leakage-safe fold"):
        split(hourly_timestamps(8))


def test_leakage_assertion_rejects_tampered_persisted_indexes() -> None:
    timestamps = hourly_timestamps(24)
    fold = split(timestamps, maximum_folds=1)[0]
    corrupted = replace(
        fold,
        training_indices=(*fold.training_indices, fold.purged_indices[-1]),
    )

    with pytest.raises(ValueError, match="overlap"):
        assert_no_temporal_leakage(
            timestamps,
            (corrupted,),
            purge_seconds=7_200,
            embargo_seconds=7_200,
        )
