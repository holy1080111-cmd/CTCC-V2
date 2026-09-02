from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.mie.contracts import ForecastHorizon
from app.mie.features import FeatureBar
from app.mie.validation import (
    PointInTimeBar,
    ReplayValidationError,
    forward_direction_label,
    replay_features_at,
    replay_features_walk_forward,
)

D = Decimal
START = datetime(2026, 1, 1, tzinfo=timezone.utc)
HORIZON = ForecastHorizon(label="15m", seconds=900)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def replay_rows(
    count: int = 48,
    *,
    future_change_after: int | None = None,
) -> tuple[PointInTimeBar, ...]:
    closes: list[Decimal] = []
    for index in range(count):
        close = D("100") + D(index) / D("10")
        if future_change_after is not None and index >= future_change_after:
            close += D("25") + D(index)
        closes.append(close)

    rows: list[PointInTimeBar] = []
    for index, close in enumerate(closes):
        previous = closes[index - 1] if index else close
        closed_at = START + timedelta(seconds=HORIZON.seconds * (index + 1))
        rows.append(
            PointInTimeBar(
                source_row_id=f"fixture:row:{index:04d}",
                source_row_sha256=digest(f"fixture-row-{index}-{close}"),
                instrument_id="BTC-USDT-SWAP",
                available_at=closed_at,
                bar=FeatureBar(
                    closed_at=closed_at,
                    open=previous,
                    high=max(previous, close) + D("0.01"),
                    low=min(previous, close) - D("0.01"),
                    close=close,
                    volume=D("100") + D(index),
                ),
            )
        )
    return tuple(rows)


def test_point_in_time_bar_rejects_preclose_availability() -> None:
    row = replay_rows(2)[0]
    payload = row.model_dump()
    payload["available_at"] = row.bar.closed_at - timedelta(microseconds=1)

    with pytest.raises(ValidationError, match="before it closes"):
        PointInTimeBar.model_validate(payload)


def test_replay_is_deterministic_shadow_only_and_ignores_future_values() -> None:
    rows = replay_rows()
    cutoff_index = 29
    cutoff = rows[cutoff_index].bar.closed_at
    future_changed = replay_rows(future_change_after=cutoff_index + 1)

    first = replay_features_at(
        rows,
        as_of=cutoff,
        bar_horizon=HORIZON,
        history_bars=24,
    )
    second = replay_features_at(
        future_changed,
        as_of=cutoff,
        bar_horizon=HORIZON,
        history_bars=24,
    )
    future_missing = replay_features_at(
        (*rows[: cutoff_index + 2], *rows[cutoff_index + 3 :]),
        as_of=cutoff,
        bar_horizon=HORIZON,
        history_bars=24,
    )

    assert first == second == future_missing
    assert first.replay_sha256 == second.replay_sha256 == future_missing.replay_sha256
    assert first.source_row_count == 24
    assert first.data_cutoff == cutoff
    assert first.feature_snapshot.data_cutoff == cutoff
    assert first.authority == "offline_shadow_only"
    assert first.runtime_consumers == 0
    assert first.execution_authority is False
    assert first.feature_snapshot.execution_authority is False


def test_replay_rejects_missing_duplicate_unsorted_and_late_due_rows() -> None:
    rows = replay_rows(32)
    cutoff = rows[-1].bar.closed_at

    with pytest.raises(ReplayValidationError, match="missing or irregular"):
        replay_features_at(
            (*rows[:10], *rows[11:]),
            as_of=cutoff,
            bar_horizon=HORIZON,
        )

    duplicate_timestamp = rows[9].model_copy(
        update={
            "source_row_id": "fixture:row:duplicate",
            "source_row_sha256": digest("duplicate-timestamp"),
        }
    )
    with pytest.raises(ReplayValidationError, match="strictly chronological"):
        replay_features_at(
            (*rows[:10], duplicate_timestamp, *rows[10:]),
            as_of=cutoff,
            bar_horizon=HORIZON,
        )

    with pytest.raises(ReplayValidationError, match="strictly chronological"):
        replay_features_at(
            (*rows[:10], rows[11], rows[10], *rows[12:]),
            as_of=cutoff,
            bar_horizon=HORIZON,
        )

    late = rows[-1].model_copy(
        update={"available_at": cutoff + timedelta(seconds=1)}
    )
    with pytest.raises(ReplayValidationError, match="not available"):
        replay_features_at(
            (*rows[:-1], late),
            as_of=cutoff,
            bar_horizon=HORIZON,
        )


