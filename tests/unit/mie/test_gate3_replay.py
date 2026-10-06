from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext

import pytest
from pydantic import ValidationError

from app.mie.contracts import ForecastHorizon
from app.mie.features import FeatureBar
from app.mie.validation import (
    ForwardDirectionLabelV2,
    FrozenFeatureReplayPlanV2,
    PointInTimeBar,
    ReplayValidationError,
    forward_direction_label,
    forward_direction_label_v2,
    replay_features_at,
    replay_features_walk_forward,
)

D = Decimal
START = datetime(2026, 1, 1, tzinfo=UTC)
HORIZON = ForecastHorizon(label="15m", seconds=900)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical_digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


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

    late = rows[-1].model_copy(update={"available_at": cutoff + timedelta(seconds=1)})
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
            as_of=datetime(2026, 1, 1),  # noqa: DTZ001 - Deliberately naive input verifies rejection, never accepted source time.
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
                    update={"closed_at": row.bar.closed_at + timedelta(microseconds=1)}
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


def v2_feature_plan() -> FrozenFeatureReplayPlanV2:
    return FrozenFeatureReplayPlanV2(
        bar_horizon=HORIZON,
        outcome_horizon_seconds=HORIZON.seconds * 2,
        positive_threshold=D("0"),
        history_bars=24,
        signal_alpha=D("0.25"),
        dynamics_window=21,
        momentum_fast_bars=5,
        momentum_slow_bars=20,
        pivot_left_bars=2,
        pivot_right_bars=2,
    )


def delayed_v2_source():
    rows = list(replay_rows(40))
    base = rows[29]
    target = rows[31]
    decision = base.bar.closed_at + timedelta(minutes=3)
    read = target.bar.closed_at + timedelta(minutes=4)
    rows[29] = base.model_copy(update={"available_at": decision})
    rows[31] = target.model_copy(update={"available_at": read})
    return tuple(rows), decision, read


def v2_label(rows, decision, read, *, snapshot=None, plan=None):
    frozen = v2_feature_plan() if plan is None else plan
    replay = (
        replay_features_at(
            rows,
            as_of=decision,
            bar_horizon=HORIZON,
            history_bars=frozen.history_bars,
        )
        if snapshot is None
        else snapshot
    )
    return forward_direction_label_v2(
        rows,
        replay_snapshot=replay,
        feature_plan=frozen,
        expected_feature_plan_sha256=frozen.canonical_sha256,
        decision_at=decision,
        read_at=read,
        bar_horizon=HORIZON,
        outcome_horizon_seconds=HORIZON.seconds * 2,
    )


def test_v2_label_keeps_actual_decision_and_outcome_receipts_separate() -> None:
    rows, decision, read = delayed_v2_source()
    label = v2_label(rows, decision, read)
    repeated = v2_label(rows, decision, read)

    assert type(label) is ForwardDirectionLabelV2
    assert label == repeated
    assert label.base_bar_closed_at == rows[29].bar.closed_at
    assert label.base_available_at == label.decision_at == decision
    assert label.outcome_at == rows[31].bar.closed_at
    assert label.outcome_available_at == label.read_at == read
    assert label.feature_plan_sha256 == v2_feature_plan().canonical_sha256
    assert label.base_bar_sha256 == canonical_digest(
        rows[29].bar.model_dump(mode="json")
    )
    assert label.outcome_bar_sha256 == canonical_digest(
        rows[31].bar.model_dump(mode="json")
    )
    assert label.outcome_window_rows_sha256 == canonical_digest(
        [row.model_dump(mode="json") for row in rows[29:32]]
    )
    assert ForwardDirectionLabelV2.model_validate_json(label.model_dump_json()) == label
    assert (
        label.feature_source_rows_sha256
        == replay_features_at(
            rows,
            as_of=decision,
            bar_horizon=HORIZON,
            history_bars=24,
        ).source_rows_sha256
    )
    assert label.authority == "offline_label_only"
    assert label.runtime_consumers == 0
    assert label.execution_authority is False
    frozen_snapshot = replay_features_at(
        rows,
        as_of=decision,
        bar_horizon=HORIZON,
        history_bars=24,
    )
    with localcontext() as context:
        context.prec = 4
        assert v2_label(rows, decision, read, snapshot=frozen_snapshot) == label
    payload = label.model_dump(mode="python")
    payload["execution_authority"] = True
    with pytest.raises(ValidationError):
        ForwardDirectionLabelV2.model_validate(payload)

    # The old API deliberately retains its exact bar-close decision semantics.
    with pytest.raises(ReplayValidationError, match="causally available"):
        forward_direction_label(
            rows,
            feature_cutoff=rows[29].bar.closed_at,
            read_at=read,
            bar_horizon=HORIZON,
            outcome_horizon_seconds=HORIZON.seconds * 2,
        )


