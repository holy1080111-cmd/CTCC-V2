"""Owned native DB-clock sample, stage one only; no durable/predictive authority.

Caller repositories are verified once and privately cloned. No caller clock,
SQL/session callback, saved V3 DTO or caller boolean can issue this carrier.
Hostile shared-engine/class/global replacement is outside the existing native
application-role trust model, as are DBAs and same-host/absolute-UTC claims.
"""

from __future__ import annotations

import asyncio
import base64
import os
from datetime import datetime
from threading import Lock, get_ident
from weakref import WeakKeyDictionary

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.repositories.public_receipt_witness import (
    WitnessRevision,
    verify_witness_chain,
)
from app.domain import native_clock
from app.domain.source_primitives import ClockStamp, sha, validate_stamps
from app.mie.validation.artifact import FrozenGate3Artifact, _freeze, _verify
from app.mie.validation.contracts import require_utc
from app.mie.validation.post_publication_availability_v3 import (
    PostPublicationCaptureV3,
    PostReadObservationV3,
)
from app.mie.validation.post_publication_availability_v4 import (
    SAMPLE_LEASE_NS,
    NativePostReadHealthV4,
    PostPublicationClockEvidenceV4,
    PostReadDatabaseIdentityV4,
    PostReadDatabaseSampleV4,
    health_fresh_at,
    replay_post_read_clock_v4,
)
from app.mie.validation.public_checkpoint_service import (
    NativeSourcePostReadCapture,
    PublicCheckpointCaptureService,
    _owned_source_repositories,
    _source_database_pins,
)

_ISSUER = object()
_RESULTS = WeakKeyDictionary()
_LOCK = Lock()
_CONSUME_SOURCE = PublicCheckpointCaptureService.consume_source_post_read_v3

_IDENTITY_COLUMNS = """
  pg_catalog.pg_backend_pid() AS backend_pid,
  (SELECT oid::bigint FROM pg_catalog.pg_database
    WHERE datname=pg_catalog.current_database()) AS database_oid,
  (SELECT oid::bigint FROM pg_catalog.pg_roles
    WHERE rolname=session_user) AS login_oid,
  pg_catalog.transaction_timestamp() AS transaction_start,
  pg_catalog.pg_postmaster_start_time() AS postmaster_start,
  pg_catalog.pg_current_xact_id_if_assigned()::text AS transaction_xid,
  pg_catalog.current_setting('server_version_num')::integer AS server_version_num,
  pg_catalog.version() AS server_build,
  pg_catalog.current_setting('transaction_read_only') AS transaction_read_only
"""
_IDENTITY_SQL = text("SELECT " + _IDENTITY_COLUMNS)
_SAMPLE_SQL = text(
    "SELECT pg_catalog.clock_timestamp() AS sampled_at," + _IDENTITY_COLUMNS
)
_READ_ONLY_SQL = text("SET TRANSACTION READ ONLY")
_WITNESS_SQL = text("""
SELECT * FROM public.public_receipt_witness_read(:key) LIMIT 8194
""")
_OBSERVATION_SQL = text("""
SELECT observation.*
FROM public.public_receipt_post_read_read(:key,:revision) AS observation
""")


class NativePostReadClockError(ValueError):
    """Fixed public codes; never render DB/native/credential exception chains."""


def _require(value):
    if not value:
        raise NativePostReadClockError("native_post_read_clock_unavailable")


def _owner():
    try:
        loop, task = asyncio.get_running_loop(), asyncio.current_task()
    except RuntimeError:
        raise NativePostReadClockError("native_post_read_clock_task_required") from None
    _require(task is not None)
    return os.getpid(), get_ident(), loop, task


class NativePostReadClockSampleV4:
    """Nonserializable, expiring internal handoff; a saved artifact cannot mint it."""

    __slots__ = ("__weakref__",)

    def __init__(self, issuer):
        _require(issuer is _ISSUER)

    def __copy__(self):
        raise NativePostReadClockError("native_post_read_clock_not_transferable")

    def __deepcopy__(self, memo):
        raise NativePostReadClockError("native_post_read_clock_not_transferable")

    def __reduce_ex__(self, protocol):
        raise NativePostReadClockError("native_post_read_clock_not_transferable")

    def __reduce__(self):
        raise NativePostReadClockError("native_post_read_clock_not_transferable")


