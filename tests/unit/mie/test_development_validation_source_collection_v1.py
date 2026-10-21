"""Synthetic source/DB wiring and native local storage; no TLS/PG acceptance."""

from __future__ import annotations

import asyncio
import copy
import json
import pickle
from datetime import timedelta

import pytest

import app.mie.validation.development_validation_source_collection_v1 as module
import app.mie.validation.post_publication_availability_v3 as availability
import app.mie.validation.public_checkpoint_service as checkpoint
from app.mie.validation.development_validation_source_plan_v1 import (
    DevelopmentValidationSourceCoordinateV1,
    DevelopmentValidationSourcePlanV1,
)
from app.public_market_source.public_market_receipts import utc_from_ns
from tests.unit.research import public_receipt_fixtures

pytest_plugins = ("tests.unit.mie.test_gate3_post_publication_availability_v3",)


@pytest.fixture(autouse=True)
def historical_plan_metadata(monkeypatch):
    original = public_receipt_fixtures.plan_for

    def plan_for(rows=240):
        plan = original(rows)
        return type(plan).model_validate(
            {**plan.model_dump(), "created_ns": plan.end_ns + 1_000_000_000}
        )

    monkeypatch.setattr(public_receipt_fixtures, "plan_for", plan_for)


@pytest.fixture
def case(monkeypatch, observation_case):
    source, clock, observation, observations = observation_case
    repository = source["witness_repository"]
    journal = source["journal"]
    plan = journal.read_all()[0][0][0]
    service = checkpoint.PublicCheckpointCaptureService(
        checkpoint._ISSUER,
        journal,
        repository,
        "b" * 32,
        type("Latest", (), {"journal_key": journal.checkpoint.genesis_sha256})(),
    )
    state = {
        "capture_calls": 0,
        "role_calls": 0,
        "claim_root": None,
        "failure": None,
        "database_guard_calls": 0,
        "late_guard_failure": None,
    }

    # Explicit synthetic DB seam. Real native-engine/constructor/method guards
    # have separate no-I/O tests; these fixtures cannot prove PostgreSQL custody.
    synthetic_pins = object()

    def synthetic_database_guard(witness, current_observation, pins=None):
        assert witness is repository and current_observation is observation
        assert pins is None or pins is synthetic_pins
        state["database_guard_calls"] += 1
        if state["database_guard_calls"] == state["late_guard_failure"]:
            raise checkpoint.PublicWitnessError("source_native_database_changed")
        return synthetic_pins

    monkeypatch.setattr(checkpoint, "_source_database_pins", synthetic_database_guard)
    # Deliberately reuse providers in this substituted wiring seam only. The
    # separate native-factory tests verify actual private clone separation.
    monkeypatch.setattr(
        checkpoint, "_owned_source_repositories", lambda _: (repository, observation)
    )

    def assert_claim():
        if state["claim_root"] is not None:
            root = state["claim_root"]
            assert (root / "batch-00000000" / "claim.json").is_file()
            assert (
                json.loads((root / "batch-00000000" / "claim.json").read_bytes())[
                    "capture_plan_sha256"
                ]
                == plan.canonical_sha256()
            )

    async def role():
        assert_claim()
        state["role_calls"] += 1

    async def capture(self, **kwargs):
        assert_claim()
        state["capture_calls"] += 1
        assert kwargs["expected_plan_sha256"] == plan.canonical_sha256()
        if state["failure"] is not None:
            raise state["failure"]
        return checkpoint.WitnessedPublicCapture(
            capture_id=source["capture_id"],
            receipt_sha256=source["expected_receipt_sha256"],
            witness_revision=source["expected_witness_revision"],
            witness_record_sha256=source["expected_witness_record_sha256"],
            checkpoint_sha256=source["expected_capture_checkpoint_sha256"],
        )

    monkeypatch.setattr(repository, "verify_role", role)
    monkeypatch.setattr(observation, "verify_role", role)
    monkeypatch.setattr(checkpoint.PublicCheckpointCaptureService, "capture", capture)
    coordinate = DevelopmentValidationSourceCoordinateV1(
        ordinal=0,
        partition="development",
        purpose="historical_warmup",
        capture_plan=plan,
        capture_plan_sha256=plan.canonical_sha256(),
    )
    schedule = DevelopmentValidationSourcePlanV1(
        collection_id="source-only:test:v1",
        origin=plan.origin,
        planned_at=utc_from_ns(plan.created_ns),
        coordinates=(coordinate,),
        expected_rows=plan.expected_rows,
    )
    return service, observation, plan, schedule, state, observations, clock


