"""Explicit synthetic Stage1/SQL/catalog seams; no native/PG/source acceptance."""

import asyncio
import base64
import copy
import json
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.source_primitives import canonical, sha
from app.mie.validation import development_validation_source_collection_v6 as finite
from app.mie.validation import native_availability_bundle_v6 as bundle
from app.mie.validation import native_availability_v6 as owner
from app.mie.validation import native_post_read_clock_retained_v6 as retained
from app.mie.validation import native_post_read_clock_v4 as stage1
from app.mie.validation import native_stage1_forensics_v6 as forensic
from tests.unit.mie import test_native_availability_v5 as fixture_v5

raw_case = fixture_v5.raw_case


@pytest.fixture
def flow6(raw_case, monkeypatch, tmp_path):
    # Reuse only the reviewed synthetic native/source/DB fixture. V4/V5
    # production implementations and saved contracts are not modified.
    monkeypatch.setattr(fixture_v5, "owner", owner)
    monkeypatch.setattr(fixture_v5, "finite", finite)
    monkeypatch.setattr(fixture_v5, "bundle", bundle)
    monkeypatch.setattr(
        finite,
        "DevelopmentValidationAvailabilityOwnerV5",
        finite.DevelopmentValidationAvailabilityOwnerV6,
        raising=False,
    )
    monkeypatch.setattr(
        owner,
        "snapshot_public_journal_v5",
        owner.snapshot_public_journal_v6,
        raising=False,
    )
    generator = fixture_v5.flow.__wrapped__(raw_case, monkeypatch, tmp_path)
    flow = next(generator)
    monkeypatch.setattr(
        owner,
        "snapshot_public_journal_v6",
        lambda journal: (flow.case.contract.checkpoint, flow.case.contract.files),
    )
    original = AsyncSession.execute
    state = flow.state
    state.update(health_calls=0, sample_calls=0, sample_mutation=None)

    def health():
        state["health_calls"] += 1
        index = state["health_calls"]
        if index == 1:
            return flow.case.clock.health_before.raw_bytes()
        if index == 2:
            state["offset"] = 20_000_000
            return flow.case.clock.health_after.raw_bytes()
        return fixture_v5.synthetic_health(
            flow.case.wall + state["offset"], flow.case.mono + state["offset"]
        ).raw_bytes()

    monkeypatch.setattr(owner.native_clock, "_native_observation_payload", health)

    async def execute(session, statement, parameters=None):
        if statement is not stage1._SAMPLE_SQL:
            return await original(session, statement, parameters)
        state["sample_calls"] += 1
        if state["sample_mutation"] == "timeout":
            raise TimeoutError("never serialized")
        if state["sample_mutation"] == "cancel":
            raise asyncio.CancelledError()
        if state["sample_mutation"] == "actual_cancel":
            asyncio.current_task().cancel()
            await asyncio.sleep(0)
        result = await original(session, statement, parameters)
        values = result.mappings().all()
        if state["sample_mutation"] == "identity":
            values[0]["backend_pid"] += 1
        elif state["sample_mutation"] == "time":
            values[0]["sampled_at"] = flow.case.clock.source_v3.available_at
        elif state["sample_mutation"] == "bool":
            values[0]["backend_pid"] = True
        elif state["sample_mutation"] == "unknown":
            values[0]["sampled_at"] = object()
        elif state["sample_mutation"] == "duplicate_rows":
            values.append(dict(values[0]))
        elif state["sample_mutation"] == "zero_rows":
            values.clear()
        elif state["sample_mutation"] == "missing":
            values[0].pop("sampled_at")
        elif state["sample_mutation"] == "null":
            values[0]["sampled_at"] = None

        def fetchmany(limit):
            assert limit == 2
            if state["sample_mutation"] == "materialize":
                raise ValueError("never serialized")
            return values[:limit]

        return SimpleNamespace(mappings=lambda: SimpleNamespace(fetchmany=fetchmany))

    monkeypatch.setattr(AsyncSession, "execute", execute)
    yield flow
    generator.close()


async def capture(flow):
    return await flow.instance().capture_once(
        ordinal=0, expected_previous_receipt_sha256s=()
    )


def originals(flow):
    path = flow.root / "batch-00000000/stage1"
    return {p.name: p.read_bytes() for p in path.iterdir()}


