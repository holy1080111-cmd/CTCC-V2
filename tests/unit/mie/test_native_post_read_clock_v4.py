"""Explicit synthetic clock/SQL-result seams; no native, DB or source acceptance."""

from __future__ import annotations

import asyncio
import base64
import copy
import json
import os
import pickle
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from types import SimpleNamespace

import asyncpg
import pytest
from sqlalchemy.engine.result import result_tuple
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.database.repositories.public_receipt_post_read_observation import (
    PublicReceiptPostReadRepository,
)
from app.database.repositories.public_receipt_witness import (
    PublicReceiptWitnessRepository,
    WitnessRevision,
    _record_hash,
    verify_witness_chain,
)
from app.domain.source_primitives import ClockStamp, canonical, sha
from app.mie.validation import native_post_read_clock_v4 as owner
from app.mie.validation import public_checkpoint_service as source_owner
from app.mie.validation.artifact import _freeze
from app.mie.validation.post_publication_availability_v3 import (
    PostPublicationCaptureV3,
    freeze_post_publication_capture_v3,
)
from app.mie.validation.post_publication_availability_v4 import (
    NativePostReadHealthV4,
    PostPublicationClockEvidenceV4,
    replay_post_read_clock_v4,
    utc_nanoseconds,
)
from tests.unit.research.test_public_clock_v2 import EN, resign, status_change
from tests.unit.research.test_public_clock_v2 import fixture as clock_fixture

pytest_plugins = (
    "tests.unit.mie.test_gate3_post_publication_availability_v3",
    "tests.unit.mie.test_development_validation_source_database_guard_v1",
)


@dataclass(frozen=True, slots=True)
class WitnessFunctionRow:
    """Exact public_receipt_witness_read table-row shape, including SQL-only time."""

    journal_key: str
    revision: int
    journal_id: str
    root_device: int
    root_inode: int
    transition: str
    state: str
    operation_id: str | None
    plan_sha256: str | None
    attempt_outcome: str | None
    previous_record_sha256: str | None
    checkpoint_json: str
    checkpoint_sha256: str
    record_sha256: str
    recorded_at: datetime


def coherent_witness_rows(frozen, checkpoint):
    """Build a synthetic genuine-hash chain; never add fields to WitnessRevision."""
    initial = type(checkpoint).model_validate(
        {
            **checkpoint.model_dump(),
            "sequence": 0,
            "head_sha256": checkpoint.genesis_sha256,
            "attempt_sequence": 0,
            "attempt_head_sha256": checkpoint.genesis_sha256,
        }
    )
    attempt = type(checkpoint).model_validate(
        {
            **initial.model_dump(),
            "attempt_sequence": checkpoint.attempt_sequence,
            "attempt_head_sha256": checkpoint.attempt_head_sha256,
        }
    )
    rows = []
    make_raw_row = result_tuple(tuple(WitnessFunctionRow.__dataclass_fields__))
    plan = frozen.contract.v2_capture.plan_sha256
    observed = frozen.contract.post_read_observation.witness_recorded_at
    for index, (transition, state, point) in enumerate(
        (
            ("initialize", "idle", initial),
            ("open_attempt", "open", initial),
            ("append_attempt", "attempt_anchored", attempt),
            ("append_capture", "idle", checkpoint),
        )
    ):
        value = WitnessRevision(
            journal_key=checkpoint.genesis_sha256,
            revision=index,
            journal_id="b" * 32,
            root_device=checkpoint.root_device,
            root_inode=checkpoint.root_inode,
            transition=transition,
            state=state,
            operation_id=None if index == 0 else "c" * 32,
            plan_sha256=None if index == 0 else plan,
            attempt_outcome="completed_collection" if index >= 2 else None,
            previous_record_sha256=None if index == 0 else rows[-1].record_sha256,
            checkpoint=point,
            checkpoint_sha256=point.canonical_sha256(),
            record_sha256="0" * 64,
        )
        raw_json = point.canonical_bytes().decode("utf-8")
        value = replace(value, record_sha256=_record_hash(value, raw_json))
        row = WitnessFunctionRow(
            **{
                name: getattr(value, name)
                for name in value.__dataclass_fields__
                if name != "checkpoint"
            },
            checkpoint_json=raw_json,
            recorded_at=observed - timedelta(seconds=3 - index),
        )
        rows.append(
            make_raw_row(tuple(getattr(row, name) for name in row.__dataclass_fields__))
        )
    selected = verify_witness_chain(tuple(rows))
    assert type(selected) is WitnessRevision and not hasattr(selected, "recorded_at")
    v2_data = frozen.contract.v2_capture.model_dump(mode="python")
    v2_data["witness_record_sha256"] = selected.record_sha256
    v2 = type(frozen.contract.v2_capture).model_validate(v2_data)
    v3_data = frozen.contract.model_dump(mode="python")
    v3_data["v2_capture"] = v2
    v3_data["v2_capture_sha256"] = v2.canonical_sha256()
    v3_data["post_read_observation"]["witness_record_sha256"] = selected.record_sha256
    v3_data["post_read_observation"]["v2_capture_sha256"] = v2.canonical_sha256()
    corrected = _freeze(
        PostPublicationCaptureV3.model_validate(v3_data),
        contract_type=PostPublicationCaptureV3,
    )
    return corrected, tuple(rows)