def test_v2_label_rejects_decision_before_receipt_or_after_next_close() -> None:
    rows, decision, read = delayed_v2_source()
    snapshot = replay_features_at(
        rows,
        as_of=decision,
        bar_horizon=HORIZON,
        history_bars=24,
    )

    with pytest.raises(ReplayValidationError, match="decision time differs"):
        v2_label(
            rows,
            rows[29].bar.closed_at,
            read,
            snapshot=snapshot,
        )
    late_decision = rows[30].bar.closed_at
    late_snapshot = snapshot.model_copy(
        update={
            "as_of": late_decision,
            "feature_snapshot": snapshot.feature_snapshot.model_copy(
                update={"as_of": late_decision}
            ),
        }
    )
    with pytest.raises(ReplayValidationError, match="next bar close"):
        v2_label(rows, late_decision, read, snapshot=late_snapshot)


def test_v2_label_rejects_unavailable_or_missing_outcome() -> None:
    rows, decision, read = delayed_v2_source()
    snapshot = replay_features_at(
        rows,
        as_of=decision,
        bar_horizon=HORIZON,
        history_bars=24,
    )

    with pytest.raises(ReplayValidationError, match="before it became available"):
        v2_label(rows, decision, read - timedelta(microseconds=1), snapshot=snapshot)
    late_intermediate = list(rows)
    late_intermediate[30] = rows[30].model_copy(
        update={"available_at": read + timedelta(seconds=1)}
    )
    with pytest.raises(ReplayValidationError, match="before it became available"):
        v2_label(tuple(late_intermediate), decision, read, snapshot=snapshot)
    with pytest.raises(ReplayValidationError, match="exact boundary rows"):
        v2_label(
            (*rows[:31], *rows[32:]),
            decision,
            read,
            snapshot=snapshot,
        )


def test_v2_label_recomputes_pinned_snapshot_from_original_source() -> None:
    rows, decision, read = delayed_v2_source()
    snapshot = replay_features_at(
        rows,
        as_of=decision,
        bar_horizon=HORIZON,
        history_bars=24,
    )
    changed = list(rows)
    changed[20] = rows[20].model_copy(
        update={"source_row_sha256": digest("revised-source-row-20")}
    )
    with pytest.raises(ReplayValidationError, match="differs from original source"):
        v2_label(tuple(changed), decision, read, snapshot=snapshot)

    forged_snapshot = snapshot.model_copy(update={"source_rows_sha256": "0" * 64})
    with pytest.raises(ReplayValidationError, match="differs from original source"):
        v2_label(rows, decision, read, snapshot=forged_snapshot)

    changed_parameters = v2_feature_plan().model_copy(update={"signal_alpha": D("0.5")})
    with pytest.raises(ReplayValidationError, match="differs from original source"):
        v2_label(
            rows,
            decision,
            read,
            snapshot=snapshot,
            plan=changed_parameters,
        )

    frozen = v2_feature_plan()
    with pytest.raises(ReplayValidationError, match="plan pin changed"):
        forward_direction_label_v2(
            rows,
            replay_snapshot=snapshot,
            feature_plan=frozen,
            expected_feature_plan_sha256="0" * 64,
            decision_at=decision,
            read_at=read,
            bar_horizon=HORIZON,
            outcome_horizon_seconds=HORIZON.seconds * 2,
        )