def test_replay_requires_utc_aligned_cutoffs_and_sufficient_history() -> None:
    rows = replay_rows(32)

    with pytest.raises(ValueError, match="timezone-aware UTC"):
        replay_features_at(
            rows,
            as_of=datetime(2026, 1, 1),
            bar_horizon=HORIZON,
        )

    with pytest.raises(ReplayValidationError, match="feature dependencies"):
        replay_features_at(
            rows,
            as_of=rows[-1].bar.closed_at,
            bar_horizon=HORIZON,
            history_bars=20,
        )

    shifted = tuple(
        row.model_copy(
            update={
                "available_at": row.available_at + timedelta(microseconds=1),
                "bar": row.bar.model_copy(
                    update={
                        "closed_at": row.bar.closed_at
                        + timedelta(microseconds=1)
                    }
                ),
            }
        )
        for row in rows
    )
    with pytest.raises(ReplayValidationError, match="align"):
        replay_features_at(
            shifted,
            as_of=shifted[-1].bar.closed_at,
            bar_horizon=HORIZON,
        )


def test_walk_forward_requires_strict_cutoffs_and_hashes_each_snapshot() -> None:
    rows = replay_rows(40)
    cutoffs = (rows[29].bar.closed_at, rows[34].bar.closed_at)

    result = replay_features_walk_forward(
        rows,
        cutoffs=cutoffs,
        bar_horizon=HORIZON,
        history_bars=24,
    )

    assert tuple(item.as_of for item in result) == cutoffs
    assert result[0].replay_sha256 != result[1].replay_sha256

    with pytest.raises(ReplayValidationError, match="strictly increasing"):
        replay_features_walk_forward(
            rows,
            cutoffs=(cutoffs[0], cutoffs[0]),
            bar_horizon=HORIZON,
        )


def test_forward_label_remains_hidden_until_outcome_is_available() -> None:
    rows = list(replay_rows(40))
    base = rows[29]
    target_index = 31
    target = rows[target_index]
    delayed = target.model_copy(
        update={"available_at": target.bar.closed_at + timedelta(minutes=3)}
    )
    rows[target_index] = delayed

    with pytest.raises(ReplayValidationError, match="before it became available"):
        forward_direction_label(
            rows,
            feature_cutoff=base.bar.closed_at,
            read_at=target.bar.closed_at,
            bar_horizon=HORIZON,
            outcome_horizon_seconds=HORIZON.seconds * 2,
        )

    label = forward_direction_label(
        rows,
        feature_cutoff=base.bar.closed_at,
        read_at=delayed.available_at,
        bar_horizon=HORIZON,
        outcome_horizon_seconds=HORIZON.seconds * 2,
    )

    expected_return = delayed.bar.close / base.bar.close - D("1")
    assert label.forward_return == expected_return
    assert label.positive is True
    assert label.feature_cutoff == base.bar.closed_at
    assert label.outcome_at == delayed.bar.closed_at
    assert label.available_at == delayed.available_at
    assert label.runtime_consumers == 0
    assert label.execution_authority is False


def test_forward_label_requires_exact_aligned_boundary_rows() -> None:
    rows = replay_rows(40)

    with pytest.raises(ReplayValidationError, match="align"):
        forward_direction_label(
            rows,
            feature_cutoff=rows[29].bar.closed_at,
            read_at=rows[35].bar.closed_at,
            bar_horizon=HORIZON,
            outcome_horizon_seconds=HORIZON.seconds + 1,
        )

    with pytest.raises(ReplayValidationError, match="exact boundary"):
        forward_direction_label(
            rows,
            feature_cutoff=rows[-1].bar.closed_at,
            read_at=rows[-1].bar.closed_at + timedelta(days=1),
            bar_horizon=HORIZON,
            outcome_horizon_seconds=HORIZON.seconds,
        )