async def capture_handle(case):
    service, observation, plan, *_ = case
    return await service.capture_source_with_post_read_v3(
        plan=plan,
        expected_plan_sha256=plan.canonical_sha256(),
        observation_repository=observation,
    )


def owner_for(case, root):
    service, observation, _, schedule, state, *_ = case
    state["claim_root"] = root
    return module.DevelopmentValidationSourceOwnerV1.initialize(
        root=root,
        plan=schedule,
        expected_plan_sha256=schedule.canonical_sha256(),
        capture_service=service,
        observation_repository=observation,
    )


def reader_for(case, root):
    schedule = case[3]
    return module.SourceCollectionReadOnly.open(
        root=root, plan=schedule, expected_plan_sha256=schedule.canonical_sha256()
    )


@pytest.mark.asyncio
async def test_positive_source_batch_preserves_post_read_time_and_has_no_higher_authority(
    case, tmp_path
):
    owner = owner_for(case, tmp_path)
    receipt = await owner.capture_once(ordinal=0, expected_previous_receipt_sha256s=())
    assert receipt.status == "CAPTURED_POST_READ_VERIFIED"
    assert receipt.row_count == case[2].expected_rows == 2
    assert receipt.available_at == case[5]["readback"].observed_at > case[6]["at"]
    payload = (tmp_path / "batch-00000000" / "capture-v3.json").read_bytes()
    stored = availability.PostPublicationCaptureV3.model_validate_json(payload)
    assert all(
        row.bar.closed_at < receipt.available_at for row in stored.v2_capture.rows
    )
    assert stored.available_at != stored.v2_capture.rows[0].bar.closed_at
    assert not receipt.predictive_oos_eligible and not receipt.execution_authority
    assert not receipt.candidate_seal_eligible and not receipt.holdout_accessed
    assert not receipt.trusted_clock_verified and not receipt.independently_protected
    reader = reader_for(case, tmp_path)
    assert not hasattr(reader, "capture_once") and not hasattr(reader, "resume")
    assert (
        await reader.reconcile_ordinal(
            0,
            expected_receipt_sha256=receipt.canonical_sha256(),
            capture_service=case[0],
            observation_repository=case[1],
        )
        == receipt
    )
    assert case[4]["capture_calls"] == case[5]["append_calls"] == 1


