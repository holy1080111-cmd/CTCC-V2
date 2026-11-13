"""Real current55 Control/local/CURRENT custody observation for one native source.

Only the parent's genuine armed registry supplies the opaque input binding.
This component neither creates a DB0049 epoch nor changes Control, claims,
reservations, intents, Arm, EStop or exchange state. Nonflat/unknown B5 facts are
retained exactly; DATA returned here never creates financial eligibility.
"""

import asyncio
import math
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import CoroutineType, FunctionType

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.native_role_preflight_0056_full_diagnostic_v1 import (
    RestrictedNativeRoles0056ObservationV1,
)
from app.core.native_role_witness_0056_v1 import RestrictedNativeRoles0056WitnessV1
from app.database.repositories import (
    collector_current_0055_multirole_catalog_v1 as catalog55,
)
from app.database.repositories import demo_control_current55_v2 as controls55
from app.database.repositories import public_runtime_bundle_custody as custody
from app.database.repositories import qualification_ledger as ledger
from app.database.repositories import qualification_native_checkpoint_v2 as checkpoint56
from app.demo_automation.native_server_selectors_v2 import (
    ConfiguredNativeDemoFactoriesV2,
)
from app.domain import native_clock
from app.domain.source_primitives import canonical, sha, utc_from_ns, validate_stamps
from app.exchange.okx.private_rest import DemoPinnedReadSession
from app.trade_qualification import account_current_source_v4 as current
from app.trade_qualification import demo_control as control
from app.trade_qualification import native_account_risk_assembly_v1 as native
from app.trade_qualification.reservations import LedgerScope

_SOURCE_KEY = object()
_REPLAY_NAME = "_replay_controlled_coverage_context_v6"
_SHA = re.compile(r"[a-f0-9]{64}\Z", re.ASCII)
_ID = re.compile(r"[a-f0-9]{32}\Z", re.ASCII)
_MAX_RECEIPT = 65536
_METADATA_SQL = (
    "SELECT (SELECT oid FROM pg_catalog.pg_roles WHERE rolname=session_user),"
    "(SELECT oid FROM pg_catalog.pg_roles WHERE rolname=current_user),"
    "pg_catalog.pg_backend_pid(),pg_catalog.pg_current_xact_id()::text,"
    "pg_catalog.clock_timestamp(),pg_catalog.current_setting('role'),"
    "pg_catalog.current_setting('transaction_read_only'),"
    "pg_catalog.current_setting('transaction_isolation'),"
    "pg_catalog.current_setting('search_path'),pg_catalog.current_setting('TimeZone'),"
    "pg_catalog.current_setting('synchronous_commit')"
)


class ControlledCoverageSourceErrorV2(ValueError):
    """Closed codes; no UID, owner token, credentials, raw source or SQL text."""


def _need(condition, code="controlled55_coverage_source_unavailable"):
    if condition is not True:
        raise ControlledCoverageSourceErrorV2(code) from None


def _digest(value):
    return type(value) is str and _SHA.fullmatch(value) is not None


def _utc(value):
    _need(
        type(value) is datetime and value.tzinfo is UTC,
        "controlled55_coverage_utc_required",
    )
    return value


def _stamp():
    value = native_clock.native_stamp()
    _need(
        type(value) is dict
        and set(value) == {"utc_ns", "monotonic_ns"}
        and all(type(item) is int and item > 0 for item in value.values()),
        "controlled55_coverage_native_clock_required",
    )
    return value


def _now():
    return utc_from_ns(_stamp()["utc_ns"])


@dataclass(frozen=True, slots=True, repr=False)
class ControlledCoverageSourceV6:
    """Parent-produced DATA, authenticated by its original private registry."""

    session_factory: object
    scope: LedgerScope
    control_state: control.ControlState
    control_event_sha256: str
    current_record: current.RecordedCurrentAccountBundleV4
    current_bundle_raw: bytes = field(repr=False)
    current_custody_repository: custody.RuntimeBundleCustodyRepository = field(
        repr=False
    )
    current_custody_id: str
    current_bundle_sha256: str
    source_generation: object = field(repr=False)
    original_expires_at: datetime
    monotonic_deadline_ns: int
    writer_inventory_sha256: str
    read_session: DemoPinnedReadSession = field(repr=False)
    db0056_factories: object | None = field(default=None, repr=False)
    db0056_roles: object | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True, repr=False)
