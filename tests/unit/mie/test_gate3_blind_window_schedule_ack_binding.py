"""Synthetic ACK and original-byte joins; no real prospective custody claim."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.mie.validation.artifact import ArtifactVerificationError
from app.mie.validation.blind_window_schedule_ack_binding import (
    BlindWindowScheduleAckBindingError,
    ScheduleBoundBlindWindowDatasetV2,
    freeze_schedule_bound_blind_window_dataset_v2,
    verify_schedule_bound_blind_window_dataset_v2,
)
from app.mie.validation.blind_window_schedule_binding import (
    BlindWindowScheduleBindingError,
)
from app.mie.validation.gate3_capture_schedule_publication_ack import (
    Gate3CaptureSchedulePublicationAckRepository,
    Gate3SchedulePublicationAckError,
    Gate3SchedulePublicationAckReadback,
)
from tests.unit.mie.test_gate3_blind_window_schedule_binding import binding_case

pytestmark = pytest.mark.asyncio


def ack_case(monkeypatch, *, late_plan: bool = False):
    start = datetime(2026, 10, 1, tzinfo=UTC)
    last_plan_created_ns = (
        int((start + timedelta(seconds=30)).timestamp()) * 1_000_000_000
        if late_plan
        else None
    )
    args, pin, schedule, dataset = binding_case(
        monkeypatch, last_plan_created_ns=last_plan_created_ns
    )
    ack = Gate3SchedulePublicationAckReadback(
        schedule_sha256=pin.schedule_sha256,
        seal_sha256=pin.seal_sha256,
        coordinate_plan_sha256=pin.coordinate_plan_sha256,
        window_key=pin.window_key,
        holdout_id=schedule.coordinate_plan.holdout_id,
        schedule_recorded_at=pin.recorded_at,
        acknowledged_at=pin.recorded_at + timedelta(seconds=1),
        # The durable ACK is re-read after the window. Its observation time,
        # not the later read time, establishes DB-server-clock ordering.
        database_readback_at=start + timedelta(days=2),
        schedule=schedule,
    )
    repository = Gate3CaptureSchedulePublicationAckRepository(
        object(), args["repository"]
    )
    calls = {"read": 0}

    async def read(*, expected_schedule_sha256, seal):
        assert expected_schedule_sha256 == pin.schedule_sha256
        assert seal == args["preregistration"]
        calls["read"] += 1
        return ack

    async def forbidden_acknowledge(**_):
        raise AssertionError("binding replay must not create an ACK")

    monkeypatch.setattr(repository, "read", read)
    monkeypatch.setattr(repository, "acknowledge", forbidden_acknowledge)
    return (
        {
            "ack_repository": repository,
            **{k: v for k, v in args.items() if k != "repository"},
        },
        ack,
        dataset,
        calls,
    )


@pytest.fixture
def case(monkeypatch):
    return ack_case(monkeypatch)


@pytest.fixture
def late_case(monkeypatch):
    return ack_case(monkeypatch, late_plan=True)


async def test_exact_ack_binds_original_byte_minutes_without_promotion(case):
    args, ack, dataset, calls = case
    frozen = await freeze_schedule_bound_blind_window_dataset_v2(**args)
    assert frozen.contract.pin_commit_observed_at == ack.acknowledged_at
    assert frozen.contract.matched_plan_count == len(dataset.rows) == 4
    assert frozen.contract.dataset_rows_sha256 == dataset.rows_sha256
    assert frozen.contract.server_clock_ordered_committed_pin_observation is True
    assert frozen.contract.trusted_clock_verified is False
    assert frozen.contract.independently_protected is False
    assert frozen.contract.evaluator_first_read_proven is False
    assert frozen.contract.predictive_oos_eligible is False
    assert frozen.contract.promotion_eligible is False
    assert frozen.contract.execution_authority is False
    checked = await verify_schedule_bound_blind_window_dataset_v2(
        frozen.payload, expected_sha256=frozen.sha256, **args
    )
    assert checked == frozen.contract
    assert calls["read"] == 2


async def test_later_ack_readback_does_not_rewrite_frozen_observation(
    case, monkeypatch
):
    args, ack, _, _ = case
    frozen = await freeze_schedule_bound_blind_window_dataset_v2(**args)

    async def later_read(**_):
        return replace(
            ack, database_readback_at=ack.database_readback_at + timedelta(days=1)
        )

    monkeypatch.setattr(args["ack_repository"], "read", later_read)
    assert (
        await verify_schedule_bound_blind_window_dataset_v2(
            frozen.payload, expected_sha256=frozen.sha256, **args
        )
        == frozen.contract
    )


async def test_missing_ack_fails_despite_valid_pin_and_dataset(case, monkeypatch):
    args, _, _, _ = case

    async def missing(**_):
        raise Gate3SchedulePublicationAckError("schedule_ack_missing_or_duplicate")

    monkeypatch.setattr(args["ack_repository"], "read", missing)
    with pytest.raises(
        BlindWindowScheduleAckBindingError, match="^schedule_ack_unavailable$"
    ):
        await freeze_schedule_bound_blind_window_dataset_v2(**args)


async def test_failed_ack_readback_redacts_internal_details(case, monkeypatch):
    args, _, _, _ = case

    async def failed(**_):
        raise RuntimeError("private database connection detail")

    monkeypatch.setattr(args["ack_repository"], "read", failed)
    with pytest.raises(
        BlindWindowScheduleAckBindingError, match="^schedule_ack_unavailable$"
    ) as caught:
        await freeze_schedule_bound_blind_window_dataset_v2(**args)
    assert caught.value.__cause__ is None


@pytest.mark.parametrize(
    "change",
    [
        {"schedule_sha256": "0" * 64},
        {"seal_sha256": "0" * 64},
        {"coordinate_plan_sha256": "0" * 64},
        {"window_key": "0" * 64},
        {"holdout_id": "wrong-holdout"},
        {"schedule_recorded_at": datetime(2026, 9, 30, tzinfo=UTC)},
        {"acknowledged_at": datetime(2026, 10, 1, tzinfo=UTC)},
        {"database_readback_at": datetime(2026, 9, 30, tzinfo=UTC)},
    ],
)
async def test_ack_identity_or_chronology_mismatch_fails(case, monkeypatch, change):
    args, ack, _, _ = case

    async def changed(**_):
        return replace(ack, **change)

    monkeypatch.setattr(args["ack_repository"], "read", changed)
    with pytest.raises(
        BlindWindowScheduleAckBindingError,
        match="^schedule_ack_dataset_identity_mismatch$",
    ):
        await freeze_schedule_bound_blind_window_dataset_v2(**args)


async def test_ack_noncanonical_schedule_with_same_claimed_hash_fails(
    case, monkeypatch
):
    args, ack, _, _ = case
    changed_schedule = ack.schedule.model_copy(
        update={"planned_at": ack.schedule.planned_at - timedelta(seconds=1)}
    )

    async def changed(**_):
        return replace(ack, schedule=changed_schedule)

    monkeypatch.setattr(args["ack_repository"], "read", changed)
    with pytest.raises(
        BlindWindowScheduleAckBindingError,
        match="^schedule_ack_dataset_identity_mismatch$",
    ):
        await freeze_schedule_bound_blind_window_dataset_v2(**args)


async def test_late_created_capture_plan_cannot_borrow_early_ack(late_case):
    args, _, _, _ = late_case
    with pytest.raises(BlindWindowScheduleBindingError, match="schedule_plan_mismatch"):
        await freeze_schedule_bound_blind_window_dataset_v2(**args)


async def test_rehashed_capture_receipt_pin_cannot_borrow_original_journal(case):
    args, _, _, _ = case
    segment = args["segments"][0]
    forged_pin = replace(segment.capture_pins[0], receipt_sha256="0" * 64)
    forged_segment = replace(
        segment, capture_pins=(forged_pin, *segment.capture_pins[1:])
    )
    with pytest.raises(
        BlindWindowScheduleBindingError, match="^blind_dataset_replay_failed$"
    ):
        await freeze_schedule_bound_blind_window_dataset_v2(
            **{**args, "segments": (forged_segment, *args["segments"][1:])}
        )


async def test_rehashed_v2_payload_cannot_change_source_join(case):
    args, _, _, _ = case
    frozen = await freeze_schedule_bound_blind_window_dataset_v2(**args)
    changed = ScheduleBoundBlindWindowDatasetV2.model_validate(
        {**frozen.contract.model_dump(mode="python"), "matched_plan_count": 3}
    )
    payload = changed.canonical_json_bytes()
    with pytest.raises(ArtifactVerificationError, match="differs from source replay"):
        await verify_schedule_bound_blind_window_dataset_v2(
            payload, expected_sha256=hashlib.sha256(payload).hexdigest(), **args
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("server_clock_ordered_committed_pin_observation", False),
        ("trusted_clock_verified", True),
        ("independently_protected", True),
        ("evaluator_first_read_proven", True),
        ("predictive_oos_eligible", True),
        ("promotion_eligible", True),
        ("execution_authority", True),
        ("runtime_consumers", 1),
    ],
)
async def test_v2_flags_cannot_be_promoted(case, field, value):
    args, _, _, _ = case
    frozen = await freeze_schedule_bound_blind_window_dataset_v2(**args)
    with pytest.raises(ValidationError):
        ScheduleBoundBlindWindowDatasetV2.model_validate(
            {**frozen.contract.model_dump(mode="python"), field: value}
        )


async def test_non_tuple_segments_and_wrong_repository_fail_closed(case):
    args, _, _, _ = case
    with pytest.raises(
        BlindWindowScheduleAckBindingError,
        match="^schedule_ack_binding_inputs_invalid$",
    ):
        await freeze_schedule_bound_blind_window_dataset_v2(
            **{**args, "segments": list(args["segments"])}
        )
    with pytest.raises(
        BlindWindowScheduleAckBindingError,
        match="^schedule_ack_binding_inputs_invalid$",
    ):
        await freeze_schedule_bound_blind_window_dataset_v2(
            **{**args, "ack_repository": object()}
        )