@pytest.mark.asyncio
async def test_source_result_is_one_use_and_saved_dto_cannot_remint(case):
    handle = await capture_handle(case)
    service, _, plan, *_ = case
    frozen = service.consume_source_post_read_v3(
        handle, expected_plan_sha256=plan.canonical_sha256()
    )
    for invalid in (
        handle,
        frozen.contract,
        frozen.payload,
        True,
        object.__new__(checkpoint.NativeSourcePostReadCapture),
    ):
        with pytest.raises(checkpoint.PublicWitnessError):
            service.consume_source_post_read_v3(
                invalid, expected_plan_sha256=plan.canonical_sha256()
            )
    with pytest.raises(checkpoint.PublicWitnessError, match="already_attempted"):
        await capture_handle(case)
    assert case[4]["capture_calls"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "transfer", ["copy", "deepcopy", "pickle", "task", "pid", "thread"]
)
async def test_native_result_rejects_transfer_and_wrong_context(
    case, monkeypatch, transfer
):
    handle = await capture_handle(case)
    service, _, plan, *_ = case
    if transfer in {"copy", "deepcopy", "pickle"}:
        operation = {
            "copy": copy.copy,
            "deepcopy": copy.deepcopy,
            "pickle": pickle.dumps,
        }[transfer]
        with pytest.raises(checkpoint.PublicWitnessError):
            operation(handle)
        return
    if transfer == "pid":
        monkeypatch.setattr(checkpoint.os, "getpid", lambda: -1)
    if transfer == "thread":
        monkeypatch.setattr(checkpoint, "get_ident", lambda: -1)

    async def consume():
        return service.consume_source_post_read_v3(
            handle, expected_plan_sha256=plan.canonical_sha256()
        )

    with pytest.raises(checkpoint.PublicWitnessError):
        if transfer == "task":
            await asyncio.create_task(consume())
        else:
            await consume()
    with pytest.raises(checkpoint.PublicWitnessError):
        service.consume_source_post_read_v3(
            handle, expected_plan_sha256=plan.canonical_sha256()
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", [RuntimeError("synthetic failure"), asyncio.CancelledError()]
)
async def test_failed_or_cancelled_attempt_retains_tombstone_and_never_refills(
    case, tmp_path, failure
):
    owner = owner_for(case, tmp_path)
    case[4]["failure"] = failure
    with pytest.raises(
        asyncio.CancelledError
        if isinstance(failure, asyncio.CancelledError)
        else module.SourceCollectionError
    ):
        await owner.capture_once(ordinal=0, expected_previous_receipt_sha256s=())
    raw = (tmp_path / "batch-00000000" / "receipt.json").read_bytes()
    receipt = module.SourceOrdinalReceiptV1.model_validate_json(raw)
    assert receipt.status == "FAILED" and receipt.available_at is None
    reader = reader_for(case, tmp_path)
    assert (
        reader.read_ordinal(0, expected_receipt_sha256=receipt.canonical_sha256())
        == receipt
    )
    with pytest.raises(module.SourceCollectionError):
        await owner.capture_once(ordinal=0, expected_previous_receipt_sha256s=())
    with pytest.raises(module.SourceCollectionError):
        owner_for(case, tmp_path)
    assert (tmp_path / "batch-00000000" / "receipt.json").read_bytes() == raw
    assert case[4]["capture_calls"] == 1 and case[5]["append_calls"] == 0


@pytest.mark.asyncio
async def test_changed_independent_v3_readback_burns_plan_and_ordinal(
    case, tmp_path, monkeypatch
):
    original = availability.verify_post_publication_capture_v3

    async def changed(*args, **kwargs):
        result = await original(*args, **kwargs)
        return result.model_copy(
            update={"available_at": result.available_at + timedelta(seconds=1)}
        )

    monkeypatch.setattr(availability, "verify_post_publication_capture_v3", changed)
    owner = owner_for(case, tmp_path)
    with pytest.raises(module.SourceCollectionError):
        await owner.capture_once(ordinal=0, expected_previous_receipt_sha256s=())
    assert case[4]["capture_calls"] == case[5]["append_calls"] == 1
    with pytest.raises(module.SourceCollectionError):
        await owner.capture_once(ordinal=0, expected_previous_receipt_sha256s=())


@pytest.mark.asyncio
async def test_late_filesystem_failure_preserves_successful_payload_without_retry(
    case, tmp_path, monkeypatch
):
    owner = owner_for(case, tmp_path)
    with module._root_context(tmp_path) as directory:
        directory_type = type(directory)
    original = directory_type.publish

    def late_failure(self, name, payload):
        if name == "receipt.sha256":
            raise OSError("synthetic sharing violation")
        return original(self, name, payload)

    monkeypatch.setattr(directory_type, "publish", late_failure)
    with pytest.raises(module.SourceCollectionError):
        await owner.capture_once(ordinal=0, expected_previous_receipt_sha256s=())
    path = tmp_path / "batch-00000000" / "capture-v3.json"
    retained = path.read_bytes()
    assert availability.PostPublicationCaptureV3.model_validate_json(retained)
    assert (
        module.SourceOrdinalReceiptV1.model_validate_json(
            path.with_name("receipt.json").read_bytes()
        ).status
        == "CAPTURED_POST_READ_VERIFIED"
    )
    with pytest.raises(module.SourceCollectionError):
        await owner.capture_once(ordinal=0, expected_previous_receipt_sha256s=())
    assert path.read_bytes() == retained and case[4]["capture_calls"] == 1


