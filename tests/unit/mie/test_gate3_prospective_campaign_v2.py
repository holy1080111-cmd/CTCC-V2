"""Synthetic campaign geometry only; no source capture or trusted seal custody."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from functools import lru_cache

import pytest
from pydantic import ValidationError

from app.mie.contracts import ForecastHorizon
from app.mie.validation.artifact import ArtifactVerificationError
from app.mie.validation.prospective import Gate3ProspectivePreregistration
from app.mie.validation.prospective_campaign_v2 import (
    MAX_CAMPAIGN_ROWS,
    MAX_CHILD_ROWS,
    ProspectiveCampaignError,
    ProspectiveCampaignV2,
    freeze_prospective_campaign_v2,
    verify_prospective_campaign_v2,
)
from app.mie.validation.prospective_capture_schedule import (
    ProspectiveCaptureScheduleV1,
    ProspectiveCoordinatePlanV1,
    build_capture_schedule,
)
from app.mie.validation.replay import FrozenFeatureReplayPlanV2
from tests.unit.mie.test_gate3_prospective import valid_prospective_preregistration

D = Decimal
FIRST_START = datetime(2027, 1, 1, tzinfo=UTC)
DECLARED = datetime(2026, 11, 15, tzinfo=UTC)
FEATURE_SECONDS = 21 * 14_400


def feature_plan() -> FrozenFeatureReplayPlanV2:
    return FrozenFeatureReplayPlanV2(
        bar_horizon=ForecastHorizon(label="4H", seconds=14_400),
        outcome_horizon_seconds=14_400,
        positive_threshold=D("0"),
        history_bars=21,
        signal_alpha=D("0.25"),
        dynamics_window=21,
        momentum_fast_bars=5,
        momentum_slow_bars=20,
        pivot_left_bars=2,
        pivot_right_bars=2,
    )


def child(
    ordinal: int,
    *,
    rows: int = MAX_CHILD_ROWS,
    start: datetime | None = None,
    feature_sha256: str | None = None,
) -> tuple[Gate3ProspectivePreregistration, ProspectiveCaptureScheduleV1]:
    start = start or FIRST_START + timedelta(minutes=ordinal * MAX_CHILD_ROWS // 2)
    end = start + timedelta(minutes=rows // 2)
    plan = feature_plan()
    feature_sha256 = feature_sha256 or plan.canonical_sha256
    coordinate = ProspectiveCoordinatePlanV1(
        holdout_id=f"okx:campaign:child:{ordinal}",
        origin="https://openapi.okx.com",
        instrument_ids=("BTC-USDT-SWAP", "ETH-USDT-SWAP"),
        start_at=start,
        end_at=end,
        expected_rows=rows,
    )
    payload = valid_prospective_preregistration().model_dump(mode="python")
    payload["preregistration_id"] = f"gate3:campaign:child:{ordinal}"
    payload["created_at"] = DECLARED - timedelta(days=1)
    payload["features"][0]["dependency_seconds"] = FEATURE_SECONDS
    payload["features"][0]["parameters"] = (
        {"name": "feature_plan_sha256", "value": feature_sha256},
        {"name": "history_bars", "value": 21},
    )
    payload["selection_split"]["development"]["end_at"] = datetime(
        2026, 4, 1, tzinfo=UTC
    )
    payload["selection_split"]["validation"]["start_at"] = datetime(
        2026, 4, 5, tzinfo=UTC
    )
    payload["selection_split"].update(
        purge_seconds=FEATURE_SECONDS,
        embargo_seconds=FEATURE_SECONDS,
        max_feature_dependency_seconds=FEATURE_SECONDS,
        label_dependency_seconds=14_400,
    )
    payload["outcome_label"].update(
        horizon_seconds=14_400,
        dependency_seconds=14_400,
    )
    payload["prospective_holdout"].update(
        holdout_id=coordinate.holdout_id,
        source=coordinate.source,
        source_version=coordinate.source_version,
        instrument_ids=coordinate.instrument_ids,
        coordinate_plan_sha256=coordinate.canonical_sha256(),
        bar_interval_seconds=60,
        artifact_interval_seconds=60,
        start_at=start,
        end_at=end,
        publication_lag_seconds=60,
        first_permitted_access_at=end + timedelta(minutes=1),
        expected_artifact_count=rows,
        expected_rows=rows,
    )
    seal = Gate3ProspectivePreregistration.model_validate(payload)
    schedule = build_capture_schedule(
        seal=seal, coordinate_plan=coordinate, planned_at=DECLARED
    )
    return seal, schedule


@lru_cache(maxsize=1)
def full_children() -> tuple[
    tuple[Gate3ProspectivePreregistration, ProspectiveCaptureScheduleV1], ...
]:
    return tuple(child(ordinal) for ordinal in range(3))


@lru_cache(maxsize=1)
def full_campaign():
    plan = feature_plan()
    return freeze_prospective_campaign_v2(
        campaign_id="gate3:campaign:synthetic:v2",
        children=full_children(),
        feature_plan=plan,
        expected_feature_plan_sha256=plan.canonical_sha256,
    )


def test_current_v1_capacity_cannot_hold_four_hour_history_and_label() -> None:
    assert 4_096 < 21 * 240 * 2
    assert 10_000 < (21 + 1) * 240 * 2
    assert MAX_CHILD_ROWS == 3_840
    assert MAX_CAMPAIGN_ROWS == 61_440


def test_three_complete_v1_children_freeze_only_computational_campaign() -> None:
    frozen = full_campaign()
    plan = feature_plan()
    assert frozen.contract.expected_rows == 11_520
    assert len(frozen.contract.children) == 3
    assert frozen.contract.children[2].first_row == 7_680
    assert frozen.contract.current_claim.value == "computational"
    assert not frozen.contract.pre_window_commit_proven
    assert not frozen.contract.complete_source_capture_proven
    assert not frozen.contract.predictive_oos_eligible
    assert not frozen.contract.execution_authority
    assert (
        verify_prospective_campaign_v2(
            frozen.payload,
            expected_sha256=frozen.sha256,
            campaign_id=frozen.contract.campaign_id,
            children=full_children(),
            feature_plan=plan,
            expected_feature_plan_sha256=plan.canonical_sha256,
        ).canonical_json_bytes()
        == frozen.payload
    )


@pytest.mark.parametrize(
    "mutation", ("missing", "reordered", "overlap", "changed_seal")
)
def test_missing_overlapping_or_changed_child_rejected(mutation: str) -> None:
    base = full_campaign()
    children = list(full_children())
    if mutation == "missing":
        children.pop(1)
    elif mutation == "reordered":
        children[0], children[1] = children[1], children[0]
    elif mutation == "overlap":
        children[1] = child(1, start=FIRST_START + timedelta(hours=28))
    else:
        seal, schedule = children[1]
        children[1] = (
            seal.model_copy(
                update={
                    "candidate": seal.candidate.model_copy(
                        update={"configuration_sha256": "0" * 64}
                    )
                }
            ),
            schedule,
        )
    plan = feature_plan()
    with pytest.raises((ValueError, ValidationError, ArtifactVerificationError)):
        verify_prospective_campaign_v2(
            base.payload,
            expected_sha256=base.sha256,
            campaign_id=base.contract.campaign_id,
            children=tuple(children),
            feature_plan=plan,
            expected_feature_plan_sha256=plan.canonical_sha256,
        )


def test_capacity_and_forged_eligibility_rejected() -> None:
    base = full_campaign().contract
    too_few = base.model_dump(mode="python")
    too_few["minimum_candidate_bars"] = 4
    with pytest.raises(ValidationError, match="cannot fit feature history"):
        ProspectiveCampaignV2.model_validate(too_few)
    outcome_tail = base.model_dump(mode="python")
    outcome_tail["outcome_horizon_seconds"] = 4 * 14_400
    with pytest.raises(ValidationError, match="cannot fit feature history"):
        ProspectiveCampaignV2.model_validate(outcome_tail)
    early_access = base.model_dump(mode="python")
    early_access["first_permitted_access_at"] = base.children[0].end_at
    with pytest.raises(ValidationError, match="global grid"):
        ProspectiveCampaignV2.model_validate(early_access)
    forged = base.model_dump(mode="python")
    forged["predictive_oos_eligible"] = True
    with pytest.raises(ValidationError):
        ProspectiveCampaignV2.model_validate(forged)


def test_changed_feature_plan_pin_and_child_plan_rejected() -> None:
    plan = feature_plan()
    with pytest.raises(ProspectiveCampaignError):
        freeze_prospective_campaign_v2(
            campaign_id="gate3:campaign:synthetic:v2",
            children=full_children(),
            feature_plan=plan,
            expected_feature_plan_sha256="0" * 64,
        )
    changed = list(full_children())
    changed[1] = child(1, feature_sha256="0" * 64)
    with pytest.raises(ProspectiveCampaignError):
        freeze_prospective_campaign_v2(
            campaign_id="gate3:campaign:synthetic:v2",
            children=tuple(changed),
            feature_plan=plan,
            expected_feature_plan_sha256=plan.canonical_sha256,
        )


def test_missing_minute_inside_child_rejected_before_campaign_freeze() -> None:
    children = list(full_children())
    seal, schedule = children[1]
    children[1] = (seal, schedule.model_copy(update={"plans": schedule.plans[:-1]}))
    plan = feature_plan()
    with pytest.raises((ValueError, ValidationError)):
        freeze_prospective_campaign_v2(
            campaign_id="gate3:campaign:synthetic:v2",
            children=tuple(children),
            feature_plan=plan,
            expected_feature_plan_sha256=plan.canonical_sha256,
        )
