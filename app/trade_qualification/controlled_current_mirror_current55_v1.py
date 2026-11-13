"""Project one genuine original CURRENT source into the existing B5 mirror.

The private native registry supplies the original context. This write neither
normalizes missing financial values nor deletes or resolves legacy exposures,
reservations, intents, protection or errors. Returned values are inert DATA.
"""

import asyncio
import math
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import CoroutineType, FunctionType
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.native_role_witness_0056_v1 import RestrictedNativeRoles0056WitnessV1
from app.database.models.okx_demo import OkxDemoSyncCheckpoint
from app.database.models.operations import AuditLog
from app.database.repositories import (
    collector_current_0055_multirole_catalog_v1 as catalog55,
)
from app.database.repositories import demo_control_current55_v2 as controls55
from app.database.repositories import public_runtime_bundle_custody as custody
from app.database.repositories import qualification_ledger as ledger
from app.database.repositories import (
    qualification_native_checkpoint_v2 as checkpoint,
)
from app.demo_automation.native_server_selectors_v2 import (
    ConfiguredNativeDemoFactoriesV2,
)
from app.domain import native_clock
from app.domain.source_primitives import (
    canonical,
    decode,
    sha,
    utc_from_ns,
    validate_stamps,
)
from app.exchange.okx.private_rest import DemoPinnedReadSession
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_current_source_v4 as current
from app.trade_qualification import account_current_source_verifier as verifier
from app.trade_qualification import controlled_writer_coverage_current55_v2 as coverage
from app.trade_qualification import demo_control as control
from app.trade_qualification import native_account_risk_assembly_v1 as native
from app.trade_qualification.reservations import LedgerScope

_SOURCE_KEY = object()
_REPLAY_NAME = "_replay_controlled_current_mirror_context_v6"
_SHA = re.compile(r"[a-f0-9]{64}\Z", re.ASCII)
_ID = re.compile(r"[a-f0-9]{32}\Z", re.ASCII)
_MAX_RECEIPT = 65536
_MAX_PROJECTION = 4 * 1024 * 1024
_MAX_CHECKPOINT = 8 * 1024 * 1024
_MAX_OPERATIONS = 16
_ACTIVE = {}
_UNKNOWN = {}
_CAPTURE_LOCK_KEY = 71062020260001
_ACTION = "native_current_mirror_current55_v1"
ControlledCoverageSourceV6 = coverage.ControlledCoverageSourceV6
CoverageDatabaseObservationV2 = coverage.CoverageDatabaseObservationV2
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


class ControlledCurrentMirrorErrorV1(ValueError):
    """Closed code only; never expose SQL, source rows, UID or credentials."""


@dataclass(frozen=True, slots=True, repr=False)
class ControlledCurrentMirrorObservationV1:
    scope: LedgerScope
    control_state: control.ControlState
    control_event_sha256: str
    ledger_state: object = field(repr=False)
    local_checkpoint: ledger.PortfolioLocalCheckpoint = field(repr=False)
    source_generation: object = field(repr=False)
    receipt_json: bytes
    source_projection_json: bytes = field(repr=False)
    observed_at: datetime
    received_at: datetime
    database_observations: tuple[CoverageDatabaseObservationV2, ...]
    account_complete: bool = field(default=False, init=False)
    account_revision_issued: bool = field(default=False, init=False)
    event_eligibility: bool = field(default=False, init=False)
    execution_authority: bool = field(default=False, init=False)


def _need(condition, code="controlled55_mirror_source_unavailable"):
    if condition is not True:
        raise ControlledCurrentMirrorErrorV1(code) from None


def _digest(value):
    return type(value) is str and _SHA.fullmatch(value) is not None


def _utc(value):
    _need(
        type(value) is datetime and value.tzinfo is UTC,
        "controlled55_mirror_utc_required",
    )
    return value


def _stamp():
    value = native_clock.native_stamp()
    _need(
        type(value) is dict
        and set(value) == {"utc_ns", "monotonic_ns"}
        and all(type(item) is int and item > 0 for item in value.values()),
        "controlled55_mirror_native_clock_required",
    )
    return value


def _now():
    return utc_from_ns(_stamp()["utc_ns"])


def _context_signature(value):
    _need(type(value) is ControlledCoverageSourceV6)
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
    _mirror_database_profile(value)
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


def _mirror_control_deadline_v1(value, now):
    """Actual stopped operator observation or unchanged Armed source, no flag."""
    _need(
        type(value) is control.ControlState,
        "controlled55_mirror_actual_control_required",
    )
    value.__post_init__()
    _utc(now)
    if value.emergency_stop is True:
        _need(
            value.arm_request_id is None
            and value.arm_expires_at is None
            and value.owner_epoch > 0
            and value.owner_sha256 is not None
            and value.updated_at <= now < value.lease_until,
            "controlled55_mirror_stopped_operator_lease_required",
        )
        # Only the fixed parent's real stopped clear/init registry can produce
        # this context. Configured allow-writes may be true; actual Control
        # remains stopped and this inventory write grants no order permission.
        return _utc(value.lease_until)
    _need(
        value.arm_intent_current(now),
        "controlled55_mirror_original_source_expired_or_revoked",
    )
    return min(_utc(value.lease_until), _utc(value.arm_expires_at))


def _mirror_database_profile(value):
    factories, roles = value.db0056_factories, value.db0056_roles
    if factories is roles is None:
        return None
    _need(
        type(factories) is ConfiguredNativeDemoFactoriesV2
        and type(roles) is RestrictedNativeRoles0056WitnessV1
        and factories.writer_factory is value.session_factory
        and roles.observation.database_head == "0056"
        and roles.observation.admission == "DENY"
        and roles.execution_authority is False,
        "controlled56_mirror_original_roles_required",
    )
    return factories, roles


