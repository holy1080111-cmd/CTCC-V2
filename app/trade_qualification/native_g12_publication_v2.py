"""One owned V2 candidate -> new six-file G12 -> immutable DB0039 readback.

The DB0039 claim/publication wire protocols remain unchanged hash custody.
Their original-run digest binds the explicit native candidate V2 document,
never a fabricated legacy PreEvidenceRun. Historical receipts have no entry.
"""

import asyncio
import sys as _native_sys
from collections.abc import Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4
from weakref import WeakKeyDictionary, finalize

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.repositories import (
    collector_current_0055_multirole_catalog_v1 as _custody_catalog,
)
from app.domain.native_clock import native_stamp
from app.domain.source_primitives import (
    canonical,
    decode,
    sha,
    utc_from_ns,
    validate_stamps,
)
from app.trade_evidence import native_v2 as evidence
from app.trade_evidence import storage
from app.trade_qualification import account_capture as capture
from app.trade_qualification import g12_invocation_custody as custody
from app.trade_qualification import native_continuation_guard_v1 as _native_guard
from app.trade_qualification import owned_native_candidate_v1 as candidate

_CANDIDATE_KEY_V2 = object()
_OWNERS = WeakKeyDictionary()
_CUSTODY_STATES = {}
_CUSTODY_STAGES = (
    "claim_write",
    "claim_read",
    "claim_read",
    "publication_write",
    "claim_read",
    "publication_read",
)
_CUSTODY_METADATA_SQL = (
    "SELECT session_user::text,current_user::text,pg_catalog.current_setting('role'),"
    "pg_catalog.pg_backend_pid(),pg_catalog.pg_current_xact_id()::text,"
    "pg_catalog.clock_timestamp(),pg_catalog.current_setting('transaction_read_only'),"
    "pg_catalog.current_setting('transaction_isolation'),"
    "pg_catalog.current_setting('search_path'),pg_catalog.current_setting('TimeZone'),"
    "pg_catalog.current_setting('synchronous_commit')"
)


class NativeG12Error(ValueError):
    """No paths, sources, credential values or SQL errors in public failures."""


def _deny(code="native_g12_unavailable"):
    raise NativeG12Error(code) from None


class OwnedNativePublishedCandidateV2:
    __slots__ = ("__weakref__",)

    def __new__(cls):
        _deny()

    def __copy__(self):
        _deny()

    def __deepcopy__(self, memo):
        _deny()

    def __reduce_ex__(self, protocol):
        _deny()

    def close(self):
        state = _OWNERS.pop(self, None)
        if state is not None:
            _release(state)


@dataclass(frozen=True, slots=True)
class NativePublicationFactsV2:
    original: candidate.NativeOriginalCandidateFactsV1
    snapshot: evidence.NativeEvidenceSnapshotV2
    receipt: storage.PublicationReceipt
    claim: custody.G12InvocationClaim
    publication: custody.G12InvocationPublication
    barrier_json: bytes
    evaluation_sha256: str
    custody_session_sha256: str


def _release(state):
    if not state.get("closed", False):
        state["closed"] = True
        binding = state.get("custody_binding")
        if type(binding) is object:
            _CUSTODY_STATES.pop(binding, None)
        candidate._release(state["candidate"])


def _check(state):
    _native_guard.check_saved_native_guard_v1(state)
    if state.get("closed", False) or asyncio.current_task() is not state["task"]:
        _deny()
    local, replay, issued, stamp = candidate._replay(state["candidate"])
    validate_stamps((state["last"], stamp))
    state["last"] = stamp
    if state.get("facts") is not None:
        _require_actual_publication(state)
    return local, replay, issued, stamp


def _custody_context(repository, binding, *, key):
    if (
        key is not custody._NATIVE_V2_KEY
        or type(binding) is not object
        or type(_CUSTODY_STATES) is not dict
    ):
        _deny("native_g12_custody_owner_required")
    state = _CUSTODY_STATES.get(binding)
    if (
        type(state) is not dict
        or state.get("closed", False)
        or type(repository) is not custody.G12InvocationCustodyRepository
        or state.get("custody_repository") is not repository
    ):
        _deny("native_g12_custody_owner_required")
    _local, _replay, issued, _stamp = _check(state)
    if repository.session_factory is not issued.session_factory:
        _deny("native_g12_custody_factory_changed")
    return state, issued


