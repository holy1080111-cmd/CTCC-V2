"""Disposable PG seal/schedule join; intentionally stops before public I/O.

The source and window are synthetic. This exercises real restricted PostgreSQL
readback of DB0030/0029/ACK, not DB0035 or a real scheduled collection.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

import app.mie.validation.scheduled_public_minute as owner
from app.database.repositories.public_receipt_post_read_observation import (
    PublicReceiptPostReadRepository,
)
from app.database.repositories.public_receipt_witness import (
    PublicReceiptWitnessRepository,
)
from app.mie.validation.gate3_preregistration_seal_observer import (
    Gate3PreregistrationSealObserver,
)
from app.mie.validation.prospective_capture_schedule import build_capture_schedule
from app.mie.validation.public_checkpoint_service import (
    _ISSUER,
    PublicCheckpointCaptureService,
)
from tests.integration.test_gate3_canonical_schedule_claim import publish_or_diagnose
from tests.integration.test_gate3_committed_preregistration_seal import (
    append_seal,
    seal_record,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
pytest_plugins = ("tests.integration.test_gate3_committed_preregistration_seal",)


async def test_exact_committed_seal_and_schedule_still_wait_before_due(
    isolated_prereg_database, monkeypatch
):
    (
        seal,
        original_schedule,
        _,
        db_engine,
        seal_engine,
        observer_engine,
        _,
        pin_repo,
        pin_ack_repo,
    ) = isolated_prereg_database
    coordinate = original_schedule.coordinate_plan
    await append_seal(seal_engine, seal_record(seal, original_schedule))
    seal_observer = Gate3PreregistrationSealObserver(
        async_sessionmaker(observer_engine, expire_on_commit=False)
    )
    seal_ack = await seal_observer.acknowledge(
        expected_seal_sha256=seal.canonical_sha256(),
        expected_coordinate_plan_sha256=coordinate.canonical_sha256(),
        coordinate_plan=coordinate,
    )
    schedule = build_capture_schedule(
        seal=seal,
        coordinate_plan=coordinate,
        planned_at=seal_ack.database_readback_at,
    )
    await publish_or_diagnose(pin_repo, schedule, seal)
    await pin_ack_repo.acknowledge(
        expected_schedule_sha256=schedule.canonical_sha256(), seal=seal
    )
    monkeypatch.setattr(
        owner,
        "native_stamp",
        lambda: {"utc_ns": schedule.plans[0].end_ns - 1},
    )

    async def forbidden_capture(_self, **_kwargs):
        pytest.fail("a future minute must not trigger any public I/O")

    monkeypatch.setattr(
        PublicCheckpointCaptureService,
        "capture_with_post_read_v3",
        forbidden_capture,
    )
    result = (
        await owner.capture_scheduled_public_minute_once(
            seal_observer=seal_observer,
            schedule_repository=pin_ack_repo,
            capture_service=PublicCheckpointCaptureService(
                _ISSUER, None, None, None, None
            ),
            observation_repository=PublicReceiptPostReadRepository(
                None, PublicReceiptWitnessRepository(None)
            ),
            coordinate_plan=coordinate,
            expected_coordinate_plan_sha256=coordinate.canonical_sha256(),
            expected_seal_sha256=seal.canonical_sha256(),
            expected_schedule_sha256=schedule.canonical_sha256(),
            ordinal=0,
        )
    ).contract
    assert result.status == "incomplete"
    assert result.reason_code == "not_due"
    assert result.capture_id is None
    assert result.predictive_oos_eligible is False
    assert result.execution_authority is False
    async with db_engine.connect() as connection:
        assert (
            await connection.scalar(
                text("SELECT count(*) FROM public.gate3_preregistration_seals")
            )
            == 1
        )
        assert (
            await connection.scalar(
                text("SELECT count(*) FROM public.gate3_capture_schedule_pins")
            )
            == 1
        )