def synthetic_health(wall, monotonic):
    observed = clock_fixture()
    stamp = {"utc_ns": wall, "monotonic_ns": monotonic}
    observed["sample"] = stamp
    for kind in ("host_before", "status", "host_after"):
        observed["diagnostic"][kind]["request_start"] = dict(stamp)
        observed["diagnostic"][kind]["completed"] = dict(stamp)
    raw = canonical(
        {
            "schema_version": owner.native_clock.CLOCK_RESULT_VERSION,
            "outcome": "accepted",
            "observation": resign(observed),
        }
    )
    return NativePostReadHealthV4(
        raw_base64=base64.b64encode(raw).decode("ascii"), raw_sha256=sha(raw)
    )


@pytest.fixture
async def evidence_case(observation_case):
    kwargs, _, observation, _ = observation_case
    source = await freeze_post_publication_capture_v3(
        observation_repository=observation, **kwargs
    )
    source, witness_rows = coherent_witness_rows(source, kwargs["journal"].checkpoint)
    kwargs = {**kwargs, "_clock_v4_witness_rows": witness_rows}
    wall = utc_nanoseconds(source.contract.available_at + timedelta(seconds=10))
    mono = 10**9

    def stamp(offset):
        return {"utc_ns": wall + offset, "monotonic_ns": mono + offset}

    identity = {
        "backend_pid": 123,
        "database_oid": 456,
        "login_oid": 789,
        "transaction_start": source.contract.available_at + timedelta(seconds=9),
        "postmaster_start": source.contract.available_at - timedelta(days=1),
        "transaction_xid": None,
        "server_version_num": 170011,
        "server_build": "PostgreSQL 17.11 on x86_64-windows, compiled by msvc-19.44.35222, 64-bit",
        "transaction_read_only": "on",
    }
    values = {
        "source_v3": source.contract,
        "source_v3_sha256": source.sha256,
        "health_before": synthetic_health(wall, mono),
        "data_readback_completed": stamp(1_000_000),
        "sample_before": stamp(2_000_000),
        "database_sample": {
            "identity": identity,
            "sampled_at": source.contract.available_at
            + timedelta(seconds=10, milliseconds=3),
        },
        "sample_after": stamp(4_000_000),
        "health_after": synthetic_health(wall + 5_000_000, mono + 5_000_000),
        "sample_observation_completed": stamp(6_000_000),
    }
    return values, source, kwargs