def _require_custody_entry_v2(
    repository, binding, stage, claim, publication=None, *, key
):
    """Only the live publisher invokes native custody; saved receipts cannot."""
    state, _issued = _custody_context(repository, binding, key=key)
    if (
        type(stage) is not str
        or stage not in _CUSTODY_STAGES
        or type(claim) is not custody.G12InvocationClaim
        or claim.claim_json != state.get("custody_claim_raw")
        or sha(claim.claim_json) != claim.claim_sha256
    ):
        _deny("native_g12_custody_claim_changed")
    if publication is not None and (
        type(publication) is not custody.G12InvocationPublication
        or publication.publication_json != state.get("custody_publication_raw")
        or sha(publication.publication_json) != publication.publication_sha256
    ):
        _deny("native_g12_custody_publication_changed")


async def _custody_metadata(repository, binding, session, *, key):
    state, issued = _custody_context(repository, binding, key=key)
    if (
        type(session) is not AsyncSession
        or not session.in_transaction()
        or session.get_bind() is not issued.session_factory.kw["bind"].sync_engine
    ):
        _deny("native_g12_actual_session_required")
    row = tuple((await session.execute(text(_CUSTODY_METADATA_SQL))).one())
    _custody_context(repository, binding, key=key)
    if (
        len(row) != 11
        or any(type(row[index]) is not str for index in (0, 1, 2, 4, 6, 7, 8, 9, 10))
        or row[0] != row[1]
        or row[2] != "none"
        or type(row[3]) is not int
        or row[3] <= 0
        or not row[4].isascii()
        or not row[4].isdigit()
        or not 0 < len(row[4]) <= 20
        or type(row[5]) is not datetime
        or row[5].tzinfo is None
        or row[6:]
        != ("off", "read committed", "pg_catalog, public, pg_temp", "UTC", "on")
    ):
        _deny("native_g12_actual_transaction_invalid")
    now = row[5].astimezone(UTC)
    if (
        not state["candidate"]["facts"].created_at
        <= now
        < state["candidate"]["deadline"]
    ):
        _deny("native_g12_database_clock_outside_original_window")
    return (*row[:5], now, *row[6:])


async def _prepare_custody_session_v2(repository, binding, session, *, stage, key):
    state, issued = _custody_context(repository, binding, key=key)
    if (
        type(session) is not AsyncSession
        or session.get_bind() is not issued.session_factory.kw["bind"].sync_engine
    ):
        _deny("native_g12_actual_session_required")
    completed = state["custody_sessions"]
    if (
        len(completed) >= len(_CUSTODY_STAGES)
        or stage != _CUSTODY_STAGES[len(completed)]
        or state.get("custody_pending_session") is not None
        or any(previous[1] is session for previous in completed)
    ):
        _deny("native_g12_independent_custody_sequence_required")
    if not session.in_transaction():
        await session.begin()
        _custody_context(repository, binding, key=key)
    for statement in (
        "SET TRANSACTION ISOLATION LEVEL READ COMMITTED",
        "SET LOCAL search_path TO pg_catalog, public, pg_temp",
        "SET LOCAL TIME ZONE 'UTC'",
        "SET LOCAL synchronous_commit='on'",
    ):
        await session.execute(text(statement))
        _custody_context(repository, binding, key=key)
    remaining = int(
        (
            state["candidate"]["deadline"] - utc_from_ns(native_stamp()["utc_ns"])
        ).total_seconds()
        * 1000
    )
    if remaining <= 0:
        _deny("native_g12_original_expired")
    await session.execute(
        text(
            "SELECT pg_catalog.set_config('statement_timeout',:statement,true),"
            "pg_catalog.set_config('lock_timeout',:lock,true)"
        ),
        {"statement": str(min(10000, remaining)), "lock": str(min(3000, remaining))},
    )
    _custody_context(repository, binding, key=key)
    await _custody_catalog.verify_actual_issuer_catalog_v2(session)
    _custody_context(repository, binding, key=key)
    metadata = await _custody_metadata(repository, binding, session, key=key)
    if any(previous[2][4] == metadata[4] for previous in completed):
        _deny("native_g12_new_custody_transaction_required")
    state["custody_pending_session"] = (stage, session, metadata)