def receipt(flow):
    return finite.CollectionReadOnlyV6.open(
        root=flow.root,
        plan=flow.plan,
        expected_plan_sha256=flow.plan.canonical_sha256(),
    ).read(0)


@pytest.mark.asyncio
async def test_v6_full_owned_positive_transcript_db46_bundle_and_one_use(flow6):
    token = await capture(flow6)
    evidence_pin = owner.evidence_sha256_v6(token)
    decision, rows = owner.consume_for_current_future_decision_v6(
        token, expected_evidence_sha256=evidence_pin
    )
    files = originals(flow6)
    assert set(files) == set(forensic.NAMES)
    result = receipt(flow6)
    assert result.forensic_status == "completed"
    snapshot = forensic.replay_forensic_files_v6(
        files, expected_claim_sha256=result.claim_sha256
    )
    assert (
        snapshot.sha256
        == result.stage1_forensic_sha256
        == decision.contract.stage1_forensic_sha256
    )
    assert files["health-before.raw"] == flow6.case.clock.health_before.raw_bytes()
    assert files["health-after.raw"] == flow6.case.clock.health_after.raw_bytes()
    bundle_raw = (flow6.root / "batch-00000000/bundle-v6.json").read_bytes()
    replay = bundle.replay_native_availability_bundle_v6(
        bundle_raw, expected_sha256=decision.contract.bundle_sha256
    )
    assert base64.b64decode(replay.stage1_forensic_base64) == snapshot.payload
    assert flow6.state["health_calls"] == 3 and flow6.state["sample_calls"] == 1
    assert (
        flow6.state["capture_count"] == flow6.state["seal_count"] == 1
        and len(rows) == 2
    )
    with pytest.raises(ValueError):
        owner.consume_for_current_future_decision_v6(
            token, expected_evidence_sha256=evidence_pin
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["unobserved", "malformed"])
async def test_before_health_raw_retained_before_original_validation(
    flow6, monkeypatch, kind
):
    raw = (
        canonical(
            {
                "schema_version": owner.native_clock.CLOCK_RESULT_VERSION,
                "outcome": "unobserved",
                "observation": None,
            }
        )
        if kind == "unobserved"
        else b"malformed actual payload"
    )
    monkeypatch.setattr(owner.native_clock, "_native_observation_payload", lambda: raw)
    with pytest.raises(ValueError):
        await capture(flow6)
    files = originals(flow6)
    assert files["health-before.raw"] == raw and "sample.json" not in files
    assert json.loads(files["outcome.json"])["status"] == "failed"
    assert (
        receipt(flow6).status == "FAILED_RETAINED" and flow6.state["sample_calls"] == 0
    )
    assert flow6.state["capture_count"] == 1 and flow6.state["seal_count"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "damage",
    [
        "timeout",
        "cancel",
        "materialize",
        "identity",
        "time",
        "bool",
        "unknown",
        "duplicate_rows",
        "zero_rows",
        "missing",
        "null",
    ],
)
async def test_original_sql_partial_or_invalid_values_retained_no_resample(
    flow6, damage
):
    flow6.state["sample_mutation"] = damage
    expected = asyncio.CancelledError if damage == "cancel" else ValueError
    with pytest.raises(expected):
        await capture(flow6)
    files = originals(flow6)
    sample = json.loads(files["sample.json"])
    assert sample["sample_before"] == flow6.case.clock.sample_before.model_dump()
    assert sample["sql_requested"] is True
    assert (
        sample["sample_after"] is None
        if damage in {"timeout", "cancel", "materialize"}
        else sample["sample_after"] == flow6.case.clock.sample_after.model_dump()
    )
    assert json.loads(files["outcome.json"])["status"] == (
        "cancelled" if damage == "cancel" else "failed"
    )
    assert flow6.state["health_calls"] == (2 if damage == "time" else 1)
    assert flow6.state["sample_calls"] == 1
    assert flow6.state["capture_count"] == 1 and flow6.state["seal_count"] == 0
    assert receipt(flow6).status == "FAILED_RETAINED"
    if damage == "duplicate_rows":
        assert (
            sample["row_cardinality"] == "at_least_two"
            and sample["rows_may_be_truncated"] is True
        )
    if damage == "zero_rows":
        assert sample["row_cardinality"] == "zero" and sample["observed_rows"] == []
    if damage in {"missing", "null"}:
        cells = {c["column"]: c["decoded"] for c in sample["observed_rows"][0]}
        assert (
            ("sampled_at" not in cells)
            if damage == "missing"
            else cells["sampled_at"] == {"type": "null"}
        )
    with pytest.raises(ValueError):
        await capture(flow6)