class CoverageDatabaseObservationV2:
    before: tuple
    after: tuple
    observed_at: datetime
    received_at: datetime


@dataclass(frozen=True, slots=True, repr=False)
class ControlledSourceCoverageObservationV2:
    scope: LedgerScope
    control_state: control.ControlState
    control_event_sha256: str
    ledger_state: object = field(repr=False)
    local_checkpoint: ledger.PortfolioLocalCheckpoint = field(repr=False)
    source_generation: object = field(repr=False)
    receipt_json: bytes
    observed_at: datetime
    received_at: datetime
    database_observations: tuple[CoverageDatabaseObservationV2, ...]
    account_complete: bool = field(default=False, init=False)
    account_revision_issued: bool = field(default=False, init=False)
    event_eligibility: bool = field(default=False, init=False)
    execution_authority: bool = field(default=False, init=False)


@dataclass(frozen=True, slots=True, repr=False)
class _LockedSnapshotV2:
    session: object = field(repr=False)
    control_state: control.ControlState
    control_event_sha256: str
    ledger_state: object = field(repr=False)
    ledger_json: str = field(repr=False)
    local_checkpoint: ledger.PortfolioLocalCheckpoint = field(repr=False)
    database: CoverageDatabaseObservationV2


def _context_signature(value):
    _need(type(value) is ControlledCoverageSourceV6)
    _database_profile(value)
    _need(
        type(value.scope) is LedgerScope
        and value.scope.environment == "demo"
        and value.scope.settlement_currency == "USDT"
    )
    _need(
        type(value.control_state) is control.ControlState
        and value.control_state.scope
        == control.ControlScope("demo", value.scope.account_id)
    )
    _need(
        type(value.current_record) is current.RecordedCurrentAccountBundleV4
        and type(value.current_bundle_raw) is bytes
        and 0 < len(value.current_bundle_raw) <= custody.MAX_BUNDLE_BYTES
        and _digest(value.current_bundle_sha256)
        and sha(value.current_bundle_raw) == value.current_bundle_sha256
        and _digest(value.control_event_sha256)
        and _digest(value.writer_inventory_sha256)
        and type(value.current_custody_id) is str
        and _ID.fullmatch(value.current_custody_id) is not None
        and type(value.source_generation) is object
        and type(value.monotonic_deadline_ns) is int
        and value.monotonic_deadline_ns > 0
    )
    _need(
        type(value.current_custody_repository) is custody.RuntimeBundleCustodyRepository
        and type(value.read_session) is DemoPinnedReadSession
    )
    _utc(value.original_expires_at)
    record = value.current_record
    _utc(record.expires_at)
    _need(
        type(record.monotonic_deadline_ns) is int
        and record.monotonic_deadline_ns > 0
        and type(record.current_receipt_json) is bytes
        and _digest(record.proof_envelope_sha256)
        and _digest(record.native_proof_sha256)
        and _digest(record.native_readback_sha256)
        and record.packet.plan.expected_uid == value.scope.account_id
        and record.packet.plan.settlement_currency == value.scope.settlement_currency
    )
    # False DATA flags are deliberately neither permission nor a rejection of
    # this record. The parent authenticates the original native source itself.
    return (
        canonical(control.document(value.control_state)),
        value.control_event_sha256,
        value.current_bundle_raw,
        value.current_custody_id,
        value.current_bundle_sha256,
        value.writer_inventory_sha256,
        value.original_expires_at,
        value.monotonic_deadline_ns,
        record.current_receipt_json,
        record.proof_envelope_sha256,
        record.native_proof_sha256,
        record.native_readback_sha256,
        record.expires_at,
        record.monotonic_deadline_ns,
    )