async def _finish_custody_session_v2(repository, binding, session, *, stage, key):
    state, _issued = _custody_context(repository, binding, key=key)
    pending = state.get("custody_pending_session")
    if pending is None or pending[0] != stage or pending[1] is not session:
        _deny("native_g12_custody_transaction_changed")
    await _custody_catalog.verify_actual_issuer_catalog_v2(session)
    _custody_context(repository, binding, key=key)
    metadata = await _custody_metadata(repository, binding, session, key=key)
    original = pending[2]
    if (
        metadata[:5] != original[:5]
        or metadata[6:] != original[6:]
        or metadata[5] < original[5]
    ):
        _deny("native_g12_custody_transaction_changed")
    state["custody_sessions"].append((stage, session, original, metadata))
    state["custody_pending_session"] = None


def _custody_transcript_sha256(state):
    sessions = state["custody_sessions"]
    if (
        type(sessions) is not list
        or len(sessions) != len(_CUSTODY_STAGES)
        or tuple(item[0] for item in sessions) != _CUSTODY_STAGES
        or state.get("custody_pending_session") is not None
        or len({item[2][4] for item in sessions}) != len(_CUSTODY_STAGES)
    ):
        _deny("native_g12_complete_custody_readbacks_required")
    return sha(
        canonical(
            [
                {
                    "stage": stage,
                    "started": [*before[:5], before[5].isoformat(), *before[6:]],
                    "completed": [*after[:5], after[5].isoformat(), *after[6:]],
                }
                for stage, _session, before, after in sessions
            ]
        )
    )


def _claim(state, local, issued):
    original = state["candidate"]["facts"]
    result = original.result
    document = {
        "schema_version": "ctcc.g12.invocation_claim.v1",
        "invocation_id": uuid4().hex,
        "environment": "demo",
        "account_uid_sha256": sha(canonical(["demo", issued.scope.account_id])),
        "account_plan_sha256": capture.plan_sha256(issued.plan),
        "event_key": original.original_event_key,
        "report_id": result.report_id,
        "instrument_id": local["computed"].intent.instrument_id,
        "direction": result.direction,
        "entry": str(result.candidate_entry),
        "stop_loss": str(result.stop_loss),
        "take_profit": str(result.take_profit),
        "deadline": original.expires_at.isoformat(),
        "candidate_sha256": original.candidate_sha256,
        "original_run_sha256": original.candidate_sha256,
        "original_market_sha256": sha(
            canonical(local["market"].model_dump(mode="json"))
        ),
        "original_inputs_sha256": sha(
            canonical(
                {
                    "local_policy_sha256": sha(local["policy_raw"]),
                    "issued_row_sha256": issued.issued_row_sha256,
                    "public_packet_sha256": local["public_packet"].bundle_sha256,
                    "source_contract": "ctcc.native_original_candidate.v2",
                }
            )
        ),
        "evidence_root_sha256": custody._root_pin(state["root"], result.report_id),
        "admission": "DENY",
        "source_authenticity_verified": False,
        "execution_authority": False,
    }
    raw, digest = custody._document(document)
    return custody.G12InvocationClaim(document["invocation_id"], raw, digest)


def _require_actual_publication(state):
    facts = state["facts"]
    if (
        facts.original != state["candidate"]["facts"]
        or facts.receipt.status != "written"
        or facts.claim.db_claimed_at is None
        or facts.publication.db_recorded_at is None
        or facts.publication.invocation_id != facts.claim.invocation_id
        or facts.receipt.completed_at <= facts.claim.db_claimed_at
        or facts.receipt.completed_at >= facts.original.expires_at
        or facts.receipt.files != state["rendered_files"]
        or sha(canonical(facts.snapshot.model_dump(mode="json", round_trip=True)))
        != state["snapshot_sha256"]
        or facts.custody_session_sha256 != _custody_transcript_sha256(state)
    ):
        _deny("native_g12_publication_binding_changed")
    actual = decode(facts.publication.publication_json)
    if (
        actual["claim_sha256"] != facts.claim.claim_sha256
        or actual["candidate_sha256"] != facts.original.candidate_sha256
        or actual["event_key"] != facts.original.original_event_key
        or actual["evidence_sha256"] != facts.evaluation_sha256
        or actual["report_sha256"] != facts.receipt.report_sha256
        or actual["file_sha256"]
        != {item.name: item.sha256 for item in facts.receipt.files}
    ):
        _deny("native_g12_committed_custody_changed")
    packet = dict(state["packet"])
    if not custody._read_exact_six(
        state["root"],
        facts.receipt,
        facts.claim.values["evidence_root_sha256"],
        expected_packet=packet,
    ):
        _deny("native_g12_computational_readback_changed")