@pytest.mark.asyncio
async def test_pure_evidence_replays_deterministically_without_authority(evidence_case):
    values, _, _ = evidence_case
    result = _freeze(
        PostPublicationClockEvidenceV4.model_validate(values),
        contract_type=PostPublicationClockEvidenceV4,
    )
    replayed = replay_post_read_clock_v4(result.payload, expected_sha256=result.sha256)
    assert replayed == result.contract
    assert replayed.quantization_cell_contained_in_native_bracket
    assert not replayed.db0046_custody_verified
    assert not replayed.db0035_schema_custody_verified
    assert not replayed.witness_schema_custody_verified
    assert not replayed.database_host_identified
    assert not replayed.database_binary_attested
    assert not replayed.independent_absolute_utc_verified
    assert not replayed.future_decision_authority
    assert not replayed.execution_authority
    assert replayed.source_v3.canonical_sha256() == replayed.source_v3_sha256
    with pytest.raises(ValueError):
        owner.consume_internal_post_read_clock_v4(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "damage",
    (
        "lower_quantization_edge",
        "upper_quantization_edge",
        "clock_reversal",
        "clock_jump",
        "bracket_timeout",
        "after_health_overlap",
        "source_hash",
        "health_raw_hash",
        "future_decision_authority",
        "db_custody",
        "compiler",
        "version",
        "missing_xid",
        "non_filetime_grid",
        "old_source_future",
        "stale_health",
    ),
)
async def test_pure_boundary_rejects_changed_units_lineage_and_claims(
    evidence_case, damage
):
    values, _, _ = evidence_case
    data = PostPublicationClockEvidenceV4.model_validate(values).model_dump(
        mode="python"
    )
    db_ns = utc_nanoseconds(data["database_sample"]["sampled_at"])
    if damage == "lower_quantization_edge":
        data["sample_before"]["utc_ns"] = db_ns + 100
        data["sample_before"]["monotonic_ns"] += 1_000_100
    elif damage == "upper_quantization_edge":
        data["sample_after"]["utc_ns"] = db_ns + 800
        data["sample_after"]["monotonic_ns"] -= 999_200
    elif damage == "clock_reversal":
        data["sample_after"]["monotonic_ns"] = data["sample_before"]["monotonic_ns"] - 1
    elif damage == "clock_jump":
        data["sample_after"]["utc_ns"] += 6_000_000
    elif damage == "bracket_timeout":
        for name in ("sample_after", "sample_observation_completed"):
            data[name]["utc_ns"] += 6_000_000_000
            data[name]["monotonic_ns"] += 6_000_000_000
    elif damage == "after_health_overlap":
        data["health_after"] = data["health_before"]
    elif damage == "source_hash":
        data["source_v3_sha256"] = "0" * 64
    elif damage == "health_raw_hash":
        data["health_before"]["raw_sha256"] = "0" * 64
    elif damage == "future_decision_authority":
        data[damage] = True
    elif damage == "db_custody":
        data["db0046_custody_verified"] = True
    elif damage == "compiler":
        data["database_sample"]["identity"]["server_build"] = "PostgreSQL 17.11 MinGW"
    elif damage == "version":
        data["database_sample"]["identity"]["server_version_num"] = 170010
    elif damage == "missing_xid":
        del data["database_sample"]["identity"]["transaction_xid"]
    elif damage == "non_filetime_grid":
        data["sample_before"]["utc_ns"] += 1
    elif damage == "old_source_future":
        for name in (
            "data_readback_completed",
            "sample_before",
            "sample_after",
            "sample_observation_completed",
        ):
            data[name]["utc_ns"] -= 20_000_000_000
        data["database_sample"]["sampled_at"] -= timedelta(seconds=20)
        for name in ("health_before", "health_after"):
            record = json.loads(base64.b64decode(data[name]["raw_base64"]))
            observed = record["observation"]
            observed["sample"]["utc_ns"] -= 20_000_000_000
            for kind in ("host_before", "status", "host_after"):
                for edge in ("request_start", "completed"):
                    observed["diagnostic"][kind][edge]["utc_ns"] -= 20_000_000_000
            record["observation"] = resign(observed)
            raw = canonical(record)
            data[name] = {
                "raw_base64": base64.b64encode(raw).decode("ascii"),
                "raw_sha256": sha(raw),
            }
    elif damage == "stale_health":
        record = json.loads(base64.b64decode(data["health_before"]["raw_base64"]))
        observed = record["observation"]
        status_change(observed, EN.replace("10.1250000s", "900.0000000s"))
        record["observation"] = resign(observed)
        raw = canonical(record)
        data["health_before"] = {
            "raw_base64": base64.b64encode(raw).decode("ascii"),
            "raw_sha256": sha(raw),
        }
    with pytest.raises(ValueError):
        PostPublicationClockEvidenceV4.model_validate(data)


