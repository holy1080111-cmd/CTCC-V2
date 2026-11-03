"""Native-owned current55 DB0054-table issue/CAS and independent transaction replay.

Only the native financial capability can enter. Data-only codec values, rows,
saved proof JSON, caller factories and clocks cannot authorize this function.
Original source/control/revision/expiry anchors are retained through own-CAS.
There is no order client, Arm, policy reset, migration or authority bypass here.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.models.account_issued_revision import (
    DemoCollectorIssuedAccountRevision as Issued,
)
from app.database.models.account_revision_collector_lineage import (
    DemoCollectorCurrentRevisionLineage as Lineage,
)
from app.database.repositories import (
    account_revision_collector_lineage as original_lineage,
)
from app.database.repositories.collector_current_0055_multirole_catalog_v1 import (
    verify_actual_issuer_catalog_v2,
)
from app.database.repositories.demo_control_current55_v2 import DemoControlRepositoryV2
from app.database.repositories.qualification_ledger import QualificationLedgerRepository
from app.domain.native_clock import native_stamp
from app.domain.source_primitives import canonical, decode, utc_from_ns
from app.trade_qualification import account_ledger_claims_v2 as codec
from app.trade_qualification import demo_control as control
from app.trade_qualification import native_account_risk_assembly_v1 as native
from app.trade_qualification.reservations import LedgerScope

_SOURCE_KEY = object()
_MAX_SOURCE_BYTES = 16 * 1024 * 1024
_MAX_PROOF_BYTES = 1024 * 1024
_ISSUED_COLUMNS = (
    "issuer_sha256",
    "environment",
    "account_id",
    "settlement_currency",
    "previous_account_revision",
    "previous_ledger_revision",
    "previous_claims_sha256",
    "account_revision",
    "ledger_revision",
    "claims_sha256",
    "claims_json",
    "control_revision",
    "control_event_sha256",
    "control_state_sha256",
    "source_lineage_sha256",
    "hwm_custody_id",
    "hwm_bundle_sha256",
    "proof_json",
    "original_expires_at",
    "db_recorded_at",
)
_LINEAGE_COLUMNS = (
    "lineage_sha256",
    "environment",
    "account_id",
    "settlement_currency",
    "account_revision",
    "ledger_revision",
    "claims_sha256",
    "claims_json",
    "control_revision",
    "control_event_sha256",
    "control_state_sha256",
    "current_capture_id",
    "current_terminal_sequence",
    "current_head_sha256",
    "custody_id",
    "lineage_json",
    "db_recorded_at",
)
_SYNC_SQL = text("SET LOCAL synchronous_commit = 'on'")
_SEARCH_PATH_SQL = text("SET LOCAL search_path=pg_catalog,public,pg_temp")
_UTC_SQL = text("SET LOCAL TIME ZONE 'UTC'")
_TIMEOUT_SQL = text(
    "SELECT pg_catalog.set_config('statement_timeout',:limit,true), "
    "pg_catalog.set_config('lock_timeout',:limit,true)"
)
_HWM_SQL = text("SELECT * FROM public.public_runtime_bundle_v1_read(:bundle_id)")


class OwnedAccountIssuerError(ValueError):
    """Fixed public code only; no SQL, account values or exception arguments."""


def _deny(code="native_account_issuer_unavailable"):
    raise OwnedAccountIssuerError(code) from None


def _sha(raw):
    if type(raw) is not bytes:
        _deny()
    return hashlib.sha256(raw).hexdigest()


def _utc(value):
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        _deny("native_account_issuer_clock_invalid")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True, repr=False)
class _IssuerSourceV1:
    """Private replay DATA returned only after the native capability check.

    Constructing this record is not eligibility; the public entry accepts only
    the native owner and obtains/revalidates this state through its private key.
    Native source closure pins this exact type and all helpers used below.
    """

    session_factory: object = field(repr=False)
    scope: LedgerScope
    control_state: control.ControlState = field(repr=False)
    control_event_sha256: str
    previous_account_revision: int
    previous_ledger_revision: int
    previous_claims_sha256: str | None
    expires_at: datetime
    monotonic_deadline_ns: int
    claims_json: bytes = field(repr=False)
    lineage_row_json: bytes = field(repr=False)
    hwm_custody_id: str
    hwm_bundle_json: bytes = field(repr=False)
    hwm_db_recorded_at: datetime
    hwm_proof_envelope_sha256: str
    proof_operands: tuple[tuple[str, bytes], ...] = field(repr=False)


@dataclass(frozen=True, slots=True, repr=False)
class _IssuerCommitBindingV1:
    """Exact own-CAS allowance, never a replacement of original anchors."""

    issuer_sha256: str
    claims_sha256: str
    previous_account_revision: int
    previous_ledger_revision: int
    account_revision: int
    ledger_revision: int
    write_role_oid: int
    write_backend_pid: int
    write_transaction_id: str


@dataclass(frozen=True, slots=True, repr=False)
class _IssuedReadbackV1:
    """Actual whole-row readback supplied to native finish, never a permit."""

    source: _IssuerSourceV1 = field(repr=False)
    transition: _IssuerCommitBindingV1
    row_json: bytes = field(repr=False)
    claims_json: bytes = field(repr=False)
    proof_json: bytes = field(repr=False)
    write_metadata: tuple = field(repr=False)
    readback_metadata: tuple = field(repr=False)
    readback_observed_at: datetime
    execution_authority: bool = field(default=False, init=False)


def _checked_source(value):
    if (
        type(value) is not _IssuerSourceV1
        or type(value.session_factory) is not async_sessionmaker
        or type(value.scope) is not LedgerScope
        or value.scope.environment != "demo"
        or value.scope.settlement_currency != "USDT"
        or type(value.control_state) is not control.ControlState
        or value.control_state.scope
        != control.ControlScope("demo", value.scope.account_id)
        or type(value.previous_account_revision) is not int
        or type(value.previous_ledger_revision) is not int
        or not 0
        <= value.previous_account_revision
        <= value.previous_ledger_revision
        < 2**63 - 1
        or type(value.monotonic_deadline_ns) is not int
        or value.monotonic_deadline_ns <= 0
        or type(value.proof_operands) is not tuple
        or len(value.proof_operands) != len(codec.ISSUER_PROOF_KEYS)
        or type(value.claims_json) is not bytes
        or type(value.lineage_row_json) is not bytes
        or type(value.hwm_bundle_json) is not bytes
    ):
        _deny("native_account_issuer_source_invalid")
    source_bytes = (
        len(value.claims_json)
        + len(value.lineage_row_json)
        + len(value.hwm_bundle_json)
    )
    for pair in value.proof_operands:
        if (
            type(pair) is not tuple
            or len(pair) != 2
            or type(pair[0]) is not str
            or type(pair[1]) is not bytes
            or not pair[1]
        ):
            _deny("native_account_issuer_source_invalid")
        source_bytes += len(pair[1])
    if tuple(pair[0] for pair in value.proof_operands) != codec.ISSUER_PROOF_KEYS:
        _deny("native_account_issuer_proof_membership_changed")
    for digest in (value.control_event_sha256, value.hwm_proof_envelope_sha256):
        if (
            type(digest) is not str
            or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
        ):
            _deny("native_account_issuer_source_invalid")
    if (
        (
            value.previous_account_revision == 0
            and value.previous_claims_sha256 is not None
        )
        or (
            value.previous_account_revision > 0
            and (
                type(value.previous_claims_sha256) is not str
                or len(value.previous_claims_sha256) != 64
                or any(
                    char not in "0123456789abcdef"
                    for char in value.previous_claims_sha256
                )
            )
        )
        or type(value.hwm_custody_id) is not str
        or len(value.hwm_custody_id) != 32
        or any(char not in "0123456789abcdef" for char in value.hwm_custody_id)
    ):
        _deny("native_account_issuer_source_invalid")
    if not 0 < source_bytes <= _MAX_SOURCE_BYTES:
        _deny("native_account_issuer_source_bound")
    claims = codec.decode_finite_account_ledger_claims_v1(value.claims_json)
    if (
        claims.scope != value.scope
        or claims.financial.original_expires_at != value.expires_at
    ):
        _deny("native_account_issuer_claims_changed")
    # Data codecs deliberately admit explicit unknowns. Native issuance does not.
    # The original seed stays None; a proven current suffix bound is distinct.
    financial = claims.financial
    if financial.missing_obligations or any(
        item is None
        for item in (
            financial.daily_net_pnl,
            financial.weekly_net_pnl,
            financial.current_loss_streak_upper_bound,
            financial.measured_peak_equity,
            financial.measured_peak_observed_at,
            financial.measured_current_equity,
            financial.measured_hwm_receipt_sha256,
        )
    ):
        _deny("native_account_issuer_financial_incomplete")
    return value


def _source(
    state,
    transition=None,
    _replay=native._replay_owned_account_issuer_v1,
    _clock=native_stamp,
):
    # The native callee is responsible for genuine origin/generation/MOVE,
    # captured factory/credential/revision bindings, all12 actual source proofs,
    # original retained raw bytes and exact own-CAS allowance. No caller map is
    # accepted as a substitute for its retained registry state.
    view = _replay(state, key=_SOURCE_KEY, transition=transition)
    _checked_source(view)
    stamp = _clock()
    now = utc_from_ns(stamp["utc_ns"])
    if not (
        now < _utc(view.expires_at)
        and stamp["monotonic_ns"] < view.monotonic_deadline_ns
    ):
        _deny("native_account_issuer_original_expiry")
    return view, stamp


def _remaining(view, stamp):
    remaining = min(
        (view.expires_at - utc_from_ns(stamp["utc_ns"])).total_seconds(),
        (view.monotonic_deadline_ns - stamp["monotonic_ns"]) / 1_000_000_000,
    )
    if remaining <= 0:
        _deny("native_account_issuer_original_expiry")
    return remaining


def _snapshot(model, exact_type, columns):
    if (
        type(model) is not exact_type
        or tuple(exact_type.__table__.columns.keys()) != columns
    ):
        _deny("native_account_issuer_row_shape_changed")
    document = {}
    for name in columns:
        value = getattr(model, name)
        if type(value) is datetime:
            value = _utc(value).isoformat()
        elif value is not None and type(value) not in (str, int):
            _deny("native_account_issuer_row_shape_changed")
        document[name] = value
    return canonical(document)


def _lineage_snapshot_v1(model):
    """Source adopter uses this on its real original row; no authority minted."""
    return _snapshot(model, Lineage, _LINEAGE_COLUMNS)


def _control_sha(state):
    return control.digest(control.canonical(control.document(state)))


async def _catalog_metadata(session, state, transition=None):
    # Use the new exact55 writer and unchanged native DB metadata validators.
    catalog = await verify_actual_issuer_catalog_v2(session)
    _source(state, transition)
    metadata = await original_lineage._metadata(session)
    view, stamp = _source(state, transition)
    if (
        catalog["role_oid"] != metadata[3]
        or catalog["database_head"] != "0055"
        or catalog["native_baseline_admission"] is not True
        or metadata[9] > utc_from_ns(stamp["utc_ns"])
        or metadata[9] >= view.expires_at
    ):
        _deny("native_account_issuer_database_identity_changed")
    return catalog, metadata


async def _locked_scope(session, state, transition=None):
    view, _ = _source(state, transition)
    observed_control, event_sha = await DemoControlRepositoryV2.read_in_transaction(
        session, control.ControlScope(view.scope.environment, view.scope.account_id)
    )
    _source(state, transition)
    ledger = QualificationLedgerRepository(
        view.session_factory, clock=original_lineage._now
    )
    row = await ledger._locked(session, view.scope)
    _source(state, transition)
    if (
        observed_control != view.control_state
        or event_sha != view.control_event_sha256
        or _control_sha(observed_control) != _control_sha(view.control_state)
    ):
        _deny("native_account_issuer_control_changed")
    expected = (
        (
            view.previous_account_revision,
            view.previous_ledger_revision,
            view.previous_claims_sha256,
        )
        if transition is None
        else (
            transition.account_revision,
            transition.ledger_revision,
            transition.claims_sha256,
        )
    )
    if (row.account_revision, row.ledger_revision, row.claims_sha256) != expected:
        _deny("native_account_issuer_revision_conflict")
    if transition is not None and row.claims_json.encode("utf-8") != view.claims_json:
        _deny("native_account_issuer_claims_changed")
    return row, observed_control, event_sha


async def _read_parents(session, state, transition=None):
    view, _ = _source(state, transition)
    original = decode(view.lineage_row_json)
    saved = await session.scalar(
        select(Lineage)
        .where(Lineage.lineage_sha256 == original["lineage_sha256"])
        .with_for_update(read=True, key_share=True)
    )
    _source(state, transition)
    if _lineage_snapshot_v1(saved) != view.lineage_row_json:
        _deny("native_account_issuer_lineage_changed")
    if _sha(saved.lineage_json.encode("utf-8")) != saved.lineage_sha256:
        _deny("native_account_issuer_lineage_changed")
    hwm = (await session.execute(_HWM_SQL, {"bundle_id": view.hwm_custody_id})).one()
    _source(state, transition)
    if (
        hwm.bundle_id != view.hwm_custody_id
        or bytes(hwm.bundle_raw) != view.hwm_bundle_json
        or hwm.bundle_sha256 != _sha(view.hwm_bundle_json)
        or hwm.proof_envelope_sha256 != view.hwm_proof_envelope_sha256
        or hwm.contract_version != 1
        or _utc(hwm.recorded_at) != view.hwm_db_recorded_at
    ):
        _deny("native_account_issuer_hwm_changed")
    return saved, hwm


def _build_proof(view, metadata, sampled_at, lineage, hwm):
    _checked_source(view)
    if not (
        _utc(lineage.db_recorded_at) <= sampled_at < view.expires_at
        and _utc(hwm.recorded_at) <= sampled_at
    ):
        _deny("native_account_issuer_parent_clock_invalid")
    document = {
        "schema_version": "ctcc.collector_issued_account_revision.v1",
        "scope": [
            view.scope.environment,
            view.scope.account_id,
            view.scope.settlement_currency,
        ],
        "claims_sha256": _sha(view.claims_json),
        "source_lineage_sha256": lineage.lineage_sha256,
        "hwm_custody_id": view.hwm_custody_id,
        "hwm_bundle_sha256": _sha(view.hwm_bundle_json),
        "previous_account_revision": view.previous_account_revision,
        "previous_ledger_revision": view.previous_ledger_revision,
        "account_revision": view.previous_account_revision + 1,
        "ledger_revision": view.previous_ledger_revision + 1,
        "control_revision": view.control_state.revision,
        "control_event_sha256": view.control_event_sha256,
        "control_state_sha256": _control_sha(view.control_state),
        "db_role_oid": metadata[3],
        "db_backend_pid": metadata[7],
        "db_transaction_id": metadata[8],
        "owner_sampled_at": sampled_at.isoformat(),
        "original_expires_at": view.expires_at.isoformat(),
        "proofs": {name: _sha(raw) for name, raw in view.proof_operands},
        "execution_authority": False,
    }
    raw = canonical(document)
    if len(raw) > _MAX_PROOF_BYTES:
        _deny("native_account_issuer_proof_bound")
    return raw


def _issued_model(view, proof):
    return Issued(
        issuer_sha256=_sha(proof),
        environment=view.scope.environment,
        account_id=view.scope.account_id,
        settlement_currency=view.scope.settlement_currency,
        previous_account_revision=view.previous_account_revision,
        previous_ledger_revision=view.previous_ledger_revision,
        previous_claims_sha256=view.previous_claims_sha256,
        account_revision=view.previous_account_revision + 1,
        ledger_revision=view.previous_ledger_revision + 1,
        claims_sha256=_sha(view.claims_json),
        claims_json=view.claims_json.decode("utf-8"),
        control_revision=view.control_state.revision,
        control_event_sha256=view.control_event_sha256,
        control_state_sha256=_control_sha(view.control_state),
        source_lineage_sha256=decode(view.lineage_row_json)["lineage_sha256"],
        hwm_custody_id=view.hwm_custody_id,
        hwm_bundle_sha256=_sha(view.hwm_bundle_json),
        proof_json=proof.decode("utf-8"),
        original_expires_at=view.expires_at,
    )


def _expected_values(model):
    if (
        type(model) is not Issued
        or tuple(Issued.__table__.columns.keys()) != _ISSUED_COLUMNS
    ):
        _deny("native_account_issuer_row_shape_changed")
    return tuple(
        getattr(model, name) for name in _ISSUED_COLUMNS if name != "db_recorded_at"
    )


def _validate_issued(saved, expected_values, *, sampled_at, observed_at):
    # The immutable expected tuple was captured before INSERT. No detached or
    # expired ORM object can silently reload/stand in for the original request.
    if (
        type(saved) is not Issued
        or type(expected_values) is not tuple
        or len(expected_values) != len(_ISSUED_COLUMNS) - 1
        or _expected_values(saved) != expected_values
        or not sampled_at
        <= _utc(saved.db_recorded_at)
        <= observed_at
        < saved.original_expires_at
    ):
        _deny("native_account_issuer_committed_readback_changed")
    return _snapshot(saved, Issued, _ISSUED_COLUMNS)


async def _issue(
    state,
    _source=_source,
    _retain=native._retain_unknown_owned_account_issuer_v1,
    _finish=native._finish_owned_account_issuer_v1,
    _clock=native_stamp,
):
    transition = None
    try:
        loop_started = asyncio.get_running_loop().time()
        view, stamp = _source(state)
        deadline = loop_started + _remaining(view, stamp)
        expected = expected_values = proof = inserted_raw = write_metadata = (
            write_catalog
        ) = None
        write_session = None
        async with asyncio.timeout_at(deadline):
            async with view.session_factory() as session, session.begin():
                write_session = session
                if type(session) is not AsyncSession:
                    _deny("native_account_issuer_native_session_required")
                await session.execute(_SYNC_SQL)
                _source(state)
                await session.execute(_SEARCH_PATH_SQL)
                _source(state)
                await session.execute(_UTC_SQL)
                view, stamp = _source(state)
                milliseconds = int(_remaining(view, stamp) * 1000)
                if milliseconds < 1:
                    _deny("native_account_issuer_original_expiry")
                milliseconds = str(min(milliseconds, 2**31 - 1))
                await session.execute(_TIMEOUT_SQL, {"limit": milliseconds})
                _source(state)
                write_catalog, write_metadata = await _catalog_metadata(session, state)
                scope_row, _, _ = await _locked_scope(session, state)
                lineage, hwm = await _read_parents(session, state)
                view, stamp = _source(state)
                sampled_at = utc_from_ns(stamp["utc_ns"])
                proof = _build_proof(view, write_metadata, sampled_at, lineage, hwm)
                expected = _issued_model(view, proof)
                expected_values = _expected_values(expected)
                transition = _IssuerCommitBindingV1(
                    expected.issuer_sha256,
                    expected.claims_sha256,
                    view.previous_account_revision,
                    view.previous_ledger_revision,
                    expected.account_revision,
                    expected.ledger_revision,
                    write_metadata[3],
                    write_metadata[7],
                    write_metadata[8],
                )
                _source(state, transition)  # Bind ONLY this own-CAS before INSERT.
                session.add(expected)
                await session.flush()  # Sole INSERT; DB0054 trigger owns the +1/+1 CAS.
                _source(state, transition)
                await session.refresh(expected)
                _source(state, transition)
                await session.refresh(scope_row)
                _source(state, transition)
                if (
                    scope_row.account_revision != transition.account_revision
                    or scope_row.ledger_revision != transition.ledger_revision
                    or scope_row.claims_sha256 != transition.claims_sha256
                    or scope_row.claims_json.encode("utf-8") != view.claims_json
                ):
                    _deny("native_account_issuer_trigger_cas_changed")
                final_catalog, final_meta = await _catalog_metadata(
                    session, state, transition
                )
                if (
                    final_catalog != write_catalog
                    or final_meta[:9] != write_metadata[:9]
                ):
                    _deny("native_account_issuer_transaction_changed")
                inserted_raw = _validate_issued(
                    expected,
                    expected_values,
                    sampled_at=sampled_at,
                    observed_at=final_meta[9],
                )
            _source(state, transition)  # Includes actual commit and session close.
            async with view.session_factory() as read_session, read_session.begin():
                if (
                    type(read_session) is not AsyncSession
                    or read_session is write_session
                ):
                    _deny("native_account_issuer_independent_session_required")
                await read_session.execute(_SYNC_SQL)
                _source(state, transition)
                await read_session.execute(_SEARCH_PATH_SQL)
                _source(state, transition)
                await read_session.execute(_UTC_SQL)
                view, stamp = _source(state, transition)
                milliseconds = int(_remaining(view, stamp) * 1000)
                if milliseconds < 1:
                    _deny("native_account_issuer_original_expiry")
                await read_session.execute(
                    _TIMEOUT_SQL, {"limit": str(min(milliseconds, 2**31 - 1))}
                )
                _source(state, transition)
                read_catalog, read_metadata = await _catalog_metadata(
                    read_session, state, transition
                )
                if (
                    read_catalog != write_catalog
                    or read_metadata[0:7] != write_metadata[0:7]
                    or read_metadata[8] == write_metadata[8]
                ):
                    _deny("native_account_issuer_independent_transaction_required")
                await _locked_scope(read_session, state, transition)
                await _read_parents(read_session, state, transition)
                saved = await read_session.scalar(
                    select(Issued)
                    .where(Issued.issuer_sha256 == transition.issuer_sha256)
                    .with_for_update(read=True, key_share=True)
                )
                _source(state, transition)
                read_raw = _validate_issued(
                    saved,
                    expected_values,
                    sampled_at=sampled_at,
                    observed_at=read_metadata[9],
                )
                if read_raw != inserted_raw:
                    _deny("native_account_issuer_committed_bytes_changed")
                final_catalog, final_read_meta = await _catalog_metadata(
                    read_session, state, transition
                )
                if (
                    final_catalog != read_catalog
                    or final_read_meta[:9] != read_metadata[:9]
                ):
                    _deny("native_account_issuer_readback_transaction_changed")
            _source(state, transition)  # Read transaction exit/close is guarded too.
            result = _IssuedReadbackV1(
                view,
                transition,
                read_raw,
                view.claims_json,
                proof,
                write_metadata,
                final_read_meta,
                utc_from_ns(_clock()["utc_ns"]),
            )
            return _finish(state, result, key=_SOURCE_KEY)
    except asyncio.CancelledError:
        _retain(state, key=_SOURCE_KEY)
        raise
    except Exception:  # noqa: BLE001 -- Retire a consumed source without leaking native SQL errors.
        # Never expose native DB/SQL/request arguments. A definitive pre-submit
        # rejection and uncertain commit/readback both consume this source; no
        # automatic retry or removal of a possibly committed row is permitted.
        _retain(state, key=_SOURCE_KEY)
        _deny("native_account_issuer_commit_or_readback_unknown")
    except BaseException:
        # Native retention remains strong/bounded through shutdown/late expiry.
        _retain(state, key=_SOURCE_KEY)
        raise


def _bind_native_issuer_entry_v1():
    # Capture the genuine source hook once, outside mutable module aliases.
    # Its retained native closure verifies this module's exact helper/code/
    # defaults/types/SQL/key inventory before MOVE and every replay.
    original_take = native._take_owned_account_issuer_v1
    original_issue = _issue
    original_retain = native._retain_unknown_owned_account_issuer_v1
    original_key = _SOURCE_KEY

    def issue_owned_account_revision_v1(source):
        """MOVE before coroutine creation; no caller factory, clock or proofs."""
        state = original_take(source, key=original_key)
        try:
            return original_issue(state)
        except BaseException:
            original_retain(state, key=original_key)
            raise

    return issue_owned_account_revision_v1


issue_owned_account_revision_v1 = _bind_native_issuer_entry_v1()


def _plain_locked_metadata_v1(value):
    kinds = (str, str, str, int, bool, bool, bool, int, str, datetime)
    if (
        type(value) is not tuple
        or len(value) != len(kinds)
        or any(type(item) is not kind for item, kind in zip(value, kinds, strict=True))
        or value[0] != value[1]
        or value[2] != "none"
        or not 0 < value[3] < 2**32
        or any(item is not False for item in value[4:7])
        or not 0 < value[7] < 2**31
        or not 1 <= len(value[8]) <= 20
        or not value[8].isascii()
        or not value[8].isdigit()
    ):
        _deny("native_issued_revision_locked_metadata_invalid")
    _utc(value[9])
    return value


async def read_issued_revision_locked_v1(
    readback,
    *,
    session,
    scope_row,
    control_state,
    control_event_sha256,
    expected_ledger_revision,
):
    """Actual DB0054 row DATA under the native ledger wrapper's existing lock.

    This reader is not source eligibility. RI's genuine issued-capability
    wrapper must validate origin/MOVE/factory/expiry/own exact reservation chain
    before and after this await, deriving expected_ledger_revision itself.
    Calling this with a constructed readback cannot mint or reserve anything.
    """
    from app.database.models.qualification_ledger import QualificationAccountScope
    from app.database.repositories import public_runtime_bundle_custody as custody

    try:
        if (
            type(readback) is not _IssuedReadbackV1
            or type(readback.transition) is not _IssuerCommitBindingV1
            or type(session) is not AsyncSession
            or not session.in_transaction()
            or type(scope_row) is not QualificationAccountScope
            or type(expected_ledger_revision) is not int
            or type(readback.claims_json) is not bytes
            or type(readback.proof_json) is not bytes
        ):
            _deny("native_issued_revision_locked_input_invalid")
        view = _checked_source(readback.source)
        transition = readback.transition
        if (
            any(
                type(value) is not int
                for value in (
                    transition.previous_account_revision,
                    transition.previous_ledger_revision,
                    transition.account_revision,
                    transition.ledger_revision,
                    transition.write_role_oid,
                    transition.write_backend_pid,
                )
            )
            or transition.previous_account_revision != view.previous_account_revision
            or transition.previous_ledger_revision != view.previous_ledger_revision
            or transition.account_revision != view.previous_account_revision + 1
            or transition.ledger_revision != view.previous_ledger_revision + 1
            or not transition.ledger_revision <= expected_ledger_revision < 2**63
            or type(transition.issuer_sha256) is not str
            or type(transition.claims_sha256) is not str
        ):
            _deny("native_issued_revision_locked_transition_invalid")
        _plain_locked_metadata_v1(readback.write_metadata)
        _plain_locked_metadata_v1(readback.readback_metadata)
        if (
            session.get_bind()
            is not custody._native_engine(view.session_factory).sync_engine
            or type(readback.row_json) is not bytes
            or readback.claims_json != view.claims_json
            or _sha(readback.claims_json) != transition.claims_sha256
            or _sha(readback.proof_json) != transition.issuer_sha256
            or readback.write_metadata[8] == readback.readback_metadata[8]
        ):
            _deny("native_issued_revision_locked_source_changed")
        expected_row = decode(readback.row_json)
        if (
            set(expected_row) != set(_ISSUED_COLUMNS)
            or canonical(expected_row) != readback.row_json
        ):
            _deny("native_issued_revision_locked_row_changed")
        for name, value in (
            ("issuer_sha256", transition.issuer_sha256),
            ("claims_sha256", transition.claims_sha256),
            ("environment", view.scope.environment),
            ("account_id", view.scope.account_id),
            ("settlement_currency", view.scope.settlement_currency),
            ("previous_account_revision", view.previous_account_revision),
            ("previous_ledger_revision", view.previous_ledger_revision),
            ("account_revision", transition.account_revision),
            ("ledger_revision", transition.ledger_revision),
            ("control_revision", view.control_state.revision),
            ("control_event_sha256", view.control_event_sha256),
            ("control_state_sha256", _control_sha(view.control_state)),
        ):
            if expected_row[name] != value:
                _deny("native_issued_revision_locked_row_changed")
        # All SQL below is read/lock only. Never autoflush a caller's pending
        # mutations before the original source/own-chain check has completed.
        with session.no_autoflush:
            catalog = await verify_actual_issuer_catalog_v2(session)
            metadata = await original_lineage._metadata(session)
            if (
                catalog["role_oid"] != readback.write_metadata[3]
                or metadata[:7] != readback.write_metadata[:7]
                or metadata[8]
                in (readback.write_metadata[8], readback.readback_metadata[8])
            ):
                _deny("native_issued_revision_locked_transaction_changed")
            (
                observed_control,
                observed_event,
            ) = await DemoControlRepositoryV2.read_in_transaction(
                session,
                control.ControlScope(view.scope.environment, view.scope.account_id),
            )
            if (
                observed_control != view.control_state
                or observed_control != control_state
                or observed_event != view.control_event_sha256
                or observed_event != control_event_sha256
            ):
                _deny("native_issued_revision_locked_control_changed")
            actual_scope = await session.scalar(
                select(QualificationAccountScope)
                .filter_by(
                    environment=view.scope.environment,
                    account_id=view.scope.account_id,
                    settlement_currency=view.scope.settlement_currency,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if (
                actual_scope is not scope_row
                or scope_row.account_revision != transition.account_revision
                or scope_row.ledger_revision != expected_ledger_revision
                or scope_row.claims_sha256 != transition.claims_sha256
                or scope_row.claims_json.encode("utf-8") != readback.claims_json
            ):
                _deny("native_issued_revision_locked_scope_changed")
            saved = await session.scalar(
                select(Issued)
                .where(Issued.issuer_sha256 == transition.issuer_sha256)
                .with_for_update(read=True, key_share=True)
                .execution_options(populate_existing=True)
            )
            if (
                _snapshot(saved, Issued, _ISSUED_COLUMNS) != readback.row_json
                or saved.claims_json.encode("utf-8") != readback.claims_json
                or saved.proof_json.encode("utf-8") != readback.proof_json
            ):
                _deny("native_issued_revision_locked_row_changed")
            final_catalog = await verify_actual_issuer_catalog_v2(session)
            final_metadata = await original_lineage._metadata(session)
            stamp = native_stamp()
            now = utc_from_ns(stamp["utc_ns"])
            if (
                final_catalog != catalog
                or final_metadata[:9] != metadata[:9]
                or not _utc(saved.db_recorded_at)
                <= final_metadata[9]
                <= now
                < view.expires_at
                or stamp["monotonic_ns"] >= view.monotonic_deadline_ns
            ):
                _deny("native_issued_revision_locked_clock_or_catalog_changed")
        # Returns strict DATA only; no truth/permit/READY bit is introduced.
        return codec.decode_finite_account_ledger_claims_v1(readback.claims_json)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 -- Any unverified locked readback denies admission.
        _deny("native_issued_revision_locked_readback_unknown")
