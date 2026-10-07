"""Synthetic original-byte joins; no real capture, custody, or predictive claim."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.mie.validation import blind_window_schedule_binding as binding_module
from app.mie.validation.artifact import ArtifactVerificationError
from app.mie.validation.blind_window_dataset import bind_complete_blind_window_dataset
from app.mie.validation.blind_window_schedule_binding import (
    BlindWindowScheduleBindingError,
    ScheduleBoundBlindWindowDatasetV1,
    freeze_schedule_bound_blind_window_dataset,
    verify_schedule_bound_blind_window_dataset,
)
from app.mie.validation.gate3_capture_schedule_pin import (
    Gate3CaptureSchedulePinRepository,
    Gate3SchedulePinError,
    Gate3SchedulePinReadback,
)
from app.mie.validation.prospective import Gate3ProspectivePreregistration
from app.mie.validation.prospective_capture_schedule import (
    ProspectiveCoordinatePlanV1,
    build_capture_schedule,
)
from tests.unit.mie.test_gate3_blind_window_dataset import fixture_inputs

pytestmark = pytest.mark.asyncio


def binding_case(
    monkeypatch,
    *,
    start: datetime | None = None,
    planned_at: datetime | None = None,
    last_plan_created_ns=None,
):
    if start is None:
        start = datetime(2026, 10, 1, tzinfo=UTC)
    if planned_at is None:
        planned_at = start - timedelta(minutes=2)
    created_ns = int(planned_at.timestamp()) * 1_000_000_000
    inputs, _ = fixture_inputs(
        monkeypatch,
        start=start,
        plan_created_ns=created_ns,
        last_plan_created_ns=last_plan_created_ns,
    )
    original = inputs["preregistration"]
    coordinates = ProspectiveCoordinatePlanV1(
        holdout_id=original.prospective_holdout.holdout_id,
        origin="https://openapi.okx.com",
        instrument_ids=("BTC-USDT-SWAP", "ETH-USDT-SWAP"),
        start_at=start,
        end_at=start + timedelta(minutes=2),
        expected_rows=4,
    )
    raw = original.model_dump(mode="python")
    raw["prospective_holdout"]["coordinate_plan_sha256"] = (
        coordinates.canonical_sha256()
    )
    seal = Gate3ProspectivePreregistration.model_validate(raw)
    inputs.update(
        preregistration=seal,
        expected_preregistration_sha256=seal.canonical_sha256(),
    )
    schedule = build_capture_schedule(
        seal=seal, coordinate_plan=coordinates, planned_at=planned_at
    )
    dataset = bind_complete_blind_window_dataset(**inputs)
    dataset_payload = dataset.canonical_json_bytes()
    pin = Gate3SchedulePinReadback(
        schedule_sha256=schedule.canonical_sha256(),
        seal_sha256=seal.canonical_sha256(),
        coordinate_plan_sha256=coordinates.canonical_sha256(),
        window_key=coordinates.window_key(),
        recorded_at=planned_at + timedelta(seconds=1),
        # This readback intentionally occurs after the window. It must never
        # be represented as a pre-window publication acknowledgement.
        database_readback_at=start + timedelta(days=1),
        schedule=schedule,
    )
    repository = object.__new__(Gate3CaptureSchedulePinRepository)

    async def read(*, expected_schedule_sha256, seal):
        assert expected_schedule_sha256 == pin.schedule_sha256
        assert seal == inputs["preregistration"]
        return pin

    monkeypatch.setattr(repository, "read", read)
    return (
        {
            "repository": repository,
            "preregistration": seal,
            "expected_preregistration_sha256": seal.canonical_sha256(),
            "expected_schedule_sha256": schedule.canonical_sha256(),
            "dataset_payload": dataset_payload,
            "expected_dataset_sha256": hashlib.sha256(dataset_payload).hexdigest(),
            "segments": inputs["segments"],
        },
        pin,
        schedule,
        dataset,
    )


@pytest.fixture
def case(monkeypatch):
    return binding_case(monkeypatch)


@pytest.fixture
def late_plan_case(monkeypatch):
    start = datetime(2026, 10, 1, tzinfo=UTC)
    return binding_case(
        monkeypatch,
        last_plan_created_ns=int((start + timedelta(seconds=30)).timestamp())
        * 1_000_000_000,
    )


@pytest.fixture
def future_case(monkeypatch):
    start = (datetime.now(UTC) + timedelta(minutes=10)).replace(second=0, microsecond=0)
    planned_at = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=1)
    return binding_case(monkeypatch, start=start, planned_at=planned_at)


async def test_exact_pre_window_schedule_binds_all_original_byte_minutes(case):
    args, pin, _, dataset = case
    assert pin.database_readback_at > pin.schedule.coordinate_plan.start_at
    frozen = await freeze_schedule_bound_blind_window_dataset(**args)
    assert frozen.contract.schedule_recorded_at == pin.recorded_at
    assert frozen.contract.matched_plan_count == len(dataset.rows) == 4
    assert frozen.contract.dataset_rows_sha256 == dataset.rows_sha256
    assert frozen.contract.predictive_oos_eligible is False
    assert frozen.contract.independently_protected is False
    assert frozen.contract.pre_window_commit_proven is False
    assert frozen.contract.execution_authority is False
    # Re-reading the DB after the window changes observation time, but cannot
    # change the immutable insertion time or the frozen binding.
    checked = await verify_schedule_bound_blind_window_dataset(
        frozen.payload, expected_sha256=frozen.sha256, **args
    )
    assert checked == frozen.contract
    with pytest.raises(ValidationError):
        ScheduleBoundBlindWindowDatasetV1.model_validate(
            {
                **frozen.contract.model_dump(mode="python"),
                "pre_window_commit_proven": True,
            }
        )


async def test_future_synthetic_fixture_builds_complete_join_without_database(
    future_case,
):
    args, _, schedule, dataset = future_case
    assert schedule.planned_at < schedule.coordinate_plan.start_at
    assert len(dataset.rows) == len(schedule.plans) == 4
    frozen = await freeze_schedule_bound_blind_window_dataset(**args)
    assert frozen.contract.matched_plan_count == 4


async def test_last_minute_plan_created_during_window_fails_exact_pin_join(
    late_plan_case,
):
    args, _, schedule, dataset = late_plan_case
    assert dataset.rows[-1].capture_plan_sha256 != schedule.plans[-1].canonical_sha256()
    with pytest.raises(BlindWindowScheduleBindingError, match="schedule_plan_mismatch"):
        await freeze_schedule_bound_blind_window_dataset(**args)


async def test_missing_pin_or_late_insert_never_binds(case, monkeypatch):
    args, pin, _, _ = case

    async def missing(**_):
        raise Gate3SchedulePinError("schedule_pin_missing_or_duplicate")

    monkeypatch.setattr(args["repository"], "read", missing)
    with pytest.raises(
        BlindWindowScheduleBindingError, match="schedule_pin_unavailable"
    ):
        await freeze_schedule_bound_blind_window_dataset(**args)

    async def forged_late(**_):
        from dataclasses import replace

        return replace(pin, recorded_at=pin.schedule.coordinate_plan.start_at)

    monkeypatch.setattr(args["repository"], "read", forged_late)
    with pytest.raises(
        BlindWindowScheduleBindingError, match="schedule_dataset_identity_mismatch"
    ):
        await freeze_schedule_bound_blind_window_dataset(**args)

    async def exact_pin(**_):
        return pin

    monkeypatch.setattr(args["repository"], "read", exact_pin)
    with pytest.raises(
        BlindWindowScheduleBindingError, match="schedule_dataset_identity_mismatch"
    ):
        await freeze_schedule_bound_blind_window_dataset(
            **{**args, "expected_schedule_sha256": "0" * 64}
        )


async def test_rehashed_binding_cannot_substitute_plan_count(case):
    args, _, _, _ = case
    frozen = await freeze_schedule_bound_blind_window_dataset(**args)
    changed = ScheduleBoundBlindWindowDatasetV1.model_validate(
        {**frozen.contract.model_dump(mode="python"), "matched_plan_count": 3}
    )
    changed_payload = changed.canonical_json_bytes()
    with pytest.raises(ArtifactVerificationError, match="differs from source replay"):
        await verify_schedule_bound_blind_window_dataset(
            changed_payload,
            expected_sha256=hashlib.sha256(changed_payload).hexdigest(),
            **args,
        )


async def test_malformed_journal_shape_has_safe_failure_code(case, monkeypatch):
    args, _, _, _ = case

    def malformed_replay(*_, **__):
        raise KeyError("private journal path")

    monkeypatch.setattr(
        binding_module, "verify_complete_blind_window_dataset", malformed_replay
    )
    with pytest.raises(
        BlindWindowScheduleBindingError, match="^blind_dataset_replay_failed$"
    ) as caught:
        await freeze_schedule_bound_blind_window_dataset(**args)
    assert caught.value.__cause__ is None