@pytest.mark.asyncio
async def test_sample_b_failure_keeps_actual_rows_and_a_without_replacement(
    flow6, monkeypatch
):
    original = stage1._stamp
    count = 0

    def stamp():
        nonlocal count
        count += 1
        if count == 3:
            raise ValueError("native stamp failure detail not retained")
        return original()

    monkeypatch.setattr(stage1, "_stamp", stamp)
    with pytest.raises(ValueError):
        await capture(flow6)
    value = json.loads(originals(flow6)["sample.json"])
    assert value["sample_after"] is None and value["execute_returned"] is True
    assert (
        value["materialization_completed"] is True and value["row_cardinality"] == "one"
    )
    assert value["last_observed_phase"] == "sample_b" and count == 3
    assert (
        flow6.state["sample_calls"] == 1 and receipt(flow6).status == "FAILED_RETAINED"
    )


@pytest.mark.asyncio
async def test_sample_a_failure_explicitly_records_no_sql_no_row_no_replacement(
    flow6, monkeypatch
):
    original = stage1._stamp
    count = 0

    def stamp():
        nonlocal count
        count += 1
        if count == 2:
            raise ValueError("native A unavailable; not serialized")
        return original()

    monkeypatch.setattr(stage1, "_stamp", stamp)
    with pytest.raises(ValueError):
        await capture(flow6)
    sample = json.loads(originals(flow6)["sample.json"])
    assert sample["sample_before"] is None and sample["sample_after"] is None
    assert sample["sql_requested"] is False and sample["observed_rows"] == []
    assert (
        sample["last_observed_phase"] == "sample_a"
        and sample["row_cardinality"] == "unavailable"
    )
    assert (
        count == 2
        and flow6.state["sample_calls"] == 0
        and receipt(flow6).status == "FAILED_RETAINED"
    )


@pytest.mark.asyncio
async def test_after_health_raw_retained_even_when_rejected_without_replacement(
    flow6, monkeypatch
):
    calls = 0
    rejected = b"original rejected after health"

    def health():
        nonlocal calls
        calls += 1
        return flow6.case.clock.health_before.raw_bytes() if calls == 1 else rejected

    monkeypatch.setattr(owner.native_clock, "_native_observation_payload", health)
    with pytest.raises(ValueError):
        await capture(flow6)
    files = originals(flow6)
    assert files["health-after.raw"] == rejected and "sample.json" in files
    assert json.loads(files["outcome.json"])["last_observed_phase"] == "health_after"
    assert (
        calls == 2
        and flow6.state["sample_calls"] == 1
        and flow6.state["seal_count"] == 0
    )
    assert receipt(flow6).status == "FAILED_RETAINED"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [None, {}, bytearray(b"x"), b"", b"x" * (forensic.MAX_HEALTH + 1)],
    ids=["none", "mapping", "mutable_bytes", "empty", "oversize"],
)
async def test_unsupported_health_never_stringified_or_replaced(
    flow6, monkeypatch, bad
):
    monkeypatch.setattr(owner.native_clock, "_native_observation_payload", lambda: bad)
    with pytest.raises(ValueError):
        await capture(flow6)
    files = originals(flow6)
    assert set(files) == {"attempt.json", "outcome.json"}
    assert json.loads(files["outcome.json"])["missing_files"] == [
        "health-before.raw",
        "sample.json",
        "health-after.raw",
    ]
    assert flow6.state["sample_calls"] == 0 and flow6.state["seal_count"] == 0


@pytest.mark.asyncio
async def test_saved_pack_or_forged_retained_owner_cannot_issue_or_restore(flow6):
    fake = object.__new__(forensic.Stage1RetentionOwnerV6)
    with pytest.raises(ValueError):
        forensic.require_pending(fake)
    with pytest.raises(ValueError):
        retained.consume_retained_post_read_clock_v6(
            object.__new__(retained.RetainedPostReadClockSampleV6)
        )
    token = await capture(flow6)
    files = originals(flow6)
    result = receipt(flow6)
    snapshot = forensic.replay_forensic_files_v6(
        files, expected_claim_sha256=result.claim_sha256
    )
    with pytest.raises(ValueError):
        retained.consume_retained_post_read_clock_v6(snapshot)
    with pytest.raises(ValueError):
        owner.consume_for_current_future_decision_v6(
            snapshot, expected_evidence_sha256=owner.evidence_sha256_v6(token)
        )