def _check(state):
    replay = state["replay"]
    _need(
        native.__dict__.get(_REPLAY_NAME) is replay
        and replay.__code__ is state["replay_code"]
        and replay.__globals__ is state["replay_globals"]
        and replay.__builtins__ is state["replay_builtins"]
        and replay.__defaults__ is state["replay_defaults"]
        and replay.__kwdefaults__ is state["replay_kwdefaults"]
        and replay.__closure__ is state["replay_closure"],
        "controlled55_coverage_original_replay_changed",
    )
    _need(
        _replay_cells(replay) == state["replay_cell_count"],
        "controlled55_coverage_original_replay_changed",
    )
    if replay.__closure__ is not None:
        for cell, original in zip(
            replay.__closure__, state["replay_cells"], strict=True
        ):
            try:
                actual = cell.cell_contents
            except ValueError:
                raise ControlledCoverageSourceErrorV2(
                    "controlled55_coverage_original_replay_changed"
                ) from None
            _need(
                _same_closed_value(actual, original),
                "controlled55_coverage_original_replay_changed",
            )
    if replay.__kwdefaults__ is not None:
        actual = tuple(replay.__kwdefaults__.items())
        expected = state["replay_kwitems"]
        _need(
            len(actual) == len(expected)
            and all(
                type(name) is str
                and name == original_name
                and _same_closed_value(value, original)
                for (name, value), (original_name, original) in zip(
                    actual, expected, strict=True
                )
            ),
            "controlled55_coverage_original_replay_changed",
        )
    value = replay(state["binding"], key=_SOURCE_KEY)
    signature = _context_signature(value)
    original = state["source"]
    _need(
        value.session_factory is original.session_factory
        and value.scope == original.scope
        and value.current_custody_repository is original.current_custody_repository
        and value.source_generation is original.source_generation
        and value.read_session is original.read_session
        and value.db0056_factories is original.db0056_factories
        and value.db0056_roles is original.db0056_roles
        and signature == state["signature"],
        "controlled55_coverage_original_source_changed",
    )
    value.read_session.verify_current()
    stamp = _stamp()
    validate_stamps((state["last"], stamp))
    now = utc_from_ns(stamp["utc_ns"])
    _need(
        value.control_state.arm_intent_current(now)
        and now < state["expires_at"]
        and stamp["monotonic_ns"] < state["monotonic_deadline_ns"],
        "controlled55_coverage_original_source_expired_or_revoked",
    )
    state["last"] = stamp
    return stamp


def _same_closed_value(actual, original):
    # Fixed dispatch/name closure only. Never invoke a foreign __eq__ callback.
    return actual is original or (
        type(actual) is str and type(original) is str and actual == original
    )


def _replay_cells(replay):
    closure = replay.__closure__
    _need(
        closure is None or (type(closure) is tuple and len(closure) <= 32),
        "controlled55_coverage_original_replay_changed",
    )
    return 0 if closure is None else len(closure)


async def _checked_await(state, operation):
    try:
        _check(state)
    except BaseException:
        if type(operation) is CoroutineType:
            operation.close()
        raise
    result = await operation
    _check(state)
    return result


async def _metadata(state, session):
    before = _check(state)
    row = tuple(
        (await _checked_await(state, session.execute(text(_METADATA_SQL)))).one()
    )
    after = _check(state)
    _need(
        len(row) == 11
        and all(type(row[index]) is int and row[index] > 0 for index in (0, 1, 2))
        and row[0] == row[1]
        and type(row[3]) is str
        and row[3].isascii()
        and row[3].isdigit()
        and 0 < len(row[3]) <= 20
        and int(row[3]) > 0
        and type(row[4]) is datetime
        and row[4].tzinfo is UTC
        and row[5:]
        == (
            "none",
            "off",
            "read committed",
            "pg_catalog, public, pg_temp",
            "UTC",
            "on",
        ),
        "controlled55_coverage_actual_transaction_invalid",
    )
    _need(
        utc_from_ns(before["utc_ns"]) <= row[4] <= utc_from_ns(after["utc_ns"])
        and row[4] < state["expires_at"],
        "controlled55_coverage_database_clock_invalid",
    )
    return row


