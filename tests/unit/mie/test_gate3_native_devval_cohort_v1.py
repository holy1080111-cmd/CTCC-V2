"""Synthetic cohort boundary tests; no real clock, PostgreSQL or OOS proof."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest
from pydantic import ValidationError

import app.mie.validation.native_devval_cohort_v1 as cohort
from app.mie.validation.artifact import ArtifactVerificationError
from app.mie.validation.batch_replay import MinuteAggregationPlan
from app.mie.validation.contracts import DatasetPartition, PartitionWindow
from app.mie.validation.post_read_batch_v3 import (
    PostReadCaptureInputV3,
    PostReadMinuteV3,
    _build,
    capture_chain_sha256_v3,
)
from app.mie.validation.post_read_stitch_v1 import PostReadBatchInputV1
from tests.unit.mie.test_gate3_post_read_batch_v3 import _minutes

pytest_plugins = ("tests.unit.mie.test_gate3_post_publication_availability_v3",)

START = datetime(2024, 1, 1, tzinfo=UTC)
SYMBOLS = ("BTC-USDT-SWAP", "ETH-USDT-SWAP")


def _sha(label: str) -> str:
    return sha256(label.encode()).hexdigest()


def _batch(group: int, original: tuple[PostReadMinuteV3, ...]):
    partition = (
        DatasetPartition.DEVELOPMENT if group < 2 else DatasetPartition.VALIDATION
    )
    instrument = SYMBOLS[group % 2]
    offset = timedelta(days=group // 2)
    minutes = []
    for ordinal, minute in enumerate(original, start=1):
        data = minute.model_dump(mode="python")
        for field in (
            "capture_v3_sha256",
            "capture_v2_sha256",
            "journal_genesis_sha256",
            "witness_record_sha256",
            "checkpoint_sha256",
            "plan_sha256",
            "receipt_sha256",
            "source_row_identity",
            "raw_page_sha256",
        ):
            data[field] = _sha(f"{group}:{field}:{ordinal}")
        data["capture_id"] = f"{group * 240 + ordinal:032x}"
        for field in (
            "validation_complete_at",
            "payload_readback_at",
            "witness_recorded_at",
            "v2_witness_observed_at",
            "persisted_observed_at",
        ):
            data[field] += offset
        data["row"]["source_row_id"] = (
            "okx-public-minute:" + data["source_row_identity"]
        )
        data["row"]["source_row_sha256"] = _sha(f"{group}:row:{ordinal}")
        data["row"]["instrument_id"] = instrument
        data["row"]["available_at"] += offset
        data["row"]["bar"]["closed_at"] += offset
        minutes.append(PostReadMinuteV3.model_validate(data))
    minutes = tuple(minutes)
    window = PartitionWindow(
        partition=partition,
        start_at=START + offset,
        end_at=START + offset + timedelta(hours=4),
    )
    plan = MinuteAggregationPlan(
        source_manifest_sha256=capture_chain_sha256_v3(
            tuple(item.capture_v3_sha256 for item in minutes)
        ),
        window=window,
        instrument_id=instrument,
        expected_minute_rows=240,
        volume_unit="contracts",
    )
    return _build(minutes, plan=plan, expected_plan_sha256=plan.canonical_sha256())


@pytest.fixture(scope="module")
def synthetic_batches():
    original = _minutes()
    return tuple(_batch(group, original) for group in range(4))


@pytest.fixture
def cohort_case(monkeypatch, observation_case, synthetic_batches):
    source_kwargs, _, repository, _ = observation_case
    journal = source_kwargs["journal"]
    witness_repository = source_kwargs["witness_repository"]
    known = {}
    inputs = []
    for batch in synthetic_batches:
        payload = batch.canonical_json_bytes()
        digest = sha256(payload).hexdigest()
        sources = tuple(
            PostReadCaptureInputV3(
                payload=b"synthetic-only",
                expected_sha256=minute.capture_v3_sha256,
                journal=journal,
                witness_repository=witness_repository,
                capture_id=minute.capture_id,
                expected_plan_sha256=minute.plan_sha256,
                expected_receipt_sha256=minute.receipt_sha256,
                expected_journal_genesis_sha256=minute.journal_genesis_sha256,
                expected_capture_checkpoint_sha256=minute.checkpoint_sha256,
                expected_witness_revision=minute.witness_revision,
                expected_witness_record_sha256=minute.witness_record_sha256,
            )
            for minute in batch.minutes
        )
        inputs.append(
            PostReadBatchInputV1(
                payload=payload,
                expected_sha256=digest,
                sources=sources,
                plan=batch.plan,
                expected_plan_sha256=batch.plan_sha256,
            )
        )
        known[digest] = (payload, batch)

    async def source_reverify(
        payload,
        *,
        expected_sha256,
        sources,
        observation_repository,
        plan,
        expected_plan_sha256,
    ):
        if (
            expected_sha256 not in known
            or known[expected_sha256][0] != payload
            or len(sources) != 240
            or observation_repository is not repository
            or plan.canonical_sha256() != expected_plan_sha256
        ):
            raise ArtifactVerificationError("synthetic V3 source changed")
        return known[expected_sha256][1]

    monkeypatch.setattr(cohort, "verify_post_read_minute_batch_v3", source_reverify)
    plan = cohort.NativeDevValCohortPlanV1(
        instrument_ids=SYMBOLS,
        development=synthetic_batches[0].plan.window,
        validation=synthetic_batches[2].plan.window,
        development_cutoff_at=START + timedelta(hours=4, seconds=30),
        validation_cutoff_at=START + timedelta(days=1, hours=4, seconds=30),
    )
    kwargs = {
        "batches": tuple(inputs),
        "plan": plan,
        "expected_plan_sha256": plan.canonical_sha256(),
        "observation_repository": repository,
    }
    return kwargs, known


@pytest.mark.asyncio
async def test_four_original_groups_replay_deterministically_but_remain_denied(
    cohort_case,
):
    kwargs, _ = cohort_case
    first = await cohort.freeze_native_devval_cohort_v1(**kwargs)
    second = await cohort.freeze_native_devval_cohort_v1(**kwargs)
    readback = await cohort.verify_native_devval_cohort_v1(
        first.payload, expected_sha256=first.sha256, **kwargs
    )
    assert first.payload == second.payload
    assert first.sha256 == second.sha256 == readback.canonical_sha256()
    assert [(item.partition, item.instrument_id) for item in readback.groups] == [
        (DatasetPartition.DEVELOPMENT, SYMBOLS[0]),
        (DatasetPartition.DEVELOPMENT, SYMBOLS[1]),
        (DatasetPartition.VALIDATION, SYMBOLS[0]),
        (DatasetPartition.VALIDATION, SYMBOLS[1]),
    ]
    assert readback.total_rows == 960
    assert readback.late_rows == 0
    assert readback.status == "DENY"
    assert readback.predictive_oos_eligible is False
    assert readback.candidate_seal_eligible is False
    assert readback.execution_authority is False


@pytest.mark.asyncio
async def test_late_source_receipt_is_counted_and_cannot_be_repaired_by_a_flag(
    cohort_case,
):
    kwargs, _ = cohort_case
    late_plan = kwargs["plan"].model_copy(
        update={
            "development_cutoff_at": kwargs["plan"].development.end_at,
            "validation_cutoff_at": kwargs["plan"].validation.end_at,
        }
    )
    changed = dict(
        kwargs, plan=late_plan, expected_plan_sha256=late_plan.canonical_sha256()
    )
    frozen = await cohort.freeze_native_devval_cohort_v1(**changed)
    assert frozen.contract.late_rows == 4
    assert "post_read_late_for_cutoff" in frozen.contract.reasons
    assert frozen.contract.status == "DENY"
    with pytest.raises(ValidationError):
        cohort.NativeDevValCohortAdmissionV1.model_validate(
            dict(
                frozen.contract.model_dump(mode="python"), predictive_oos_eligible=True
            )
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["missing", "reordered", "duplicate_pin"])
async def test_missing_reordered_or_duplicate_group_fails_before_source_readback(
    cohort_case, damage
):
    kwargs, _ = cohort_case
    batches = kwargs["batches"]
    if damage == "missing":
        batches = batches[:-1]
    elif damage == "reordered":
        batches = (batches[1], batches[0], *batches[2:])
    else:
        batches = (
            batches[0],
            replace(batches[1], expected_sha256=batches[0].expected_sha256),
            *batches[2:],
        )
    with pytest.raises(
        cohort.NativeDevValCohortError, match="exact_inputs|source_grid"
    ):
        await cohort.freeze_native_devval_cohort_v1(**dict(kwargs, batches=batches))


@pytest.mark.asyncio
async def test_cross_group_capture_identity_reuse_is_rejected(
    cohort_case, synthetic_batches
):
    kwargs, known = cohort_case
    first_id = synthetic_batches[0].minutes[0].capture_id
    second = synthetic_batches[1]
    changed_minute = second.minutes[0].model_copy(update={"capture_id": first_id})
    changed_batch = _build(
        (changed_minute, *second.minutes[1:]),
        plan=second.plan,
        expected_plan_sha256=second.plan_sha256,
    )
    payload = changed_batch.canonical_json_bytes()
    digest = sha256(payload).hexdigest()
    known[digest] = (payload, changed_batch)
    old_input = kwargs["batches"][1]
    new_input = replace(
        old_input,
        payload=payload,
        expected_sha256=digest,
        sources=(
            replace(old_input.sources[0], capture_id=first_id),
            *old_input.sources[1:],
        ),
    )
    changed = dict(
        kwargs,
        batches=(kwargs["batches"][0], new_input, *kwargs["batches"][2:]),
    )
    with pytest.raises(cohort.NativeDevValCohortError, match="global_identity_reused"):
        await cohort.freeze_native_devval_cohort_v1(**changed)


@pytest.mark.asyncio
async def test_changed_original_batch_or_conflict_cannot_read_back(cohort_case):
    kwargs, known = cohort_case
    frozen = await cohort.freeze_native_devval_cohort_v1(**kwargs)
    first = kwargs["batches"][0]
    damaged = (replace(first, payload=first.payload + b" "), *kwargs["batches"][1:])
    with pytest.raises(ArtifactVerificationError, match="source changed"):
        await cohort.verify_native_devval_cohort_v1(
            frozen.payload,
            expected_sha256=frozen.sha256,
            **dict(kwargs, batches=damaged),
        )
    del known[first.expected_sha256]
    with pytest.raises(ArtifactVerificationError, match="source changed"):
        await cohort.verify_native_devval_cohort_v1(
            frozen.payload, expected_sha256=frozen.sha256, **kwargs
        )