@pytest.mark.asyncio
async def test_actual_finite_claim_issues_retention_only_once_and_foreign_task_denied(
    flow6, monkeypatch
):
    original = owner._new_retention_owner
    observed = []

    def acquire(claim):
        result = original(claim)
        observed.append((claim, result))
        return result

    monkeypatch.setattr(owner, "_new_retention_owner", acquire)
    token = await capture(flow6)
    assert len(observed) == 1
    claim, retention = observed[0]
    with pytest.raises(ValueError):
        forensic._new_retention_owner(claim)
    with pytest.raises(ValueError):
        forensic._new_retention_owner(
            SimpleNamespace(state=claim.state, claim=claim.claim)
        )

    async def foreign():
        with pytest.raises(ValueError):
            forensic.readback(retention)

    await asyncio.create_task(foreign())
    assert forensic.readback(retention).status == "completed"
    assert owner.evidence_sha256_v6(token)


@pytest.mark.asyncio
async def test_stage1_raw_change_after_completion_denies_capability_read(flow6):
    token = await capture(flow6)
    path = flow6.root / "batch-00000000/stage1/health-before.raw"
    path.write_bytes(b"changed original")
    with pytest.raises(ValueError):
        owner.evidence_sha256_v6(token)
    assert token not in owner._READY
    assert path.read_bytes() == b"changed original"


@pytest.mark.asyncio
async def test_v5_reader_does_not_accept_v6_identity_or_files(flow6):
    flow6.instance()
    from app.mie.validation.development_validation_source_collection_v5 import (
        CollectionReadOnlyV5,
    )

    with pytest.raises(ValueError):
        CollectionReadOnlyV5.open(
            root=flow6.root,
            plan=flow6.plan,
            expected_plan_sha256=flow6.plan.canonical_sha256(),
        )


@pytest.mark.asyncio
async def test_actual_task_cancellation_preserves_original_partial_and_burn(flow6):
    flow6.state["sample_mutation"] = "actual_cancel"
    task = asyncio.current_task()
    try:
        with pytest.raises(asyncio.CancelledError):
            await capture(flow6)
        assert task.cancelling() == 1
        files = originals(flow6)
        sample = json.loads(files["sample.json"])
        assert sample["sample_before"] is not None and sample["sample_after"] is None
        assert sample["sql_requested"] is True and sample["execute_returned"] is False
        assert sample["observed_rows"] == []
        assert json.loads(files["outcome.json"])["status"] == "cancelled"
        assert receipt(flow6).reason == "cancelled"
        assert flow6.state["sample_calls"] == 1 and flow6.state["health_calls"] == 1
        assert not owner._READY and flow6.state["seal_count"] == 0
    finally:
        task.uncancel()
    with pytest.raises(ValueError):
        await capture(flow6)


@pytest.mark.asyncio
async def test_expiry_after_sample_cannot_block_owned_failure_retention(
    flow6, monkeypatch
):
    original = finite._write_stage1

    def expire_after_write(claim, name, raw):
        original(claim, name, raw)
        if name == "sample.json":
            monkeypatch.setattr(
                finite,
                "time",
                SimpleNamespace(monotonic_ns=lambda: claim.deadline_ns + 1),
            )

    monkeypatch.setattr(finite, "_write_stage1", expire_after_write)
    with pytest.raises(ValueError):
        await capture(flow6)
    files = originals(flow6)
    assert json.loads(files["outcome.json"])["status"] == "failed"
    sample = json.loads(files["sample.json"])
    assert sample["sample_before"] is not None and sample["sample_after"] is not None
    assert receipt(flow6).status == "FAILED_RETAINED"
    assert flow6.state["sample_calls"] == 1 and flow6.state["seal_count"] == 0


