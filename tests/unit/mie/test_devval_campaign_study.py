"""Synthetic full-grid controls for the inert D/V candidate study seam.

The measured-basis rows here are fixtures, not original exchange receipts.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from app.mie.contracts import ForecastHorizon
from app.mie.validation.prospective_devval_campaign_batch_v1 import _build_batch
from app.mie.validation.prospective_devval_campaign_study_v1 import (
    DevvalCampaignStudyError,
    DevvalCampaignStudyPlanV1,
    _prepare_study,
)
from app.mie.validation.replay import FrozenFeatureReplayPlanV2
from tests.unit.mie.test_devval_campaign_batch import full_grid


@pytest.fixture(scope="module")
def synthetic_batch():
    campaign, rows, pins = full_grid.__wrapped__()
    return _build_batch(campaign, rows, **pins)


def _feature_plan(horizon: str = "1H") -> FrozenFeatureReplayPlanV2:
    seconds = {"1H": 3_600, "4H": 14_400}[horizon]
    return FrozenFeatureReplayPlanV2(
        bar_horizon=ForecastHorizon(label=horizon, seconds=seconds),
        outcome_horizon_seconds=seconds,
        positive_threshold=Decimal(0),
        history_bars=21,
        signal_alpha=Decimal("0.25"),
        dynamics_window=21,
        momentum_fast_bars=5,
        momentum_slow_bars=20,
        pivot_left_bars=2,
        pivot_right_bars=2,
    )


def _study_plan(batch, *, horizon: str = "1H", feature_delay: int = 2):
    feature = _feature_plan(horizon)
    return DevvalCampaignStudyPlanV1(
        campaign_sha256=batch.contract.campaign_sha256,
        feature_plan=feature,
        minimum_training_events=5,
        fold_validation_events=4,
        minimum_final_validation_events=5,
        feature_dependency_seconds=feature.history_bars * feature.bar_horizon.seconds
        + feature_delay,
        label_dependency_seconds=feature.outcome_horizon_seconds + 2,
        purge_seconds=feature.history_bars * feature.bar_horizon.seconds + 3_604,
        embargo_seconds=feature.history_bars * feature.bar_horizon.seconds + 3_604,
        step_events=4,
        maximum_folds=2,
    )


def test_same_time_two_symbol_features_labels_and_purged_folds(synthetic_batch):
    plan = _study_plan(synthetic_batch)
    frozen = _prepare_study(
        synthetic_batch, plan=plan, expected_plan_sha256=plan.canonical_sha256()
    )
    study = frozen.contract
    assert study.batch_sha256 == synthetic_batch.sha256
    assert study.completion_sha256 == synthetic_batch.contract.completion_sha256
    assert not study.predictive_oos_eligible and not study.execution_authority
    assert len(study.development) == len(study.validation) == 2 * 63
    assert len(study.development_folds) == 2

    for partition in (study.development, study.validation):
        times = []
        for left, right in zip(partition[::2], partition[1::2], strict=True):
            assert (left.label.instrument_id, right.label.instrument_id) == (
                "BTC-USDT-SWAP",
                "ETH-USDT-SWAP",
            )
            assert left.label.decision_at == right.label.decision_at
            times.append(left.label.decision_at)
            for row in (left, right):
                assert row.feature.as_of == row.label.decision_at
                assert row.feature.data_cutoff == row.label.base_bar_closed_at
                assert row.feature.source_row_count == 21
                assert row.label.base_available_at <= row.label.decision_at
                assert row.label.decision_at < row.label.base_bar_closed_at + timedelta(
                    hours=1
                )
                assert row.label.outcome_at == row.label.base_bar_closed_at + timedelta(
                    hours=1
                )
                assert row.label.outcome_available_at <= row.label.read_at
        assert times == sorted(set(times))

    for fold in study.development_folds:
        assert fold.training_indices and fold.validation_indices
        assert not set(fold.training_indices) & set(fold.validation_indices)
        assert max((*fold.training_indices, *fold.validation_indices)) < len(
            study.development
        )
        for indices in (fold.training_indices, fold.validation_indices):
            assert len(indices) % 2 == 0
            assert all(
                indices[offset + 1] == indices[offset] + 1 and indices[offset] % 2 == 0
                for offset in range(0, len(indices), 2)
            )


def test_four_hour_minimum_window_has_no_forward_label(synthetic_batch):
    plan = _study_plan(synthetic_batch, horizon="4H")
    with pytest.raises(DevvalCampaignStudyError, match="window_insufficient"):
        _prepare_study(
            synthetic_batch, plan=plan, expected_plan_sha256=plan.canonical_sha256()
        )


def test_changed_study_plan_pin_is_refused_before_research(synthetic_batch):
    plan = _study_plan(synthetic_batch)
    with pytest.raises(DevvalCampaignStudyError, match="pins_changed"):
        _prepare_study(synthetic_batch, plan=plan, expected_plan_sha256="0" * 64)


def test_actual_one_second_receipt_delay_cannot_be_underdeclared(synthetic_batch):
    plan = _study_plan(synthetic_batch, feature_delay=0)
    with pytest.raises(DevvalCampaignStudyError, match="dependency_understated"):
        _prepare_study(
            synthetic_batch, plan=plan, expected_plan_sha256=plan.canonical_sha256()
        )