def _check(state):
    if type(state) is dict and _CONFIGURATION_OPERATIONS_V1.get(id(state)) is state:
        return _check_configuration_operation_v1(state)
    replay = state["replay"]
    _need(
        native.__dict__.get(_REPLAY_NAME) is replay
        and replay.__code__ is state["replay_code"]
        and replay.__globals__ is state["replay_globals"]
        and replay.__builtins__ is state["replay_builtins"]
        and replay.__defaults__ is state["replay_defaults"]
        and replay.__kwdefaults__ is state["replay_kwdefaults"]
        and replay.__closure__ is state["replay_closure"],
        "controlled55_mirror_original_replay_changed",
    )
    _need(
        _replay_cells(replay) == state["replay_cell_count"],
        "controlled55_mirror_original_replay_changed",
    )
    if replay.__closure__ is not None:
        for cell, original in zip(
            replay.__closure__, state["replay_cells"], strict=True
        ):
            try:
                actual = cell.cell_contents
            except ValueError:
                raise ControlledCurrentMirrorErrorV1(
                    "controlled55_mirror_original_replay_changed"
                ) from None
            _need(
                _same_closed_value(actual, original),
                "controlled55_mirror_original_replay_changed",
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
            "controlled55_mirror_original_replay_changed",
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
        "controlled55_mirror_original_source_changed",
    )
    value.read_session.verify_current()
    stamp = _stamp()
    validate_stamps((state["last"], stamp))
    now = utc_from_ns(stamp["utc_ns"])
    _mirror_control_deadline_v1(value.control_state, now)
    _need(
        now < state["expires_at"]
        and stamp["monotonic_ns"] < state["monotonic_deadline_ns"],
        "controlled55_mirror_original_source_expired_or_revoked",
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
        "controlled55_mirror_original_replay_changed",
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
        "controlled55_mirror_actual_transaction_invalid",
    )
    _need(
        utc_from_ns(before["utc_ns"]) <= row[4] <= utc_from_ns(after["utc_ns"])
        and row[4] < state["expires_at"],
        "controlled55_mirror_database_clock_invalid",
    )
    return row


def _page_memberships(packet, receipt):
    """Pure page DATA replay; only _projection admits actual original bytes."""
    _need(
        type(packet) is capture.DemoAccountPacket
        and type(packet.observations) is tuple
        and 0 < len(packet.observations) <= 256
        and all(
            type(page) is capture.DemoAccountObservation
            and type(page.rows) is tuple
            and type(page.request) is capture.AccountRequest
            and all(type(row) is capture.AccountRow for row in page.rows)
            for page in packet.observations
        )
        and sum(len(page.rows) for page in packet.observations) <= 8192,
        "controlled55_mirror_packet_membership_invalid",
    )
    pages = []
    counts = {stream: 0 for stream in verifier.V7_INVENTORY_STREAMS}
    stream_pages = {stream: [] for stream in verifier.V7_CURRENT_STREAMS}
    row_bytes = 0
    for request_index, page in enumerate(packet.observations):
        stream = page.request.stream
        _need(stream in stream_pages, "controlled55_mirror_unexpected_current_stream")
        stream_pages[stream].append(page)
        if stream in counts:
            counts[stream] += len(page.rows)
        rows = []
        for ordinal, row in enumerate(page.rows):
            encoded = row.canonical_json.encode("utf-8")
            row_bytes += len(encoded)
            _need(row_bytes <= _MAX_PROJECTION, "controlled55_mirror_projection_bound")
            rows.append(
                {
                    "ordinal": ordinal,
                    "row_id": row.row_id,
                    "row_sha256": sha(encoded),
                    "original_row_json": row.canonical_json,
                }
            )
        pages.append(
            {
                "request_index": request_index,
                "stream": stream,
                "page_index": page.page_index,
                "request_query": list(page.request.parameters),
                "after": page.after,
                "previous_page_sha256": page.previous_page_sha256,
                "body_sha256": page.body_sha256,
                "receipt_sha256": page.receipt_sha256,
                "request_started_at": page.request_started_at.isoformat(),
                "headers_received_at": page.headers_received_at.isoformat(),
                "body_completed_at": page.body_completed_at.isoformat(),
                "terminal": page.terminal,
                "rows": rows,
            }
        )
    _need(
        all(
            type(group) is list and len(group) > 0 and group[-1].terminal is True
            for group in stream_pages.values()
        )
        and type(receipt["inventory_row_counts"]) is dict
        and set(receipt["inventory_row_counts"]) == set(counts)
        and all(
            type(value) is int for value in receipt["inventory_row_counts"].values()
        )
        and receipt["inventory_row_counts"] == counts,
        "controlled55_mirror_complete_page_chain_required",
    )
    _need(
        all(type(value) is int and 0 <= value <= 8192 for value in counts.values())
        and sum(counts.values()) <= 8192,
        "controlled55_mirror_source_count_bound",
    )
    return pages, counts


