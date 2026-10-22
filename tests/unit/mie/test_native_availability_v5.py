"""Synthetic TLS/clock/catalog/SQL-result substitutions; no native/PG acceptance.

The positive fixture replays a complete raw attempt and witness chain, executes
the real Stage1 carrier lifecycle and real V5 coordinator/finite publisher, and
consumes a real DB0046 receipt-registry token. Network and DB connects are barred.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import pickle
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import asyncpg
import pytest
from sqlalchemy.engine.result import result_tuple
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.database.repositories import public_runtime_bundle_custody as db46
from app.database.repositories.public_receipt_post_read_observation import (
    PublicReceiptPostReadRepository,
)
from app.database.repositories.public_receipt_witness import (
    PublicReceiptWitnessRepository,
    WitnessRevision,
    _record_hash,
)
from app.domain.source_primitives import ClockStamp, canonical, sha
from app.mie.features import FeatureBar
from app.mie.validation import development_validation_source_collection_v5 as finite
from app.mie.validation import native_availability_bundle_v5 as bundle
from app.mie.validation import native_availability_v5 as owner
from app.mie.validation import native_post_read_clock_v4 as stage1
from app.mie.validation import public_checkpoint_service as source_owner
from app.mie.validation.artifact import _freeze
from app.mie.validation.development_validation_source_plan_v1 import (
    DevelopmentValidationSourceCoordinateV1,
    DevelopmentValidationSourcePlanV1,
)
from app.mie.validation.post_publication_availability_v2 import (
    PostPublicationCaptureV2,
    PostPublicationMinuteV2,
    _rows_sha256,
)
from app.mie.validation.post_publication_availability_v3 import (
    PostPublicationCaptureV3,
    PostReadObservationV3,
)
from app.mie.validation.post_publication_availability_v4 import (
    PostPublicationClockEvidenceV4,
    utc_nanoseconds,
)
from app.mie.validation.post_publication_availability_v5 import (
    NativeAvailabilityEvidenceV5,
    ceil_native_utc,
)
from app.public_market_source import public_market_capture as capture
from app.public_market_source import public_receipt_storage as journal_storage
from app.public_market_source.public_market_receipts import parsed_rows, utc_from_ns
from tests.unit.mie.test_native_post_read_clock_v4 import synthetic_health
from tests.unit.research import public_receipt_fixtures
from tests.unit.research.public_receipt_fixtures import storage_fixture_capture
from tests.unit.research.test_public_journal_contracts import memory_publisher


@pytest.fixture
def raw_case(monkeypatch):
    old_plan = public_receipt_fixtures.plan_for

    def historical(rows=240):
        plan = old_plan(rows)
        return type(plan).model_validate(
            {**plan.model_dump(), "created_ns": plan.end_ns + 1_000_000_000}
        )

    monkeypatch.setattr(public_receipt_fixtures, "plan_for", historical)
    fixture, directory, journal, _ = memory_publisher(monkeypatch)
    with journal._begin_attempt(fixture.plan) as attempt:
        packet, files = asyncio.run(
            capture._collect_packet(
                fixture.plan, fixture.plan.canonical_sha256(), _attempt=attempt
            )
        )
        packet = storage_fixture_capture(packet, files).receipt
        # Explicit synthetic native labels, as in existing attempt-binding tests.
        for index, (result, page) in enumerate(
            zip(
                attempt.requests,
                (packet.time_before, *packet.pages, packet.time_after),
                strict=True,
            )
        ):
            result["headers"]["tls"] = {
                "classification": "owned_native_tls",
                "peer_sha256": page["tls_peer_sha256"],
                "hostname": page["tls_hostname"],
                "version": page["tls_version"],
            }
            node = directory.content["attempts"]["attempt-00000001"][
                f"request-{index:03d}"
            ]
            node["headers.json"], node["result.json"] = (
                canonical(result["headers"]),
                canonical(result),
            )
        digest = attempt.seal(
            disposition="completed_collection",
            code="none",
            stamp=fixture.stamp(),
            receipt_sha256=packet.canonical_sha256(),
        )
    packet = type(packet).model_validate(
        {**packet.model_dump(), "attempt_sha256": digest}
    )
    journal._publish_owned(capture._OwnedPublicCapture(capture._ISSUER, packet, files))
    checkpoint = journal.checkpoint
    entry = journal.read_all()[0][0][3]
    at = utc_from_ns(entry["payload_readback_complete"]["utc_ns"])
    minutes = []
    for locator in packet.rows:
        raw = parsed_rows(files[f"page-{locator['page_index']:03d}.raw"])[0][
            locator["row_ordinal"]
        ]
        minutes.append(
            PostPublicationMinuteV2(
                source_row_identity=locator["identity"],
                source_row_sha256=locator["content_sha256"],
                raw_page_sha256=locator["raw_sha256"],
                bar=FeatureBar(
                    closed_at=utc_from_ns(int(raw[0]) * 1_000_000 + 60_000_000_000),
                    open=Decimal(raw[1]),
                    high=Decimal(raw[2]),
                    low=Decimal(raw[3]),
                    close=Decimal(raw[4]),
                    volume=Decimal(raw[5]),
                ),
            )
        )
    minutes = tuple(sorted(minutes, key=lambda r: r.bar.closed_at))
    genesis = json.loads(directory.content["genesis.json"])
    initial = type(checkpoint).model_validate(
        {
            **checkpoint.model_dump(),
            "sequence": 0,
            "head_sha256": checkpoint.genesis_sha256,
            "attempt_sequence": 0,
            "attempt_head_sha256": checkpoint.genesis_sha256,
        }
    )
    attempted = type(checkpoint).model_validate(
        {
            **initial.model_dump(),
            "attempt_sequence": checkpoint.attempt_sequence,
            "attempt_head_sha256": checkpoint.attempt_head_sha256,
        }
    )
    rows = []
    for index, (transition, state, point) in enumerate(
        (
            ("initialize", "idle", initial),
            ("open_attempt", "open", initial),
            ("append_attempt", "attempt_anchored", attempted),
            ("append_capture", "idle", checkpoint),
        )
    ):
        value = WitnessRevision(
            journal_key=checkpoint.genesis_sha256,
            revision=index,
            journal_id=genesis["journal_id"],
            root_device=point.root_device,
            root_inode=point.root_inode,
            transition=transition,
            state=state,
            operation_id=None if index == 0 else "c" * 32,
            plan_sha256=None if index == 0 else fixture.plan.canonical_sha256(),
            attempt_outcome="completed_collection" if index >= 2 else None,
            previous_record_sha256=None if index == 0 else rows[-1].record_sha256,
            checkpoint=point,
            checkpoint_sha256=point.canonical_sha256(),
            record_sha256="0" * 64,
        )
        value = replace(
            value, record_sha256=_record_hash(value, point.canonical_bytes().decode())
        )
        packed = bundle.WitnessRawRowV5(
            **{
                k: getattr(value, k)
                for k in value.__dataclass_fields__
                if k != "checkpoint"
            },
            checkpoint_json=point.canonical_bytes().decode(),
            recorded_at=at - timedelta(seconds=3 - index),
        )
        rows.append(packed)
    v2 = PostPublicationCaptureV2(
        capture_id=packet.capture_id,
        plan_sha256=fixture.plan.canonical_sha256(),
        receipt_sha256=packet.canonical_sha256(),
        journal_genesis_sha256=checkpoint.genesis_sha256,
        capture_checkpoint_sha256=checkpoint.canonical_sha256(),
        capture_sequence=1,
        witness_revision=3,
        witness_record_sha256=rows[-1].record_sha256,
        instrument_id=fixture.plan.instrument_id,
        validation_complete_at=utc_from_ns(packet.validation_complete["utc_ns"]),
        payload_readback_at=at,
        committed_witness_observed_at=at + timedelta(seconds=1),
        available_at=at + timedelta(seconds=1),
        rows=minutes,
        source_rows_sha256=_rows_sha256(minutes),
    )
    observation = PostReadObservationV3(
        journal_key=checkpoint.genesis_sha256,
        witness_revision=3,
        witness_record_sha256=rows[-1].record_sha256,
        checkpoint_sha256=checkpoint.canonical_sha256(),
        capture_sequence=1,
        capture_head_sha256=checkpoint.head_sha256,
        capture_id=packet.capture_id,
        plan_sha256=v2.plan_sha256,
        receipt_sha256=v2.receipt_sha256,
        source_rows_sha256=v2.source_rows_sha256,
        v2_capture_sha256=v2.canonical_sha256(),
        witness_recorded_at=at,
        observed_at=at + timedelta(seconds=2),
    )
    v3 = PostPublicationCaptureV3(
        v2_capture=v2,
        v2_capture_sha256=v2.canonical_sha256(),
        post_read_observation=observation,
        available_at=observation.observed_at,
    )
    wall, mono = utc_nanoseconds(v3.available_at + timedelta(seconds=10)), 10**9
    stamp = lambda n: ClockStamp(utc_ns=wall + n, monotonic_ns=mono + n)
    identity = {
        "backend_pid": 123,
        "database_oid": 456,
        "login_oid": 789,
        "transaction_start": v3.available_at + timedelta(seconds=9),
        "postmaster_start": v3.available_at - timedelta(days=1),
        "transaction_xid": None,
        "server_version_num": 170011,
        "server_build": "PostgreSQL 17.11 on x86_64-windows, compiled by msvc-19.44.35222, 64-bit",
        "transaction_read_only": "on",
    }
    clock = PostPublicationClockEvidenceV4(
        source_v3=v3,
        source_v3_sha256=v3.canonical_sha256(),
        health_before=synthetic_health(wall, mono),
        data_readback_completed=stamp(1_000_000),
        sample_before=stamp(2_000_000),
        database_sample={
            "identity": identity,
            "sampled_at": v3.available_at + timedelta(seconds=10, milliseconds=3),
        },
        sample_after=stamp(4_000_000),
        health_after=synthetic_health(wall + 5_000_000, mono + 5_000_000),
        sample_observation_completed=stamp(6_000_000),
    )
    packed_files = []

    def flatten(content, prefix=""):
        for name, value in content.items():
            if type(value) is dict:
                flatten(value, prefix + name + "/")
            else:
                packed_files.append(
                    bundle.PackedPublicFileV5(
                        path=prefix + name,
                        raw_base64=base64.b64encode(value).decode(),
                        raw_sha256=sha(value),
                    )
                )

    flatten(directory.content)
    deployment = canonical({"synthetic_catalog_only": True})
    contract = bundle.NativeAvailabilityBundleV5(
        ordinal_claim_sha256="a" * 64,
        clock_v4=clock,
        checkpoint=checkpoint,
        files=tuple(packed_files),
        witness_rows=tuple(rows),
        deployment_json_base64=base64.b64encode(deployment).decode(),
        deployment_sha256=sha(deployment),
    )
    return SimpleNamespace(
        plan=fixture.plan,
        journal=journal,
        directory=directory,
        clock=clock,
        contract=contract,
        rows=tuple(rows),
        wall=wall,
        mono=mono,
        stamp=stamp,
    )


def test_complete_raw_bundle_replays_source_attempt_witness_and_clock(raw_case):
    case = raw_case
    frozen = bundle.freeze_native_availability_bundle_v5(case.contract)
    assert (
        bundle.replay_native_availability_bundle_v5(
            frozen.payload, expected_sha256=frozen.sha256
        )
        == case.contract
    )
    assert case.clock.source_v3.trusted_clock_verified is False
    with pytest.raises(owner.NativeAvailabilityError):
        owner.consume_for_current_future_decision_v5(
            frozen, expected_evidence_sha256=frozen.sha256
        )


def test_native_snapshot_replays_complete_original_tree_before_and_after_copy(
    raw_case, monkeypatch
):
    from app.public_market_source import public_receipt_storage

    monkeypatch.setattr(bundle, "_root_context", public_receipt_storage._root_context)
    point, files = bundle.snapshot_public_journal_v5(raw_case.journal)
    assert point == raw_case.contract.checkpoint and files == raw_case.contract.files
    with pytest.raises(ValueError):
        bundle.snapshot_public_journal_v5(SimpleNamespace(checkpoint=point))


@pytest.mark.parametrize(
    "damage",
    [
        "raw",
        "missing",
        "duplicate",
        "traversal",
        "root_bool",
        "witness_time",
        "witness_prefix",
        "row",
        "attempt",
        "deployment",
    ],
)
def test_rehashed_bundle_tampering_is_rejected(raw_case, damage):
    data = raw_case.contract.model_dump(mode="python")
    if damage == "raw":
        item = next(f for f in data["files"] if f["path"].endswith("page-000.raw"))
        raw = base64.b64decode(item["raw_base64"]) + b" "
        item.update(raw_base64=base64.b64encode(raw).decode(), raw_sha256=sha(raw))
    elif damage == "missing":
        data["files"] = data["files"][:-1]
    elif damage == "duplicate":
        data["files"] = (*data["files"], data["files"][0])
    elif damage == "traversal":
        data["files"][0]["path"] = "../genesis.json"
    elif damage == "root_bool":
        data["checkpoint"]["root_device"] = True
    elif damage == "witness_time":
        data["witness_rows"][-1]["recorded_at"] += timedelta(microseconds=1)
    elif damage == "witness_prefix":
        data["witness_rows"] = data["witness_rows"][1:]
    elif damage == "row":
        data["clock_v4"]["source_v3"]["v2_capture"]["rows"][0]["source_row_sha256"] = (
            "f" * 64
        )
    elif damage == "attempt":
        item = next(
            f for f in data["files"] if f["path"].endswith("request-000/chunk-000.raw")
        )
        raw = b"changed"
        item.update(raw_base64=base64.b64encode(raw).decode(), raw_sha256=sha(raw))
    else:
        data["deployment_sha256"] = "f" * 64
    with pytest.raises((ValueError, KeyError, IndexError)):
        bundle.NativeAvailabilityBundleV5.model_validate(data)


@pytest.fixture
def flow(raw_case, monkeypatch, tmp_path):
    case = raw_case
    engines = [
        create_async_engine(
            f"postgresql+asyncpg://fixture@127.0.0.1/no_io_{i}", poolclass=NullPool
        )
        for i in range(3)
    ]
    factories = [async_sessionmaker(e, expire_on_commit=False) for e in engines]
    witness = PublicReceiptWitnessRepository(factories[0])
    observation = PublicReceiptPostReadRepository(factories[1], witness)
    service = source_owner.PublicCheckpointCaptureService(
        source_owner._ISSUER,
        case.journal,
        witness,
        json.loads(case.directory.content["genesis.json"])["journal_id"],
        SimpleNamespace(journal_key=case.journal.checkpoint.genesis_sha256),
    )
    coordinate = DevelopmentValidationSourceCoordinateV1(
        ordinal=0,
        partition="development",
        purpose="historical_warmup",
        capture_plan=case.plan,
        capture_plan_sha256=case.plan.canonical_sha256(),
    )
    plan = DevelopmentValidationSourcePlanV1(
        collection_id="native-availability-synthetic-v5",
        origin=case.plan.origin,
        planned_at=utc_from_ns(case.plan.created_ns),
        coordinates=(coordinate,),
        expected_rows=case.plan.expected_rows,
    )
    root = tmp_path / "private-finite"
    root.mkdir()
    state = {
        "offset": -1_000_000,
        "events": [],
        "failure": None,
        "capture_count": 0,
        "seal_count": 0,
    }

    def now():
        state["offset"] += 10_000
        return case.stamp(state["offset"])

    monkeypatch.setattr(owner, "_stamp", now)

    # Native, unhooked factories are real objects; no connection is ever allowed.
    async def never_connect(*a, **kw):
        raise AssertionError("no database/network IO")

    monkeypatch.setattr(asyncpg, "connect", never_connect)

    def assert_claim():
        assert (root / "batch-00000000/claim.json").is_file()

    sql_scope = {
        "database_oid": 456,
        "database_name": "synthetic_db",
        "server_address": "127.0.0.1/32",
        "server_port": 5432,
        "server_started": case.clock.database_sample.identity.postmaster_start,
        "server_version_num": "170011",
        "login_oid": 102,
        "owner_oid": 900,
    }
    safe_scope = {
        k: v
        for k, v in sql_scope.items()
        if k not in {"database_name", "server_address", "server_started"}
    }
    safe_scope.update(
        database_name_sha256=sha(b"synthetic_db"),
        server_address_sha256=sha(b"127.0.0.1/32"),
        server_started=sql_scope["server_started"].isoformat(),
    )
    deployment_raw = canonical(
        {
            "synthetic_catalog_only": True,
            "scopes": [
                {**safe_scope, "login_oid": 100},
                {**safe_scope, "login_oid": 101},
            ],
        }
    )

    class Catalog:
        async def acquire_clock_custody_deployment_v1(self, **kw):
            assert_claim()
            state["events"].append("catalog")
            await asyncio.sleep(0)
            return object()

        async def recheck_clock_custody_deployment_v1(self, *a, **kw):
            assert_claim()
            state["events"].append("catalog_recheck")
            await asyncio.sleep(0)
            if state["failure"] == "catalog":
                raise ValueError("synthetic")

        def readback_clock_custody_deployment_v1(self, *a):
            if state["failure"] == "catalog_expired":
                raise ValueError("synthetic_expired_owner")
            return SimpleNamespace(canonical_json_bytes=lambda: deployment_raw)

    monkeypatch.setattr(owner, "_catalog_api", lambda: Catalog())

    async def verify(self):
        assert_claim()
        state["events"].append("custody_deployment")
        self._deployment = db46._Deployment("b" * 64, db46._owner())
        await asyncio.sleep(0)

    async def seal(self, *, bundle_raw, expected_sha256, proof_envelope_sha256):
        state["events"].append("append_commit_readback")
        state["seal_count"] += 1
        await asyncio.sleep(0)
        if state["failure"] == "commit":
            raise db46.RuntimeBundleCustodyError("runtime_bundle_commit_uncertain")
        raw = bundle_raw if state["failure"] != "readback" else bundle_raw + b" "
        read = db46.RetainedRuntimeBundleReadback(
            db46.runtime_bundle_id_v1(
                bundle_sha256=expected_sha256,
                proof_envelope_sha256=proof_envelope_sha256,
            ),
            expected_sha256,
            proof_envelope_sha256,
            case.clock.source_v3.available_at,
            case.clock.source_v3.available_at + timedelta(seconds=1),
            raw,
            1,
            "11",
            2,
            "12",
        )
        token = db46.OwnedRuntimeBundleCustodyReceipt(db46._ISSUER)
        db46._RECEIPTS[token] = (read, db46._owner(), db46.time.monotonic_ns())
        return token

    monkeypatch.setattr(
        db46.RuntimeBundleCustodyRepository, "verify_deployment", verify
    )
    monkeypatch.setattr(db46.RuntimeBundleCustodyRepository, "seal_bundle", seal)

    async def source(self, **kw):
        assert_claim()
        state["capture_count"] += 1
        state["events"].append("source")
        if state["failure"] == "source":
            raise ValueError("synthetic")
        if state["failure"] == "cancel":
            raise asyncio.CancelledError
        frozen = _freeze(case.clock.source_v3, contract_type=PostPublicationCaptureV3)
        token = source_owner.NativeSourcePostReadCapture(source_owner._SOURCE_ISSUER)
        source_owner._SOURCE_RESULTS[token] = (
            self,
            source_owner._source_context(),
            case.plan.canonical_sha256(),
            frozen,
        )
        return token

    monkeypatch.setattr(
        source_owner.PublicCheckpointCaptureService,
        "capture_source_with_post_read_v3",
        source,
    )
    make_row = result_tuple(tuple(bundle.WitnessRawRowV5.model_fields))
    rawrows = tuple(make_row(tuple(r.model_dump().values())) for r in case.rows)

    async def execute(session, statement, parameters=None):
        sql = str(statement)
        state["events"].append("sql")
        await asyncio.sleep(0)
        if statement is stage1._WITNESS_SQL or statement is owner._WITNESS_SQL:
            return SimpleNamespace(all=lambda: rawrows)
        if statement is stage1._IDENTITY_SQL:
            value = case.clock.database_sample.identity.model_dump()
        elif statement is stage1._OBSERVATION_SQL:
            value = case.clock.source_v3.post_read_observation.model_dump()
        elif statement is stage1._SAMPLE_SQL:
            value = {
                **case.clock.database_sample.identity.model_dump(),
                "sampled_at": case.clock.database_sample.sampled_at,
            }
        elif "AS direct_login" in sql:
            row = SimpleNamespace(
                direct_login=True,
                privileged=False,
                owner_member=False,
                member_of_other_role=False,
                can_read=True,
                can_append=True,
                direct_table_access=False,
                can_create=False,
                can_witness=False,
            )
            return SimpleNamespace(one_or_none=lambda: row)
        elif statement is owner._CUSTODY_SCOPE_SQL:
            value = dict(sql_scope)
            if state["failure"] == "scope":
                value["owner_oid"] += 1
            if state["failure"] == "scope_bool":
                value["server_port"] = True
        elif sql in {
            "SET TRANSACTION READ ONLY",
            "SET LOCAL synchronous_commit = 'on'",
        }:
            value = {}
        else:
            raise AssertionError("unexpected synthetic SQL")
        return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: [value]))

    monkeypatch.setattr(AsyncSession, "execute", execute)
    stage_stamps = iter(
        [
            case.clock.data_readback_completed,
            case.clock.sample_before,
            case.clock.sample_after,
            case.clock.sample_observation_completed,
            case.stamp(6_100_000),
        ]
    )
    monkeypatch.setattr(stage1, "_stamp", lambda: next(stage_stamps))
    monkeypatch.setattr(stage1, "os", SimpleNamespace(name="nt", getpid=os.getpid))
    health = [case.clock.health_before, case.clock.health_after]

    def stage_health():
        result = health.pop(0)
        if not health:
            state["offset"] = 20_000_000
        return result

    monkeypatch.setattr(stage1, "_health", stage_health)
    native_health_producer = owner.native_clock._native_observation_payload
    monkeypatch.setattr(
        owner.native_clock,
        "_native_observation_payload",
        lambda: synthetic_health(
            case.wall + state["offset"], case.mono + state["offset"]
        ).raw_bytes(),
    )
    monkeypatch.setattr(
        owner,
        "snapshot_public_journal_v5",
        lambda journal: (case.contract.checkpoint, case.contract.files),
    )
    instances = []

    def instance():
        if not instances:
            instances.append(
                finite.DevelopmentValidationAvailabilityOwnerV5.initialize(
                    root=root,
                    plan=plan,
                    expected_plan_sha256=plan.canonical_sha256(),
                    capture_service=service,
                    observation_repository=observation,
                    custody_session_factory=factories[2],
                )
            )
        return instances[0]

    yield SimpleNamespace(
        case=case,
        instance=instance,
        state=state,
        root=root,
        plan=plan,
        factories=factories,
        service=service,
        observation=observation,
        native_health_producer=native_health_producer,
    )
    for engine in engines:
        engine.sync_engine.dispose()


@pytest.mark.asyncio
async def test_owned_end_to_end_synthetic_positive_future_cutoff_and_one_use(flow):
    f = flow
    token = await f.instance().capture_once(
        ordinal=0, expected_previous_receipt_sha256s=()
    )
    pin = owner.evidence_sha256_v5(token)
    record, rows = owner.consume_for_current_future_decision_v5(
        token, expected_evidence_sha256=pin
    )
    assert (
        record.contract.same_domain_future_availability_eligible is True
        and len(rows) == 2
    )
    assert all(r.available_at > f.case.clock.source_v3.available_at for r in rows)
    assert (
        record.contract.execution_authority is False
        and record.contract.predictive_oos_eligible is False
    )
    assert f.state["capture_count"] == f.state["seal_count"] == 1
    assert (
        f.state["events"].index("custody_deployment")
        < f.state["events"].index("source")
        < f.state["events"].index("append_commit_readback")
    )
    with pytest.raises(owner.NativeAvailabilityError):
        owner.consume_for_current_future_decision_v5(
            token, expected_evidence_sha256=pin
        )
    with pytest.raises(ValueError):
        await f.instance().capture_once(ordinal=0, expected_previous_receipt_sha256s=())


@pytest.mark.asyncio
async def test_final_health_real_producer_returns_bytes_retained_without_reencoding(
    flow, monkeypatch
):
    returned = []

    def synthetic_native_observation():
        # Exercise the actual producer, substituting only its native OS I/O.
        raw = synthetic_health(
            flow.case.wall + flow.state["offset"],
            flow.case.mono + flow.state["offset"],
        ).raw_bytes()
        returned.append(raw)
        return json.loads(raw)["observation"]

    monkeypatch.setattr(
        owner.native_clock, "native_os_clock", synthetic_native_observation
    )
    monkeypatch.setattr(
        owner.native_clock, "_native_observation_payload", flow.native_health_producer
    )
    token = await flow.instance().capture_once(
        ordinal=0, expected_previous_receipt_sha256s=()
    )
    assert len(returned) == 1 and type(returned[0]) is bytes
    retained = (flow.root / "batch-00000000/final-health.json").read_bytes()
    assert retained == returned[0]
    record, _ = owner.consume_for_current_future_decision_v5(
        token, expected_evidence_sha256=owner.evidence_sha256_v5(token)
    )
    assert record.contract.same_domain_future_availability_eligible is True
    evidence = json.loads((flow.root / "batch-00000000/evidence-v5.json").read_bytes())
    assert base64.b64decode(evidence["final_health"]["raw_base64"]) == retained
    assert evidence["final_health"]["raw_sha256"] == sha(returned[0])


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ("dict", "bytearray", "str", "empty", "oversize"))
async def test_final_health_non_native_type_or_bound_is_denied_without_raw_retention(
    flow, monkeypatch, kind
):
    raw = flow.case.clock.health_after.raw_bytes()
    invalid = {
        "dict": json.loads(raw),
        "bytearray": bytearray(raw),
        "str": raw.decode("utf-8"),
        "empty": b"",
        "oversize": b"x" * (owner.native_clock.MAX_CLOCK_PAYLOAD + 1),
    }[kind]
    monkeypatch.setattr(
        owner.native_clock, "_native_observation_payload", lambda: invalid
    )
    with pytest.raises(ValueError):
        await flow.instance().capture_once(
            ordinal=0, expected_previous_receipt_sha256s=()
        )
    reader = finite.CollectionReadOnlyV5.open(
        root=flow.root,
        plan=flow.plan,
        expected_plan_sha256=flow.plan.canonical_sha256(),
    )
    receipt = reader.read(0)
    assert receipt.status == "FAILED_RETAINED"
    assert {f.name for f in receipt.files} == {"bundle-v5.json"}
    assert flow.state["capture_count"] == flow.state["seal_count"] == 1
    with pytest.raises(ValueError):
        await flow.instance().capture_once(
            ordinal=0, expected_previous_receipt_sha256s=()
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ("unobserved", "malformed"))
async def test_final_health_bounded_failed_payload_retained_before_validation(
    flow, monkeypatch, kind
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
        else b"malformed-native-health-json"
    )
    monkeypatch.setattr(owner.native_clock, "_native_observation_payload", lambda: raw)
    with pytest.raises(ValueError):
        await flow.instance().capture_once(
            ordinal=0, expected_previous_receipt_sha256s=()
        )
    reader = finite.CollectionReadOnlyV5.open(
        root=flow.root,
        plan=flow.plan,
        expected_plan_sha256=flow.plan.canonical_sha256(),
    )
    receipt = reader.read(0)
    assert receipt.status == "FAILED_RETAINED"
    assert {f.name for f in receipt.files} == {"bundle-v5.json", "final-health.json"}
    assert (flow.root / "batch-00000000/final-health.json").read_bytes() == raw
    assert next(
        f.sha256 for f in receipt.files if f.name == "final-health.json"
    ) == sha(raw)
    assert flow.state["capture_count"] == flow.state["seal_count"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    ["catalog", "source", "cancel", "commit", "readback", "scope", "scope_bool"],
)
async def test_failure_retains_claim_and_tombstone_and_never_refills(flow, failure):
    f = flow
    f.state["failure"] = failure
    with pytest.raises((ValueError, asyncio.CancelledError)):
        await f.instance().capture_once(ordinal=0, expected_previous_receipt_sha256s=())
    reader = finite.CollectionReadOnlyV5.open(
        root=f.root, plan=f.plan, expected_plan_sha256=f.plan.canonical_sha256()
    )
    assert reader.read(0).status == "FAILED_RETAINED"
    assert (f.root / "batch-00000000/claim.json").is_file()
    calls = f.state["capture_count"]
    with pytest.raises(ValueError):
        await f.instance().capture_once(ordinal=0, expected_previous_receipt_sha256s=())
    assert f.state["capture_count"] == calls and f.state["seal_count"] <= 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "damage", ["expiry", "foreign", "saved", "changed_store", "wrong_pin"]
)
async def test_future_consumer_burns_on_invalid_context_or_proof(flow, damage):
    f = flow
    token = await f.instance().capture_once(
        ordinal=0, expected_previous_receipt_sha256s=()
    )
    pin = owner.evidence_sha256_v5(token)
    if damage == "expiry":
        f.state["offset"] += 6_000_000_000
    elif damage == "changed_store":
        (f.root / "batch-00000000/evidence-v5.json").write_bytes(b"changed")
    elif damage == "wrong_pin":
        pin = "f" * 64
    elif damage == "saved":
        saved = (f.root / "batch-00000000/evidence-v5.json").read_bytes()
        with pytest.raises(ValueError):
            owner.consume_for_current_future_decision_v5(
                saved, expected_evidence_sha256=pin
            )
        with pytest.raises(ValueError):
            pickle.dumps(token)
        return
    with pytest.raises(ValueError):
        if damage == "foreign":
            await asyncio.create_task(_consume(token, pin))
        else:
            owner.consume_for_current_future_decision_v5(
                token, expected_evidence_sha256=pin
            )
    with pytest.raises(ValueError):
        owner.consume_for_current_future_decision_v5(
            token, expected_evidence_sha256=pin
        )


async def _consume(token, pin):
    return owner.consume_for_current_future_decision_v5(
        token, expected_evidence_sha256=pin
    )


def test_native_factory_refuses_fake_or_callback_without_io():
    with pytest.raises(ValueError):
        owner._factory_pin(lambda: True)
    engine = create_async_engine(
        "postgresql+asyncpg://fixture@127.0.0.1/no_io", poolclass=NullPool
    )
    factory = async_sessionmaker(engine)
    assert owner._factory_pin(factory)[0] is factory
    factory.kw["info"] = {"callback": lambda: True}
    with pytest.raises(ValueError):
        owner._factory_pin(factory)
    engine.sync_engine.dispose()


@pytest.mark.parametrize("value", [True, 1.0, -100, 101])
def test_native_rounding_refuses_wrong_units(value):
    with pytest.raises(ValueError):
        ceil_native_utc(value)


def test_native_rounding_is_conservative():
    assert utc_nanoseconds(ceil_native_utc(1100)) == 2000


@pytest.mark.asyncio
@pytest.mark.parametrize("hook", ["dialect", "json", "pool"])
async def test_third_custody_engine_callbacks_denied_before_any_io(
    flow, monkeypatch, hook
):
    engine = flow.factories[2].kw["bind"]
    calls = []

    def forbidden(*a, **k):
        calls.append(True)
        raise AssertionError("caller engine callback")

    if hook == "dialect":
        monkeypatch.setattr(engine.dialect, "do_execute", forbidden)
    elif hook == "json":
        monkeypatch.setattr(engine.dialect, "_json_deserializer", forbidden)
    else:
        monkeypatch.setattr(engine.sync_engine.pool, "_invoke_creator", forbidden)
    with pytest.raises(ValueError):
        await flow.instance().capture_once(
            ordinal=0, expected_previous_receipt_sha256s=()
        )
    assert not calls and flow.state["events"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["existing", "missing"])
async def test_initialization_preserves_old_store_and_redacts_private_paths(flow, kind):
    flow.instance()
    original = {p.name: p.read_bytes() for p in flow.root.iterdir()}
    target = flow.root if kind == "existing" else flow.root / "missing"
    with pytest.raises(ValueError, match="^availability_initialization_unresolved$"):
        finite.DevelopmentValidationAvailabilityOwnerV5.initialize(
            root=target,
            plan=flow.plan,
            expected_plan_sha256=flow.plan.canonical_sha256(),
            capture_service=flow.service,
            observation_repository=flow.observation,
            custody_session_factory=flow.factories[2],
        )
    assert {p.name: p.read_bytes() for p in flow.root.iterdir()} == original


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "damage",
    [
        "native_post_read_measured",
        "restricted_app_role_raw_retained",
        "execution_authority",
        "proof",
        "domain",
        "reversal",
    ],
)
async def test_evidence_contract_types_and_clocks_cannot_be_rehashed_to_pass(
    flow, damage
):
    f = flow
    token = await f.instance().capture_once(
        ordinal=0, expected_previous_receipt_sha256s=()
    )
    evidence = json.loads((f.root / "batch-00000000/evidence-v5.json").read_bytes())
    if damage in {"native_post_read_measured", "restricted_app_role_raw_retained"}:
        evidence[damage] = 1
    elif damage == "execution_authority":
        evidence[damage] = 0
    elif damage == "proof":
        evidence["custody"]["proof_sha256"] = "f" * 64
    elif damage == "reversal":
        evidence["final_ready"]["monotonic_ns"] = 0
    else:
        evidence["final_health"] = evidence["source_clock_v4"]["health_before"]
    with pytest.raises(ValueError):
        NativeAvailabilityEvidenceV5.model_validate_json(canonical(evidence))
    assert token in owner._READY


@pytest.mark.asyncio
async def test_late_retention_failure_preserves_all_durable_bytes_and_no_capability(
    flow, monkeypatch
):
    original = owner._retain

    def late(claim, name, raw):
        original(claim, name, raw)
        if name == "evidence-v5.json":
            raise PermissionError("synthetic_late_failure")

    monkeypatch.setattr(owner, "_retain", late)
    with pytest.raises(ValueError):
        await flow.instance().capture_once(
            ordinal=0, expected_previous_receipt_sha256s=()
        )
    reader = finite.CollectionReadOnlyV5.open(
        root=flow.root,
        plan=flow.plan,
        expected_plan_sha256=flow.plan.canonical_sha256(),
    )
    receipt = reader.read(0)
    assert receipt.status == "FAILED_RETAINED" and {f.name for f in receipt.files} == {
        "bundle-v5.json",
        "final-health.json",
        "evidence-v5.json",
    }
    assert flow.state["seal_count"] == 1


@pytest.mark.asyncio
async def test_late_local_bundle_rehash_does_not_become_owned_expected_evidence(
    flow, monkeypatch
):
    original = db46.RuntimeBundleCustodyRepository.seal_bundle
    changed = canonical({"different_but_canonical": "raw_bundle"})

    async def tamper(self, **kwargs):
        token = await original(self, **kwargs)
        (flow.root / "batch-00000000/bundle-v5.json").write_bytes(changed)
        return token

    monkeypatch.setattr(db46.RuntimeBundleCustodyRepository, "seal_bundle", tamper)
    with pytest.raises(ValueError):
        await flow.instance().capture_once(
            ordinal=0, expected_previous_receipt_sha256s=()
        )
    reader = finite.CollectionReadOnlyV5.open(
        root=flow.root,
        plan=flow.plan,
        expected_plan_sha256=flow.plan.canonical_sha256(),
    )
    assert reader.read(0).status == "FAILED_RETAINED"
    assert (flow.root / "batch-00000000/bundle-v5.json").read_bytes() == changed
    assert flow.state["seal_count"] == 1


@pytest.mark.asyncio
async def test_late_evidence_rehash_cannot_publish_a_capability(flow, monkeypatch):
    original = finite._publish

    def tamper(claim, **kwargs):
        if kwargs.get("success"):
            (flow.root / "batch-00000000/evidence-v5.json").write_bytes(
                canonical({"changed": "canonical_evidence"})
            )
        return original(claim, **kwargs)

    monkeypatch.setattr(finite, "_publish", tamper)
    with pytest.raises(ValueError):
        await flow.instance().capture_once(
            ordinal=0, expected_previous_receipt_sha256s=()
        )
    assert (
        finite.CollectionReadOnlyV5.open(
            root=flow.root,
            plan=flow.plan,
            expected_plan_sha256=flow.plan.canonical_sha256(),
        )
        .read(0)
        .status
        == "FAILED_RETAINED"
    )


def _other_genuine_journal(flow, monkeypatch):
    from tests.unit.research.test_public_journal_contracts import MemoryDirectory

    other_root = flow.root / "synthetic_other_source_root"
    other = MemoryDirectory({})

    @contextmanager
    def directories(root):
        yield other if root == other_root else flow.case.directory

    monkeypatch.setattr(journal_storage, "_root_context", directories)
    journal = journal_storage.initialize_public_receipt_journal(other_root)
    assert journal.read_all() == ((), {})
    return journal, json.loads(other.content["genesis.json"])["journal_id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("when", ["before_source", "after_source"])
async def test_second_genuine_root_cannot_replace_original_finite_lineage(
    flow, monkeypatch, when
):
    flow.instance()
    other, identifier = _other_genuine_journal(flow, monkeypatch)

    def swap():
        flow.service._journal = other
        flow.service._journal_id = identifier
        flow.service._latest = SimpleNamespace(
            journal_key=other.checkpoint.genesis_sha256
        )

    if when == "before_source":
        swap()
    else:
        original = (
            source_owner.PublicCheckpointCaptureService.capture_source_with_post_read_v3
        )

        async def capture_other(self, **kwargs):
            token = await original(self, **kwargs)
            swap()
            return token

        monkeypatch.setattr(
            source_owner.PublicCheckpointCaptureService,
            "capture_source_with_post_read_v3",
            capture_other,
        )
    with pytest.raises(ValueError):
        await flow.instance().capture_once(
            ordinal=0, expected_previous_receipt_sha256s=()
        )
    assert flow.state["capture_count"] == (0 if when == "before_source" else 1)
    assert flow.state["seal_count"] == 0
    assert (
        finite.CollectionReadOnlyV5.open(
            root=flow.root,
            plan=flow.plan,
            expected_plan_sha256=flow.plan.canonical_sha256(),
        )
        .read(0)
        .status
        == "FAILED_RETAINED"
    )


@pytest.mark.asyncio
async def test_catalog_owner_expiry_remains_live_dependency_at_consume(flow):
    token = await flow.instance().capture_once(
        ordinal=0, expected_previous_receipt_sha256s=()
    )
    pin = owner.evidence_sha256_v5(token)
    flow.state["failure"] = "catalog_expired"
    with pytest.raises(ValueError):
        owner.consume_for_current_future_decision_v5(
            token, expected_evidence_sha256=pin
        )
    assert token not in owner._READY


@pytest.mark.asyncio
async def test_bad_caller_factory_denies_before_catalog_or_source_io(flow):
    flow.factories[2].kw["info"] = {"callback": lambda: True}
    with pytest.raises(ValueError):
        await flow.instance().capture_once(
            ordinal=0, expected_previous_receipt_sha256s=()
        )
    assert flow.state["events"] == [] and flow.state["capture_count"] == 0
    assert (flow.root / "batch-00000000/claim.json").is_file()


@pytest.mark.asyncio
async def test_pin_read_store_loss_is_redacted_and_burns_capability(flow):
    token = await flow.instance().capture_once(
        ordinal=0, expected_previous_receipt_sha256s=()
    )
    claim = flow.root / "batch-00000000/claim.json"
    original = claim.read_bytes()
    claim.write_bytes(b"changed")
    with pytest.raises(
        owner.NativeAvailabilityError, match="^native_availability_pin_unavailable$"
    ):
        owner.evidence_sha256_v5(token)
    claim.write_bytes(original)
    with pytest.raises(owner.NativeAvailabilityError):
        owner.evidence_sha256_v5(token)


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["factory", "repository"])
async def test_caller_swap_restore_cannot_enter_private_nested_await(
    flow, monkeypatch, target
):
    original = AsyncSession.execute
    calls = []

    class Forbidden:
        def __init__(self, *a, **k):
            calls.append("forbidden")
            raise AssertionError("caller provider executed")

    used = False

    async def swap(session, statement, parameters=None):
        nonlocal used
        if used or statement is not stage1._OBSERVATION_SQL:
            return await original(session, statement, parameters)
        used = True
        if target == "factory":
            previous = flow.factories[1].class_
            flow.factories[1].class_ = Forbidden
        else:
            previous = flow.observation.session_factory
            flow.observation.session_factory = Forbidden
        try:
            return await original(session, statement, parameters)
        finally:
            if target == "factory":
                flow.factories[1].class_ = previous
            else:
                flow.observation.session_factory = previous

    monkeypatch.setattr(AsyncSession, "execute", swap)
    token = await flow.instance().capture_once(
        ordinal=0, expected_previous_receipt_sha256s=()
    )
    assert used and not calls
    pin = owner.evidence_sha256_v5(token)
    assert owner.consume_for_current_future_decision_v5(
        token, expected_evidence_sha256=pin
    )[0].contract.same_domain_future_availability_eligible


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "damage", ["missing_root", "missing_claim", "partial", "changed_identity"]
)
async def test_read_only_restart_denies_missing_or_partial_without_initialising(
    flow, damage
):
    instance = flow.instance()
    if damage == "missing_root":
        root = flow.root / "missing"
    elif damage == "changed_identity":
        root = flow.root
        (root / "identity.json").write_bytes(b"{}")
    else:
        root = flow.root
        slot = root / "batch-00000000"
        slot.mkdir()
        if damage == "partial":
            (slot / "claim.json").write_bytes(b"partial")
    with pytest.raises(ValueError, match="availability_readback_unresolved"):
        finite.CollectionReadOnlyV5.open(
            root=root, plan=flow.plan, expected_plan_sha256=flow.plan.canonical_sha256()
        )
    if damage == "missing_root":
        assert not root.exists()
    assert instance is flow.instance()
