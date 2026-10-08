"""Synthetic multi-batch replay tests; V3 and PostgreSQL have separate tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import ROUND_DOWN, DefaultContext, Inexact, localcontext
from hashlib import sha256

import pytest
from pydantic import ValidationError

import app.mie.validation.post_read_stitch_v1 as stitch
from app.mie.validation.artifact import ArtifactVerificationError
from app.mie.validation.batch_replay import MinuteAggregationPlan
from app.mie.validation.contracts import DatasetPartition, PartitionWindow
from app.mie.validation.post_read_batch_v3 import (
    PostReadCaptureInputV3,
    _build,
    capture_chain_sha256_v3,
)
from tests.unit.mie.test_gate3_post_read_batch_v3 import _minutes

pytest_plugins = ("tests.unit.mie.test_gate3_post_publication_availability_v3",)


def _plan(minutes, *, partition=DatasetPartition.DEVELOPMENT, symbol="BTC-USDT-SWAP"):
    return MinuteAggregationPlan(
        source_manifest_sha256=capture_chain_sha256_v3(
            tuple(item.capture_v3_sha256 for item in minutes)
        ),
        window=PartitionWindow(
            partition=partition,
            start_at=minutes[0].row.bar.closed_at - timedelta(minutes=1),
            end_at=minutes[-1].row.bar.closed_at,
        ),
        instrument_id=symbol,
        expected_minute_rows=len(minutes),
        volume_unit="contracts",
    )


@pytest.fixture(scope="module")
def synthetic_batches():
    minutes = _minutes(480)
    result = []
    for portion in (minutes[:240], minutes[240:]):
        plan = _plan(portion)
        result.append(
            _build(portion, plan=plan, expected_plan_sha256=plan.canonical_sha256())
        )
    return tuple(result)


def _case_from_batches(monkeypatch, observation_case, batches):
    source_kwargs, _, repository, _ = observation_case
    journal = source_kwargs["journal"]
    witness_repository = source_kwargs["witness_repository"]
    by_digest = {}
    inputs = []
    identities = []
    seen = []
    for batch in batches:
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
            stitch.PostReadBatchInputV1(
                payload=payload,
                expected_sha256=digest,
                sources=sources,
                plan=batch.plan,
                expected_plan_sha256=batch.plan_sha256,
            )
        )
        identities.append(stitch._identity(batch, batch_sha256=digest))
        by_digest[digest] = (payload, batch)

    async def fake_source_reverify(
        payload,
        *,
        expected_sha256,
        sources,
        observation_repository,
        plan,
        expected_plan_sha256,
    ):
        seen.append(expected_sha256)
        if (
            expected_sha256 not in by_digest
            or by_digest[expected_sha256][0] != payload
            or len(sources) != len(by_digest[expected_sha256][1].minutes)
            or observation_repository is not repository
            or plan.canonical_sha256() != expected_plan_sha256
        ):
            raise ArtifactVerificationError("synthetic V3 source drift")
        return by_digest[expected_sha256][1]

    monkeypatch.setattr(
        stitch, "verify_post_read_minute_batch_v3", fake_source_reverify
    )
    identities = tuple(identities)
    kwargs = {
        "batches": tuple(inputs),
        "expected_identities": identities,
        "expected_chain_sha256": stitch.ordered_batch_chain_sha256_v1(identities),
        "observation_repository": repository,
        "horizon_seconds": 900,
        "as_of": batches[-1].plan.window.end_at + timedelta(seconds=10),
        "history_bars": 24,
    }
    return kwargs, seen, by_digest


@pytest.fixture
def stitch_case(monkeypatch, observation_case, synthetic_batches):
    return _case_from_batches(monkeypatch, observation_case, synthetic_batches)


@pytest.mark.asyncio
async def test_two_source_reverified_invocations_are_identical_and_non_authorizing(
    stitch_case,
):
    kwargs, seen, _ = stitch_case
    first = await stitch.freeze_post_read_stitched_replay_v1(**kwargs)
    with localcontext() as context:
        context.prec = 4
        second = await stitch.freeze_post_read_stitched_replay_v1(**kwargs)
    verified = await stitch.verify_post_read_stitched_replay_v1(
        first.payload, expected_sha256=first.sha256, **kwargs
    )
    assert first.payload == second.payload
    assert first.sha256 == second.sha256 == verified.canonical_sha256()
    assert seen == [item.expected_sha256 for item in kwargs["batches"]] * 3
    assert verified.due_bar_count == 32
    assert verified.replay.source_row_count == 24
    assert verified.replay.feature_snapshot.horizon.seconds == 900
    assert verified.current_claim == "computational"
    assert verified.trusted_clock_verified is False
    assert verified.independently_protected is False
    assert verified.evaluator_first_read_proven is False
    assert verified.predictive_oos_eligible is False
    assert verified.promotion_eligible is False
    assert verified.runtime_consumers == 0
    assert verified.execution_authority is False


@pytest.mark.asyncio
async def test_mutated_decimal_defaults_cannot_change_v3_or_stitched_bytes(
    stitch_case, synthetic_batches
):
    kwargs, _, _ = stitch_case
    baseline = await stitch.freeze_post_read_stitched_replay_v1(**kwargs)
    original = DefaultContext.copy()
    try:
        DefaultContext.rounding = ROUND_DOWN
        DefaultContext.Emin = -999
        DefaultContext.Emax = 999
        DefaultContext.traps[Inexact] = True
        replayed = await stitch.freeze_post_read_stitched_replay_v1(**kwargs)
        first_batch = synthetic_batches[0]
        rebuilt_batch = _build(
            first_batch.minutes,
            plan=first_batch.plan,
            expected_plan_sha256=first_batch.plan_sha256,
        )
        assert replayed.payload == baseline.payload
        assert (
            rebuilt_batch.canonical_json_bytes() == first_batch.canonical_json_bytes()
        )
    finally:
        DefaultContext.rounding = original.rounding
        DefaultContext.Emin = original.Emin
        DefaultContext.Emax = original.Emax
        for signal, trapped in original.traps.items():
            DefaultContext.traps[signal] = trapped


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["count", "source_type", "payload", "total_budget"])
async def test_input_budget_and_types_fail_before_source_or_journal_access(
    stitch_case, monkeypatch, damage
):
    kwargs, seen, _ = stitch_case
    changed = dict(kwargs)
    first = changed["batches"][0]
    if damage == "count":
        first = replace(first, sources=first.sources[:-1])
    elif damage == "source_type":
        first = replace(first, sources=(object(), *first.sources[1:]))
    elif damage == "payload":
        first = replace(first, payload=b"x" * (stitch.MAX_BATCH_ARTIFACT_BYTES + 1))
    else:
        monkeypatch.setattr(
            stitch,
            "MAX_STITCH_INPUT_BYTES",
            sum(len(item.payload) for item in changed["batches"]) + 1,
        )
    changed["batches"] = (first, *changed["batches"][1:])
    with pytest.raises(stitch.PostReadStitchError, match="input_budget|capture_input"):
        await stitch.freeze_post_read_stitched_replay_v1(**changed)
    assert seen == []


@pytest.mark.asyncio
async def test_contiguous_twenty_one_four_hour_bars_replay_without_promotion(
    monkeypatch, observation_case
):
    minutes = _minutes(5040)
    batches = []
    for offset in range(0, len(minutes), 240):
        portion = minutes[offset : offset + 240]
        plan = _plan(portion)
        batches.append(
            _build(portion, plan=plan, expected_plan_sha256=plan.canonical_sha256())
        )
    kwargs, seen, _ = _case_from_batches(monkeypatch, observation_case, tuple(batches))
    kwargs["horizon_seconds"] = 14400
    kwargs["history_bars"] = 21
    frozen = await stitch.freeze_post_read_stitched_replay_v1(**kwargs)
    assert len(seen) == 21
    assert frozen.contract.due_bar_count == 21
    assert frozen.contract.replay.source_row_count == 21
    assert frozen.contract.replay.feature_snapshot.horizon.seconds == 14400
    assert frozen.contract.predictive_oos_eligible is False
    assert frozen.contract.execution_authority is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("damage", "code"),
    [
        ("missing", "inputs_invalid"),
        ("reordered", "sequence_invalid"),
        ("overlap", "sequence_invalid"),
        ("gap", "sequence_invalid"),
        ("symbol", "sequence_invalid"),
        ("partition", "sequence_invalid"),
        ("duplicate_batch", "sequence_invalid"),
    ],
)
async def test_missing_reordered_overlapping_or_mixed_batch_fails_closed(
    stitch_case, damage, code
):
    kwargs, seen, _ = stitch_case
    kwargs = dict(kwargs)
    identities = list(kwargs["expected_identities"])
    if damage == "missing":
        kwargs["batches"] = kwargs["batches"][:1]
        identities = identities[:1]
    elif damage == "reordered":
        kwargs["batches"] = kwargs["batches"][::-1]
        identities.reverse()
    elif damage in {"overlap", "gap"}:
        old = identities[1]
        delta = timedelta(hours=-4 if damage == "overlap" else 4)
        identities[1] = old.model_copy(
            update={
                "window": old.window.model_copy(
                    update={
                        "start_at": old.window.start_at + delta,
                        "end_at": old.window.end_at + delta,
                    }
                )
            }
        )
    elif damage == "symbol":
        identities[1] = identities[1].model_copy(
            update={"instrument_id": "ETH-USDT-SWAP"}
        )
    elif damage == "partition":
        old = identities[1]
        identities[1] = old.model_copy(
            update={
                "window": old.window.model_copy(
                    update={"partition": DatasetPartition.VALIDATION}
                )
            }
        )
    else:
        identities[1] = identities[0]
    if len(identities) >= 2:
        kwargs["expected_identities"] = tuple(identities)
        kwargs["expected_chain_sha256"] = stitch.ordered_batch_chain_sha256_v1(
            tuple(identities)
        )
    with pytest.raises(stitch.PostReadStitchError, match=code):
        await stitch.freeze_post_read_stitched_replay_v1(**kwargs)
    assert seen == []


@pytest.mark.asyncio
async def test_source_drift_and_changed_external_pin_fail_closed(stitch_case):
    kwargs, seen, _ = stitch_case
    changed = dict(kwargs)
    second = changed["batches"][1]
    changed["batches"] = (changed["batches"][0], replace(second, payload=b"changed"))
    with pytest.raises(ArtifactVerificationError, match="source drift"):
        await stitch.freeze_post_read_stitched_replay_v1(**changed)
    assert seen == [item.expected_sha256 for item in kwargs["batches"]]

    seen.clear()
    changed = dict(kwargs)
    changed["expected_chain_sha256"] = "f" * 64
    with pytest.raises(stitch.PostReadStitchError, match="chain_pin_mismatch"):
        await stitch.freeze_post_read_stitched_replay_v1(**changed)
    assert seen == []


@pytest.mark.asyncio
async def test_global_journal_change_during_batch_readback_fails_closed(
    stitch_case, monkeypatch
):
    kwargs, _, by_digest = stitch_case
    journal = kwargs["batches"][0].sources[0].journal
    checkpoint = journal._checkpoint

    async def advancing_verifier(
        payload,
        *,
        expected_sha256,
        sources,
        observation_repository,
        plan,
        expected_plan_sha256,
    ):
        if expected_sha256 == kwargs["batches"][-1].expected_sha256:
            journal._checkpoint = checkpoint.model_copy(
                update={"head_sha256": "f" * 64}
            )
        return by_digest[expected_sha256][1]

    monkeypatch.setattr(stitch, "verify_post_read_minute_batch_v3", advancing_verifier)
    try:
        with pytest.raises(stitch.PostReadStitchError, match="journal_changed"):
            await stitch.freeze_post_read_stitched_replay_v1(**kwargs)
    finally:
        journal._checkpoint = checkpoint


@pytest.mark.asyncio
@pytest.mark.parametrize("reused_kind", ["capture", "row", "witness"])
async def test_global_duplicate_source_identity_is_rejected(
    stitch_case, synthetic_batches, reused_kind
):
    kwargs, _, by_digest = stitch_case
    first, second = synthetic_batches
    old = second.minutes[0]
    if reused_kind == "capture":
        reused = old.model_copy(update={"capture_id": first.minutes[0].capture_id})
    elif reused_kind == "row":
        reused = old.model_copy(
            update={
                "source_row_identity": first.minutes[0].source_row_identity,
                "row": old.row.model_copy(
                    update={"source_row_id": first.minutes[0].row.source_row_id}
                ),
            }
        )
    else:
        reused = old.model_copy(
            update={
                "journal_genesis_sha256": first.minutes[0].journal_genesis_sha256,
                "witness_revision": first.minutes[0].witness_revision,
            }
        )
    forged = _build(
        (reused, *second.minutes[1:]),
        plan=second.plan,
        expected_plan_sha256=second.plan_sha256,
    )
    payload = forged.canonical_json_bytes()
    digest = sha256(payload).hexdigest()
    by_digest[digest] = (payload, forged)
    changed = dict(kwargs)
    changed["batches"] = (
        kwargs["batches"][0],
        replace(kwargs["batches"][1], payload=payload, expected_sha256=digest),
    )
    changed["expected_identities"] = (
        kwargs["expected_identities"][0],
        stitch._identity(forged, batch_sha256=digest),
    )
    changed["expected_chain_sha256"] = stitch.ordered_batch_chain_sha256_v1(
        changed["expected_identities"]
    )
    with pytest.raises(stitch.PostReadStitchError, match="global_identity_reused"):
        await stitch.freeze_post_read_stitched_replay_v1(**changed)


@pytest.mark.asyncio
async def test_late_constituent_rejects_entire_due_aggregate(
    stitch_case, synthetic_batches
):
    kwargs, _, by_digest = stitch_case
    second = synthetic_batches[1]
    last = second.minutes[-1]
    observed = last.persisted_observed_at + timedelta(seconds=1)
    late = last.model_copy(
        update={
            "persisted_observed_at": observed,
            "row": last.row.model_copy(update={"available_at": observed}),
        }
    )
    changed_batch = _build(
        (*second.minutes[:-1], late),
        plan=second.plan,
        expected_plan_sha256=second.plan_sha256,
    )
    payload = changed_batch.canonical_json_bytes()
    digest = sha256(payload).hexdigest()
    by_digest[digest] = (payload, changed_batch)
    changed = dict(kwargs)
    changed["batches"] = (
        kwargs["batches"][0],
        replace(kwargs["batches"][1], payload=payload, expected_sha256=digest),
    )
    changed["expected_identities"] = (
        kwargs["expected_identities"][0],
        stitch._identity(changed_batch, batch_sha256=digest),
    )
    changed["expected_chain_sha256"] = stitch.ordered_batch_chain_sha256_v1(
        changed["expected_identities"]
    )
    with pytest.raises(stitch.PostReadStitchError, match="due_constituent_unavailable"):
        await stitch.freeze_post_read_stitched_replay_v1(**changed)


@pytest.mark.asyncio
async def test_history_shortfall_fails_instead_of_filling_missing_bars(stitch_case):
    kwargs, _, _ = stitch_case
    changed = dict(kwargs)
    changed["horizon_seconds"] = 3600
    with pytest.raises(ValueError, match="insufficient causal history"):
        await stitch.freeze_post_read_stitched_replay_v1(**changed)


@pytest.mark.asyncio
async def test_forged_predictive_or_execution_flags_are_rejected(stitch_case):
    kwargs, _, _ = stitch_case
    identity = kwargs["expected_identities"][0]
    with pytest.raises(ValidationError):
        stitch.PostReadBatchIdentityV1.model_validate(
            {**identity.model_dump(mode="python"), "volume_unit": "quote_currency"}
        )
    frozen = await stitch.freeze_post_read_stitched_replay_v1(**kwargs)
    for field, value in (
        ("trusted_clock_verified", True),
        ("independently_protected", True),
        ("evaluator_first_read_proven", True),
        ("current_claim", "predictive_oos"),
        ("predictive_oos_eligible", True),
        ("promotion_eligible", True),
        ("runtime_consumers", 1),
        ("execution_authority", True),
    ):
        changed = frozen.contract.model_dump(mode="python")
        changed[field] = value
        with pytest.raises(ValidationError):
            stitch.PostReadStitchedReplayV1.model_validate(changed)
    changed = frozen.contract.model_dump(mode="python")
    changed["due_rows_sha256"] = "f" * 64
    forged = stitch.PostReadStitchedReplayV1.model_validate(changed)
    payload = forged.canonical_json_bytes()
    with pytest.raises(ArtifactVerificationError, match="differs from source replay"):
        await stitch.verify_post_read_stitched_replay_v1(
            payload, expected_sha256=sha256(payload).hexdigest(), **kwargs
        )