def _projection(source):
    """Pure retained-source bytes; this DATA builder grants no eligibility."""
    record = current.replay_current_account_bundle_v4(
        source.current_bundle_raw, expected_sha256=source.current_bundle_sha256
    )
    expected = source.current_record
    _need(
        record.packet == expected.packet
        and record.reference == expected.reference
        and record.current_receipt_json == expected.current_receipt_json
        and record.proof_envelope_sha256 == expected.proof_envelope_sha256
        and record.native_proof_sha256 == expected.native_proof_sha256
        and record.native_readback_sha256 == expected.native_readback_sha256
        and record.expires_at == expected.expires_at
        and record.monotonic_deadline_ns == expected.monotonic_deadline_ns,
        "controlled55_mirror_original_packet_changed",
    )
    packet = record.packet
    _need(
        type(packet) is capture.DemoAccountPacket
        and type(packet.plan) is capture.CurrentDemoAccountCapturePlanV7
        and packet.plan.expected_uid == source.scope.account_id
        and packet.plan.session_binding_id == source.read_session.binding_id,
        "controlled55_mirror_original_identity_changed",
    )
    receipt = decode(record.current_receipt_json, _MAX_PROJECTION)
    _need(
        type(receipt) is dict
        and journal.canonical(receipt) == record.current_receipt_json
        and receipt["policy_sha256"] == verifier.V7_POLICY_SHA256
        and receipt["identity"]["environment"] == "demo"
        and receipt["identity"]["uid"] == source.scope.account_id
        and receipt["identity"]["settlement_currency"] == "USDT"
        and type(receipt["observed_flat"]) is bool
        and type(receipt["blocking_reasons"]) is list,
        "controlled55_mirror_current_receipt_invalid",
    )
    pages, counts = _page_memberships(packet, receipt)
    original = decode(source.current_bundle_raw, custody.MAX_BUNDLE_BYTES)
    chain = current._chain_replay(original["chain"])
    start = decode(chain[0].event.event_json, 262144)
    _need(
        start["kind"] == "capture_start"
        and _digest(start["data"]["local_checkpoint_sha256"]),
        "controlled55_mirror_original_revision_anchor_required",
    )
    payload = canonical(
        {
            "schema_version": "ctcc.controlled_current_mirror.source_projection.v1",
            "scope_sha256": sha(ledger.canonical(source.scope).encode("utf-8")),
            "original_bundle_sha256": source.current_bundle_sha256,
            "original_current_receipt_json": record.current_receipt_json.decode(
                "utf-8"
            ),
            "original_custody_id": source.current_custody_id,
            "original_proof_envelope_sha256": record.proof_envelope_sha256,
            "original_local_checkpoint_sha256": start["data"][
                "local_checkpoint_sha256"
            ],
            "original_expires_at": source.original_expires_at.isoformat(),
            "inventory_row_counts": counts,
            "pages": pages,
            "account_complete": False,
            "account_revision_issued": False,
            "execution_authority": False,
        }
    )
    _need(len(payload) <= _MAX_PROJECTION, "controlled55_mirror_projection_bound")
    return payload, counts, receipt, start["data"]["local_checkpoint_sha256"]


def _row_document(row):
    if row is None:
        return canonical(None)
    _need(
        type(row) is OkxDemoSyncCheckpoint
        and row.id == 1
        and type(row.status) is str
        and type(row.details) is dict
        and all(
            type(value) is int and 0 <= value <= 8192
            for value in (row.order_count, row.position_count, row.algo_order_count)
        )
        and (row.last_error is None or type(row.last_error) is str),
        "controlled55_mirror_checkpoint_row_invalid",
    )
    raw = canonical(
        {
            "id": row.id,
            "status": row.status,
            "order_count": row.order_count,
            "position_count": row.position_count,
            "algo_order_count": row.algo_order_count,
            "details": row.details,
            "last_error": row.last_error,
            "reconciled_at": None
            if row.reconciled_at is None
            else _utc(row.reconciled_at).isoformat(),
            "updated_at": _utc(row.updated_at).isoformat(),
        }
    )
    _need(len(raw) <= _MAX_CHECKPOINT, "controlled55_mirror_checkpoint_byte_bound")
    return raw


def _same_other_local(before, after):
    first, second = (decode(item.document_json, 262144) for item in (before, after))
    for value in (first, second):
        del value["legacy"]["sync_checkpoint"]
        value["blocking_reasons"] = [
            reason
            for reason in value["blocking_reasons"]
            if reason
            not in {
                "legacy_reconciliation_initialization_unknown",
                "legacy_reconciliation_uid_unbound",
                "legacy_reconciliation_credential_session_unbound",
                "legacy_reconciliation_incomplete_or_exposed",
            }
        ]
    _need(
        canonical(first) == canonical(second),
        "controlled55_mirror_other_local_state_changed",
    )


def _next_checkpoint_values(source, previous, projection, counts, current_receipt, at):
    """Only actual source inventory and binding; never clear an existing error."""
    _need(
        type(counts) is dict
        and set(counts) == set(verifier.V7_INVENTORY_STREAMS)
        and all(type(value) is int and 0 <= value <= 8192 for value in counts.values())
        and sum(counts.values()) <= 8192
        and type(current_receipt) is dict
        and type(current_receipt["observed_flat"]) is bool
        and type(current_receipt["blocking_reasons"]) is list
        and (
            current_receipt["observed_flat"] is False
            or (not any(counts.values()) and current_receipt["blocking_reasons"] == [])
        ),
        "controlled55_mirror_source_counts_invalid",
    )
    uid = sha(source.scope.account_id.encode("ascii"))
    details = {} if previous is None else dict(previous.details)
    old_uid = details.get("account_uid_sha256")
    _need(
        old_uid is None or (type(old_uid) is str and old_uid == uid),
        "controlled55_mirror_other_account_bound",
    )
    # Existing unbound exposure rows are independently retained by B5. No row
    # absence is rewritten into a terminal outcome or resolved local intent.
    details.update(
        {
            "account_uid_sha256": uid,
            "credential_session_binding_id": source.read_session.binding_id,
            "native_current_mirror_projection_json": projection.decode("utf-8"),
            "native_current_mirror_projection_sha256": sha(projection),
            "current_custody_id": source.current_custody_id,
            "current_bundle_sha256": source.current_bundle_sha256,
        }
    )
    error = None if previous is None else previous.last_error
    status = (
        "reconciled"
        if (
            current_receipt["observed_flat"] is True
            and current_receipt["blocking_reasons"] == []
        )
        else "source_incomplete_or_exposed"
    )
    if error not in (None, "") or (previous is not None and previous.status == "error"):
        status = "error"
    return {
        "status": status,
        "order_count": counts["orders_pending"],
        "position_count": counts["positions"],
        "algo_order_count": sum(
            value for stream, value in counts.items() if stream.startswith("algo_")
        ),
        "details": details,
        "last_error": error,
        "reconciled_at": at if status == "reconciled" else None,
        "updated_at": at,
    }


