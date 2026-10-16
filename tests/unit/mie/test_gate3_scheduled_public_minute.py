"""Synthetic one-coordinate orchestration only; no market or DB access."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

import app.mie.validation.scheduled_public_minute as owner
from app.database.repositories.public_receipt_post_read_observation import (
    PublicReceiptPostReadRepository,
)
from app.database.repositories.public_receipt_witness import (
    PublicReceiptWitnessRepository,
)
from app.mie.validation.artifact import ArtifactVerificationError
from app.mie.validation.gate3_capture_schedule_pin import (
    Gate3CaptureSchedulePinRepository,
)
from app.mie.validation.gate3_capture_schedule_publication_ack import (
    Gate3CaptureSchedulePublicationAckRepository,
    Gate3SchedulePublicationAckReadback,
)
from app.mie.validation.gate3_preregistration_seal_observer import (
    Gate3CommittedSealAckReadback,
    Gate3CommittedSealReadback,
    Gate3PreregistrationSealObserver,
)
from app.mie.validation.public_checkpoint_service import (
    _ISSUER,
    OwnedPostReadCapture,
    PublicCheckpointCaptureService,
    WitnessedPublicCapture,
)
from app.public_market_source.public_market_receipts import utc_from_ns
from tests.unit.mie.test_gate3_capture_schedule import valid_schedule

pytestmark = pytest.mark.asyncio


@pytest.fixture
def case(monkeypatch):
    seal, schedule = valid_schedule()
    coordinate = schedule.coordinate_plan
    seal_sha = seal.canonical_sha256()
    schedule_sha = schedule.canonical_sha256()
    ack_at = schedule.planned_at - timedelta(seconds=10)
    recorded_at = ack_at - timedelta(seconds=10)
    observer = Gate3PreregistrationSealObserver(None)
    pin_repo = Gate3CaptureSchedulePinRepository(None)
    schedule_repo = Gate3CaptureSchedulePublicationAckRepository(None, pin_repo)
    witness_repo = PublicReceiptWitnessRepository(None)
    observation_repo = PublicReceiptPostReadRepository(None, witness_repo)
    capture = PublicCheckpointCaptureService(_ISSUER, None, None, None, None)
    state = {"seal_reads": 0, "ack_reads": 0, "schedule_reads": 0, "captures": 0}
    seal_read = Gate3CommittedSealReadback(
        seal_sha256=seal_sha,
        coordinate_plan_sha256=coordinate.canonical_sha256(),
        window_key=coordinate.window_key(),
        seal_recorded_at=recorded_at,
        database_readback_at=recorded_at + timedelta(seconds=1),
        seal=seal,
    )
    seal_ack = Gate3CommittedSealAckReadback(
        seal_sha256=seal_sha,
        seal_recorded_at=recorded_at,
        acknowledged_at=ack_at,
        database_readback_at=ack_at + timedelta(seconds=1),
    )
    schedule_ack = Gate3SchedulePublicationAckReadback(
        schedule_sha256=schedule_sha,
        seal_sha256=seal_sha,
        coordinate_plan_sha256=coordinate.canonical_sha256(),
        window_key=coordinate.window_key(),
        holdout_id=coordinate.holdout_id,
        schedule_recorded_at=schedule.planned_at,
        acknowledged_at=schedule.planned_at + timedelta(seconds=1),
        database_readback_at=schedule.planned_at + timedelta(seconds=2),
        schedule=schedule,
    )
    state["schedule_ack"] = schedule_ack

    async def read_seal(**_kwargs):
        state["seal_reads"] += 1
        return seal_read

    async def read_ack(**_kwargs):
        state["ack_reads"] += 1
        return seal_ack

    async def read_schedule(**_kwargs):
        state["schedule_reads"] += 1
        return state["schedule_ack"]

    async def capture_one(_self, **_kwargs):
        state["captures"] += 1
        due = utc_from_ns(schedule.plans[0].end_ns + 1_000_000_000)
        return OwnedPostReadCapture(
            witnessed=WitnessedPublicCapture(
                capture_id="a" * 32,
                receipt_sha256="b" * 64,
                witness_revision=3,
                witness_record_sha256="c" * 64,
                checkpoint_sha256="d" * 64,
            ),
            blind_capture_sha256="e" * 64,
            post_read_v3_sha256="f" * 64,
            post_read_observed_at=due + timedelta(seconds=1),
        )

    monkeypatch.setattr(observer, "read_seal", read_seal)
    monkeypatch.setattr(observer, "read_ack", read_ack)
    monkeypatch.setattr(schedule_repo, "read", read_schedule)
    monkeypatch.setattr(
        PublicCheckpointCaptureService, "capture_with_post_read_v3", capture_one
    )
    monkeypatch.setattr(
        owner,
        "native_stamp",
        lambda: {"utc_ns": schedule.plans[0].end_ns + 1_000_000_000},
    )
    kwargs = {
        "seal_observer": observer,
        "schedule_repository": schedule_repo,
        "capture_service": capture,
        "observation_repository": observation_repo,
        "coordinate_plan": coordinate,
        "expected_coordinate_plan_sha256": coordinate.canonical_sha256(),
        "expected_seal_sha256": seal_sha,
        "expected_schedule_sha256": schedule_sha,
        "ordinal": 0,
    }
    return kwargs, state, schedule


async def test_one_due_coordinate_yields_hash_only_computational_receipt(case):
    kwargs, state, schedule = case
    frozen = await owner.capture_scheduled_public_minute_once(**kwargs)
    result = owner.verify_scheduled_public_minute_receipt(
        frozen.payload, expected_sha256=frozen.sha256
    )
    assert result.status == "incomplete"
    assert result.reason_code == "source_chain_replayed_custody_unproven"
    assert result.schema_version == "ctcc.mie.gate3.scheduled_public_minute.v2"
    assert result.due_observed_utc_ns == schedule.plans[0].end_ns + 1_000_000_000
    assert result.plan_sha256 == schedule.plans[0].canonical_sha256()
    assert result.post_read_v3_sha256 == "f" * 64
    assert result.blind_capture_sha256 == "e" * 64
    assert not result.predictive_oos_eligible
    assert not result.promotion_eligible
    assert not result.execution_authority
    assert result.runtime_consumers == 0
    assert state["captures"] == 1
    assert state["seal_reads"] == state["ack_reads"] == state["schedule_reads"] == 1
    with pytest.raises(ArtifactVerificationError):
        owner.verify_scheduled_public_minute_receipt(
            frozen.payload + b" ", expected_sha256=frozen.sha256
        )


async def test_native_due_stamp_must_match_display_time(case):
    kwargs, _, _ = case
    receipt = (await owner.capture_scheduled_public_minute_once(**kwargs)).contract
    fields = receipt.model_dump(mode="python")
    with pytest.raises(ValueError, match="native due timestamp differs"):
        owner.ScheduledPublicMinuteReceiptV2.model_validate(
            {**fields, "due_observed_utc_ns": fields["due_observed_utc_ns"] + 1}
        )
    with pytest.raises(ValueError, match="must pair"):
        owner.ScheduledPublicMinuteReceiptV2.model_validate(
            {**fields, "due_observed_utc_ns": None}
        )


@pytest.mark.parametrize("offset,reason", [(-1, "not_due"), (60_000_000_000, "late")])
async def test_not_due_or_late_never_calls_capture(case, monkeypatch, offset, reason):
    kwargs, state, schedule = case
    monkeypatch.setattr(
        owner,
        "native_stamp",
        lambda: {"utc_ns": schedule.plans[0].end_ns + offset},
    )
    result = (await owner.capture_scheduled_public_minute_once(**kwargs)).contract
    assert result.status == "incomplete"
    assert result.reason_code == reason
    assert result.capture_id is None
    assert state["captures"] == 0


async def test_changed_ack_fails_before_clock_or_capture(case, monkeypatch):
    kwargs, state, _ = case
    state["schedule_ack"] = replace(state["schedule_ack"], seal_sha256="0" * 64)
    monkeypatch.setattr(
        owner,
        "native_stamp",
        lambda: pytest.fail("clock must not be sampled after bad DB readback"),
    )
    result = (await owner.capture_scheduled_public_minute_once(**kwargs)).contract
    assert result.status == "incomplete"
    assert result.reason_code == "preflight_mismatch"
    assert state["captures"] == 0


async def test_uncertain_capture_result_is_not_retried(case, monkeypatch):
    kwargs, state, _ = case

    async def uncertain(_self, **_kwargs):
        state["captures"] += 1
        raise TimeoutError("database commit result may be unknown")

    monkeypatch.setattr(
        PublicCheckpointCaptureService, "capture_with_post_read_v3", uncertain
    )
    result = (await owner.capture_scheduled_public_minute_once(**kwargs)).contract
    assert result.status == "incomplete"
    assert result.reason_code == "capture_or_post_read_unresolved"
    assert state["captures"] == 1
    assert result.post_read_v3_sha256 is None


async def test_foreign_or_raised_claim_cannot_create_complete_receipt(case):
    kwargs, _, _ = case
    invalid = owner.ScheduledPublicMinuteReceiptV1(
        seal_sha256="a" * 64,
        coordinate_plan_sha256="b" * 64,
        schedule_sha256="c" * 64,
        holdout_id="synthetic:test",
        ordinal=0,
        status="incomplete",
        reason_code="preflight_unavailable",
    )
    with pytest.raises(ValueError):
        owner.ScheduledPublicMinuteReceiptV1.model_validate(
            {**invalid.model_dump(mode="python"), "predictive_oos_eligible": True}
        )
    assert kwargs["coordinate_plan"].start_at > datetime.now(UTC)
