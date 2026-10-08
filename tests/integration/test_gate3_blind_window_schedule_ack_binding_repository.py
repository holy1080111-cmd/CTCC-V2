"""Disposable PostgreSQL 0026/0028 and original-byte journal join.

The four minute rows and their source packets are synthetic. This exercises
the restricted database roles and immutable readback, not real Gate 3 custody,
availability, evaluator access, predictive OOS, or execution authority.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.mie.validation.blind_window_schedule_ack_binding import (
    BlindWindowScheduleAckBindingError,
    freeze_schedule_bound_blind_window_dataset_v2,
    verify_schedule_bound_blind_window_dataset_v2,
)
from app.mie.validation.gate3_capture_schedule_pin import (
    Gate3CaptureSchedulePinRepository,
)
from app.mie.validation.gate3_capture_schedule_publication_ack import (
    Gate3CaptureSchedulePublicationAckRepository,
)
from tests.integration.test_gate3_schedule_publication_ack import isolated_database
from tests.unit.mie.test_gate3_blind_window_schedule_binding import binding_case

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio,
    pytest.mark.usefixtures(isolated_database.name),
]


async def test_restricted_pg_ack_binds_complete_original_byte_window(
    isolated_database, monkeypatch
):
    pin_repository, ack_repository, _, pin_engine, ack_engine, _ = isolated_database
    now = datetime.now(UTC).replace(microsecond=0)
    start = (now + timedelta(minutes=30)).replace(second=0)
    args, _, schedule, dataset = binding_case(
        monkeypatch, start=start, planned_at=now - timedelta(minutes=1)
    )
    seal = args["preregistration"]
    schedule_sha256 = schedule.canonical_sha256()
    assert args["expected_schedule_sha256"] == schedule_sha256

    bound_args = {
        "ack_repository": ack_repository,
        **{key: value for key, value in args.items() if key != "repository"},
    }
    await pin_repository.verify_role()
    await ack_repository.verify_role()
    pin = await pin_repository.publish(schedule=schedule, seal=seal)
    assert pin.schedule_sha256 == schedule_sha256
    with pytest.raises(
        BlindWindowScheduleAckBindingError, match="^schedule_ack_unavailable$"
    ):
        await freeze_schedule_bound_blind_window_dataset_v2(**bound_args)

    ack = await ack_repository.acknowledge(
        expected_schedule_sha256=schedule_sha256, seal=seal
    )
    assert pin.recorded_at <= ack.acknowledged_at < start
    assert ack.schedule_recorded_at == pin.recorded_at
    frozen = await freeze_schedule_bound_blind_window_dataset_v2(**bound_args)
    assert frozen.contract.matched_plan_count == len(dataset.rows) == 4
    assert frozen.contract.dataset_rows_sha256 == dataset.rows_sha256
    assert frozen.contract.pin_commit_observed_at == ack.acknowledged_at
    assert frozen.contract.server_clock_ordered_committed_pin_observation is True
    assert frozen.contract.trusted_clock_verified is False
    assert frozen.contract.independently_protected is False
    assert frozen.contract.evaluator_first_read_proven is False
    assert frozen.contract.predictive_oos_eligible is False
    assert frozen.contract.promotion_eligible is False
    assert frozen.contract.execution_authority is False

    # Reconstruct both restricted pools and replay the immutable database rows
    # against every original journal byte, rather than trusting the artifact.
    restarted_pin_engine = create_async_engine(pin_engine.url, poolclass=NullPool)
    restarted_ack_engine = create_async_engine(ack_engine.url, poolclass=NullPool)
    try:
        restarted_pin = Gate3CaptureSchedulePinRepository(
            async_sessionmaker(restarted_pin_engine, expire_on_commit=False)
        )
        restarted_ack = Gate3CaptureSchedulePublicationAckRepository(
            async_sessionmaker(restarted_ack_engine, expire_on_commit=False),
            restarted_pin,
        )
        replay_args = {**bound_args, "ack_repository": restarted_ack}
        verified = await verify_schedule_bound_blind_window_dataset_v2(
            frozen.payload, expected_sha256=frozen.sha256, **replay_args
        )
        assert verified == frozen.contract

        # A missing original journal segment cannot borrow a valid pin/ACK.
        with pytest.raises(
            BlindWindowScheduleAckBindingError,
            match="^blind_dataset_replay_failed$",
        ):
            await verify_schedule_bound_blind_window_dataset_v2(
                frozen.payload,
                expected_sha256=frozen.sha256,
                **{**replay_args, "segments": args["segments"][:-1]},
            )

        # A new capture identity cannot use another window's durable ACK.
        with pytest.raises(
            BlindWindowScheduleAckBindingError, match="^schedule_ack_unavailable$"
        ):
            await freeze_schedule_bound_blind_window_dataset_v2(
                **{**replay_args, "expected_schedule_sha256": "f" * 64}
            )
    finally:
        await restarted_ack_engine.dispose()
        await restarted_pin_engine.dispose()