async def _setup_and_lock(state, session):
    source = state["source"]
    _check(state)
    _need(
        session.get_bind() is source.session_factory.kw["bind"].sync_engine
        and session.in_transaction(),
        "controlled55_mirror_actual_factory_required",
    )
    for statement in (
        "SET TRANSACTION ISOLATION LEVEL READ COMMITTED",
        "SET LOCAL search_path TO pg_catalog, public, pg_temp",
        "SET LOCAL TIME ZONE 'UTC'",
        "SET LOCAL synchronous_commit='on'",
    ):
        await _checked_await(state, session.execute(text(statement)))
    remaining_ms = min(1000, int((state["expires_at"] - _now()).total_seconds() * 1000))
    _need(remaining_ms > 0, "controlled55_mirror_original_source_expired_or_revoked")
    await _checked_await(
        state,
        session.execute(
            text(
                "SELECT pg_catalog.set_config('statement_timeout',:value,true),"
                "pg_catalog.set_config('lock_timeout',:value,true)"
            ),
            {"value": str(remaining_ms)},
        ),
    )
    if _mirror_database_profile(source) is None:
        await _checked_await(state, catalog55.verify_actual_issuer_catalog_v2(session))
    # Same legacy capture lock, before the UID lock: no reverse legacy
    # capture->UID dependency is introduced. Both waits remain bounded.
    await _checked_await(
        state, session.execute(select(func.pg_advisory_xact_lock(_CAPTURE_LOCK_KEY)))
    )
    await _checked_await(
        state,
        session.execute(
            select(
                func.pg_advisory_xact_lock(ledger._account_uid_lock_key(source.scope))
            )
        ),
    )
    return control.ControlScope("demo", source.scope.account_id)


async def _lock_scope_and_mirror(state, session, actual_control, event):
    source = state["source"]
    _need(
        canonical(control.document(actual_control))
        == canonical(control.document(source.control_state))
        and event == source.control_event_sha256,
        "controlled55_mirror_control_changed",
    )
    repository = ledger.QualificationLedgerRepository(
        source.session_factory, clock=_now
    )
    selected = await _checked_await(state, repository._locked(session, source.scope))
    await _checked_await(
        state,
        session.execute(
            text(
                "LOCK TABLE public.demo_automation_state, public.okx_demo_sync_checkpoints, "
                "public.okx_demo_order_state, public.okx_demo_position_state, "
                "public.okx_demo_algo_order_state IN SHARE ROW EXCLUSIVE MODE"
            )
        ),
    )
    size = await _checked_await(
        state,
        session.scalar(
            text(
                "SELECT pg_catalog.octet_length(details::text) "
                "FROM public.okx_demo_sync_checkpoints WHERE id=1"
            )
        ),
    )
    _need(
        size is None or (type(size) is int and 0 <= size <= _MAX_CHECKPOINT),
        "controlled55_mirror_checkpoint_byte_bound",
    )
    return repository, selected


@asynccontextmanager
async def _held_mirror_transaction(state, session):
    control_scope = await _setup_and_lock(state, session)
    profile = _mirror_database_profile(state["source"])
    if profile is None:
        actual, event = await _checked_await(
            state,
            controls55.DemoControlRepositoryV2.read_in_transaction(
                session, control_scope
            ),
        )
        repository, row = await _lock_scope_and_mirror(state, session, actual, event)
        yield repository, row, None
        return
    factories, roles = profile
    async with checkpoint.held_qualification_checkpoint_0056_v1(
        factories, session, control_scope, roles=roles
    ) as held:
        _check(state)
        observation = held.control.control
        repository, row = await _lock_scope_and_mirror(
            state, session, observation.state, observation.event_sha256
        )
        yield repository, row, held
        _check(state)


def _held_checkpoint_args(held):
    return {} if held is None else {"_held0056": held}


async def _mirror_catalog(state, session, held):
    check = (
        catalog55.verify_actual_issuer_catalog_v2(session)
        if held is None
        else held.recheck_catalog()
    )
    await _checked_await(state, check)


async def _read_final_control(state, session, held):
    scope = control.ControlScope("demo", state["source"].scope.account_id)
    if held is not None:
        return await _checked_await(state, held.recheck_control())
    return await _checked_await(
        state, controls55.DemoControlRepositoryV2.read_in_transaction(session, scope)
    )