@pytest.mark.asyncio
async def test_exact_whole_quantization_cell_endpoints_are_accepted(evidence_case):
    values, _, _ = evidence_case
    data = PostPublicationClockEvidenceV4.model_validate(values).model_dump(
        mode="python"
    )
    sampled = utc_nanoseconds(data["database_sample"]["sampled_at"])
    old_a = data["sample_before"]["utc_ns"]
    old_b = data["sample_after"]["utc_ns"]
    data["sample_before"]["utc_ns"] = sampled
    data["sample_before"]["monotonic_ns"] += sampled - old_a
    data["sample_after"]["utc_ns"] = sampled + 900
    data["sample_after"]["monotonic_ns"] += sampled + 900 - old_b
    result = PostPublicationClockEvidenceV4.model_validate(data)
    assert result.sample_after.utc_ns - result.sample_before.utc_ns == 900


def source_token(source, kwargs):
    service = source_owner.PublicCheckpointCaptureService(
        source_owner._ISSUER,
        kwargs["journal"],
        kwargs["witness_repository"],
        "a" * 32,
        None,
    )
    token = source_owner.NativeSourcePostReadCapture(source_owner._SOURCE_ISSUER)
    source_owner._SOURCE_RESULTS[token] = (
        service,
        source_owner._source_context(),
        source.contract.v2_capture.plan_sha256,
        source,
    )
    return service, token


@pytest.mark.asyncio
async def test_invalid_native_factory_burns_real_registry_input_before_health(
    evidence_case, monkeypatch
):
    _, frozen, kwargs = evidence_case
    service, token = source_token(frozen, kwargs)
    witness = PublicReceiptWitnessRepository(None)
    observation = PublicReceiptPostReadRepository(None, witness)
    service._repository = witness
    calls = []
    monkeypatch.setattr(owner, "os", SimpleNamespace(name="nt", getpid=os.getpid))
    monkeypatch.setattr(owner, "_health", lambda: calls.append("forbidden"))
    with pytest.raises(owner.NativePostReadClockError):
        await owner.acquire_native_post_read_clock_v4(
            service=service,
            source=token,
            expected_plan_sha256=frozen.contract.v2_capture.plan_sha256,
            observation_repository=observation,
        )
    assert token not in source_owner._SOURCE_RESULTS and not calls
    with pytest.raises(ValueError):
        service.consume_source_post_read_v3(
            token, expected_plan_sha256=frozen.contract.v2_capture.plan_sha256
        )


