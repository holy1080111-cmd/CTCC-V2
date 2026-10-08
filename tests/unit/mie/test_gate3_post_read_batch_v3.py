"""Synthetic computational V3 batch tests; no PostgreSQL or OOS claim."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from hashlib import sha256

import pytest
from pydantic import ValidationError

from app.mie.features import FeatureBar
from app.mie.validation.artifact import ArtifactVerificationError
from app.mie.validation.batch_replay import MinuteAggregationPlan
from app.mie.validation.contracts import DatasetPartition, PartitionWindow
from app.mie.validation.post_publication_availability_v3 import (
    freeze_post_publication_capture_v3,
)
from app.mie.validation.post_read_batch_v3 import (
    PostReadBatchError,
    PostReadCaptureInputV3,
    PostReadMinuteBatchV3,
    PostReadMinuteV3,
    _build,
    _verified_minute,
    capture_chain_sha256_v3,
    computational_rows_at_cutoff_v3,
    freeze_post_read_minute_batch_v3,
    verify_post_read_minute_batch_v3,
)
from app.mie.validation.replay import PointInTimeBar

START = datetime(2024, 1, 1, tzinfo=UTC)
pytest_plugins = ("tests.unit.mie.test_gate3_post_publication_availability_v3",)


def _digest(label: str, ordinal: int) -> str:
    return sha256(f"{label}:{ordinal}".encode()).hexdigest()


def _minutes(count: int = 240) -> tuple[PostReadMinuteV3, ...]:
    result = []
    for ordinal in range(1, count + 1):
        closed = START + timedelta(minutes=ordinal)
        price = Decimal(100) + Decimal(ordinal) / 100
        identity = _digest("identity", ordinal)
        observed = closed + timedelta(seconds=10 if ordinal == count else 5)
        result.append(
            PostReadMinuteV3(
                capture_v3_sha256=_digest("capture-v3", ordinal),
                capture_v2_sha256=_digest("capture-v2", ordinal),
                journal_genesis_sha256=_digest("journal", ordinal),
                witness_revision=3,
                witness_record_sha256=_digest("witness", ordinal),
                checkpoint_sha256=_digest("checkpoint", ordinal),
                capture_id=f"{ordinal:032x}",
                plan_sha256=_digest("capture-plan", ordinal),
                receipt_sha256=_digest("receipt", ordinal),
                source_row_identity=identity,
                raw_page_sha256=_digest("raw", ordinal),
                validation_complete_at=closed + timedelta(seconds=1),
                payload_readback_at=closed + timedelta(seconds=2),
                witness_recorded_at=closed + timedelta(seconds=3),
                v2_witness_observed_at=closed + timedelta(seconds=4),
                persisted_observed_at=observed,
                row=PointInTimeBar(
                    source_row_id="okx-public-minute:" + identity,
                    source_row_sha256=_digest("row", ordinal),
                    instrument_id="BTC-USDT-SWAP",
                    available_at=observed,
                    bar=FeatureBar(
                        closed_at=closed,
                        open=price,
                        high=price + 1,
                        low=price - 1,
                        close=price + Decimal("0.25"),
                        volume=Decimal("0.01234567890123456789"),
                    ),
                ),
            )
        )
    return tuple(result)


def _plan(minutes: tuple[PostReadMinuteV3, ...]) -> MinuteAggregationPlan:
    return MinuteAggregationPlan(
        source_manifest_sha256=capture_chain_sha256_v3(
            tuple(item.capture_v3_sha256 for item in minutes)
        ),
        window=PartitionWindow(
            partition=DatasetPartition.DEVELOPMENT,
            start_at=START,
            end_at=START + timedelta(minutes=len(minutes)),
        ),
        instrument_id="BTC-USDT-SWAP",
        expected_minute_rows=len(minutes),
        volume_unit="contracts",
    )


def _build_case(
    minutes: tuple[PostReadMinuteV3, ...],
) -> PostReadMinuteBatchV3:
    plan = _plan(minutes)
    return _build(minutes, plan=plan, expected_plan_sha256=plan.canonical_sha256())


def test_post_read_batch_is_deterministic_and_never_promoted():
    minutes = _minutes()
    first = _build_case(minutes)
    with localcontext() as context:
        context.prec = 4
        second = _build_case(minutes)
    assert first.canonical_json_bytes() == second.canonical_json_bytes()
    assert [len(frame.bars) for frame in first.timeframes] == [16, 4, 1]
    for frame in first.timeframes:
        width = frame.horizon.seconds // 60
        first_bar = frame.bars[0]
        assert first_bar.row.bar.volume == sum(
            (item.row.bar.volume for item in minutes[:width]), Decimal(0)
        )
        assert first_bar.row.available_at == max(
            item.persisted_observed_at for item in minutes[:width]
        )
        assert first_bar.capture_v3_sha256s == tuple(
            item.capture_v3_sha256 for item in minutes[:width]
        )
    assert first.timeframes[-1].bars[0].row.available_at == (
        first.plan.window.end_at + timedelta(seconds=10)
    )
    assert first.current_claim == "computational"
    assert first.predictive_oos_eligible is False
    assert first.promotion_eligible is False
    assert first.execution_authority is False
    assert first.runtime_consumers == 0
    assert all(
        item.payload_readback_at < item.persisted_observed_at for item in first.minutes
    )


@pytest.mark.parametrize(
    "damage",
    ["missing", "duplicate", "reordered", "instrument", "witness", "late_pin"],
)
def test_missing_duplicate_reordered_or_changed_source_fails_closed(damage: str):
    original = _minutes()
    minutes = list(original)
    if damage == "missing":
        minutes.pop(10)
        plan = _plan(original)
    else:
        if damage == "duplicate":
            minutes[10] = minutes[9]
        elif damage == "reordered":
            minutes[9], minutes[10] = minutes[10], minutes[9]
        elif damage == "instrument":
            row = minutes[10].row.model_copy(update={"instrument_id": "ETH-USDT-SWAP"})
            minutes[10] = minutes[10].model_copy(update={"row": row})
        elif damage == "witness":
            minutes[10] = minutes[10].model_copy(
                update={"journal_genesis_sha256": minutes[9].journal_genesis_sha256}
            )
        else:
            minutes[10] = minutes[10].model_copy(
                update={"persisted_observed_at": START}
            )
        plan = _plan(original) if damage == "late_pin" else _plan(tuple(minutes))
    with pytest.raises(
        (PostReadBatchError, ValidationError), match="post.read|validation"
    ):
        _build(tuple(minutes), plan=plan, expected_plan_sha256=plan.canonical_sha256())


def test_post_read_minute_refuses_faked_retrieval_or_authority():
    original = _minutes()[0]
    changed = original.model_dump(mode="python")
    changed["payload_readback_at"] = original.persisted_observed_at + timedelta(
        seconds=1
    )
    with pytest.raises(ValidationError, match="noncausal"):
        PostReadMinuteV3.model_validate(changed)
    changed = original.model_dump(mode="python")
    changed["row"]["available_at"] = original.v2_witness_observed_at
    with pytest.raises(ValidationError, match="noncausal"):
        PostReadMinuteV3.model_validate(changed)
    changed = original.model_dump(mode="python")
    changed["predictive_oos_eligible"] = True
    with pytest.raises(ValidationError):
        PostReadMinuteV3.model_validate(changed)
    changed = original.model_dump(mode="python")
    changed["retrieved_at"] = original.persisted_observed_at
    with pytest.raises(ValidationError):
        PostReadMinuteV3.model_validate(changed)


@pytest.mark.asyncio
async def test_full_source_verifier_rejects_multi_minute_capture(observation_case):
    source_kwargs, _, repository, _ = observation_case
    frozen = await freeze_post_publication_capture_v3(
        observation_repository=repository, **source_kwargs
    )
    source = PostReadCaptureInputV3(
        payload=frozen.payload,
        expected_sha256=frozen.sha256,
        **source_kwargs,
    )
    with pytest.raises(PostReadBatchError, match="requires_one_minute"):
        await _verified_minute(source, repository)


@pytest.mark.asyncio
async def test_exact_sources_replayed_and_late_bar_rejected_at_cutoff(
    monkeypatch, observation_case
):
    minutes = _minutes()
    plan = _plan(minutes)
    source_kwargs, _, repository, _ = observation_case
    sources = tuple(
        PostReadCaptureInputV3(
            payload=b"unit-only",
            expected_sha256=minute.capture_v3_sha256,
            journal=source_kwargs["journal"],
            witness_repository=source_kwargs["witness_repository"],
            capture_id=minute.capture_id,
            expected_plan_sha256=minute.plan_sha256,
            expected_receipt_sha256=minute.receipt_sha256,
            expected_journal_genesis_sha256=minute.journal_genesis_sha256,
            expected_capture_checkpoint_sha256=minute.checkpoint_sha256,
            expected_witness_revision=minute.witness_revision,
            expected_witness_record_sha256=minute.witness_record_sha256,
        )
        for minute in minutes
    )
    by_digest = {minute.capture_v3_sha256: minute for minute in minutes}
    seen = []

    async def fake_verified(source, repository):
        seen.append(source.expected_sha256)
        return by_digest[source.expected_sha256]

    monkeypatch.setattr(
        "app.mie.validation.post_read_batch_v3._verified_minute", fake_verified
    )
    frozen = await freeze_post_read_minute_batch_v3(
        sources,
        observation_repository=repository,
        plan=plan,
        expected_plan_sha256=plan.canonical_sha256(),
    )
    assert seen == [item.expected_sha256 for item in sources]
    verified = await verify_post_read_minute_batch_v3(
        frozen.payload,
        expected_sha256=frozen.sha256,
        sources=sources,
        observation_repository=repository,
        plan=plan,
        expected_plan_sha256=plan.canonical_sha256(),
    )
    assert verified.canonical_sha256() == frozen.sha256
    with pytest.raises(PostReadBatchError, match="due_bar_unavailable"):
        await computational_rows_at_cutoff_v3(
            frozen.payload,
            expected_sha256=frozen.sha256,
            sources=sources,
            observation_repository=repository,
            plan=plan,
            expected_plan_sha256=plan.canonical_sha256(),
            horizon_seconds=14400,
            as_of=plan.window.end_at + timedelta(seconds=5),
        )
    due = await computational_rows_at_cutoff_v3(
        frozen.payload,
        expected_sha256=frozen.sha256,
        sources=sources,
        observation_repository=repository,
        plan=plan,
        expected_plan_sha256=plan.canonical_sha256(),
        horizon_seconds=14400,
        as_of=plan.window.end_at + timedelta(seconds=10),
    )
    assert len(due) == 1
    assert due[0].available_at == plan.window.end_at + timedelta(seconds=10)

    changed = frozen.contract.model_dump(mode="python")
    changed["timeframes"][0]["bars"][0]["row"]["bar"]["open"] = Decimal("100.05")
    with pytest.raises(ValidationError, match="aggregate source or time differs"):
        PostReadMinuteBatchV3.model_validate(changed)
    forged_row = minutes[0].row.model_copy(
        update={
            "bar": minutes[0].row.bar.model_copy(update={"open": Decimal("100.05")})
        }
    )
    forged_minute = minutes[0].model_copy(update={"row": forged_row})
    forged = _build_case((forged_minute,) + minutes[1:]).canonical_json_bytes()
    with pytest.raises(ArtifactVerificationError, match="differs from source replay"):
        await verify_post_read_minute_batch_v3(
            forged,
            expected_sha256=sha256(forged).hexdigest(),
            sources=sources,
            observation_repository=repository,
            plan=plan,
            expected_plan_sha256=plan.canonical_sha256(),
        )
    assert len(seen) == 5 * len(sources)
    changed_sources = (
        sources[:10] + (replace(sources[10], expected_sha256="f" * 64),) + sources[11:]
    )
    with pytest.raises(PostReadBatchError, match="external_capture_chain_mismatch"):
        await freeze_post_read_minute_batch_v3(
            changed_sources,
            observation_repository=repository,
            plan=plan,
            expected_plan_sha256=plan.canonical_sha256(),
        )
    assert len(seen) == 5 * len(sources)

    initial_checkpoint = source_kwargs["journal"]._checkpoint

    async def advancing_verified(source, repository):
        if source is sources[100]:
            source_kwargs["journal"]._checkpoint = initial_checkpoint.model_copy(
                update={"head_sha256": "f" * 64}
            )
        return by_digest[source.expected_sha256]

    monkeypatch.setattr(
        "app.mie.validation.post_read_batch_v3._verified_minute",
        advancing_verified,
    )
    with pytest.raises(PostReadBatchError, match="journal_changed_during_batch"):
        await freeze_post_read_minute_batch_v3(
            sources,
            observation_repository=repository,
            plan=plan,
            expected_plan_sha256=plan.canonical_sha256(),
        )
    source_kwargs["journal"]._checkpoint = initial_checkpoint
    monkeypatch.setattr(
        "app.mie.validation.post_read_batch_v3._verified_minute", fake_verified
    )

    def changed_inventory(_journal):
        raise ValueError("synthetic_external_append")

    monkeypatch.setattr(type(source_kwargs["journal"]), "read_all", changed_inventory)
    with pytest.raises(PostReadBatchError, match="journal_final_replay_failed"):
        await freeze_post_read_minute_batch_v3(
            sources,
            observation_repository=repository,
            plan=plan,
            expected_plan_sha256=plan.canonical_sha256(),
        )


def test_holdout_plan_and_unknown_volume_unit_are_rejected():
    minutes = _minutes()
    plan = _plan(minutes)
    changed = plan.model_dump(mode="python")
    changed["window"]["partition"] = DatasetPartition.RETROSPECTIVE_HOLDOUT
    with pytest.raises(ValidationError, match="excludes holdout"):
        MinuteAggregationPlan.model_validate(changed)
    changed = plan.model_dump(mode="python")
    changed["volume_unit"] = "source_unspecified"
    unknown = MinuteAggregationPlan.model_validate(changed)
    with pytest.raises(PostReadBatchError, match="volume_unit"):
        _build(minutes, plan=unknown, expected_plan_sha256=unknown.canonical_sha256())