async def _write(state, projection, counts, current_receipt, anchor):
    source = state["source"]
    _check(state)
    session = source.session_factory()
    _need(type(session) is AsyncSession, "controlled55_mirror_actual_factory_required")
    state["sessions"].append(session)
    transaction = session.begin()
    state["transactions"].append(transaction)
    async with (
        session,
        transaction,
        _held_mirror_transaction(state, session) as (repository, selected, held),
    ):
        checkpoint_args = _held_checkpoint_args(held)
        before = await _metadata(state, session)
        point = await _checked_await(
            state,
            repository._bootstrap_checkpoint(
                session, source.scope, selected, **checkpoint_args
            ),
        )
        _need(
            point.state_sha256 == anchor,
            "controlled55_mirror_original_revision_changed",
        )
        local_before = await _checked_await(
            state,
            repository._read_portfolio_checkpoint_locked(
                session, source.scope, selected, **checkpoint_args
            ),
        )
        previous = await _checked_await(state, session.get(OkxDemoSyncCheckpoint, 1))
        previous_json = _row_document(previous)
        state["local_before"] = local_before
        state["previous_checkpoint_json"] = previous_json
        values = _next_checkpoint_values(
            source, previous, projection, counts, current_receipt, before[4]
        )
        if previous is None:
            row = OkxDemoSyncCheckpoint(id=1, **values)
            session.add(row)
        else:
            row = previous
            for name, value in values.items():
                setattr(row, name, value)
        state["checkpoint_row"] = row
        await _checked_await(state, session.flush())
        await _checked_await(state, session.refresh(row))
        checkpoint_json = _row_document(row)
        state["checkpoint_json"] = checkpoint_json
        local_after = await _checked_await(
            state,
            repository._read_portfolio_checkpoint_locked(
                session, source.scope, selected, **checkpoint_args
            ),
        )
        state["local_after"] = local_after
        _same_other_local(local_before, local_after)
        after_point = await _checked_await(
            state,
            repository._bootstrap_checkpoint(
                session, source.scope, selected, **checkpoint_args
            ),
        )
        _need(
            after_point.state_sha256 == anchor
            and ledger.canonical(point.state) == ledger.canonical(after_point.state),
            "controlled55_mirror_revision_changed",
        )
        sampled = await _metadata(state, session)
        write_receipt = canonical(
            {
                "schema_version": "ctcc.controlled_current_mirror.write.v1",
                "source_projection_sha256": sha(projection),
                "previous_checkpoint_sha256": sha(previous_json),
                "checkpoint_sha256": sha(checkpoint_json),
                "original_bundle_sha256": source.current_bundle_sha256,
                "control_event_sha256": source.control_event_sha256,
                "ledger_state_sha256": anchor,
                "writer_inventory_sha256": source.writer_inventory_sha256,
                "database_before": _wire_metadata(before),
                "database_sampled": _wire_metadata(sampled),
                "original_expires_at": state["expires_at"].isoformat(),
                "account_complete": False,
                "account_revision_issued": False,
                "execution_authority": False,
            }
        )
        _need(len(write_receipt) <= _MAX_RECEIPT, "controlled55_mirror_receipt_bound")
        audit_after = {
            "receipt_json": write_receipt.decode("utf-8"),
            "source_projection_json": projection.decode("utf-8"),
            "checkpoint_row_json": checkpoint_json.decode("utf-8"),
        }
        audit = AuditLog(
            actor="ctcc_native_current55",
            action=_ACTION,
            resource_type="okx_demo_sync_checkpoint",
            resource_id=source.current_bundle_sha256,
            before={"checkpoint_row_json": previous_json.decode("utf-8")},
            after=audit_after,
            occurred_at=sampled[4],
        )
        state["write_receipt"] = write_receipt
        state["audit_row"] = audit
        session.add(audit)
        await _checked_await(state, session.flush())
        _need(type(audit.id) is UUID, "controlled55_mirror_audit_identity_unknown")
        # All writes are complete. Re-read their actual rows and unchanged
        # Control/revision/full B5 in this transaction before the single commit.
        await _checked_await(state, session.refresh(row))
        await _checked_await(state, session.refresh(audit))
        await _checked_await(state, session.refresh(selected))
        _need(
            _row_document(row) == checkpoint_json
            and audit.actor == "ctcc_native_current55"
            and audit.action == _ACTION
            and audit.resource_type == "okx_demo_sync_checkpoint"
            and audit.resource_id == source.current_bundle_sha256
            and audit.before == {"checkpoint_row_json": previous_json.decode("utf-8")}
            and audit.after == audit_after
            and audit.ip_address is None
            and audit.occurred_at == sampled[4],
            "controlled55_mirror_written_rows_changed",
        )
        final_control, final_event = await _read_final_control(state, session, held)
        _need(
            canonical(control.document(final_control))
            == canonical(control.document(source.control_state))
            and final_event == source.control_event_sha256,
            "controlled55_mirror_control_changed",
        )
        final_point = await _checked_await(
            state,
            repository._bootstrap_checkpoint(
                session, source.scope, selected, **checkpoint_args
            ),
        )
        final_local = await _checked_await(
            state,
            repository._read_portfolio_checkpoint_locked(
                session, source.scope, selected, **checkpoint_args
            ),
        )
        _need(
            final_point.state_sha256 == anchor
            and ledger.canonical(final_point.state) == ledger.canonical(point.state)
            and final_local.document_json == local_after.document_json,
            "controlled55_mirror_final_local_or_revision_changed",
        )
        state["final_local"] = final_local
        await _mirror_catalog(state, session, held)
        final = await _metadata(state, session)
        _need(
            before[:4] == final[:4] and before[4] <= sampled[4] <= final[4],
            "controlled55_mirror_actual_transaction_changed",
        )
        result = {
            "session": session,
            "audit_id": audit.id,
            "audit_after": audit_after,
            "audit_before": audit.before,
            "audit_at": audit.occurred_at,
            "checkpoint_json": checkpoint_json,
            "write_receipt": write_receipt,
            "ledger_json": ledger.canonical(point.state),
            "ledger_state": point.state,
            "local_before": local_before,
            "local_after": local_after,
            "database": CoverageDatabaseObservationV2(
                before, final, before[4], final[4]
            ),
        }
        state["written"] = result
        _check(state)
    _check(state)
    return result