@pytest.fixture
async def synthetic_owner_case(evidence_case, monkeypatch):
    """Explicit native/SQL-result substitutes; not an authenticated DB/source test."""
    values, frozen, kwargs = evidence_case
    events = []
    engine = create_async_engine(
        "postgresql+asyncpg://ctcc_fixture@127.0.0.1/ctcc_no_io", poolclass=NullPool
    )
    witness_engine = create_async_engine(
        "postgresql+asyncpg://ctcc_fixture@127.0.0.1/ctcc_witness_no_io",
        poolclass=NullPool,
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    witness_factory = async_sessionmaker(witness_engine, expire_on_commit=False)
    pins = SimpleNamespace(engines=(witness_engine, engine))
    expected = frozen.contract.post_read_observation
    witness_rows = kwargs["_clock_v4_witness_rows"]

    async def forbidden_connect(*args, **kw):
        raise AssertionError("synthetic tests must not connect")

    async def read_revision(*args):
        events.append("old_private_witness_dataclass_read")
        result = verify_witness_chain(witness_rows)
        assert type(result) is WitnessRevision and not hasattr(result, "recorded_at")
        return result

    async def role(session):
        events.append(
            "private_witness_role_guard"
            if session.bind is witness_engine
            else "private_role_guard"
        )

    private_witness = SimpleNamespace(
        read_revision=read_revision, session_factory=witness_factory, _role_guard=role
    )
    private_observation = SimpleNamespace(session_factory=factory, _role_guard=role)

    def check(*args):
        events.append("pins_rechecked")
        return pins

    def stamp():
        name = stamp.names.pop(0)
        events.append(name)
        return ClockStamp.model_validate(values[name])

    stamp.names = [
        "data_readback_completed",
        "sample_before",
        "sample_after",
        "sample_observation_completed",
    ]
    health = [values["health_before"], values["health_after"]]

    async def execute(session, statement, parameters=None):
        assert type(session) is AsyncSession
        if statement is owner._READ_ONLY_SQL:
            events.append("set_read_only")
            result = {}
        elif statement is owner._WITNESS_SQL:
            assert session.bind is witness_engine
            assert parameters == {"key": expected.journal_key}
            events.append("fixed_private_witness_rows_materialized")
            await asyncio.sleep(0)
            return SimpleNamespace(all=lambda: witness_rows)
        elif statement is owner._IDENTITY_SQL:
            assert session.bind is engine
            events.append("identity_sql")
            result = values["database_sample"]["identity"]
        elif statement is owner._OBSERVATION_SQL:
            assert session.bind is engine
            events.append("db0035_exact_read_materialized")
            assert parameters == {
                "key": expected.journal_key,
                "revision": expected.witness_revision,
            }
            result = expected.model_dump()
        elif statement is owner._SAMPLE_SQL:
            assert session.bind is engine
            events.append("separate_clock_sql_materialized")
            result = {
                **values["database_sample"]["identity"],
                "sampled_at": values["database_sample"]["sampled_at"],
            }
        else:
            raise AssertionError("unexpected SQL")
        await asyncio.sleep(0)
        return SimpleNamespace(
            mappings=lambda: SimpleNamespace(all=lambda: [dict(result)])
        )

    monkeypatch.setattr(asyncpg, "connect", forbidden_connect)
    monkeypatch.setattr(AsyncSession, "execute", execute)
    monkeypatch.setattr(owner, "os", SimpleNamespace(name="nt", getpid=os.getpid))
    monkeypatch.setattr(owner, "_source_database_pins", check)
    monkeypatch.setattr(
        owner,
        "_owned_source_repositories",
        lambda _: (private_witness, private_observation),
    )
    monkeypatch.setattr(owner, "_stamp", stamp)
    monkeypatch.setattr(owner, "_health", lambda: health.pop(0))
    yield SimpleNamespace(
        issue_source=lambda: source_token(frozen, kwargs),
        observation=object(),
        plan=frozen.contract.v2_capture.plan_sha256,
        events=events,
        values=values,
        engine=engine,
        witness_engine=witness_engine,
        witness_rows=witness_rows,
        private_witness=private_witness,
        private_observation=private_observation,
    )
    engine.sync_engine.dispose()
    witness_engine.sync_engine.dispose()


@pytest.mark.asyncio
async def test_synthetic_fixed_sql_native_bracket_and_one_use_internal_handoff(
    synthetic_owner_case, monkeypatch
):
    case = synthetic_owner_case
    case.service, case.source = case.issue_source()
    token = await owner.acquire_native_post_read_clock_v4(
        service=case.service,
        source=case.source,
        expected_plan_sha256=case.plan,
        observation_repository=case.observation,
    )
    events = case.events
    assert "old_private_witness_dataclass_read" not in events
    assert events.index("fixed_private_witness_rows_materialized") < events.index(
        "db0035_exact_read_materialized"
    )
    assert events.index("db0035_exact_read_materialized") < events.index(
        "data_readback_completed"
    )
    assert events.index("sample_before") < events.index(
        "separate_clock_sql_materialized"
    )
    assert events[events.index("separate_clock_sql_materialized") + 1] == "sample_after"
    monkeypatch.setattr(
        owner,
        "_stamp",
        lambda: ClockStamp.model_validate(case.values["sample_observation_completed"]),
    )
    result = owner.consume_internal_post_read_clock_v4(token)
    assert not result.contract.future_decision_authority
    assert not result.contract.db0046_custody_verified
    with pytest.raises(ValueError):
        owner.consume_internal_post_read_clock_v4(token)
    assert case.source not in source_owner._SOURCE_RESULTS


@pytest.mark.asyncio
async def test_real_witness_revision_has_no_time_and_exact_raw_sql_row_supplies_it(
    evidence_case,
):
    _, frozen, kwargs = evidence_case
    rows = kwargs["_clock_v4_witness_rows"]
    revision = verify_witness_chain(rows)
    assert type(revision) is WitnessRevision
    assert "recorded_at" not in WitnessRevision.__dataclass_fields__
    # This is the incompatible field access in the preserved initial169e owner.
    # It fails with the production dataclass, without any invented DTO fields.
    with pytest.raises(AttributeError, match="recorded_at"):
        _ = revision.recorded_at
    selected, recorded_at = owner._witness_selection(rows, revision.revision)
    assert type(selected) is WitnessRevision and selected == revision
    assert type(rows[-1]).__name__ == "Row"
    assert tuple(rows[-1]._mapping) == tuple(WitnessFunctionRow.__dataclass_fields__)
    assert recorded_at is rows[-1].recorded_at
    assert recorded_at == frozen.contract.post_read_observation.witness_recorded_at


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "damage",
    (
        "duplicate",
        "missing_revision",
        "missing_time",
        "naive_time",
        "changed_time",
        "sentinel_overflow",
    ),
)
async def test_malformed_or_mismatched_raw_witness_is_rejected_before_db0035(
    synthetic_owner_case, monkeypatch, damage
):
    case = synthetic_owner_case
    case.service, case.source = case.issue_source()
    original = AsyncSession.execute
    rows = case.witness_rows
    if damage == "duplicate":
        damaged = (*rows, rows[-1])
    elif damage == "missing_revision":
        damaged = rows[:-1]
    elif damage == "sentinel_overflow":
        damaged = (rows[0],) * 8194
    else:
        changed = dict(rows[-1]._mapping)
        if damage == "missing_time":
            del changed["recorded_at"]
        elif damage == "naive_time":
            changed["recorded_at"] = changed["recorded_at"].replace(tzinfo=None)
        elif damage == "changed_time":
            changed["recorded_at"] += timedelta(microseconds=1)
        raw_row = result_tuple(tuple(changed))(tuple(changed.values()))
        damaged = (*rows[:-1], raw_row)

    async def execute(session, statement, parameters=None):
        if statement is owner._WITNESS_SQL:
            assert session.bind is case.witness_engine
            return SimpleNamespace(all=lambda: damaged)
        return await original(session, statement, parameters)

    monkeypatch.setattr(AsyncSession, "execute", execute)
    with pytest.raises(owner.NativePostReadClockError, match="acquisition_failed"):
        await owner.acquire_native_post_read_clock_v4(
            service=case.service,
            source=case.source,
            expected_plan_sha256=case.plan,
            observation_repository=case.observation,
        )
    assert "db0035_exact_read_materialized" not in case.events
    assert "separate_clock_sql_materialized" not in case.events
    assert case.source not in source_owner._SOURCE_RESULTS