def test_v2_label_definition_is_frozen_before_outcome_read() -> None:
    rows, decision, read = delayed_v2_source()
    frozen = v2_feature_plan()
    snapshot = replay_features_at(
        rows,
        as_of=decision,
        bar_horizon=HORIZON,
        history_bars=24,
    )
    common = {
        "replay_snapshot": snapshot,
        "feature_plan": frozen,
        "expected_feature_plan_sha256": frozen.canonical_sha256,
        "decision_at": decision,
        "read_at": read,
        "bar_horizon": HORIZON,
    }
    with pytest.raises(ReplayValidationError, match="definition differs"):
        forward_direction_label_v2(
            rows,
            outcome_horizon_seconds=HORIZON.seconds * 3,
            **common,
        )
    with pytest.raises(ReplayValidationError, match="definition differs"):
        forward_direction_label_v2(
            rows,
            outcome_horizon_seconds=HORIZON.seconds * 2,
            positive_threshold=D("0.01"),
            **common,
        )
    different_bar = v2_feature_plan().model_copy(
        update={"bar_horizon": ForecastHorizon(label="900s", seconds=900)}
    )
    with pytest.raises(ReplayValidationError, match="definition differs"):
        forward_direction_label_v2(
            rows,
            replay_snapshot=snapshot,
            feature_plan=different_bar,
            expected_feature_plan_sha256=different_bar.canonical_sha256,
            decision_at=decision,
            read_at=read,
            bar_horizon=HORIZON,
            outcome_horizon_seconds=HORIZON.seconds * 2,
        )


def test_v2_label_ignores_future_mutations_beyond_frozen_outcome() -> None:
    rows, decision, read = delayed_v2_source()
    snapshot = replay_features_at(
        rows,
        as_of=decision,
        bar_horizon=HORIZON,
        history_bars=24,
    )
    changed = list(rows)
    changed[35] = rows[35].model_copy(
        update={"source_row_sha256": digest("future-row-after-outcome")}
    )
    assert v2_label(rows, decision, read, snapshot=snapshot) == v2_label(
        tuple(changed), decision, read, snapshot=snapshot
    )


def test_v2_label_hashes_actual_outcome_content_even_if_claimed_hash_is_reused() -> (
    None
):
    rows, decision, read = delayed_v2_source()
    snapshot = replay_features_at(
        rows,
        as_of=decision,
        bar_horizon=HORIZON,
        history_bars=24,
    )
    original = v2_label(rows, decision, read, snapshot=snapshot)
    changed = list(rows)
    changed[31] = rows[31].model_copy(
        update={
            "bar": rows[31].bar.model_copy(
                update={"close": rows[31].bar.close + D("0.001")}
            )
        }
    )
    revised = v2_label(tuple(changed), decision, read, snapshot=snapshot)

    assert revised.outcome_row_sha256 == original.outcome_row_sha256
    assert revised.outcome_bar_sha256 != original.outcome_bar_sha256
    assert revised.outcome_window_rows_sha256 != original.outcome_window_rows_sha256
    assert revised.forward_return != original.forward_return
    assert revised.base_bar_sha256 == original.base_bar_sha256

    changed[30] = rows[30].model_copy(
        update={
            "bar": rows[30].bar.model_copy(
                update={"close": rows[30].bar.close + D("0.001")}
            )
        }
    )
    intermediate = v2_label(tuple(changed), decision, read, snapshot=snapshot)
    assert intermediate.outcome_bar_sha256 == revised.outcome_bar_sha256
    assert intermediate.outcome_window_rows_sha256 != revised.outcome_window_rows_sha256