async def _readback(state, written):
    source = state["source"]
    _check(state)
    session = source.session_factory()
    _need(type(session) is AsyncSession, "controlled55_mirror_actual_factory_required")
    state["sessions"].append(session)
    transaction = session.begin()
    state["transactions"].append(transaction)
    async with (
        session,
        transaction,
        _held_mirror_transaction(state, session) as (repository, selected, held),
    ):
        checkpoint_args = _held_checkpoint_args(held)
        before = await _metadata(state, session)
        _need(
            session is not written["session"]
            and before[3] != written["database"].before[3]
            and written["database"].received_at <= before[4],
            "controlled55_mirror_independent_readback_required",
        )
        await _checked_await(state, session.refresh(selected))
        point = await _checked_await(
            state,
            repository._bootstrap_checkpoint(
                session, source.scope, selected, **checkpoint_args
            ),
        )
        _need(
            ledger.canonical(point.state) == written["ledger_json"],
            "controlled55_mirror_readback_revision_changed",
        )
        rows = (
            await _checked_await(
                state, session.scalars(select(OkxDemoSyncCheckpoint).limit(2))
            )
        ).all()
        _need(
            len(rows) == 1 and _row_document(rows[0]) == written["checkpoint_json"],
            "controlled55_mirror_checkpoint_readback_changed",
        )
        audit = await _checked_await(state, session.get(AuditLog, written["audit_id"]))
        _need(
            type(audit) is AuditLog
            and audit.id == written["audit_id"]
            and audit.actor == "ctcc_native_current55"
            and audit.action == _ACTION
            and audit.resource_type == "okx_demo_sync_checkpoint"
            and audit.resource_id == source.current_bundle_sha256
            and audit.before == written["audit_before"]
            and audit.after == written["audit_after"]
            and audit.ip_address is None
            and audit.occurred_at == written["audit_at"],
            "controlled55_mirror_audit_readback_changed",
        )
        local = await _checked_await(
            state,
            repository._read_portfolio_checkpoint_locked(
                session, source.scope, selected, **checkpoint_args
            ),
        )
        _need(
            local.document_json == written["local_after"].document_json,
            "controlled55_mirror_full_local_readback_changed",
        )
        await _mirror_catalog(state, session, held)
        after = await _metadata(state, session)
        _need(
            before[:4] == after[:4] and before[4] <= after[4],
            "controlled55_mirror_actual_transaction_changed",
        )
        result = (
            point.state,
            local,
            CoverageDatabaseObservationV2(before, after, before[4], after[4]),
        )
        state["readback"] = result
        state["readback_audit_row"] = audit
        _check(state)
    _check(state)
    return result


def _wire_metadata(value):
    return [*value[:4], value[4].isoformat(), *value[5:]]


def _retain_unknown(state):
    # Unknown commit/rollback/cancellation never becomes absence. Keep the real
    # source, exact sessions/transactions and coroutine state strongly. No
    # inferred async close, retry, original-expiry reset or exposure deletion.
    state["phase"] = "UNKNOWN"
    _UNKNOWN[state["operation_token"]] = state
    _ACTIVE.pop(state["operation_token"], None)


async def _synchronize(state, absolute_deadline):
    try:
        async with asyncio.timeout_at(absolute_deadline):
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
                "controlled55_mirror_original_custody_changed",
            )
            projection, counts, current_receipt, anchor = _projection(source)
            state["source_projection_json"] = projection
            state["source_receipt_json"] = source.current_record.current_receipt_json
            _check(state)
            written = await _write(state, projection, counts, current_receipt, anchor)
            ledger_state, local, readback = await _readback(state, written)
            _check(state)
            receipt = canonical(
                {
                    "schema_version": "ctcc.controlled_current_mirror.readback.v1",
                    "source_projection_sha256": sha(projection),
                    "write_receipt_sha256": sha(written["write_receipt"]),
                    "checkpoint_row_sha256": sha(written["checkpoint_json"]),
                    "audit_row_id": written["audit_id"].hex,
                    "original_bundle_sha256": source.current_bundle_sha256,
                    "current_custody_id": source.current_custody_id,
                    "control_event_sha256": source.control_event_sha256,
                    "full_local_checkpoint_sha256": local.state_sha256,
                    "database_write_before": _wire_metadata(written["database"].before),
                    "database_write_after": _wire_metadata(written["database"].after),
                    "database_read_before": _wire_metadata(readback.before),
                    "database_read_after": _wire_metadata(readback.after),
                    "observed_at": written["database"].observed_at.isoformat(),
                    "received_at": readback.received_at.isoformat(),
                    "account_complete": False,
                    "account_revision_issued": False,
                    "event_eligibility": False,
                    "execution_authority": False,
                }
            )
            _need(len(receipt) <= _MAX_RECEIPT, "controlled55_mirror_receipt_bound")
            result = ControlledCurrentMirrorObservationV1(
                source.scope,
                source.control_state,
                source.control_event_sha256,
                ledger_state,
                local,
                source.source_generation,
                receipt,
                projection,
                written["database"].observed_at,
                readback.received_at,
                (written["database"], readback),
            )
            _check(state)
            state["receipt_json"] = receipt
            state["source_projection_json"] = projection
            state["result"] = result
            _need(
                _ACTIVE.get(state["operation_token"]) is state,
                "controlled55_mirror_operation_registry_changed",
            )
            _ACTIVE.pop(state["operation_token"])
            state["phase"] = "CONFIRMED"
            state.pop("coroutine", None)
            return result
    except asyncio.CancelledError:
        raise asyncio.CancelledError() from None
    except ControlledCurrentMirrorErrorV1:
        raise
    except Exception:  # noqa: BLE001 -- SQL/source/credential error details remain private.
        raise ControlledCurrentMirrorErrorV1(
            "controlled55_mirror_sync_failed"
        ) from None
    finally:
        if state["phase"] != "CONFIRMED":
            _retain_unknown(state)