@pytest.mark.asyncio
async def test_underlying_flush_failure_preserves_journal_and_never_issues(
    flow6, monkeypatch
):
    from app.trade_evidence import storage

    raw = flow6.case.clock.health_before.raw_bytes()
    original = storage._write_fd
    calls = 0
    journal_before = dict(flow6.case.directory.content)

    def fail_flush(fd, payload):
        nonlocal calls
        if payload == raw:
            calls += 1
            # Actual owned file bytes are written once; flush acknowledgment
            # is deliberately absent. No other file/journal gets this fault.
            view = memoryview(payload)
            while view:
                written = storage.os.write(fd, view)
                assert written > 0
                view = view[written:]
            raise OSError("synthetic fsync unavailable; not serialized")
        return original(fd, payload)

    monkeypatch.setattr(storage, "_write_fd", fail_flush)
    with pytest.raises(ValueError):
        await capture(flow6)
    assert dict(flow6.case.directory.content) == journal_before
    assert (
        calls == 1
        and flow6.state["sample_calls"] == 0
        and flow6.state["seal_count"] == 0
    )
    assert not owner._READY
    files = originals(flow6)
    if "health-before.raw" in files:
        assert files["health-before.raw"] == raw
        assert json.loads(files["outcome.json"])["durability_unconfirmed_files"] == [
            "health-before.raw"
        ]
    else:
        assert "health-before.raw" in json.loads(files["outcome.json"])["missing_files"]
    assert receipt(flow6).status == "FAILED_RETAINED"


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["health-before.raw", "sample.json", "outcome.json"])
async def test_late_forensic_publish_failure_keeps_bytes_and_never_issues(
    flow6, monkeypatch, name
):
    original = finite._write_stage1

    def late_failure(claim, actual, raw):
        original(claim, actual, raw)
        if actual == name:
            raise OSError("synthetic post-fsync/readback failure; never serialized")

    monkeypatch.setattr(finite, "_write_stage1", late_failure)
    with pytest.raises(ValueError):
        await capture(flow6)
    files = originals(flow6)
    assert name in files and not owner._READY and flow6.state["seal_count"] == 0
    assert flow6.state["capture_count"] == 1 and flow6.state["sample_calls"] <= 1
    before = {
        p.relative_to(flow6.root): p.read_bytes()
        for p in flow6.root.rglob("*")
        if p.is_file()
    }
    readback = finite.inspect_partial_forensics_v6(
        root=flow6.root,
        plan=flow6.plan,
        expected_plan_sha256=flow6.plan.canonical_sha256(),
        ordinal=0,
    )
    assert (
        readback.execution_authority is False
        and readback.replay_grants_capability is False
    )
    if name == "outcome.json":
        assert json.loads(files[name])["status"] == "completed"
        snapshot = forensic.replay_forensic_files_v6(
            files, expected_claim_sha256=readback.claim_sha256
        )
        assert (
            snapshot.status == "completed"
            and receipt(flow6).status == "FAILED_RETAINED"
        )
        with pytest.raises(ValueError):
            owner.evidence_sha256_v6(snapshot)
        with pytest.raises(ValueError):
            retained.consume_retained_post_read_clock_v6(snapshot)
    assert {
        p.relative_to(flow6.root): p.read_bytes()
        for p in flow6.root.rglob("*")
        if p.is_file()
    } == before
    with pytest.raises(ValueError):
        owner.consume_for_current_future_decision_v6(
            readback, expected_evidence_sha256="0" * 64
        )
    with pytest.raises(ValueError):
        await capture(flow6)