class _Attempt(Coroutine):
    def __init__(self, state):
        self._state, self._body = state, _publish(state)

    def __await__(self):
        return self

    def __iter__(self):
        return self

    def __next__(self):
        return self.send(None)

    def send(self, value):
        try:
            return self._body.send(value)
        except BaseException:
            if not self._state.get("issued", False):
                _release(self._state)
            raise

    def throw(self, *args):
        try:
            return self._body.throw(*args)
        except BaseException:
            if not self._state.get("issued", False):
                _release(self._state)
            raise

    def close(self):
        try:
            self._body.close()
        finally:
            if not self._state.get("issued", False):
                _release(self._state)


def publish_owned_native_candidate_v2(owner, *, evidence_root):
    """MOVE before returning an awaitable; no first-send/cancel reuse."""
    if type(evidence_root) is not type(Path()) or not evidence_root.is_absolute():
        _deny("native_g12_root_invalid")
    original = candidate._take_native_candidate_for_g12_v1(owner, key=_CANDIDATE_KEY_V2)
    state = {
        "candidate": original,
        "root": evidence_root,
        "task": asyncio.current_task(),
        "last": native_stamp(),
        "custody_sessions": [],
        "custody_pending_session": None,
    }
    _native_guard.save_native_guard_v1(state, _NATIVE_GUARD)
    try:
        if len(_CUSTODY_STATES) >= 16:
            _deny("native_g12_live_custody_capacity")
        state["custody_binding"] = object()
        _CUSTODY_STATES[state["custody_binding"]] = state
        _check(state)
        return _Attempt(state)
    except BaseException:
        _release(state)
        raise


