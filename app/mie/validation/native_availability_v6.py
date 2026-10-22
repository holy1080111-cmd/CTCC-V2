"""Native source + measured clock + DB custody, future same-domain use only.

No constructor, saved DTO, success boolean or callback can issue these carriers.
Only a finite pre-I/O durable claim, actual V3/Stage1 carriers, real restricted
DB commit/readback and fresh final native boundary can reach the consumer.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
from dataclasses import dataclass
from datetime import datetime
from threading import get_ident
from weakref import WeakKeyDictionary

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.repositories.public_receipt_clock_custody_v1 import (
    _native_query_engine,
)
from app.database.repositories.public_receipt_witness import _native_identity_integer
from app.database.repositories.public_runtime_bundle_custody import (
    RuntimeBundleCustodyRepository,
    _consume_runtime_bundle_custody_receipt,
)
from app.domain import native_clock
from app.domain.source_primitives import ClockStamp, canonical, sha, validate_stamps
from app.mie.validation.artifact import _freeze
from app.mie.validation.contracts import require_utc
from app.mie.validation.development_validation_source_collection_v6 import (
    _check_claim,
    _check_service,
    _consume_claim,
    _retain,
)
from app.mie.validation.native_availability_bundle_v6 import (
    NativeAvailabilityBundleV6,
    WitnessRawRowV6,
    freeze_native_availability_bundle_v6,
    replay_native_availability_bundle_v6,
    snapshot_public_journal_v6,
)
from app.mie.validation.native_post_read_clock_retained_v6 import (
    acquire_retained_post_read_clock_v6,
    consume_retained_post_read_clock_v6,
)
from app.mie.validation.native_post_read_clock_v4 import (
    _CONSUME_SOURCE,
    _witness_selection,
)
from app.mie.validation.native_stage1_forensics_v6 import (
    _new_retention_owner,
    replay_forensic_pack_v6,
)
from app.mie.validation.post_publication_availability_v4 import (
    NativePostReadHealthV4,
    health_fresh_at,
    utc_nanoseconds,
)
from app.mie.validation.post_publication_availability_v6 import (
    ACQUISITION_BUDGET_SECONDS,
    CONSUMER_LEASE_NS,
    DurableReadbackV6,
    FutureAvailabilityDecisionV6,
    NativeAvailabilityEvidenceV6,
    ceil_native_utc,
    replay_native_availability_v6,
)
from app.mie.validation.public_checkpoint_service import (
    _owned_source_repositories,
    _source_database_pins,
)
from app.mie.validation.replay import PointInTimeBar

_ISSUER = object()
_PREPARED, _COMPLETED, _READY = (
    WeakKeyDictionary(),
    WeakKeyDictionary(),
    WeakKeyDictionary(),
)
_READ_ONLY = text("SET TRANSACTION READ ONLY")
_WITNESS_SQL = text("SELECT * FROM public.public_receipt_witness_read(:key) LIMIT 8194")
_CUSTODY_SCOPE_SQL = text("""
SELECT (SELECT oid::bigint FROM pg_catalog.pg_database WHERE datname=pg_catalog.current_database()) AS database_oid,
 pg_catalog.current_database() AS database_name,
 pg_catalog.inet_server_addr()::text AS server_address,
 pg_catalog.inet_server_port() AS server_port,
 pg_catalog.pg_postmaster_start_time() AS server_started,
 pg_catalog.current_setting('server_version_num') AS server_version_num,
 (SELECT oid::bigint FROM pg_catalog.pg_roles WHERE rolname=session_user) AS login_oid,
 (SELECT relowner::bigint FROM pg_catalog.pg_class WHERE oid='public.public_runtime_raw_bundles_v1'::pg_catalog.regclass) AS owner_oid