@pytest.mark.asyncio
async def test_claim_readback_failure_prevents_every_db_and_source_await(
    case, tmp_path, monkeypatch
):
    owner = owner_for(case, tmp_path)
    with module._root_context(tmp_path) as directory:
        directory_type = type(directory)
    original = directory_type.read

    def changed(self, name, maximum):
        raw = original(self, name, maximum)
        return raw + b" " if name == "claim.json" else raw

    monkeypatch.setattr(directory_type, "read", changed)
    with pytest.raises(module.SourceCollectionError):
        await owner.capture_once(ordinal=0, expected_previous_receipt_sha256s=())
    assert (tmp_path / "batch-00000000" / "claim.json").is_file()
    assert case[4]["capture_calls"] == case[4]["role_calls"] == 0


@pytest.mark.asyncio
async def test_owner_rejects_saved_fake_and_other_task_before_capture(case, tmp_path):
    owner = owner_for(case, tmp_path)
    fake = object.__new__(module.DevelopmentValidationSourceOwnerV1)
    with pytest.raises(module.SourceCollectionError):
        await fake.capture_once(ordinal=0, expected_previous_receipt_sha256s=())
    with pytest.raises(module.SourceCollectionError):
        await asyncio.create_task(
            owner.capture_once(ordinal=0, expected_previous_receipt_sha256s=())
        )
    assert not (tmp_path / "batch-00000000").exists()
    receipt = await owner.capture_once(ordinal=0, expected_previous_receipt_sha256s=())
    assert receipt.status == "CAPTURED_POST_READ_VERIFIED"


@pytest.mark.asyncio
async def test_changed_retained_plan_prevents_claim_and_source(case, tmp_path):
    owner = owner_for(case, tmp_path)
    path = tmp_path / "plan.json"
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(module.SourceCollectionError):
        await owner.capture_once(ordinal=0, expected_previous_receipt_sha256s=())
    assert not (tmp_path / "batch-00000000").exists()
    assert case[4]["capture_calls"] == case[4]["role_calls"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["commit_ack", "readback"])
async def test_unknown_post_read_result_keeps_committed_evidence_and_never_retries(
    case, tmp_path, monkeypatch, stage
):
    observation = case[1]
    original = observation.append_new

    async def commit_unknown(**pins):
        await original(**pins)
        raise RuntimeError("synthetic lost commit acknowledgement")

    async def read_unknown(**pins):
        raise RuntimeError("synthetic readback timeout")

    monkeypatch.setattr(
        observation,
        "append_new" if stage == "commit_ack" else "read",
        commit_unknown if stage == "commit_ack" else read_unknown,
    )
    owner = owner_for(case, tmp_path)
    with pytest.raises(module.SourceCollectionError):
        await owner.capture_once(ordinal=0, expected_previous_receipt_sha256s=())
    assert case[5]["readback"] is not None and case[5]["append_calls"] == 1
    assert (tmp_path / "batch-00000000" / "claim.json").is_file()
    assert not (tmp_path / "batch-00000000" / "capture-v3.json").exists()
    receipt = module.SourceOrdinalReceiptV1.model_validate_json(
        (tmp_path / "batch-00000000" / "receipt.json").read_bytes()
    )
    assert receipt.status == "FAILED" and receipt.reason == "capture_unresolved"
    with pytest.raises(module.SourceCollectionError):
        await owner.capture_once(ordinal=0, expected_previous_receipt_sha256s=())
    assert case[4]["capture_calls"] == case[5]["append_calls"] == 1