def _health():
    raw = native_clock._native_observation_payload()
    return NativePostReadHealthV4(
        raw_base64=base64.b64encode(raw).decode("ascii"), raw_sha256=sha(raw)
    )


def _stamp():
    _require(os.name == "nt")
    return ClockStamp.model_validate(native_clock.native_stamp())


def _one_mapping(result):
    rows = result.mappings().all()
    _require(len(rows) == 1)
    return dict(rows[0])


def _witness_selection(rows: tuple, revision_number: int):
    """Replay native function rows; timestamp stays on its actual raw SQL row.

    WitnessRevision deliberately has no recorded_at. This adapter never adds
    one or infers a missing time from a model/clock/current timestamp.
    """
    _require(type(rows) is tuple and 1 <= len(rows) <= 8193)
    _require(type(revision_number) is int and 3 <= revision_number <= 8192)
    latest = verify_witness_chain(rows)
    _require(revision_number <= latest.revision)
    selected = verify_witness_chain(rows[: revision_number + 1])
    _require(type(selected) is WitnessRevision and selected.revision == revision_number)
    for row in rows:
        _require(type(row.recorded_at) is datetime)
        require_utc(row.recorded_at, "witness_recorded_at")
    return selected, rows[revision_number].recorded_at


async def acquire_native_post_read_clock_v4(
    *,
    service: PublicCheckpointCaptureService,
    source: NativeSourcePostReadCapture,
    expected_plan_sha256: str,
    observation_repository,
) -> NativePostReadClockSampleV4:
    """Burn native V3 input, exact-read DB0035, sample once, issue internal token.

    No retries, writes, environment/settings reads or caller SQL/native provider.
    Cancellation/failure cannot restore the already consumed native source input.
    A later durable owner must add DB0046 retention before any decision consumer.
    """
    _require(
        type(service) is PublicCheckpointCaptureService
        and type(source) is NativeSourcePostReadCapture
        and PublicCheckpointCaptureService.consume_source_post_read_v3
        is _CONSUME_SOURCE
    )
    # The native input is consumed before any DB/native-health await or I/O here.
    frozen = _CONSUME_SOURCE(service, source, expected_plan_sha256=expected_plan_sha256)
    try:
        context = _owner()
        _require(os.name == "nt" and type(frozen) is FrozenGate3Artifact)
        recorded = _verify(
            frozen.payload,
            expected_sha256=frozen.sha256,
            contract_type=PostPublicationCaptureV3,
        )
        _require(recorded == frozen.contract)
        original = _source_database_pins(service._repository, observation_repository)
        witness, observation = _owned_source_repositories(original)
        private = _source_database_pins(witness, observation)

        def recheck(session=None, *, witness_session=False):
            _require(_owner() == context)
            _source_database_pins(service._repository, observation_repository, original)
            _source_database_pins(witness, observation, private)
            if session is not None:
                engine = private.engines[0 if witness_session else 1]
                _require(
                    type(session) is AsyncSession
                    and session.bind is engine
                    and session.sync_session.bind is engine.sync_engine
                )

        async def execute(
            session, statement, parameters=None, *, witness_session=False
        ):
            recheck(session, witness_session=witness_session)
            result = await session.execute(statement, parameters)
            recheck(session, witness_session=witness_session)
            return result

        before = _health()
        recheck()
        expected = recorded.post_read_observation
        async with (
            witness.session_factory() as witness_session,
            witness_session.begin(),
        ):
            await execute(witness_session, _READ_ONLY_SQL, witness_session=True)
            recheck(witness_session, witness_session=True)
            await witness._role_guard(witness_session)
            recheck(witness_session, witness_session=True)
            raw_witness_rows = tuple(
                (
                    await execute(
                        witness_session,
                        _WITNESS_SQL,
                        {"key": expected.journal_key},
                        witness_session=True,
                    )
                ).all()
            )
            revision, recorded_at = _witness_selection(
                raw_witness_rows, expected.witness_revision
            )
        recheck()
        _require(
            revision.journal_key == expected.journal_key
            and revision.revision == expected.witness_revision
            and revision.transition == "append_capture"
            and revision.state == "idle"
            and revision.attempt_outcome == "completed_collection"
            and revision.record_sha256 == expected.witness_record_sha256
            and revision.checkpoint_sha256 == expected.checkpoint_sha256
            and revision.plan_sha256 == expected.plan_sha256
            and revision.checkpoint.sequence == expected.capture_sequence
            and revision.checkpoint.head_sha256 == expected.capture_head_sha256
            and recorded_at == expected.witness_recorded_at
        )
        async with observation.session_factory() as session, session.begin():
            await execute(session, _READ_ONLY_SQL)
            recheck(session)
            await observation._role_guard(session)
            recheck(session)
            identity = PostReadDatabaseIdentityV4.model_validate(
                _one_mapping(await execute(session, _IDENTITY_SQL))
            )
            row = PostReadObservationV3.model_validate(
                _one_mapping(
                    await execute(
                        session,
                        _OBSERVATION_SQL,
                        {
                            "key": expected.journal_key,
                            "revision": expected.witness_revision,
                        },
                    )
                )
            )
            _require(row == expected)
            readback_completed = _stamp()
            recheck(session)
            stamp_a = _stamp()
            # Fixed actual SQL only. Materialize completely and immediately stamp
            # B, with no caller callback or additional await after result return.
            returned = await session.execute(_SAMPLE_SQL)
            sampled_row = _one_mapping(returned)
            stamp_b = _stamp()
            recheck(session)
            sampled_at = sampled_row.pop("sampled_at")
            current_identity = PostReadDatabaseIdentityV4.model_validate(sampled_row)
            _require(current_identity == identity)
        recheck()
        after = _health()
        recheck()
        completed = _stamp()
        contract = PostPublicationClockEvidenceV4(
            source_v3=recorded,
            source_v3_sha256=frozen.sha256,
            health_before=before,
            data_readback_completed=readback_completed,
            sample_before=stamp_a,
            database_sample=PostReadDatabaseSampleV4(
                identity=identity, sampled_at=sampled_at
            ),
            sample_after=stamp_b,
            health_after=after,
            sample_observation_completed=completed,
        )
        sealed = _freeze(contract, contract_type=PostPublicationClockEvidenceV4)
        replay_post_read_clock_v4(sealed.payload, expected_sha256=sealed.sha256)
        recheck()
        result = NativePostReadClockSampleV4(_ISSUER)
        with _LOCK:
            _RESULTS[result] = (context, sealed)
        return result
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - Only fixed codes leave native/DB acquisition.
        raise NativePostReadClockError(
            "native_post_read_clock_acquisition_failed"
        ) from None


def consume_internal_post_read_clock_v4(
    sample: NativePostReadClockSampleV4,
) -> FrozenGate3Artifact[PostPublicationClockEvidenceV4]:
    """Burn internal same-owner handoff; never a formal future-decision grant."""
    _require(type(sample) is NativePostReadClockSampleV4)
    with _LOCK:
        bound = _RESULTS.pop(sample, None)
    _require(bound is not None and bound[0] == _owner())
    try:
        frozen = bound[1]
        recorded = replay_post_read_clock_v4(
            frozen.payload, expected_sha256=frozen.sha256
        )
        current = _stamp()
        validate_stamps(
            [recorded.sample_observation_completed.model_dump(), current.model_dump()]
        )
        _require(
            current.monotonic_ns - recorded.sample_observation_completed.monotonic_ns
            <= SAMPLE_LEASE_NS
        )
        health_fresh_at(recorded.health_before.raw_bytes(), current)
        health_fresh_at(recorded.health_after.raw_bytes(), current)
        return frozen
    except Exception:  # noqa: BLE001 - Burned tokens never resume after failure.
        raise NativePostReadClockError(
            "native_post_read_clock_consume_failed"
        ) from None