@pytest.mark.asyncio
async def test_foreign_task_burns_internal_token(synthetic_owner_case):
    case = synthetic_owner_case
    case.service, case.source = case.issue_source()
    token = await owner.acquire_native_post_read_clock_v4(
        service=case.service,
        source=case.source,
        expected_plan_sha256=case.plan,
        observation_repository=case.observation,
    )

    async def foreign():
        with pytest.raises(owner.NativePostReadClockError):
            owner.consume_internal_post_read_clock_v4(token)

    await asyncio.create_task(foreign())
    with pytest.raises(owner.NativePostReadClockError):
        owner.consume_internal_post_read_clock_v4(token)


@pytest.mark.asyncio
async def test_cancelled_sample_cannot_restore_source(
    synthetic_owner_case, monkeypatch
):
    case = synthetic_owner_case
    case.service, case.source = case.issue_source()

    async def cancelled(*args):
        raise asyncio.CancelledError()

    monkeypatch.setattr(AsyncSession, "execute", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await owner.acquire_native_post_read_clock_v4(
            service=case.service,
            source=case.source,
            expected_plan_sha256=case.plan,
            observation_repository=case.observation,
        )
    assert case.source not in source_owner._SOURCE_RESULTS


@pytest.mark.asyncio
async def test_expired_internal_sample_burns_and_never_becomes_decision_authority(
    synthetic_owner_case, monkeypatch
):
    case = synthetic_owner_case
    case.service, case.source = case.issue_source()
    token = await owner.acquire_native_post_read_clock_v4(
        service=case.service,
        source=case.source,
        expected_plan_sha256=case.plan,
        observation_repository=case.observation,
    )
    current = dict(case.values["sample_observation_completed"])
    current = {name: value + 5_000_000_100 for name, value in current.items()}
    monkeypatch.setattr(owner, "_stamp", lambda: ClockStamp.model_validate(current))
    with pytest.raises(owner.NativePostReadClockError, match="consume_failed"):
        owner.consume_internal_post_read_clock_v4(token)
    assert token not in owner._RESULTS


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "damage", ("backend_changed", "missing_row", "duplicate_row", "db_exception")
)
async def test_changed_native_results_fail_without_retry_or_source_remint(
    synthetic_owner_case, monkeypatch, damage
):
    case = synthetic_owner_case
    case.service, case.source = case.issue_source()
    original = AsyncSession.execute
    sample_calls = []

    async def changed(session, statement, parameters=None):
        returned = await original(session, statement, parameters)
        if statement is owner._SAMPLE_SQL:
            sample_calls.append(True)
            if damage == "db_exception":
                raise ValueError("SYNTHETIC_PRIVATE_DB_DETAIL")
            row = dict(returned.mappings().all()[0])
            if damage == "backend_changed":
                row["backend_pid"] += 1
            rows = [] if damage == "missing_row" else [row]
            if damage == "duplicate_row":
                rows.append(dict(row))
            return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: rows))
        return returned

    monkeypatch.setattr(AsyncSession, "execute", changed)
    with pytest.raises(
        owner.NativePostReadClockError, match="acquisition_failed"
    ) as caught:
        await owner.acquire_native_post_read_clock_v4(
            service=case.service,
            source=case.source,
            expected_plan_sha256=case.plan,
            observation_repository=case.observation,
        )
    assert sample_calls == [True]
    assert case.source not in source_owner._SOURCE_RESULTS
    assert caught.value.__cause__ is None and caught.value.__suppress_context__
    assert "SYNTHETIC_PRIVATE_DB_DETAIL" not in str(caught.value)


