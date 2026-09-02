"""Deterministic, offline-only purged walk-forward splitting.

The splitter operates exclusively on already materialized observation timestamps.
It does not load data, fit a model, or interact with any runtime/execution path.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Sequence


@dataclass(frozen=True, slots=True)
class PurgedWalkForwardFold:
    """Immutable indexes and boundaries for one expanding-window fold."""

    fold_index: int
    training_indices: tuple[int, ...]
    validation_indices: tuple[int, ...]
    purged_indices: tuple[int, ...]
    prior_embargoed_indices: tuple[int, ...]
    embargoed_indices: tuple[int, ...]
    training_start_at: datetime
    training_end_at: datetime
    validation_start_at: datetime
    validation_end_at: datetime


def _validated_timestamps(
    timestamps: Sequence[datetime],
) -> tuple[datetime, ...]:
    values = tuple(timestamps)
    if not values:
        raise ValueError("walk-forward timestamps cannot be empty")
    for timestamp in values:
        if not isinstance(timestamp, datetime):
            raise ValueError("walk-forward timestamps must be datetimes")
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("walk-forward timestamps must be timezone-aware UTC")
        if timestamp.utcoffset() != timedelta(0):
            raise ValueError("walk-forward timestamps must use UTC")
    if any(
        current <= previous
        for previous, current in zip(
            values[:-1], values[1:], strict=True
        )
    ):
        raise ValueError("walk-forward timestamps must be strictly increasing")
    return values


def _validate_positive(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")


def purged_walk_forward_folds(
    timestamps: Sequence[datetime],
    *,
    minimum_training_observations: int,
    validation_observations: int,
    feature_dependency_seconds: int,
    label_dependency_seconds: int,
    purge_seconds: int,
    embargo_seconds: int,
    step_observations: int | None = None,
    maximum_folds: int | None = None,
) -> tuple[PurgedWalkForwardFold, ...]:
    """Create deterministic expanding-window folds with purge and embargo.

    ``purge_seconds`` prevents any training observation whose dependency window
    can touch the next validation window. ``embargo_seconds`` removes the
    observations immediately following each validation window from all later
    training folds and ensures that validation windows cannot enter that range.

    The returned indices always refer to the original ``timestamps`` sequence.
    Every validation window has exactly ``validation_observations`` rows. A
    trailing partial validation window is deliberately rejected by omission.
    """

    values = _validated_timestamps(timestamps)
    for name, value in (
        ("minimum_training_observations", minimum_training_observations),
        ("validation_observations", validation_observations),
        ("feature_dependency_seconds", feature_dependency_seconds),
        ("label_dependency_seconds", label_dependency_seconds),
        ("purge_seconds", purge_seconds),
        ("embargo_seconds", embargo_seconds),
    ):
        _validate_positive(name, value)

    step = (
        validation_observations
        if step_observations is None
        else step_observations
    )
    _validate_positive("step_observations", step)
    if maximum_folds is not None:
        _validate_positive("maximum_folds", maximum_folds)

    required_separation = max(
        feature_dependency_seconds,
        label_dependency_seconds,
    )
    if purge_seconds < required_separation:
        raise ValueError("purge must cover the largest dependency window")
    if embargo_seconds < required_separation:
        raise ValueError("embargo must cover the largest dependency window")

    purge = timedelta(seconds=purge_seconds)
    embargo = timedelta(seconds=embargo_seconds)
    previous_embargoes: list[tuple[datetime, datetime]] = []
    folds: list[PurgedWalkForwardFold] = []
    validation_start_index = minimum_training_observations

    while validation_start_index + validation_observations <= len(values):
        validation_start = values[validation_start_index]

        # A later validation fold cannot start inside a prior embargo window.
        for embargo_start, embargo_end in previous_embargoes:
            if embargo_start < validation_start <= embargo_end:
                while (
                    validation_start_index < len(values)
                    and values[validation_start_index] <= embargo_end
                ):
                    validation_start_index += 1
                break
        if validation_start_index + validation_observations > len(values):
            break
        validation_start = values[validation_start_index]

        purge_cutoff = validation_start - purge
        prior_embargoed_indices = tuple(
            index
            for index in range(validation_start_index)
            if any(
                start < values[index] <= end
                for start, end in previous_embargoes
            )
        )
        prior_embargoed = frozenset(prior_embargoed_indices)
        training_indices = tuple(
            index
            for index in range(validation_start_index)
            if values[index] < purge_cutoff
            and index not in prior_embargoed
        )

        if len(training_indices) < minimum_training_observations:
            validation_start_index += 1
            continue

        purged_indices = tuple(
            index
            for index in range(validation_start_index)
            if values[index] >= purge_cutoff
            and index not in prior_embargoed
        )
        validation_indices = tuple(
            range(
                validation_start_index,
                validation_start_index + validation_observations,
            )
        )
        validation_end = values[validation_indices[-1]]
        current_embargo_end = validation_end + embargo
        embargoed_indices = tuple(
            index
            for index in range(validation_indices[-1] + 1, len(values))
            if values[index] <= current_embargo_end
        )

        folds.append(
            PurgedWalkForwardFold(
                fold_index=len(folds),
                training_indices=training_indices,
                validation_indices=validation_indices,
                purged_indices=purged_indices,
                prior_embargoed_indices=prior_embargoed_indices,
                embargoed_indices=embargoed_indices,
                training_start_at=values[training_indices[0]],
                training_end_at=values[training_indices[-1]],
                validation_start_at=validation_start,
                validation_end_at=validation_end,
            )
        )
        if maximum_folds is not None and len(folds) >= maximum_folds:
            break

        previous_embargoes.append((validation_end, current_embargo_end))
        candidate = validation_start_index + step
        while (
            candidate < len(values)
            and values[candidate] <= current_embargo_end
        ):
            candidate += 1
        validation_start_index = candidate

    if not folds:
        raise ValueError("timestamps cannot produce a complete leakage-safe fold")
    return tuple(folds)


def assert_no_temporal_leakage(
    timestamps: Sequence[datetime],
    folds: Sequence[PurgedWalkForwardFold],
    *,
    purge_seconds: int,
    embargo_seconds: int,
) -> None:
    """Fail closed if persisted fold indexes violate purge/embargo rules."""

    values = _validated_timestamps(timestamps)
    _validate_positive("purge_seconds", purge_seconds)
    _validate_positive("embargo_seconds", embargo_seconds)
    if not folds:
        raise ValueError("at least one walk-forward fold is required")
    purge = timedelta(seconds=purge_seconds)
    embargo = timedelta(seconds=embargo_seconds)
    known_embargoed_indices: set[int] = set()
    for expected_index, fold in enumerate(folds):
        if fold.fold_index != expected_index:
            raise ValueError("walk-forward fold indexes must be contiguous")
        if not fold.training_indices or not fold.validation_indices:
            raise ValueError("walk-forward folds require train and validation rows")
        groups = (
            fold.training_indices,
            fold.validation_indices,
            fold.purged_indices,
            fold.prior_embargoed_indices,
            fold.embargoed_indices,
        )
        if any(group != tuple(sorted(set(group))) for group in groups):
            raise ValueError("walk-forward indexes must be unique and sorted")
        indexes = tuple(index for group in groups for index in group)
        if any(index < 0 or index >= len(values) for index in indexes):
            raise ValueError("walk-forward fold index is outside the dataset")
        if len(indexes) != len(set(indexes)):
            raise ValueError("walk-forward index groups cannot overlap")
        if fold.validation_indices != tuple(
            range(
                fold.validation_indices[0],
                fold.validation_indices[-1] + 1,
            )
        ):
            raise ValueError("validation indexes must be contiguous")
        if fold.training_indices[-1] >= fold.validation_indices[0]:
            raise ValueError("walk-forward training must precede validation")

        validation_start = values[fold.validation_indices[0]]
        validation_end = values[fold.validation_indices[-1]]
        if (
            fold.training_start_at != values[fold.training_indices[0]]
            or fold.training_end_at != values[fold.training_indices[-1]]
            or fold.validation_start_at != validation_start
            or fold.validation_end_at != validation_end
        ):
            raise ValueError("walk-forward recorded boundaries do not match indexes")

        purge_cutoff = validation_start - purge
        pre_validation = set(range(fold.validation_indices[0]))
        expected_prior_embargoed = known_embargoed_indices & pre_validation
        expected_training = {
            index
            for index in pre_validation - expected_prior_embargoed
            if values[index] < purge_cutoff
        }
        expected_purged = (
            pre_validation - expected_prior_embargoed - expected_training
        )
        if set(fold.prior_embargoed_indices) != expected_prior_embargoed:
            raise ValueError("prior embargo accounting is incomplete")
        if set(fold.training_indices) != expected_training:
            raise ValueError("training dependency overlaps validation")
        if set(fold.purged_indices) != expected_purged:
            raise ValueError("purged row accounting is incomplete")

        current_embargo_end = validation_end + embargo
        expected_current_embargo = {
            index
            for index in range(fold.validation_indices[-1] + 1, len(values))
            if values[index] <= current_embargo_end
        }
        if set(fold.embargoed_indices) != expected_current_embargo:
            raise ValueError("current embargo accounting is incomplete")
        if expected_index and validation_start <= (
            folds[expected_index - 1].validation_end_at + embargo
        ):
            raise ValueError("validation starts inside a prior embargo")
        known_embargoed_indices.update(expected_current_embargo)