""")


class NativeAvailabilityError(ValueError):
    """Only fixed codes; never native/DB/source error messages or private paths."""


def _require(condition):
    if not condition:
        raise NativeAvailabilityError("native_availability_unavailable")


def _context():
    task = asyncio.current_task()
    _require(task is not None and not task.cancelling())
    return os.getpid(), get_ident(), asyncio.get_running_loop(), task


def _stamp():
    _require(os.name == "nt")
    return ClockStamp.model_validate(native_clock.native_stamp())


def _factory_pin(factory):
    _require(
        type(factory) is async_sessionmaker
        and set(vars(factory)) == {"kw", "class_"}
        and factory.class_ is AsyncSession
        and type(factory.kw) is dict
        and not set(factory.kw) - {"bind", "expire_on_commit", "autoflush"}
        and all(
            type(factory.kw[k]) is bool
            for k in ("expire_on_commit", "autoflush")
            if k in factory.kw
        )
        and _native_query_engine(factory.kw.get("bind"))
    )
    return factory, tuple(sorted(factory.kw.items()))


def _catalog_api():
    # Fixed module/functions, not a caller-supplied provider or callback.
    from app.database.repositories import public_receipt_clock_custody_v1 as catalog

    return catalog


class _Carrier:
    __slots__ = ("__weakref__",)

    def __init__(self, issuer):
        if issuer is not _ISSUER:
            raise NativeAvailabilityError("native_availability_owner_required")

    def __reduce_ex__(self, protocol):
        raise NativeAvailabilityError("native_availability_not_transferable")

    def __copy__(self):
        raise NativeAvailabilityError("native_availability_not_transferable")

    def __deepcopy__(self, memo):
        raise NativeAvailabilityError("native_availability_not_transferable")


class NativeAvailabilityEligibilityV6(_Carrier):
    """One-use, same task/domain future-data eligibility; no trading permission."""


@dataclass(slots=True)
class _State:
    claim: object
    context: tuple
    started: ClockStamp
    original: object
    private: object
    witness: object
    observation: object
    custody_factory_pin: tuple
    custody: RuntimeBundleCustodyRepository
    deployment: object = None
    deployment_raw: bytes | None = None
    custody_scope: dict | None = None


def _recheck(state):
    _require(type(state) is _State and state.context == _context())
    _check_claim(state.claim)
    _check_service(state.claim.state)
    source = state.claim.state
    _source_database_pins(
        source.service._repository, source.observation, state.original
    )
    _source_database_pins(state.witness, state.observation, state.private)
    _require(all(_native_query_engine(engine) for engine in state.private.engines))
    _require(_factory_pin(source.custody_factory) == state.custody_factory_pin)
    state.custody._check_factory()
    if state.deployment is not None:
        raw = (
            _catalog_api()
            .readback_clock_custody_deployment_v1(state.deployment)
            .canonical_json_bytes()
        )
        _require(
            type(raw) is bytes
            and 0 < len(raw) <= 65536
            and (state.deployment_raw is None or raw == state.deployment_raw)
        )
    now = _stamp()
    validate_stamps([state.started.model_dump(), now.model_dump()])
    _require(
        now.monotonic_ns - state.started.monotonic_ns
        <= ACQUISITION_BUDGET_SECONDS * 1_000_000_000
    )


async def _deployment_recheck(state):
    _recheck(state)
    catalog = _catalog_api()
    await catalog.recheck_clock_custody_deployment_v1(
        state.deployment,
        witness_repository=state.witness,
        observation_repository=state.observation,
    )
    _recheck(state)
    raw = catalog.readback_clock_custody_deployment_v1(
        state.deployment
    ).canonical_json_bytes()
    _require(type(raw) is bytes and 0 < len(raw) <= 65536)
    if state.deployment_raw is None:
        state.deployment_raw = raw
    else:
        _require(state.deployment_raw == raw)


async def _custody_scope(state):
    """Fixed actual DB46 scope; SQL identity is not host/binary attestation."""
    _recheck(state)
    async with state.custody._factory() as session, session.begin():
        _require(
            type(session) is AsyncSession
            and session.bind is state.custody._engine
            and session.sync_session.bind is state.custody._engine.sync_engine
        )
        for statement in (
            text("SET LOCAL synchronous_commit = 'on'"),
            _READ_ONLY,
            _CUSTODY_SCOPE_SQL,
        ):
            _recheck(state)
            result = await session.execute(statement)
            _recheck(state)
        rows = tuple(result.mappings().all())
    _require(len(rows) == 1)
    row = dict(rows[0])
    _require(
        set(row)
        == {
            "database_oid",
            "database_name",
            "server_address",
            "server_port",
            "server_started",
            "server_version_num",
            "login_oid",
            "owner_oid",
        }
    )
    _require(
        all(
            type(row[k]) is int and row[k] > 0
            for k in ("database_oid", "server_port", "login_oid", "owner_oid")
        )
    )
    _require(
        all(
            type(row[k]) is str and 0 < len(row[k]) <= 256
            for k in ("database_name", "server_address")
        )
        and row["server_version_num"] == "170011"
        and type(row["server_version_num"]) is str
    )
    _require(type(row["server_started"]) is datetime)
    scope = {
        k: v
        for k, v in row.items()
        if k not in {"database_name", "server_address", "server_started"}
    }
    scope.update(
        database_name_sha256=sha(row["database_name"].encode()),
        server_address_sha256=sha(row["server_address"].encode()),
        server_started=require_utc(row["server_started"], "server_started").isoformat(),
    )
    expected = json.loads(state.deployment_raw)["scopes"]
    keys = set(scope) - {"login_oid"}
    _require(
        type(expected) is list
        and len(expected) == 2
        and all(
            type(s) is dict
            and all(type(scope[k]) is type(s.get(k)) and scope[k] == s[k] for k in keys)
            for s in expected
        )
    )
    if state.custody_scope is None:
        state.custody_scope = scope
    else:
        _require(state.custody_scope == scope)
    return scope


async def _prepare(lease):
    """Called only after durable claim readback; no source request occurs here."""
    claim = _consume_claim(lease)
    try:
        source = claim.state
        context, started = _context(), _stamp()
        original = _source_database_pins(source.service._repository, source.observation)
        witness, observation = _owned_source_repositories(original)
        private = _source_database_pins(witness, observation)
        factory_pin = _factory_pin(source.custody_factory)
        owned_factory = async_sessionmaker(class_=AsyncSession, **dict(factory_pin[1]))
        custody = RuntimeBundleCustodyRepository(owned_factory)
        state = _State(
            claim,
            context,
            started,
            original,
            private,
            witness,
            observation,
            factory_pin,
            custody,
        )
        _recheck(state)
        catalog = _catalog_api()
        state.deployment = await catalog.acquire_clock_custody_deployment_v1(
            witness_repository=witness, observation_repository=observation
        )
        _recheck(state)
        await _deployment_recheck(state)
        await custody.verify_deployment()
        _recheck(state)
        await _custody_scope(state)
        result = _Carrier(_ISSUER)
        _PREPARED[result] = state
        return result
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - Never disclose native details.
        raise NativeAvailabilityError("native_availability_preflight_failed") from None


async def _read_witness(state, recorded):
    await _deployment_recheck(state)
    async with state.witness.session_factory() as session, session.begin():
        _require(
            type(session) is AsyncSession
            and session.bind is state.private.engines[0]
            and session.sync_session.bind is state.private.engines[0].sync_engine
        )
        await session.execute(_READ_ONLY)
        _recheck(state)
        await state.witness._role_guard(session)
        _recheck(state)
        returned = await session.execute(
            _WITNESS_SQL, {"key": recorded.post_read_observation.journal_key}
        )
        rows = tuple(returned.all())
        _recheck(state)
    selected, when = _witness_selection(
        rows, recorded.post_read_observation.witness_revision
    )
    _require(
        selected.record_sha256 == recorded.post_read_observation.witness_record_sha256
        and when == recorded.post_read_observation.witness_recorded_at
    )
    packed = []
    for row in rows:
        value = dict(row._mapping)
        value["root_device"] = _native_identity_integer(
            value["root_device"], minimum=0, maximum=2**64 - 1
        )
        value["root_inode"] = _native_identity_integer(
            value["root_inode"], minimum=1, maximum=2**128 - 1
        )
        packed.append(WitnessRawRowV6.model_validate(value))
    await _deployment_recheck(state)
    return tuple(packed)


def _proof(claim_sha256, clock_sha256, deployment_sha256, forensic_sha256):
    return sha(
        canonical(
            {
                "schema_version": "ctcc.mie.native_availability_proof.v6",
                "ordinal_claim_sha256": claim_sha256,
                "clock_v4_sha256": clock_sha256,
                "deployment_sha256": deployment_sha256,
                "stage1_forensic_sha256": forensic_sha256,
            }
        )
    )


async def _complete(acquisition, source):
    """Consume native lineage, append once, exact readback, then fresh ready time."""
    _require(type(acquisition) is _Carrier)
    state = _PREPARED.pop(acquisition, None)
    _require(state is not None)
    try:
        _recheck(state)
        # Stage1 consumes the actual native source before its first DB await.
        retention = _new_retention_owner(state.claim)
        sample = await acquire_retained_post_read_clock_v6(
            service=state.claim.state.service,
            source=source,
            expected_plan_sha256=state.claim.claim.capture_plan_sha256,
            observation_repository=state.claim.state.observation,
            retention=retention,
        )
        clock, forensic = consume_retained_post_read_clock_v6(sample)
        _recheck(state)
        _require(
            clock.contract.source_v3.v2_capture.plan_sha256
            == state.claim.claim.capture_plan_sha256
        )
        _require(
            clock.contract.source_v3.v2_capture.journal_genesis_sha256
            == state.claim.state.reader.identity.journal_genesis_sha256
        )
        rows = await _read_witness(state, clock.contract.source_v3)
        checkpoint, files = snapshot_public_journal_v6(
            state.claim.state.service._journal
        )
        _require(
            checkpoint.genesis_sha256
            == state.claim.state.reader.identity.journal_genesis_sha256
        )
        deployment = canonical(
            {
                "clock_custody": json.loads(state.deployment_raw),
                "runtime_bundle_custody_fingerprint": state.custody._require_deployment().fingerprint,
                "runtime_bundle_authenticated_scope": state.custody_scope,
            }
        )
        contract = NativeAvailabilityBundleV6(
            ordinal_claim_sha256=state.claim.claim.canonical_sha256(),
            clock_v4=clock.contract,
            checkpoint=checkpoint,
            files=files,
            witness_rows=rows,
            deployment_json_base64=base64.b64encode(deployment).decode("ascii"),
            deployment_sha256=sha(deployment),
            stage1_forensic_base64=base64.b64encode(forensic.payload).decode("ascii"),
            stage1_forensic_sha256=forensic.sha256,
        )
        _require(
            contract.witness_rows[0].journal_id
            == state.claim.state.reader.identity.journal_id
        )
        bundle = freeze_native_availability_bundle_v6(contract)
        proof = _proof(
            contract.ordinal_claim_sha256,
            clock.sha256,
            contract.deployment_sha256,
            forensic.sha256,
        )
        _retain(state.claim, "bundle-v6.json", bundle.payload)
        await _deployment_recheck(state)
        _recheck(state)
        receipt = await state.custody.seal_bundle(
            bundle_raw=bundle.payload,
            expected_sha256=bundle.sha256,
            proof_envelope_sha256=proof,
        )
        readback = _consume_runtime_bundle_custody_receipt(
            receipt,
            expected_bundle_sha256=bundle.sha256,
            expected_proof_envelope_sha256=proof,
        )
        _require(readback.bundle_raw == bundle.payload)
        replayed = replay_native_availability_bundle_v6(
            readback.bundle_raw, expected_sha256=bundle.sha256
        )
        _require(replayed == contract)
        _recheck(state)
        await _deployment_recheck(state)
        await _custody_scope(state)
        current_checkpoint, current_files = snapshot_public_journal_v6(
            state.claim.state.service._journal
        )
        _require(current_checkpoint == checkpoint and current_files == files)
        # Fixed native health, retained even if rejected; no caller clock/provider.
        health_raw = native_clock._native_observation_payload()
        _require(
            type(health_raw) is bytes
            and 0 < len(health_raw) <= native_clock.MAX_CLOCK_PAYLOAD
        )
        _retain(state.claim, "final-health.json", health_raw)
        health = NativePostReadHealthV4(
            raw_base64=base64.b64encode(health_raw).decode("ascii"),
            raw_sha256=sha(health_raw),
        )
        ready = _stamp()
        _recheck(state)
        evidence = NativeAvailabilityEvidenceV6(
            ordinal_claim_sha256=contract.ordinal_claim_sha256,
            source_clock_v4=clock.contract,
            deployment_sha256=contract.deployment_sha256,
            stage1_forensic_sha256=forensic.sha256,
            custody=DurableReadbackV6(
                bundle_id=readback.bundle_id,
                bundle_sha256=bundle.sha256,
                proof_sha256=proof,
                append_backend_pid=readback.append_backend_pid,
                append_transaction_id=readback.append_transaction_id,
                readback_backend_pid=readback.readback_backend_pid,
                readback_transaction_id=readback.readback_transaction_id,
            ),
            final_health=health,
            final_ready=ready,
        )
        frozen = _freeze(evidence, contract_type=NativeAvailabilityEvidenceV6)
        _retain(state.claim, "evidence-v6.json", frozen.payload)
        result = _Carrier(_ISSUER)
        _COMPLETED[result] = (state, frozen, bundle)
        return result
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - Retained raw/claim, fixed outward code, no retry.
        raise NativeAvailabilityError("native_availability_completion_failed") from None
    finally:
        # A pre-Stage1 pin/context failure must not leave an unused source lease.
        # This fixed synchronous registry consume performs no source/DB writes.
        try:
            _CONSUME_SOURCE(
                state.claim.state.service,
                source,
                expected_plan_sha256=state.claim.claim.capture_plan_sha256,
            )
        except Exception:  # noqa: BLE001, S110 - Already consumed/invalid tokens stay burned.
            pass


def _retained_pins(evidence, bundle):
    return tuple(
        sorted(
            (
                ("bundle-v6.json", bundle.sha256),
                ("evidence-v6.json", evidence.sha256),
                ("final-health.json", evidence.contract.final_health.raw_sha256),
            )
        )
    )


def _expected_retained_files(completed):
    _require(type(completed) is _Carrier)
    state = _COMPLETED.get(completed)
    _require(state is not None)
    _recheck(state[0])
    return _retained_pins(state[1], state[2])


def _forensic_pins(bundle):
    raw = base64.b64decode(bundle.contract.stage1_forensic_base64, validate=True)
    return replay_forensic_pack_v6(
        raw,
        expected_sha256=bundle.contract.stage1_forensic_sha256,
        expected_claim_sha256=bundle.contract.ordinal_claim_sha256,
    )


def _expected_forensic_files(completed):
    _require(type(completed) is _Carrier)
    state = _COMPLETED.get(completed)
    _require(state is not None)
    _recheck(state[0])
    snapshot = _forensic_pins(state[2])
    return snapshot.sha256, snapshot.files


def _publish_capability(completed, receipt_sha256):
    _require(type(completed) is _Carrier)
    state = _COMPLETED.pop(completed, None)
    _require(state is not None)
    owner, evidence, bundle = state
    _recheck(owner)
    receipt = owner.claim.state.reader.read(
        owner.claim.claim.ordinal, expected_receipt_sha256=receipt_sha256
    )
    _require(
        receipt.status == "NATIVE_DURABLE_AVAILABLE"
        and tuple(sorted((f.name, f.sha256) for f in receipt.files))
        == _retained_pins(evidence, bundle)
        and receipt.stage1_forensic_sha256 == bundle.contract.stage1_forensic_sha256
        and tuple((f.name, f.sha256) for f in receipt.forensic_files)
        == _forensic_pins(bundle).files
    )
    now = _stamp()
    validate_stamps([evidence.contract.final_ready.model_dump(), now.model_dump()])
    _require(
        now.monotonic_ns - evidence.contract.final_ready.monotonic_ns
        <= CONSUMER_LEASE_NS
    )
    token = NativeAvailabilityEligibilityV6(_ISSUER)
    _READY[token] = (owner, evidence, bundle, receipt_sha256)
    return token


def _consume_current(eligibility, *, expected_evidence_sha256):
    """Generate the cutoff natively now; caller cannot backdate or supply a clock."""
    _require(type(eligibility) is NativeAvailabilityEligibilityV6)
    state = _READY.pop(eligibility, None)
    _require(state is not None)
    owner, frozen, bundle, receipt_pin = state
    _recheck(owner)
    _require(
        type(expected_evidence_sha256) is str
        and frozen.sha256 == expected_evidence_sha256
    )
    evidence = replay_native_availability_v6(
        frozen.payload, expected_sha256=frozen.sha256
    )
    owner.claim.state.reader.read(
        owner.claim.claim.ordinal, expected_receipt_sha256=receipt_pin
    )
    cutoff = _stamp()
    validate_stamps([evidence.final_ready.model_dump(), cutoff.model_dump()])
    _require(
        cutoff.monotonic_ns - evidence.final_ready.monotonic_ns <= CONSUMER_LEASE_NS
    )
    health_fresh_at(evidence.final_health.raw_bytes(), cutoff)
    source = bundle.contract.clock_v4.source_v3.v2_capture
    rows = tuple(
        PointInTimeBar(
            source_row_id="okx-public-minute:" + row.source_row_identity,
            source_row_sha256=row.source_row_sha256,
            instrument_id=source.instrument_id,
            available_at=ceil_native_utc(evidence.final_ready.utc_ns),
            bar=row.bar,
        )
        for row in source.rows
    )
    _require(all(utc_nanoseconds(row.available_at) <= cutoff.utc_ns for row in rows))
    decision = FutureAvailabilityDecisionV6(
        evidence_sha256=frozen.sha256,
        bundle_sha256=bundle.sha256,
        stage1_forensic_sha256=bundle.contract.stage1_forensic_sha256,
        ordinal_claim_sha256=evidence.ordinal_claim_sha256,
        cutoff=cutoff,
        source_rows_sha256=source.source_rows_sha256,
        row_count=len(rows),
    )
    return _freeze(decision, contract_type=FutureAvailabilityDecisionV6), rows


def consume_for_current_future_decision_v6(eligibility, *, expected_evidence_sha256):
    """One-use owned admission with fixed safe errors, including filesystem loss."""
    try:
        return _consume_current(
            eligibility, expected_evidence_sha256=expected_evidence_sha256
        )
    except Exception:  # noqa: BLE001 - No private path or native error leaves the boundary.
        raise NativeAvailabilityError("native_future_availability_denied") from None


def _evidence_pin(eligibility):
    """Read the expected pin of a still-owned live token, without consuming it."""
    _require(type(eligibility) is NativeAvailabilityEligibilityV6)
    state = _READY.get(eligibility)
    _require(state is not None)
    _recheck(state[0])
    state[0].claim.state.reader.read(
        state[0].claim.claim.ordinal, expected_receipt_sha256=state[3]
    )
    return state[1].sha256


def evidence_sha256_v6(eligibility):
    """Safe pin read; failed ownership/store checks burn any still-live lease."""
    try:
        return _evidence_pin(eligibility)
    except Exception:  # noqa: BLE001 - No private native path or driver message.
        if type(eligibility) is NativeAvailabilityEligibilityV6:
            _READY.pop(eligibility, None)
        raise NativeAvailabilityError("native_availability_pin_unavailable") from None