def _database_profile(source):
    factories, roles = source.db0056_factories, source.db0056_roles
    if factories is roles is None:
        return None
    _need(
        type(factories) is ConfiguredNativeDemoFactoriesV2
        and type(roles) is RestrictedNativeRoles0056WitnessV1
        and factories.writer_factory is source.session_factory
        and type(roles.observation) is RestrictedNativeRoles0056ObservationV1
        and roles.observation.database_head == "0056"
        and roles.startup_admission is roles.execution_authority is False,
        "controlled55_coverage_original_0056_roles_required",
    )
    return factories, roles


@asynccontextmanager
async def _held_db0056_coverage_control(
    factories, roles, session, scope, expected_control, expected_event
):
    async with checkpoint56.held_qualification_checkpoint_0056_v1(
        factories, session, scope, roles=roles
    ) as held:
        original = held.control.control
        _need(
            (original.state, original.event_sha256)
            == (expected_control, expected_event),
            "controlled55_coverage_actual_control_changed",
        )
        yield held
        _need(
            await held.recheck_control() == (original.state, original.event_sha256),
            "controlled55_coverage_actual_control_changed",
        )


@asynccontextmanager
async def _held_coverage_control(state, session):
    source = state["source"]
    scope = control.ControlScope("demo", source.scope.account_id)
    profile = _database_profile(source)
    if profile is None:
        await _checked_await(state, catalog55.verify_actual_issuer_catalog_v2(session))
        await _checked_await(
            state,
            session.execute(
                select(
                    func.pg_advisory_xact_lock(
                        ledger._account_uid_lock_key(source.scope)
                    )
                )
            ),
        )
        actual, event = await _checked_await(
            state,
            controls55.DemoControlRepositoryV2.read_in_transaction(session, scope),
        )
        yield actual, event, None
        return
    factories, roles = profile
    async with _held_db0056_coverage_control(
        factories,
        roles,
        session,
        scope,
        source.control_state,
        source.control_event_sha256,
    ) as held:
        _check(state)
        original = held.control.control
        yield original.state, original.event_sha256, held
        _check(state)
    _check(state)


async def _read_locked_coverage(
    state, session, repository, current_state, current_event, held
):
    source = state["source"]
    _need(
        canonical(control.document(current_state))
        == canonical(control.document(source.control_state))
        and current_event == source.control_event_sha256,
        "controlled55_coverage_actual_control_changed",
    )
    selected = await _checked_await(state, repository._locked(session, source.scope))
    before = await _metadata(state, session)
    held_args = {} if held is None else {"_held0056": held}
    checkpoint = await _checked_await(
        state,
        repository._bootstrap_checkpoint(session, source.scope, selected, **held_args),
    )
    local = await _checked_await(
        state,
        repository._read_portfolio_checkpoint_locked(
            session, source.scope, selected, **held_args
        ),
    )
    _need(
        type(checkpoint) is ledger.LedgerBootstrapCheckpoint
        and type(local) is ledger.PortfolioLocalCheckpoint
        and type(local.document_json) is bytes
        and 0 < len(local.document_json) <= 262144,
        "controlled55_coverage_full_local_observation_required",
    )
    await _checked_await(
        state,
        catalog55.verify_actual_issuer_catalog_v2(session)
        if held is None
        else held.recheck_catalog(),
    )
    after = await _metadata(state, session)
    _need(
        before[:4] == after[:4]
        and before[5:] == after[5:]
        and before[4] <= checkpoint.observed_at <= checkpoint.received_at <= after[4]
        and before[4] <= local.observed_at <= local.received_at <= after[4],
        "controlled55_coverage_locked_transaction_changed",
    )
    return _LockedSnapshotV2(
        session,
        current_state,
        current_event,
        checkpoint.state,
        ledger.canonical(checkpoint.state),
        local,
        CoverageDatabaseObservationV2(before, after, before[4], after[4]),
    )