@pytest.mark.asyncio
async def test_partial_restart_read_only_with_missing_receipt_cannot_refill(
    flow6, monkeypatch
):
    original = forensic.failed
    monkeypatch.setattr(forensic, "failed", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        finite,
        "_publish",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("late receipt refused")),
    )
    flow6.state["sample_mutation"] = "timeout"
    with pytest.raises(ValueError):
        await capture(flow6)
    monkeypatch.setattr(forensic, "failed", original)
    files = originals(flow6)
    assert "sample.json" in files and "outcome.json" not in files
    with pytest.raises(ValueError):
        receipt(flow6)
    before = {
        p.relative_to(flow6.root): p.read_bytes()
        for p in flow6.root.rglob("*")
        if p.is_file()
    }
    result = finite.inspect_partial_forensics_v6(
        root=flow6.root,
        plan=flow6.plan,
        expected_plan_sha256=flow6.plan.canonical_sha256(),
        ordinal=0,
    )
    assert (
        result.forensic_status == "unresolved"
        and result.published_receipt_sha256 is None
    )
    assert result.stage1_forensic_sha256 is None and result.execution_authority is False
    assert {
        p.relative_to(flow6.root): p.read_bytes()
        for p in flow6.root.rglob("*")
        if p.is_file()
    } == before
    with pytest.raises(ValueError):
        finite.DevelopmentValidationAvailabilityOwnerV6.initialize(
            root=flow6.root,
            plan=flow6.plan,
            expected_plan_sha256=flow6.plan.canonical_sha256(),
            capture_service=flow6.service,
            observation_repository=flow6.observation,
            custody_session_factory=flow6.factories[2],
        )
    with pytest.raises(ValueError):
        await capture(flow6)


@pytest.mark.asyncio
async def test_strict_replay_rejects_rehashed_unknown_alias_missing_and_typed_damage(
    flow6,
):
    await capture(flow6)
    baseline = originals(flow6)
    claim = receipt(flow6).claim_sha256

    def attempt_extra(v):
        v["unknown"] = False

    def bool_alias(v):
        v["sql_requested"] = 1

    def unknown(v):
        v["unknown"] = 1

    def missing(v):
        del v["execute_returned"]

    def type_alias(v):
        cell = next(c for c in v["observed_rows"][0] if c["decoded"]["type"] == "int")
        cell["decoded"]["value"] = True

    def missing_value(v):
        cell = next(c for c in v["observed_rows"][0] if c["decoded"]["type"] == "int")
        del cell["decoded"]["value"]

    def extra_value(v):
        v["observed_rows"][0][0]["decoded"]["unknown"] = "hidden"

    def stamp_alias(v):
        v["sample_after"]["utc_ns"] = True

    def impossible_phase(v):
        v["last_observed_phase"] = "sample_sql"

    def row_limit_alias(v):
        v["materialization_limit"] = True

    mutations = [
        ("attempt.json", attempt_extra),
        *[
            ("sample.json", f)
            for f in (
                bool_alias,
                unknown,
                missing,
                type_alias,
                missing_value,
                extra_value,
                stamp_alias,
                impossible_phase,
                row_limit_alias,
            )
        ],
    ]
    for name, mutate in mutations:
        files = dict(baseline)
        value = copy.deepcopy(json.loads(files[name]))
        mutate(value)
        files[name] = canonical(value)
        outcome = json.loads(files["outcome.json"])
        outcome["files"] = [
            {"name": n, "sha256": sha(files[n])} for n in forensic.NAMES[:-1]
        ]
        files["outcome.json"] = canonical(outcome)
        with pytest.raises((ValueError, KeyError)):
            forensic.replay_forensic_files_v6(files, expected_claim_sha256=claim)
    for extra in ("sample.raw", "unknown.json"):
        with pytest.raises(ValueError):
            forensic.replay_forensic_files_v6(
                {**baseline, extra: b"x"}, expected_claim_sha256=claim
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["journal_id", "journal_genesis_sha256"])
async def test_success_bundle_rejects_rehashed_transcript_journal_substitution(
    flow6, field
):
    await capture(flow6)
    data = json.loads((flow6.root / "batch-00000000/bundle-v6.json").read_bytes())
    files = originals(flow6)
    header = json.loads(files["attempt.json"])
    header[field] = "f" * (32 if field == "journal_id" else 64)
    files["attempt.json"] = canonical(header)
    outcome = json.loads(files["outcome.json"])
    outcome["files"] = [
        {"name": name, "sha256": sha(files[name])} for name in forensic.NAMES[:-1]
    ]
    files["outcome.json"] = canonical(outcome)
    # A self-consistent forensic hash alone cannot replace original lineage.
    snapshot = forensic.replay_forensic_files_v6(
        files, expected_claim_sha256=data["ordinal_claim_sha256"]
    )
    data["stage1_forensic_base64"] = base64.b64encode(snapshot.payload).decode("ascii")
    data["stage1_forensic_sha256"] = snapshot.sha256
    with pytest.raises(ValueError, match="availability_stage1_clock_binding"):
        bundle.NativeAvailabilityBundleV6.model_validate_json(canonical(data))