@pytest.mark.asyncio
async def test_two_service_workers_cannot_start_duplicate_source_capture(
    case, monkeypatch
):
    original = checkpoint.PublicCheckpointCaptureService.capture
    started, release = asyncio.Event(), asyncio.Event()

    async def paused(self, **kwargs):
        started.set()
        await release.wait()
        return await original(self, **kwargs)

    monkeypatch.setattr(checkpoint.PublicCheckpointCaptureService, "capture", paused)

    async def first_worker():
        handle = await capture_handle(case)
        return case[0].consume_source_post_read_v3(
            handle, expected_plan_sha256=case[2].canonical_sha256()
        )

    task = asyncio.create_task(first_worker())
    await started.wait()
    with pytest.raises(checkpoint.PublicWitnessError, match="already_attempted"):
        await capture_handle(case)
    release.set()
    assert (await task).contract.available_at == case[5]["readback"].observed_at
    assert case[4]["capture_calls"] == case[5]["append_calls"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_pin", [False, 0, None, "wrong"])
async def test_wrong_consume_pin_burns_native_result(case, bad_pin):
    handle = await capture_handle(case)
    with pytest.raises(checkpoint.PublicWitnessError):
        case[0].consume_source_post_read_v3(handle, expected_plan_sha256=bad_pin)
    with pytest.raises(checkpoint.PublicWitnessError):
        case[0].consume_source_post_read_v3(
            handle, expected_plan_sha256=case[2].canonical_sha256()
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "defect", ["missing_claim", "partial", "hash", "payload", "root"]
)
async def test_readonly_reopen_rejects_incomplete_or_changed_storage_without_refill(
    case, tmp_path, monkeypatch, defect
):
    owner = owner_for(case, tmp_path)
    await owner.capture_once(ordinal=0, expected_previous_receipt_sha256s=())
    slot = tmp_path / "batch-00000000"
    if defect == "missing_claim":
        (slot / "claim.json").unlink()
    elif defect == "partial":
        (slot / "receipt.sha256").unlink()
    elif defect == "hash":
        (slot / "receipt.sha256").write_bytes(b"0" * 64 + b"\n")
    elif defect == "payload":
        path = slot / "capture-v3.json"
        path.write_bytes(path.read_bytes() + b" ")
    else:
        device, inode = module._root_identity(tmp_path)
        monkeypatch.setattr(module, "_root_identity", lambda _: (device, inode + 1))
    inventory = tuple(sorted(path.name for path in slot.iterdir()))
    with pytest.raises(module.SourceCollectionError):
        reader_for(case, tmp_path)
    assert tuple(sorted(path.name for path in slot.iterdir())) == inventory
    assert case[4]["capture_calls"] == case[5]["append_calls"] == 1


@pytest.mark.asyncio
async def test_readonly_missing_root_is_not_created_or_reinitialized(case, tmp_path):
    absent = tmp_path / "missing-root"
    with pytest.raises(module.SourceCollectionError):
        reader_for(case, absent)
    assert not absent.exists() and case[4]["capture_calls"] == 0


@pytest.mark.asyncio
async def test_private_capture_latest_syncs_back_even_when_capture_raises(
    case, monkeypatch
):
    original_service = case[0]
    latest = type("Latest", (), {"journal_key": original_service.genesis_sha256})()

    async def uncertain(self, **kwargs):
        assert self is not original_service
        self._latest = latest
        raise checkpoint.PublicWitnessError("synthetic uncertain capture")

    monkeypatch.setattr(checkpoint.PublicCheckpointCaptureService, "capture", uncertain)
    with pytest.raises(checkpoint.PublicWitnessError):
        await capture_handle(case)
    assert original_service._latest is latest


@pytest.mark.asyncio
@pytest.mark.parametrize("after_await", [3, 5, 7, 9, 11])
async def test_each_await_rechecks_database_provider_before_native_result(
    case, tmp_path, after_await
):
    case[4]["late_guard_failure"] = after_await
    owner = owner_for(case, tmp_path)
    with pytest.raises(module.SourceCollectionError):
        await owner.capture_once(ordinal=0, expected_previous_receipt_sha256s=())
    assert case[4]["database_guard_calls"] == after_await
    assert case[4]["capture_calls"] == (1 if after_await >= 7 else 0)
    assert not (tmp_path / "batch-00000000" / "capture-v3.json").exists()
    assert (
        module.SourceOrdinalReceiptV1.model_validate_json(
            (tmp_path / "batch-00000000" / "receipt.json").read_bytes()
        ).status
        == "FAILED"
    )