async def _locked_snapshot(state):
    source = state["source"]
    _check(state)
    repository = ledger.QualificationLedgerRepository(
        source.session_factory, clock=_now
    )
    async with source.session_factory() as session, session.begin():
        _check(state)
        _need(
            type(session) is AsyncSession
            and session.get_bind() is source.session_factory.kw["bind"].sync_engine
            and session.in_transaction(),
            "controlled55_coverage_actual_factory_required",
        )
        for statement in (
            "SET TRANSACTION ISOLATION LEVEL READ COMMITTED",
            "SET LOCAL search_path TO pg_catalog, public, pg_temp",
            "SET LOCAL TIME ZONE 'UTC'",
            "SET LOCAL synchronous_commit='on'",
        ):
            await _checked_await(state, session.execute(text(statement)))
        remaining = (state["expires_at"] - _now()).total_seconds()
        budget_ms = min(1000, int(remaining * 1000))
        _need(budget_ms > 0, "controlled55_coverage_original_source_expired_or_revoked")
        await _checked_await(
            state,
            session.execute(
                text(
                    "SELECT pg_catalog.set_config('statement_timeout',:value,true),"
                    "pg_catalog.set_config('lock_timeout',:value,true)"
                ),
                {"value": str(budget_ms)},
            ),
        )
        async with _held_coverage_control(state, session) as (
            current_state,
            current_event,
            held,
        ):
            result = await _read_locked_coverage(
                state, session, repository, current_state, current_event, held
            )
    _check(state)
    return result


def _require_same_snapshots(first, second):
    _need(type(first) is _LockedSnapshotV2 and type(second) is _LockedSnapshotV2)
    _need(
        first.session is not second.session
        and first.database.before[3] != second.database.before[3]
        and first.database.received_at <= second.database.observed_at
        and canonical(control.document(first.control_state))
        == canonical(control.document(second.control_state))
        and first.control_event_sha256 == second.control_event_sha256
        and first.ledger_json == second.ledger_json
        and first.local_checkpoint.document_json
        == second.local_checkpoint.document_json,
        "controlled55_coverage_original_local_or_control_changed",
    )


def _wire_metadata(value):
    return [*value[:4], value[4].isoformat(), *value[5:]]


async def _observe(state, absolute_deadline):
    try:
        async with asyncio.timeout_at(absolute_deadline):
            first = await _locked_snapshot(state)
            source = state["source"]
            retained = await _checked_await(
                state,
                custody.RuntimeBundleCustodyRepository.read_retained_bundle(
                    source.current_custody_repository,
                    bundle_id=source.current_custody_id,
                    expected_sha256=source.current_bundle_sha256,
                    proof_envelope_sha256=source.current_record.proof_envelope_sha256,
                ),
            )
            stamp = _check(state)
            _need(
                type(retained) is custody.RetainedRuntimeBundleReadback
                and retained.bundle_id == source.current_custody_id
                and retained.bundle_sha256 == source.current_bundle_sha256
                and retained.proof_envelope_sha256
                == source.current_record.proof_envelope_sha256
                and retained.bundle_raw == source.current_bundle_raw
                and _utc(retained.recorded_at)
                <= _utc(retained.database_readback_at)
                <= utc_from_ns(stamp["utc_ns"]),
                "controlled55_coverage_original_current_custody_changed",
            )
            second = await _locked_snapshot(state)
            _require_same_snapshots(first, second)
            _check(state)
            receipt = canonical(
                {
                    "schema_version": "ctcc.controlled_source_coverage.current55.v2",
                    "environment": "demo",
                    "scope_sha256": sha(ledger.canonical(source.scope).encode()),
                    "control_state_sha256": sha(
                        canonical(control.document(second.control_state))
                    ),
                    "control_event_sha256": second.control_event_sha256,
                    "ledger_state_sha256": sha(second.ledger_json.encode()),
                    "all_currency_local_checkpoint_sha256": second.local_checkpoint.state_sha256,
                    "current_bundle_sha256": source.current_bundle_sha256,
                    "current_custody_id": source.current_custody_id,
                    "current_proof_envelope_sha256": source.current_record.proof_envelope_sha256,
                    "current_receipt_sha256": sha(
                        source.current_record.current_receipt_json
                    ),
                    "writer_inventory_sha256": source.writer_inventory_sha256,
                    "original_expires_at": state["expires_at"].isoformat(),
                    "transactions": [
                        {
                            "before": _wire_metadata(item.database.before),
                            "after": _wire_metadata(item.database.after),
                        }
                        for item in (first, second)
                    ],
                    "observed_at": first.database.observed_at.isoformat(),
                    "received_at": second.database.received_at.isoformat(),
                    "future_writer_exclusion": False,
                    "account_complete": False,
                    "account_revision_issued": False,
                    "event_eligibility": False,
                    "execution_authority": False,
                }
            )
            _need(len(receipt) <= _MAX_RECEIPT, "controlled55_coverage_receipt_bound")
            return ControlledSourceCoverageObservationV2(
                source.scope,
                second.control_state,
                second.control_event_sha256,
                second.ledger_state,
                second.local_checkpoint,
                source.source_generation,
                receipt,
                first.database.observed_at,
                second.database.received_at,
                (first.database, second.database),
            )
    except asyncio.CancelledError:
        raise asyncio.CancelledError() from None
    except ControlledCoverageSourceErrorV2:
        raise
    except Exception:  # noqa: BLE001 -- never export source/SQL/credential exception details.
        raise ControlledCoverageSourceErrorV2(
            "controlled55_coverage_observation_failed"
        ) from None