def synchronize_owned_current_mirror_v1(binding):
    """Fixed native purpose; no caller factory, clock, Control DTO, or pass flag.

    The genuine parent SourceKernel validates this original module/wrapper and
    its complete dependencies and original operation registries before invoking it. Capturing the replay below is
    a continuation check, not admission of an arbitrary preexisting callback.
    The parent owns failures/unknown retention; this observer never releases it.
    """
    try:
        _need(
            len(_ACTIVE) + len(_UNKNOWN) < _MAX_OPERATIONS,
            "controlled55_mirror_operation_capacity",
        )
        loop_before = asyncio.get_running_loop().time()
        initial = _stamp()
        replay = native.__dict__.get(_REPLAY_NAME)
        _need(
            type(replay) is FunctionType, "controlled55_mirror_native_parent_required"
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
        control_deadline = _mirror_control_deadline_v1(
            source.control_state, utc_from_ns(initial["utc_ns"])
        )
        expires_at = min(
            _utc(source.original_expires_at),
            _utc(source.current_record.expires_at),
            control_deadline,
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
            remaining_ns > 0, "controlled55_mirror_original_source_expired_or_revoked"
        )
        deadline = math.nextafter(loop_before + remaining_ns / 1_000_000_000, -math.inf)
        state.update(
            operation_token=object(), phase="ACTIVE", sessions=[], transactions=[]
        )
        operation = _synchronize(state, deadline)
        state["coroutine"] = operation
        _ACTIVE[state["operation_token"]] = state
        return operation
    except ControlledCurrentMirrorErrorV1:
        raise
    except Exception:  # noqa: BLE001 -- a caller value never supplies a native source context.
        raise ControlledCurrentMirrorErrorV1(
            "controlled55_mirror_native_parent_required"
        ) from None


def _buffers_from_operation_v1(state):
    """Actual mirror-owned payload buffers only; no descriptors or callbacks."""
    _need(type(state) is dict, "controlled55_mirror_retained_state_invalid")
    payloads = [
        state.get(name)
        for name in (
            "source_projection_json",
            "source_receipt_json",
            "previous_checkpoint_json",
            "checkpoint_json",
            "write_receipt",
            "receipt_json",
        )
    ]
    for name in ("local_before", "local_after", "final_local"):
        point = state.get(name)
        if point is not None:
            _need(
                type(point) is ledger.PortfolioLocalCheckpoint,
                "controlled55_mirror_retained_state_invalid",
            )
            payloads.append(point.document_json)
    written = state.get("written")
    if written is not None:
        _need(type(written) is dict, "controlled55_mirror_retained_state_invalid")
        payloads.extend(
            written.get(name)
            for name in (
                "audit_after",
                "audit_before",
                "checkpoint_json",
                "write_receipt",
                "ledger_json",
            )
        )
    readback = state.get("readback")
    if readback is not None:
        _need(
            type(readback) is tuple
            and len(readback) == 3
            and type(readback[1]) is ledger.PortfolioLocalCheckpoint,
            "controlled55_mirror_retained_state_invalid",
        )
        payloads.append(readback[1].document_json)
    for name, expected in (
        ("checkpoint_row", OkxDemoSyncCheckpoint),
        ("audit_row", AuditLog),
        ("readback_audit_row", AuditLog),
    ):
        row = state.get(name)
        if row is not None:
            _need(type(row) is expected, "controlled55_mirror_retained_state_invalid")
            # Read already-loaded state directly, never an ORM attribute that
            # could lazily load after commit or touch a database connection.
            loaded = object.__getattribute__(row, "__dict__")
            _need(type(loaded) is dict, "controlled55_mirror_retained_state_invalid")
            fields = (
                ("details", "last_error", "status")
                if expected is OkxDemoSyncCheckpoint
                else ("before", "after")
            )
            payloads.extend(loaded.get(field) for field in fields)
    buffers, seen, stack = [], set(), [(item, 0) for item in payloads]
    nodes, total = 0, 0
    while stack:
        value, depth = stack.pop()
        nodes += 1
        _need(
            nodes <= 65536 and depth <= 32,
            "controlled55_mirror_retained_traversal_bound",
        )
        if value is None or type(value) in (bool, int):
            continue
        identity = id(value)
        if identity in seen:
            continue
        seen.add(identity)
        if type(value) in (bytes, str):
            _need(
                len(value) <= _MAX_CHECKPOINT, "controlled55_mirror_retained_byte_bound"
            )
            size = len(value) if type(value) is bytes else len(value.encode("utf-8"))
            total += size
            _need(
                total <= 16 * 1024 * 1024 and len(buffers) < 32768,
                "controlled55_mirror_retained_byte_bound",
            )
            buffers.append(value)
        elif type(value) in (dict, list, tuple):
            _need(
                len(value) <= 32768 and len(stack) + len(value) <= 32768,
                "controlled55_mirror_retained_traversal_bound",
            )
            if type(value) is dict:
                _need(
                    all(type(name) is str for name in value),
                    "controlled55_mirror_retained_state_invalid",
                )
                stack.extend(
                    (item, depth + 1) for pair in value.items() for item in pair
                )
            else:
                stack.extend((item, depth + 1) for item in value)
        elif type(value) is float:
            _need(math.isfinite(value), "controlled55_mirror_retained_state_invalid")
        else:
            _need(False, "controlled55_mirror_retained_state_invalid")
    return tuple(buffers)


def _retained_mirror_buffers_v1(binding, *, key):
    """Fixed parent's pure accounting read; no replay, DB, clock or permission.

    The genuine parent SourceKernel authenticates this original function and
    both original registries before calling. Its existing whole-source budget
    adds these exact buffers before every physical write/commit boundary. The
    local sixteen-MiB bound below does not replace that combined source budget.
    """
    # Read only the already-loaded fixed native module. No import fallback,
    # module loader, arbitrary callback or dynamic source selector is invoked.
    system = vars(native).get("sys")
    _need(type(system) is type(native), "controlled55_mirror_fixed_parent_required")
    modules = vars(system).get("modules")
    _need(type(modules) is dict, "controlled55_mirror_fixed_parent_required")
    controlled = modules.get(
        "app.trade_qualification.native_controlled_account_source_v6"
    )
    _need(type(controlled) is type(native), "controlled55_mirror_fixed_parent_required")
    original_key = vars(controlled).get("_SOURCE_KEY_V6")
    _need(
        type(original_key) is object and key is original_key,
        "controlled55_mirror_fixed_parent_required",
    )
    _need(
        type(_ACTIVE) is dict
        and type(_UNKNOWN) is dict
        and len(_ACTIVE) + len(_UNKNOWN) <= _MAX_OPERATIONS,
        "controlled55_mirror_operation_registry_changed",
    )
    result, seen = [], set()
    for state in (*_ACTIVE.values(), *_UNKNOWN.values()):
        _need(type(state) is dict, "controlled55_mirror_operation_registry_changed")
        if state.get("binding") is binding:
            for value in _buffers_from_operation_v1(state):
                if id(value) not in seen:
                    seen.add(id(value))
                    result.append(value)
    return tuple(result)


# Separate fixed configuration purpose; no caller mode/Control DTO branch.
_CONFIGURATION_SOURCE_KEY_V1 = object()
_CONFIGURATION_OPERATIONS_V1 = {}


def _configuration_control_deadline_v1(value, now):
    _need(
        type(value) is control.ControlState
        and value.emergency_stop is False
        and value.arm_request_id is None
        and value.arm_expires_at is None
        and value.owner_sha256 is not None
        and value.owner_epoch > 0
    )
    deadline = _utc(value.lease_until)
    _need(now < deadline, "controlled55_configuration_original_lease_expired")
    return deadline


async def _synchronize_configuration_v1(state, deadline):
    try:
        return await _synchronize(state, deadline)
    finally:
        if (
            state["phase"] == "CONFIRMED"
            and _CONFIGURATION_OPERATIONS_V1.get(id(state)) is state
        ):
            _CONFIGURATION_OPERATIONS_V1.pop(id(state))


def _check_configuration_operation_v1(state):
    from app.trade_qualification import (
        native_flat_leverage_account_source_v1 as fixed_source,
    )

    replay = state["replay"]
    _need(
        fixed_source.__dict__.get("_replay_flat_leverage_mirror_context_v1") is replay
        and replay.__code__ is state["replay_code"]
        and replay.__globals__ is state["replay_globals"]
        and replay.__builtins__ is state["replay_builtins"]
        and replay.__defaults__ is state["replay_defaults"]
        and replay.__kwdefaults__ is state["replay_kwdefaults"]
        and replay.__closure__ is state["replay_closure"],
        "controlled55_mirror_original_replay_changed",
    )
    _need(
        _replay_cells(replay) == state["replay_cell_count"],
        "controlled55_mirror_original_replay_changed",
    )
    if replay.__closure__ is not None:
        for cell, original in zip(
            replay.__closure__, state["replay_cells"], strict=True
        ):
            try:
                actual = cell.cell_contents
            except ValueError:
                raise ControlledCurrentMirrorErrorV1(
                    "controlled55_mirror_original_replay_changed"
                ) from None
            _need(
                _same_closed_value(actual, original),
                "controlled55_mirror_original_replay_changed",
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
            "controlled55_mirror_original_replay_changed",
        )
    value = replay(state["binding"], key=_CONFIGURATION_SOURCE_KEY_V1)
    signature = _context_signature(value)
    original = state["source"]
    _need(
        value.session_factory is original.session_factory
        and value.scope == original.scope
        and value.current_custody_repository is original.current_custody_repository
        and value.source_generation is original.source_generation
        and value.read_session is original.read_session
        and signature == state["signature"],
        "controlled55_mirror_original_source_changed",
    )
    value.read_session.verify_current()
    stamp = _stamp()
    validate_stamps((state["last"], stamp))
    now = utc_from_ns(stamp["utc_ns"])
    _configuration_control_deadline_v1(value.control_state, now)
    _need(
        now < state["expires_at"]
        and stamp["monotonic_ns"] < state["monotonic_deadline_ns"],
        "controlled55_mirror_original_source_expired_or_revoked",
    )
    state["last"] = stamp
    return stamp


def synchronize_owned_configuration_current_mirror_v1(binding):
    from app.trade_qualification import (
        native_flat_leverage_account_source_v1 as fixed_source,
    )

    """Fixed native purpose; no caller factory, clock, Control DTO, or pass flag.

    The genuine parent SourceKernel validates this original module/wrapper and
    its complete dependencies and original operation registries before invoking it. Capturing the replay below is
    a continuation check, not admission of an arbitrary preexisting callback.
    The parent owns failures/unknown retention; this observer never releases it.
    """
    try:
        _need(
            len(_ACTIVE) + len(_UNKNOWN) < _MAX_OPERATIONS,
            "controlled55_mirror_operation_capacity",
        )
        loop_before = asyncio.get_running_loop().time()
        initial = _stamp()
        replay = fixed_source.__dict__.get("_replay_flat_leverage_mirror_context_v1")
        _need(
            type(replay) is FunctionType, "controlled55_mirror_native_parent_required"
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
        source = replay(binding, key=_CONFIGURATION_SOURCE_KEY_V1)
        signature = _context_signature(source)
        control_deadline = _configuration_control_deadline_v1(
            source.control_state, utc_from_ns(initial["utc_ns"])
        )
        expires_at = min(
            _utc(source.original_expires_at),
            _utc(source.current_record.expires_at),
            control_deadline,
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
        _CONFIGURATION_OPERATIONS_V1[id(state)] = state
        _check(state)
        remaining = expires_at - utc_from_ns(initial["utc_ns"])
        utc_ns = (
            (remaining.days * 86400 + remaining.seconds) * 1_000_000
            + remaining.microseconds
        ) * 1000
        remaining_ns = min(utc_ns, mono - initial["monotonic_ns"])
        _need(
            remaining_ns > 0, "controlled55_mirror_original_source_expired_or_revoked"
        )
        deadline = math.nextafter(loop_before + remaining_ns / 1_000_000_000, -math.inf)
        state.update(
            operation_token=object(), phase="ACTIVE", sessions=[], transactions=[]
        )
        operation = _synchronize_configuration_v1(state, deadline)
        state["coroutine"] = operation
        _ACTIVE[state["operation_token"]] = state
        return operation
    except ControlledCurrentMirrorErrorV1:
        raise
    except Exception:  # noqa: BLE001 -- a caller value never supplies a native source context.
        raise ControlledCurrentMirrorErrorV1(
            "controlled55_mirror_native_parent_required"
        ) from None