async def _publish(state):
    if state.get("started", False):
        _deny("native_g12_attempt_spent")
    state["started"] = True
    try:
        local, _, issued, stamp = _check(state)
        remaining = (
            state["candidate"]["deadline"] - utc_from_ns(stamp["utc_ns"])
        ).total_seconds()
        if remaining <= 0:
            _deny("native_g12_original_expired")
        async with asyncio.timeout(remaining):
            repository = custody.G12InvocationCustodyRepository(issued.session_factory)
            state["custody_repository"] = repository
            prepared_claim = _claim(state, local, issued)
            state["custody_claim_raw"] = prepared_claim.claim_json
            claim = await repository.claim(
                prepared_claim, _native_binding=state["custody_binding"]
            )
            local, _, issued, stamp = _check(state)
            custody.require_prepublication_root(claim, state["root"])
            original = state["candidate"]["facts"]
            snapshot = evidence.prepare_native_evidence_v2(
                local["market"],
                local["analysis"],
                qualification=original.result,
                detection=local["computed"].detection,
                quote=local["quote_value"],
                protection=local["selection"],
                prepared_at=utc_from_ns(stamp["utc_ns"]),
                zone_tick_size=local["tick_size"],
            )
            packet = dict(evidence.render_native_evidence_v2(snapshot))
            candidate.account.scan_issued_metadata_v1(
                state["candidate"]["account_binding"],
                canonical(snapshot.model_dump(mode="json", round_trip=True)),
                key=candidate._G11_KEY_V1,
            )
            for filename, raw_artifact in packet.items():
                candidate.account.scan_issued_artifact_v1(
                    state["candidate"]["account_binding"],
                    filename,
                    raw_artifact,
                    key=candidate._G11_KEY_V1,
                )
            state["packet"] = tuple((name, packet[name]) for name in storage.FILE_NAMES)
            state["snapshot_sha256"] = sha(
                canonical(snapshot.model_dump(mode="json", round_trip=True))
            )
            _check(state)
            state["rendered_files"] = tuple(
                storage.PublishedFile(
                    name=name, sha256=sha(packet[name]), size_bytes=len(packet[name])
                )
                for name in storage.FILE_NAMES
            )

            def clock():
                return utc_from_ns(_check(state)[3]["utc_ns"])

            receipt = storage.publish_evidence(
                state["root"],
                report_id=original.result.report_id,
                files=packet,
                clock=clock,
            )
            if (
                receipt.status != "written"
                or receipt.files != state["rendered_files"]
                or receipt.completed_at <= claim.db_claimed_at
                or not custody._read_exact_six(
                    state["root"],
                    receipt,
                    claim.values["evidence_root_sha256"],
                    expected_packet=packet,
                )
            ):
                _deny("native_g12_fresh_publication_required")
            evaluation = sha(
                canonical(
                    {
                        "schema_version": "ctcc.native_g12_evaluation.v2",
                        "candidate_sha256": original.candidate_sha256,
                        "snapshot_sha256": sha(
                            canonical(snapshot.model_dump(mode="json", round_trip=True))
                        ),
                        "file_sha256": {
                            item.name: item.sha256 for item in receipt.files
                        },
                        "publication_completed_at": receipt.completed_at.isoformat(),
                    }
                )
            )
            raw, digest = custody._document(
                {
                    "schema_version": "ctcc.g12.invocation_publication.v1",
                    "invocation_id": claim.invocation_id,
                    "claim_sha256": claim.claim_sha256,
                    "candidate_sha256": original.candidate_sha256,
                    "event_key": original.original_event_key,
                    "evidence_root_sha256": claim.values["evidence_root_sha256"],
                    "evidence_sha256": evaluation,
                    "report_sha256": receipt.report_sha256,
                    "file_sha256": {item.name: item.sha256 for item in receipt.files},
                    "publication_completed_at": receipt.completed_at.isoformat(),
                    "admission": "DENY",
                    "publication_provenance_verified": False,
                    "execution_authority": False,
                }
            )
            state["custody_publication_raw"] = raw
            committed = await repository.publication(
                claim,
                custody.G12InvocationPublication(claim.invocation_id, raw, digest),
                _native_binding=state["custody_binding"],
            )
            _, _, _, barrier = _check(state)
            if utc_from_ns(barrier["utc_ns"]) <= receipt.completed_at:
                _deny("native_g12_barrier_invalid")
            state["facts"] = NativePublicationFactsV2(
                original,
                snapshot,
                receipt,
                claim,
                committed,
                canonical(barrier),
                evaluation,
                _custody_transcript_sha256(state),
            )
            regenerated = evidence.render_native_evidence_v2(snapshot)
            if (
                tuple((name, regenerated[name]) for name in storage.FILE_NAMES)
                != state["packet"]
            ):
                _deny("native_g12_computational_packet_changed")
            _check(state)  # Actual six-file computation/readback after DB await.
            # The barrier follows the FINAL physical re-read, not merely DB commit.
            barrier = native_stamp()
            validate_stamps((state["last"], barrier))
            if utc_from_ns(barrier["utc_ns"]) >= original.expires_at:
                _deny("native_g12_original_expired_after_readback")
            state["last"] = barrier
            state["facts"] = NativePublicationFactsV2(
                original,
                snapshot,
                receipt,
                claim,
                committed,
                canonical(barrier),
                evaluation,
                _custody_transcript_sha256(state),
            )
            owner = object.__new__(OwnedNativePublishedCandidateV2)
            _OWNERS[owner] = state
            state["finalizer"] = finalize(owner, _release, state)
            state["issued"] = True
            state["task"].add_done_callback(lambda task: _release(state))
            return owner
    except asyncio.CancelledError:
        raise
    except NativeG12Error:
        raise
    except Exception:  # noqa: BLE001 -- retain original ownership and fail closed on unknown/cancellation.
        _deny("native_g12_publication_failed")
    finally:
        if not state.get("issued", False):
            _release(state)


def _take_native_published_for_recheck_v2(owner, *, key):
    from app.trade_qualification import native_postpublication_recheck_v2 as recheck

    if key is not recheck._PUBLICATION_KEY_V2:
        _deny("native_g12_recheck_fixed_purpose_required")
    state = (
        _OWNERS.pop(owner, None)
        if type(owner) is OwnedNativePublishedCandidateV2
        else None
    )
    if state is None:
        _deny()
    state["finalizer"].detach()
    try:
        _check(state)
        return state
    except BaseException:
        _release(state)
        raise


_NATIVE_GUARD = _native_guard.install_native_guards_v1(
    _native_sys.modules[__name__],
    ("publish_owned_native_candidate_v2",),
    dependencies=(
        candidate,
        candidate.account,
        evidence,
        evidence.service,
        evidence.renderer,
        storage,
        custody,
        capture,
        _custody_catalog,
        _custody_catalog.policy,
        _custody_catalog.policy.old54,
        _custody_catalog.policy.old53,
        _custody_catalog.policy.lineage,
        _custody_catalog.policy.issued,
        _custody_catalog.policy.operator,
    ),
)