def observe_controlled_source_coverage_v2(binding):
    """Fixed native purpose; no caller factory, clock, Control DTO, or pass flag.

    The genuine parent SourceKernel validates this original module/wrapper and
    its complete dependencies before invoking it. Capturing the replay below is
    a continuation check, not admission of an arbitrary preexisting callback.
    The parent owns failures/unknown retention; this observer never releases it.
    """
    try:
        loop_before = asyncio.get_running_loop().time()
        initial = _stamp()
        replay = native.__dict__.get(_REPLAY_NAME)
        _need(
            type(replay) is FunctionType, "controlled55_coverage_native_parent_required"
        )
        replay_identity = (replay.__code__, replay.__globals__, replay.__builtins__)
        defaults, kwdefaults, closure = (
            replay.__defaults__,
            replay.__kwdefaults__,
            replay.__closure__,
        )
        _need(defaults is None or (type(defaults) is tuple and len(defaults) <= 16))
        _need(
            kwdefaults is None
            or (
                type(kwdefaults) is dict
                and len(kwdefaults) <= 16
                and all(type(name) is str for name in kwdefaults)
            )
        )
        cell_count = _replay_cells(replay)
        cells = () if closure is None else tuple(cell.cell_contents for cell in closure)
        kwitems = () if kwdefaults is None else tuple(kwdefaults.items())
        source = replay(binding, key=_SOURCE_KEY)
        signature = _context_signature(source)
        expires_at = min(
            _utc(source.original_expires_at),
            _utc(source.current_record.expires_at),
            _utc(source.control_state.lease_until),
            _utc(source.control_state.arm_expires_at),
        )
        mono = min(
            source.monotonic_deadline_ns, source.current_record.monotonic_deadline_ns
        )
        state = {
            "binding": binding,
            "source": source,
            "signature": signature,
            "replay": replay,
            "replay_code": replay_identity[0],
            "replay_globals": replay_identity[1],
            "replay_builtins": replay_identity[2],
            "replay_defaults": defaults,
            "replay_kwdefaults": kwdefaults,
            "replay_kwitems": kwitems,
            "replay_closure": closure,
            "replay_cells": cells,
            "replay_cell_count": cell_count,
            "expires_at": expires_at,
            "monotonic_deadline_ns": mono,
            "last": initial,
        }
        _check(state)
        remaining = expires_at - utc_from_ns(initial["utc_ns"])
        utc_ns = (
            (remaining.days * 86400 + remaining.seconds) * 1_000_000
            + remaining.microseconds
        ) * 1000
        remaining_ns = min(utc_ns, mono - initial["monotonic_ns"])
        _need(
            remaining_ns > 0, "controlled55_coverage_original_source_expired_or_revoked"
        )
        deadline = math.nextafter(loop_before + remaining_ns / 1_000_000_000, -math.inf)
        return _observe(state, deadline)
    except ControlledCoverageSourceErrorV2:
        raise
    except Exception:  # noqa: BLE001 -- caller DATA or invalid source never gains a native entry.
        raise ControlledCoverageSourceErrorV2(
            "controlled55_coverage_native_parent_required"
        ) from None
