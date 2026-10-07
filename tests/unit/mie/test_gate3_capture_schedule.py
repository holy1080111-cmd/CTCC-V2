"""Synthetic contract tests only; no real pre-window pin or source capture."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.mie.validation.gate3_capture_schedule_pin import (
    Gate3CaptureSchedulePinRepository,
    Gate3SchedulePinError,
    Gate3SchedulePinReadback,
)
from app.mie.validation.prospective import Gate3ProspectivePreregistration
from app.mie.validation.prospective_capture_schedule import (
    ProspectiveCaptureScheduleV1,
    ProspectiveCoordinatePlanV1,
    build_capture_schedule,
    checked_capture_schedule,
)
from tests.unit.mie.test_gate3_prospective import valid_prospective_preregistration


def valid_schedule(
    *, start: datetime | None = None
) -> tuple[Gate3ProspectivePreregistration, ProspectiveCaptureScheduleV1]:
    now = datetime.now(UTC).replace(microsecond=0)
    if start is None:
        # Keep a generous future window for slow PostgreSQL CI while the seal
        # and planned time still precede the actual server observation.
        current = now + timedelta(minutes=10)
        start = current.replace(second=0, microsecond=0)
    planned_at = min(start - timedelta(minutes=2), now - timedelta(minutes=2))
    end = start + timedelta(minutes=2)
    coordinates = ProspectiveCoordinatePlanV1(
        holdout_id="okx:synthetic:future:2m",
        origin="https://openapi.okx.com",
        instrument_ids=("BTC-USDT-SWAP", "ETH-USDT-SWAP"),
        start_at=start,
        end_at=end,
        expected_rows=4,
    )
    raw = valid_prospective_preregistration().model_dump(mode="python")
    raw["created_at"] = planned_at - timedelta(minutes=2)
    raw["prospective_holdout"].update(
        holdout_id=coordinates.holdout_id,
        source=coordinates.source,
        source_version=coordinates.source_version,
        instrument_ids=coordinates.instrument_ids,
        coordinate_plan_sha256=coordinates.canonical_sha256(),
        bar_interval_seconds=60,
        artifact_interval_seconds=60,
        start_at=start,
        end_at=end,
        publication_lag_seconds=60,
        first_permitted_access_at=end + timedelta(minutes=1),
        expected_artifact_count=4,
        expected_rows=4,
    )
    seal = Gate3ProspectivePreregistration.model_validate(raw)
    schedule = build_capture_schedule(
        seal=seal,
        coordinate_plan=coordinates,
        planned_at=planned_at,
    )
    return seal, schedule


def test_exact_future_grid_and_replay_without_claim_promotion():
    seal, schedule = valid_schedule()
    assert (
        seal.created_at
        < schedule.planned_at
        <= datetime.now(UTC)
        < schedule.coordinate_plan.start_at
    )
    assert tuple((plan.instrument_id, plan.start_ns) for plan in schedule.plans) == (
        ("BTC-USDT-SWAP", schedule.plans[0].start_ns),
        ("ETH-USDT-SWAP", schedule.plans[0].start_ns),
        ("BTC-USDT-SWAP", schedule.plans[0].end_ns),
        ("ETH-USDT-SWAP", schedule.plans[0].end_ns),
    )
    assert all(plan.created_ns < plan.start_ns for plan in schedule.plans)
    replayed = ProspectiveCaptureScheduleV1.model_validate_json(
        schedule.canonical_json_bytes()
    )
    assert checked_capture_schedule(replayed, seal=seal) == schedule
    assert replayed.canonical_sha256() == schedule.canonical_sha256()
    assert not schedule.predictive_oos_eligible
    assert not schedule.execution_authority


@pytest.mark.parametrize(
    "mutation",
    ("missing", "duplicate", "reordered", "different_source", "late_plan"),
)
def test_changed_minute_or_source_cannot_be_revalidated(mutation):
    seal, schedule = valid_schedule()
    plans = list(schedule.plans)
    if mutation == "missing":
        plans.pop()
    elif mutation == "duplicate":
        plans[1] = plans[0]
    elif mutation == "reordered":
        plans[0], plans[2] = plans[2], plans[0]
    elif mutation == "different_source":
        plans[0] = plans[0].model_copy(update={"origin": "https://us.okx.com"})
    else:
        plans[0] = plans[0].model_copy(update={"created_ns": plans[0].start_ns})
    damaged = schedule.model_copy(update={"plans": tuple(plans)})
    with pytest.raises((ValueError, ValidationError)):
        checked_capture_schedule(damaged, seal=seal)


def test_seal_change_or_claim_forgery_is_rejected():
    seal, schedule = valid_schedule()
    changed = seal.model_copy(update={"preregistration_id": "new:identifier:v1"})
    with pytest.raises(ValueError, match="differs from future-window seal"):
        checked_capture_schedule(schedule, seal=changed)
    payload = schedule.model_dump(mode="python")
    payload["predictive_oos_eligible"] = True
    with pytest.raises(ValidationError):
        ProspectiveCaptureScheduleV1.model_validate(payload)


def test_coordinate_plan_is_materialized_before_seal_and_plan_after_it():
    seal, schedule = valid_schedule()
    assert (
        schedule.coordinate_plan_sha256
        == seal.prospective_holdout.coordinate_plan_sha256
    )
    assert seal.created_at < schedule.planned_at < schedule.coordinate_plan.start_at
    shifted = schedule.model_copy(
        update={"planned_at": seal.created_at - timedelta(seconds=1)}
    )
    with pytest.raises((ValueError, ValidationError)):
        checked_capture_schedule(shifted, seal=seal)


def test_post_commit_database_readback_must_still_precede_window():
    seal, schedule = valid_schedule()
    start = schedule.coordinate_plan.start_at
    record = Gate3SchedulePinReadback(
        schedule_sha256=schedule.canonical_sha256(),
        seal_sha256=seal.canonical_sha256(),
        coordinate_plan_sha256=schedule.coordinate_plan_sha256,
        window_key=schedule.coordinate_plan.window_key(),
        recorded_at=schedule.planned_at,
        database_readback_at=start - timedelta(microseconds=1),
        schedule=schedule,
    )
    assert (
        Gate3CaptureSchedulePinRepository._timely_publication_readback(record) is record
    )
    with pytest.raises(
        Gate3SchedulePinError, match="schedule_publication_readback_late"
    ):
        Gate3CaptureSchedulePinRepository._timely_publication_readback(
            replace(record, database_readback_at=start)
        )
