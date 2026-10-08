"""Synthetic V2 timing tests; no real TLS, PostgreSQL, or predictive claim."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import timedelta

import pytest

from app.database.repositories.public_receipt_witness import (
    CommittedWitnessObservation,
    PublicReceiptWitnessRepository,
    WitnessRevision,
)
from app.mie.validation.artifact import ArtifactVerificationError
from app.mie.validation.measured_public_replay import measured_public_minutes
from app.mie.validation.post_publication_availability_v2 import (
    PostPublicationAvailabilityError,
    PostPublicationCaptureV2,
    freeze_post_publication_capture_v2,
    verify_post_publication_capture_v2,
)
from app.public_market_source import public_receipt_storage as storage
from app.public_market_source.public_market_receipts import canonical, sha, utc_from_ns
from tests.unit.research.test_public_journal_contracts import fixture_chain

pytestmark = pytest.mark.asyncio


@pytest.fixture
def synthetic_case(monkeypatch):
    _, _, directory, original_checkpoint = fixture_chain(monkeypatch, rows=2)
    entries, first_rows = storage._replay_directory(directory, original_checkpoint)
    plan, original_receipt, files, original_entry = entries[0]
    # Synthetic-only attempt label. The production journal requires its real
    # original-byte attempt chain before admitting a capture.
    receipt = type(original_receipt).model_validate(
        {**original_receipt.model_dump(), "attempt_sha256": "a" * 64}
    )
    entry = {
        **original_entry,
        "receipt_sha256": receipt.canonical_sha256(),
    }
    checkpoint = original_checkpoint.model_copy(
        update={
            "head_sha256": sha(canonical(entry)),
            "attempt_sequence": 1,
            "attempt_head_sha256": "e" * 64,
        }
    )
    journal = object.__new__(storage.ControlledPublicReceiptJournal)
    journal._checkpoint = checkpoint
    replay = ((plan, receipt, files, entry),), first_rows
    monkeypatch.setattr(
        storage.ControlledPublicReceiptJournal, "read_all", lambda _: replay
    )
    witness = WitnessRevision(
        journal_key=checkpoint.genesis_sha256,
        revision=3,
        journal_id="b" * 32,
        root_device=checkpoint.root_device,
        root_inode=checkpoint.root_inode,
        transition="append_capture",
        state="idle",
        operation_id="c" * 32,
        plan_sha256=plan.canonical_sha256(),
        attempt_outcome="completed_collection",
        previous_record_sha256="d" * 64,
        checkpoint=checkpoint,
        checkpoint_sha256=checkpoint.canonical_sha256(),
        record_sha256="f" * 64,
    )
    repository = PublicReceiptWitnessRepository(None)
    clock = {
        "at": utc_from_ns(entry["payload_readback_complete"]["utc_ns"])
        + timedelta(seconds=1)
    }
    evidence = {"revision": witness}

    async def observe(_key, _revision):
        return CommittedWitnessObservation(
            revision=evidence["revision"], observed_at=clock["at"]
        )

    monkeypatch.setattr(repository, "observe_committed_revision", observe)
    args = {
        "journal": journal,
        "witness_repository": repository,
        "capture_id": receipt.capture_id,
        "expected_plan_sha256": plan.canonical_sha256(),
        "expected_receipt_sha256": receipt.canonical_sha256(),
        "expected_journal_genesis_sha256": checkpoint.genesis_sha256,
        "expected_capture_checkpoint_sha256": checkpoint.canonical_sha256(),
        "expected_witness_revision": witness.revision,
        "expected_witness_record_sha256": witness.record_sha256,
    }
    return args, clock, evidence


async def test_post_commit_observation_is_conservative_and_v1_unchanged(
    synthetic_case,
):
    args, clock, _ = synthetic_case
    prior = measured_public_minutes(
        journal=args["journal"],
        capture_id=args["capture_id"],
        expected_receipt_sha256=args["expected_receipt_sha256"],
        expected_plan_sha256=args["expected_plan_sha256"],
    )
    prior_bytes = tuple(row.canonical_json_bytes() for row in prior)
    frozen = await freeze_post_publication_capture_v2(**args)
    record = frozen.contract
    assert len(record.rows) == 2
    assert record.available_at == clock["at"]
    assert record.available_at > record.payload_readback_at
    assert all(row.available_at == clock["at"] for row in record.point_in_time_rows())
    assert all(row.available_at < clock["at"] for row in (item.row for item in prior))
    assert record.predictive_oos_eligible is False
    assert record.execution_authority is False
    assert record.historical_observation_independently_replayable is False
    assert tuple(row.canonical_json_bytes() for row in prior) == prior_bytes

    clock["at"] += timedelta(seconds=1)
    assert (
        await verify_post_publication_capture_v2(
            frozen.payload, expected_sha256=frozen.sha256, **args
        )
        == record
    )


@pytest.mark.parametrize(
    "field",
    (
        "expected_plan_sha256",
        "expected_receipt_sha256",
        "expected_journal_genesis_sha256",
        "expected_capture_checkpoint_sha256",
        "expected_witness_record_sha256",
    ),
)
async def test_external_identity_pins_cannot_be_replaced(synthetic_case, field):
    args, _, _ = synthetic_case
    with pytest.raises(PostPublicationAvailabilityError):
        await freeze_post_publication_capture_v2(**{**args, field: "0" * 64})


async def test_witness_commit_order_and_revision_must_match(synthetic_case):
    args, clock, evidence = synthetic_case
    frozen = await freeze_post_publication_capture_v2(**args)
    clock["at"] = frozen.contract.payload_readback_at - timedelta(microseconds=1)
    with pytest.raises(
        PostPublicationAvailabilityError, match="^witness_clock_order_invalid$"
    ):
        await freeze_post_publication_capture_v2(**args)
    clock["at"] = frozen.contract.available_at
    evidence["revision"] = replace(evidence["revision"], transition="append_attempt")
    with pytest.raises(
        PostPublicationAvailabilityError, match="^committed_witness_pin_mismatch$"
    ):
        await freeze_post_publication_capture_v2(**args)


async def test_later_duplicate_cannot_borrow_first_observation(
    monkeypatch, synthetic_case
):
    args, _, _ = synthetic_case
    entries, first_rows = args["journal"].read_all()
    changed = {
        identity: (content, 1, locator)
        for identity, (content, _, locator) in first_rows.items()
    }
    monkeypatch.setattr(
        storage.ControlledPublicReceiptJournal,
        "read_all",
        lambda _: (entries, changed),
    )
    with pytest.raises(
        PostPublicationAvailabilityError, match="^capture_not_first_observation$"
    ):
        await freeze_post_publication_capture_v2(**args)


async def test_raw_page_change_denies_readback(synthetic_case):
    args, _, _ = synthetic_case
    frozen = await freeze_post_publication_capture_v2(**args)
    entries, _ = args["journal"].read_all()
    entries[0][2]["page-000.raw"] += b" "
    with pytest.raises(PostPublicationAvailabilityError):
        await verify_post_publication_capture_v2(
            frozen.payload, expected_sha256=frozen.sha256, **args
        )


async def test_old_artifact_cannot_gain_authority_or_future_clock(synthetic_case):
    args, clock, _ = synthetic_case
    frozen = await freeze_post_publication_capture_v2(**args)
    changed = frozen.contract.model_dump(mode="python")
    changed["predictive_oos_eligible"] = True
    with pytest.raises(ValueError):
        PostPublicationCaptureV2.model_validate(changed)
    changed = frozen.contract.model_dump(mode="python")
    changed["committed_witness_observed_at"] += timedelta(hours=1)
    changed["available_at"] = changed["committed_witness_observed_at"]
    changed_record = PostPublicationCaptureV2.model_validate(changed)
    payload = changed_record.canonical_json_bytes()
    clock["at"] = frozen.contract.available_at + timedelta(seconds=1)
    with pytest.raises(ArtifactVerificationError):
        await verify_post_publication_capture_v2(
            payload, expected_sha256=hashlib.sha256(payload).hexdigest(), **args
        )