@pytest.mark.asyncio
async def test_late_provider_pin_rejection_burns_source_without_repeating_sql(
    synthetic_owner_case, monkeypatch
):
    case = synthetic_owner_case
    case.service, case.source = case.issue_source()
    original = owner._source_database_pins

    def changed(*args):
        if "db0035_exact_read_materialized" in case.events:
            raise ValueError("synthetic_late_factory_change")
        return original(*args)

    monkeypatch.setattr(owner, "_source_database_pins", changed)
    with pytest.raises(owner.NativePostReadClockError):
        await owner.acquire_native_post_read_clock_v4(
            service=case.service,
            source=case.source,
            expected_plan_sha256=case.plan,
            observation_repository=case.observation,
        )
    assert case.events.count("db0035_exact_read_materialized") == 1
    assert "separate_clock_sql_materialized" not in case.events
    assert case.source not in source_owner._SOURCE_RESULTS


def test_private_native_factories_are_fresh_and_caller_callbacks_are_rejected(
    native_pair,
):
    witness, observation, engine, calls = native_pair
    pins = owner._source_database_pins(witness, observation)
    private_witness, private_observation = owner._owned_source_repositories(pins)
    private_pins = owner._source_database_pins(private_witness, private_observation)
    assert private_pins.engines == (engine, engine)
    assert private_observation.session_factory is not observation.session_factory
    assert private_witness.session_factory is not witness.session_factory

    def callback():
        calls.append(True)
        raise AssertionError("callback must not execute")

    observation.session_factory = callback
    with pytest.raises(ValueError):
        owner._source_database_pins(witness, observation, pins)
    assert (
        owner._source_database_pins(private_witness, private_observation, private_pins)
        == private_pins
    )
    assert not calls


@pytest.mark.parametrize("operation", [copy.copy, copy.deepcopy, pickle.dumps])
def test_carrier_is_not_serializable_or_constructible(operation):
    with pytest.raises(ValueError):
        owner.NativePostReadClockSampleV4(object())
    token = object.__new__(owner.NativePostReadClockSampleV4)
    with pytest.raises(ValueError):
        operation(token)
    with pytest.raises(ValueError):
        owner.consume_internal_post_read_clock_v4(token)
