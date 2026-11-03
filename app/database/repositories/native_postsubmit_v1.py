"""Native DATA adapter for existing DB0017/18, with actual55 transaction replay.

No new table/ledger or public expected-revision/claims/complete flag enters.
The caller's opaque binding is authenticated by the native reconciliation
registry. Legacy reconciliation guards remain unchanged; only the explicit
actual55 closure body can resolve a genuinely observed complete zero-fill FOK.
"""

from __future__ import annotations

import itertools
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.models.account_issued_revision import (
    DemoCollectorIssuedAccountRevision as Issued,
)
from app.database.models.account_revision_collector_lineage import (
    DemoCollectorCurrentRevisionLineage as Lineage,
)
from app.database.models.qualification_ledger import (
    QualificationReservation as Reservation,
)
from app.database.models.qualification_ledger import (
    QualificationReservationTransition as Transition,
)
from app.database.models.submission_reporting import (
    QualificationSubmissionOutcome as Outcome,
)
from app.database.repositories import account_issued_revision_current55_v1 as issued
from app.database.repositories import qualification_ledger as ledger
from app.database.repositories.account_capture_journal import (
    AccountCaptureJournalRepository,
)
from app.database.repositories.collector_current_0055_multirole_catalog_v1 import (
    verify_actual_issuer_catalog_v2,
)
from app.database.repositories.demo_control_current55_v2 import DemoControlRepositoryV2
from app.domain.native_clock import native_stamp
from app.domain.source_primitives import canonical, decode, sha, utc_from_ns
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification import control_bound_ledger as control_bound
from app.trade_qualification import demo_control as control
from app.trade_qualification import native_ledger_contracts_v2 as wire
from app.trade_qualification import native_reconciliation_facts_v1 as computation
from app.trade_qualification import reservations

_SOURCE_KEY_V1 = object()
_RECOVERY_CLOSE_HISTORIES_V1 = {}
_RECOVERY_HISTORY_BUFFERS_V1 = {}
_MAX_OUTCOMES = 4096
_CHUNK = 65536
_META = text(
    "SELECT current_user,session_user,current_setting('role'),r.oid::bigint,"
    "r.rolsuper,r.rolcreaterole,r.rolbypassrls,pg_backend_pid(),"
    "pg_current_xact_id()::text,clock_timestamp() FROM pg_roles r WHERE r.rolname=current_user"
)


class NativePostsubmitRepositoryError(ValueError):
    pass


def need(condition, code="native_postsubmit_durable_binding_invalid"):
    if condition is not True:
        raise NativePostsubmitRepositoryError(code) from None


def _now():
    return utc_from_ns(native_stamp()["utc_ns"])


def _source(binding):
    from app.trade_qualification import native_postsubmit_reconciliation_v1 as native

    state, source = native._replay_persistence_source_v1(binding, key=_SOURCE_KEY_V1)
    need(
        type(source) is native.NativeReconciliationSourceContextV1
        and type(source.session_factory) is async_sessionmaker
    )
    return state, source


def _row(model, *, exclude=()):
    result = {}
    for column in type(model).__table__.columns:
        name, value = column.name, getattr(model, column.name)
        if name in exclude:
            continue
        if type(value) is datetime:
            need(value.tzinfo is not None)
            value = value.astimezone(UTC).isoformat()
        elif type(value) is Decimal:
            value = str(value)
        else:
            need(value is None or type(value) in (str, int, bool))
        result[name] = value
    return result


@dataclass(frozen=True, slots=True, repr=False)
class NativeTargetReadbackV1:
    scope: reservations.LedgerScope = field(repr=False)
    session_factory: object = field(repr=False)
    reservation_id: str = field(repr=False)
    request: wire.NativeReservationRequestV4 = field(repr=False)
    request_json: str = field(repr=False)
    intent: object = field(repr=False)
    consumed: reservations.ReservationReceipt = field(repr=False)
    reserved: reservations.ReservationReceipt = field(repr=False)
    core_json: bytes = field(repr=False)
    issued_row_json: bytes = field(repr=False)
    lineage_row_json: bytes = field(repr=False)
    current_capture_id: str = field(repr=False)
    current_head_sha256: str = field(repr=False)
    read_metadata: tuple = field(repr=False)
    current_state: str = field(repr=False)
    current_state_revision: int = field(repr=False)
    facts: computation.NativeReconciliationTargetV1 | None = field(
        default=None, repr=False
    )


@dataclass(frozen=True, slots=True, repr=False)
class NativeRecoveryCloseHistoryV1:
    """Historical DATA. Only the actual two-session repository registry admits it."""

    close_outcome_id: str
    close_row_json: bytes
    close_intent_json: bytes
    original_outcome_id: str
    original_manifest_outcome_id: str
    original_assessment: computation.NativeReconciliationAssessmentV1
    original_observations: tuple
    preflight_manifest_outcome_id: str
    preflight_assessment: object
    preflight_observations: tuple
    retained_rows: tuple
    initial_outcome_id: str
    read_metadata: tuple
    execution_authority: bool = field(default=False, init=False)
    order_retry_authority: bool = field(default=False, init=False)


@asynccontextmanager
async def _transaction(binding):
    state, source = _source(binding)
    repository = ledger.QualificationLedgerRepository(
        source.session_factory, clock=_now
    )
    async with source.session_factory() as session, session.begin():
        need(
            type(session) is AsyncSession
            and session.get_bind().dialect.name == "postgresql"
            and session.get_bind() is source.session_factory.kw["bind"].sync_engine
        )
        for sql in (
            "SET LOCAL search_path=pg_catalog,public,pg_temp",
            "SET LOCAL TIME ZONE 'UTC'",
            "SET LOCAL synchronous_commit=on",
            "SET LOCAL statement_timeout='1500ms'",
            "SET LOCAL lock_timeout='1500ms'",
        ):
            await session.execute(text(sql))
            _source(binding)
        need(
            await session.scalar(
                text("SELECT current_setting('transaction_isolation')")
            )
            == "read committed"
        )
        _source(binding)
        await verify_actual_issuer_catalog_v2(session)
        _source(binding)
        # Same UID -> current55 Control -> scope lock order as native reserve.
        await ledger._require_event_journal_write_schema(session, source.scope)
        _source(binding)
        control_state, event = await DemoControlRepositoryV2.read_in_transaction(
            session,
            control.ControlScope(source.scope.environment, source.scope.account_id),
        )
        _source(binding)
        scope_row = await repository._locked(session, source.scope)
        _source(binding)
        metadata = tuple((await session.execute(_META)).one())
        _source(binding)
        need(
            len(metadata) == 10
            and metadata[0] == metadata[1]
            and metadata[2] == "none"
            and type(metadata[3]) is int
            and metadata[3] > 0
            and all(item is False for item in metadata[4:7])
            and type(metadata[7]) is int
            and type(metadata[8]) is str
            and metadata[8].isascii()
            and metadata[8].isdigit()
            and type(metadata[9]) is datetime
        )
        yield (
            state,
            source,
            repository,
            session,
            scope_row,
            control_state,
            event,
            metadata,
        )
        _source(binding)


async def _target_locked(binding, source, repository, session, scope_row, metadata):
    state, _ = _source(binding)
    record = await session.get(
        Reservation, state["reservation_id"], populate_existing=True
    )
    _source(binding)
    need(
        type(record) is Reservation
        and (record.environment, record.account_id, record.settlement_currency)
        == (
            source.scope.environment,
            source.scope.account_id,
            source.scope.settlement_currency,
        )
        and record.state in {"consumed", "uncertain", "reconciled_flat"}
    )
    request = reservations.decode_reservation_request(record.request_json)
    need(
        wire.is_native_request(request)
        and wire.canonical_request(request) == record.request_json
        and sha(record.request_json.encode()) == record.request_sha256
        and request.scope == source.scope
        and reservations.reservation_id(source.scope, request.origin.original_event_key)
        == record.reservation_id
    )
    transitions = tuple(
        (
            await session.scalars(
                select(Transition)
                .filter_by(reservation_id=record.reservation_id)
                .order_by(Transition.state_revision)
                .limit(9)
            )
        ).all()
    )
    _source(binding)
    need(
        2 <= len(transitions) <= 4
        and len(transitions) == record.state_revision
        and all(
            item.state_revision == index for index, item in enumerate(transitions, 1)
        )
        and transitions[0].from_state is None
        and transitions[0].to_state == "reserved"
        and transitions[0].reason_code == control_bound.RESERVED_REASON
        and transitions[0].occurred_at == record.created_at
        and transitions[1].from_state == "reserved"
        and transitions[1].to_state == "consumed"
        and transitions[1].reason_code == control_bound.CONSUMED_REASON
        and transitions[-1].to_state == record.state
        and transitions[-1].occurred_at == record.updated_at
        and all(
            left.to_state == right.from_state and left.occurred_at <= right.occurred_at
            for left, right in itertools.pairwise(transitions)
        )
    )
    if len(transitions) >= 3:
        need(
            transitions[2].from_state == "consumed"
            and transitions[2].to_state == "uncertain"
            and transitions[2].reason_code == "post_submit_native_uncertain_v1"
        )
    if len(transitions) == 4:
        need(
            transitions[3].from_state == "uncertain"
            and transitions[3].to_state == "reconciled_flat"
            and transitions[3].reason_code
            in {
                "post_submit_native_zero_fok_flat_v1",
                "post_submit_native_filled_flat_v1",
            }
        )
        if transitions[3].reason_code == "post_submit_native_filled_flat_v1":
            await _require_filled_flat_schema_v55(binding, session)
            evidence = decode(transitions[3].evidence_json.encode(), 140000)
            need(
                evidence["version"] == "ctcc.native_owned_filled_flat_closure.v1"
                and type(evidence.get("outcome_id")) is str
                and type(evidence.get("terminal_data_outcome_id")) is str
            )
    reserved, reserved_control, reserved_event = control_bound.replay_reserved_evidence(
        transitions[0].evidence_json, request
    )
    intent, consumed = control_bound.replay_control_bound_intent(
        transitions[1].evidence_json,
        request,
        expected_sha256=sha(transitions[1].evidence_json.encode()),
    )
    outer = decode(intent.canonical_json.encode(), 16 * 1024 * 1024)
    consume_control = control.decode(outer["consume_control_state_json"])
    await DemoControlRepositoryV2.verify_historical_binding(
        session, reserved_control, reserved_event
    )
    _source(binding)
    await DemoControlRepositoryV2.verify_historical_binding(
        session, consume_control, outer["consume_control_event_sha256"]
    )
    _source(binding)
    need(
        reserved.account_revision
        == consumed.account_revision
        == request.expected_account_revision
        and reserved.ledger_revision == request.expected_ledger_revision + 1
        and consumed.ledger_revision == request.expected_ledger_revision + 2
        and consumed.updated_at == transitions[1].occurred_at
        and consumed.reservation_id == record.reservation_id
        and consumed.scope == source.scope
        and consumed.original_event_key == record.original_event_key
        and reservations.canonical(consumed.coverage) == record.coverage_json
        and all(
            getattr(record, key) == getattr(consumed, key)
            for key in (
                "report_id",
                "instrument_id",
                "direction",
                "correlation_group",
                "request_sha256",
                "deadline",
                "created_at",
            )
        )
        and (record.risk_amount, record.margin_amount, record.notional_amount)
        == (
            consumed.coverage.risk_amount,
            consumed.coverage.margin_amount,
            consumed.coverage.notional_amount,
        )
    )
    issued_row = await session.get(
        Issued, request.issuer_sha256, populate_existing=True
    )
    _source(binding)
    need(type(issued_row) is Issued)
    issued_raw = issued._snapshot(issued_row, Issued, issued._ISSUED_COLUMNS)
    need(
        sha(issued_raw) == request.issued_row_sha256
        and sha(issued_row.proof_json.encode()) == request.issuer_sha256
        and sha(issued_row.claims_json.encode()) == issued_row.claims_sha256
        and (
            issued_row.environment,
            issued_row.account_id,
            issued_row.settlement_currency,
        )
        == (
            source.scope.environment,
            source.scope.account_id,
            source.scope.settlement_currency,
        )
        and issued_row.account_revision == request.expected_account_revision
        and issued_row.ledger_revision == request.expected_ledger_revision
        and issued_row.control_revision == reserved_control.revision
        and issued_row.control_event_sha256 == reserved_event
        and request.origin.deadline <= issued_row.original_expires_at
    )
    lineage = await session.get(
        Lineage, issued_row.source_lineage_sha256, populate_existing=True
    )
    _source(binding)
    need(
        type(lineage) is Lineage
        and sha(lineage.lineage_json.encode()) == lineage.lineage_sha256
        and (lineage.environment, lineage.account_id, lineage.settlement_currency)
        == (
            source.scope.environment,
            source.scope.account_id,
            source.scope.settlement_currency,
        )
    )
    lineage_raw = issued._lineage_snapshot_v1(lineage)
    core = canonical(
        {
            "reservation": _row(
                record, exclude=("state", "state_revision", "updated_at")
            ),
            "reserved_transition": _row(transitions[0]),
            "consumed_transition": _row(transitions[1]),
            "issued_row": decode(issued_raw, 32 * 1024 * 1024),
            "lineage_row": decode(lineage_raw, 32 * 1024 * 1024),
        }
    )
    need(len(core) <= 64 * 1024 * 1024)
    need(
        scope_row.account_revision >= request.expected_account_revision
        and scope_row.ledger_revision >= consumed.ledger_revision
    )
    return NativeTargetReadbackV1(
        source.scope,
        source.session_factory,
        record.reservation_id,
        request,
        record.request_json,
        intent,
        consumed,
        reserved,
        core,
        issued_raw,
        lineage_raw,
        lineage.current_capture_id,
        lineage.current_head_sha256,
        metadata,
        record.state,
        record.state_revision,
    )


async def read_native_target_v1(binding):
    from app.trade_qualification import native_postsubmit_reconciliation_v1 as native

    async with _transaction(binding) as parts:
        _state, source, repository, session, scope_row, _, _, metadata = parts
        first = await _target_locked(
            binding, source, repository, session, scope_row, metadata
        )
        native._note_durable_target_v1(binding, first, key=_SOURCE_KEY_V1)
        # This is the SAME genuine V5 capture writer transaction. Its real
        # SELECT privilege is measured, never projected from a raw-reader role.
        permitted = await session.scalar(
            text(
                "SELECT pg_catalog.has_table_privilege(current_user,"
                "'public.demo_account_capture_events','SELECT')"
            )
        )
        _source(binding)
        need(permitted is True, "native_postsubmit_original_journal_reader_unavailable")
        reader = AccountCaptureJournalRepository(source.session_factory, clock=_now)
        chain = await reader._read_chain_locked(
            session, source.scope, first.current_capture_id
        )
        _source(binding)
    need(
        type(chain) is tuple
        and chain
        and all(type(item) is journal.JournalReadback for item in chain)
        and journal.digest(chain[-1].event.event_json) == first.current_head_sha256
    )
    reference = observed.source_reference(chain)
    lineage = decode(first.lineage_row_json, 32 * 1024 * 1024)
    lineage_document = decode(lineage["lineage_json"].encode())
    need(observed.reference_document(reference) == lineage_document["source_reference"])
    header = journal.checked_event(chain[0].event)
    need(header["kind"] == "capture_start")
    plan = header["data"]["plan"]
    need(
        plan["expected_uid"] == source.scope.account_id
        and plan["environment"] == "demo"
        and plan["settlement_currency"] == source.scope.settlement_currency
        and plan["origin"] == source.origin
        and type(plan["expected_main_uid"]) is str
    )
    # The old session identity is an immutable historical fact, not the NEW auth.
    need(
        sha(plan["session_binding_id"].encode("ascii"))
        == first.request.credential_session_sha256
    )
    factual = computation.NativeReconciliationTargetV1(
        first.request,
        first.request_json,
        first.intent.canonical_json,
        first.intent.sha256,
        first.reservation_id,
        plan["expected_main_uid"],
        plan["origin"],
        first.request.credential_session_sha256,
        sha(
            canonical(
                {
                    "core_sha256": sha(first.core_json),
                    "original_capture_head": first.current_head_sha256,
                    "original_plan_sha256": header["data"]["plan_sha256"],
                }
            )
        ),
    )
    async with _transaction(binding) as parts:
        _, source, repository, session, scope_row, _, _, metadata = parts
        second = await _target_locked(
            binding, source, repository, session, scope_row, metadata
        )
        need(
            second.core_json == first.core_json
            and second.read_metadata[8] != first.read_metadata[8]
        )
    result = replace(second, facts=factual)
    native._note_durable_target_v1(binding, result, key=_SOURCE_KEY_V1)
    return result


async def _outcomes(session, target):
    inner = decode(
        decode(target.intent.canonical_json.encode(), 16 * 1024 * 1024)[
            "intent_json"
        ].encode()
    )
    expected_exchange = sha(canonical(inner["exact_order"]))
    values = tuple(
        (
            await session.scalars(
                select(Outcome)
                .filter_by(reservation_id=target.reservation_id)
                .order_by(Outcome.sequence)
                .limit(_MAX_OUTCOMES + 1)
            )
        ).all()
    )
    need(len(values) <= _MAX_OUTCOMES)
    previous, revision = None, 0
    for index, row in enumerate(values, 1):
        raw, body = row.capture_json.encode(), row.outcome_json.encode()
        document = decode(body, 16384)
        need(
            row.sequence == index
            and row.previous_sha256 == previous
            and row.capture_sha256 == sha(raw)
            and row.outcome_id == sha(body)
            and row.intent_sha256 == target.intent.sha256
            and row.exchange_request_sha256 == expected_exchange
            and document["version"] == "ctcc.native_submission_outcome.v1"
            and document["capture_sha256"] == row.capture_sha256
            and document["sequence"] == index
            and document["previous_sha256"] == previous
            and document["reservation_id"] == row.reservation_id
            and document["intent_sha256"] == row.intent_sha256
            and document["intent_transition_id"] == row.intent_transition_id
            and document["exchange_request_sha256"] == expected_exchange
            and document["request_sha256"] == sha(target.request_json.encode())
            and document["ledger_revision"] == row.ledger_revision > revision
            and document["status"] == row.status
            and document["observation_kind"] == row.observation_kind
            and document["recorded_at"] == row.recorded_at.astimezone(UTC).isoformat()
            and document["observed_at"] == row.observed_at.astimezone(UTC).isoformat()
            and document["execution_authority"] is False
            and document["order_retry_authority"] is False
        )
        previous, revision = row.outcome_id, row.ledger_revision
    return values


async def _scan(binding, raw):
    from app.trade_qualification import native_account_risk_assembly_v1 as account

    _, source = _source(binding)
    from app.trade_qualification import native_postsubmit_reconciliation_v1 as post

    if post._ZERO_PERSISTENCE_V2.get(binding) is not None:
        post._scan_native_zero_cleanup_persistence_v2(binding, raw, key=_SOURCE_KEY_V1)
        _source(binding)
        return
    if post._FILLED_PERSISTENCE.get(binding) is not None:
        post._scan_native_filled_cleanup_persistence_v1(
            binding, raw, key=_SOURCE_KEY_V1
        )
        _source(binding)
        return
    account.scan_native_reconciliation_private_data_v1(
        source.source_generation, raw, key=_native_key()
    )
    _source(binding)


def _native_key():
    from app.trade_qualification import native_postsubmit_reconciliation_v1 as native

    return native._SOURCE_KEY_V1


async def _append(binding, parts, capture_document, *, kind, status="uncertain"):
    state, source, repository, session, scope_row, _, _, _ = parts
    target = state["target"]
    current = await _target_locked(
        binding, source, repository, session, scope_row, parts[-1]
    )
    need(current.core_json == target.core_json)
    prior = await _outcomes(session, target)
    _source(binding)
    if current.current_state == "reconciled_flat" and status == "uncertain":
        closure = state.get("verified_flat_closure") or state.get(
            "verified_filled_flat_closure"
        )
        need(
            state["kind"] == "recovery"
            and kind == "reconciliation"
            and closure is not None
        )
        capture_document = {
            **capture_document,
            "recovery_closed_outcome_id": closure[0],
            "execution_authority": False,
            "order_retry_authority": False,
        }
    capture_raw = canonical(capture_document)
    need(0 < len(capture_raw) <= 140000 and len(prior) < _MAX_OUTCOMES)
    await _scan(binding, capture_raw)
    now = repository._now(scope_row)
    scope_row.ledger_revision += 1
    scope_row.updated_at = now
    sequence = len(prior) + 1
    inner = decode(
        decode(target.intent.canonical_json.encode(), 16 * 1024 * 1024)[
            "intent_json"
        ].encode()
    )
    exchange_sha = sha(canonical(inner["exact_order"]))
    consumed_entry = await session.scalar(
        select(Transition).filter_by(
            reservation_id=target.reservation_id, state_revision=2
        )
    )
    _source(binding)
    need(
        type(consumed_entry) is Transition
        and type(consumed_entry.id) is int
        and consumed_entry.id > 0
    )
    observed_at = now  # Observation of this retained DATA, not a fabricated send.
    document = {
        "version": "ctcc.native_submission_outcome.v1",
        "reservation_id": target.reservation_id,
        "intent_transition_id": consumed_entry.id,
        "intent_sha256": target.intent.sha256,
        "exchange_request_sha256": exchange_sha,
        "request_sha256": target.consumed.request_sha256,
        "sequence": sequence,
        "previous_sha256": prior[-1].outcome_id if prior else None,
        "observation_kind": kind,
        "status": status,
        "capture_sha256": sha(capture_raw),
        "ledger_revision": scope_row.ledger_revision,
        "observed_at": observed_at.isoformat(),
        "recorded_at": now.isoformat(),
        "execution_authority": False,
        "order_retry_authority": False,
    }
    outcome_raw = canonical(document)
    need(len(outcome_raw) <= 16384)
    await session.flush()
    _source(binding)
    outcome = Outcome(
        outcome_id=sha(outcome_raw),
        reservation_id=target.reservation_id,
        intent_transition_id=consumed_entry.id,
        sequence=sequence,
        previous_sha256=document["previous_sha256"],
        observation_kind=kind,
        status=status,
        intent_sha256=target.intent.sha256,
        exchange_request_sha256=exchange_sha,
        capture_json=capture_raw.decode(),
        capture_sha256=sha(capture_raw),
        outcome_json=outcome_raw.decode(),
        ledger_revision=scope_row.ledger_revision,
        observed_at=observed_at,
        recorded_at=now,
    )
    session.add(outcome)
    await session.flush()
    _source(binding)
    return outcome.outcome_id


async def record_native_initial_v1(binding):
    state, _ = _source(binding)
    async with _transaction(binding) as parts:
        _, _, repository, session, scope_row, _, _, _ = parts
        prior = await _outcomes(session, state["target"])
        _source(binding)
        if prior:
            need(
                prior[0].observation_kind == "initial"
                and prior[0].status == "uncertain"
            )
            state["initial_outcome_id"] = prior[0].outcome_id
            return prior[0].outcome_id
        record = await session.get(
            Reservation, state["target"].reservation_id, populate_existing=True
        )
        _source(binding)
        need(record.state == "consumed" and record.state_revision == 2)
        previous, now = record.state, repository._now(scope_row)
        record.state, record.state_revision, record.updated_at = "uncertain", 3, now
        capture_document = {
            "version": "ctcc.native_initial_transport_data.v1",
            "kind": state["kind"],
            "reservation_id": record.reservation_id,
            "transport_response_missing_after_restart": state["kind"] == "recovery",
            "execution_authority": False,
            "order_retry_authority": False,
        }
        if state["kind"] == "current":
            source = state["response_source"]
            body = source.received_journal.body_raw
            need(type(body) is bytes and len(body) <= _CHUNK)
            if body:
                await _scan(binding, body)
            for key, value in source.received_journal.headers:
                await _scan(binding, key + b": " + value)
            capture_document.update(
                raw_body_hex=body.hex(),
                raw_body_sha256=sha(body),
                received_http_status=source.received_journal.status,
                received_eof=source.received_journal.eof,
                received_overflow=source.received_journal.overflow,
                received_faults=tuple(source.received_journal.faults),
                response_observation_sha256=source.observed.response_body_sha256,
            )
        repository._journal(
            session,
            record,
            previous,
            "post_submit_native_uncertain_v1",
            now,
            canonical(
                {
                    "version": "ctcc.native_uncertain_observation.v1",
                    "intent_sha256": state["target"].intent.sha256,
                    "capture_sha256": sha(canonical(capture_document)),
                }
            ).decode(),
        )
        await session.flush()
        _source(binding)
        outcome = await _append(binding, parts, capture_document, kind="initial")
        state["initial_write_metadata"] = parts[-1]
    async with _transaction(binding) as parts:
        rows = await _outcomes(parts[3], state["target"])
        need(
            rows
            and rows[0].outcome_id == outcome
            and parts[-1][8] != state["initial_write_metadata"][8]
        )
    state["initial_outcome_id"] = outcome
    return outcome


async def record_native_close_response_v1(binding):
    """Retain actual negative POST DATA; missing/partial ACK never grants EOF."""
    from app.exchange.okx import native_demo_write_v2 as write
    from app.trade_qualification import native_controlled_close_v1 as close

    state, _ = _source(binding)
    need(
        state["kind"] == "controlled_close_post"
        and state.get("close_response_outcome_id") is None
    )
    source = write._replay_native_close_response_for_reconciliation_v1(
        state["root_handle"], key=close._POSTSUBMIT_KEY_V1
    )
    need(
        source is state["response_source"]
        and source is state["close_response_view"].response_data
    )
    received = source.received_journal
    body = received.body_raw
    need(
        type(body) is bytes
        and len(body) <= 1024 * 1024
        and type(received.eof) is bool
        and type(received.overflow) is bool
        and type(received.body_complete) is bool
        and type(received.headers) is tuple
    )
    rows = tuple((name.hex(), value.hex()) for name, value in received.headers)
    for name, value in received.headers:
        await _scan(binding, name + b": " + value)
    complete = received.body_complete
    persist_raw = body == b"" or (complete and len(body) <= _CHUNK)
    if persist_raw and body:
        # Strict complete-document terminal scan occurs before hex encoding.
        await _scan(binding, body)
    view = state["close_response_view"].close_intent_view
    document = decode(view.intent_readback[1], 140000)
    capture = {
        "version": "ctcc.native_close_transport_response.v1",
        "reservation_id": state["target"].reservation_id,
        "close_intent_outcome_id": view.intent_readback[2],
        "close_request_sha256": document["close_request_sha256"],
        "raw_body_hex": body.hex() if persist_raw else None,
        "raw_body_sha256": sha(body),
        "raw_body_bytes": len(body),
        "received_http_status": received.status,
        "received_eof": received.eof,
        "received_overflow": received.overflow,
        "received_body_complete": complete,
        "received_faults": tuple(received.faults),
        "raw_headers_hex": rows,
        "raw_headers_sha256": sha(canonical(rows)),
        "raw_body_disposition": "retained_exact"
        if persist_raw
        else "private_native_memory_metadata_only",
        "response_observation_sha256": None
        if source.observed is None
        else source.observed.response_body_sha256,
        "execution_authority": False,
        "order_retry_authority": False,
    }
    if len(canonical(capture)) > 140000:
        capture["raw_body_hex"] = None
        capture["raw_body_disposition"] = "private_native_memory_metadata_only"
    need(len(canonical(capture)) <= 140000)
    async with _transaction(binding) as parts:
        outcome = await _append(binding, parts, capture, kind="reconciliation")
        written = parts[-1]
    async with _transaction(binding) as parts:
        saved = await _outcomes(parts[3], state["target"])
        _source(binding)
        need(
            saved[-1].outcome_id == outcome
            and parts[-1][8] != written[8]
            and saved[-1].capture_json.encode() == canonical(capture)
        )
        readback = (outcome, canonical(_row(saved[-1])), canonical(capture), parts[-1])
    actual = write._replay_native_close_response_for_reconciliation_v1(
        state["root_handle"], key=close._POSTSUBMIT_KEY_V1
    )
    need(
        actual is source
        and received.body_raw == body
        and received.eof is capture["received_eof"]
        and received.overflow is capture["received_overflow"]
        and received.body_complete is capture["received_body_complete"]
        and tuple((name.hex(), value.hex()) for name, value in received.headers) == rows
    )
    state["close_response_outcome_id"] = outcome
    state["close_response_readback"] = readback
    return outcome


async def retain_native_readback_v1(binding):
    state, source = _source(binding)
    observations = tuple(state["observations"])
    need(bool(observations))
    capture_id = sha(
        canonical(
            {
                "first_session": observations[0][1].read_session_id,
                "first_started_ns": observations[0][1].started_monotonic_ns,
                "reservation_id": state["reservation_id"],
            }
        )
    )
    expected = []
    async with _transaction(binding) as parts:
        for index, (role, page) in enumerate(observations):
            await _scan(binding, page.raw_body)
            for key, value in page.raw_headers:
                await _scan(binding, key + b": " + value)
            metadata = {
                "version": "ctcc.native_read_page_metadata.v1",
                "capture_id": capture_id,
                "page_index": index,
                "role": role,
                "path": page.path,
                "parameters": list(page.parameters),
                "request_url": page.request_url,
                "request_signature_sha256": page.request_signature_sha256,
                "read_session_id": page.read_session_id,
                "credential_session_sha256": source.credential_session_sha256,
                "original_credential_session_sha256": state[
                    "target"
                ].request.credential_session_sha256,
                "started_at": page.started_at.isoformat(),
                "started_monotonic_ns": page.started_monotonic_ns,
                "headers_at": page.headers_at.isoformat(),
                "headers_monotonic_ns": page.headers_monotonic_ns,
                "eof_at": page.eof_at.isoformat(),
                "eof_monotonic_ns": page.eof_monotonic_ns,
                "status": page.http_status,
                "raw_headers_hex": [[k.hex(), v.hex()] for k, v in page.raw_headers],
                "certificate_sha256": page.certificate_sha256,
                "tls_hostname": page.tls_hostname,
                "body_sha256": sha(page.raw_body),
                "body_bytes": len(page.raw_body),
            }
            metadata_id = await _append(binding, parts, metadata, kind="reconciliation")
            part_ids = []
            for offset in range(0, len(page.raw_body), _CHUNK):
                chunk = page.raw_body[offset : offset + _CHUNK]
                part_ids.append(
                    await _append(
                        binding,
                        parts,
                        {
                            "version": "ctcc.native_read_raw_part.v1",
                            "capture_id": capture_id,
                            "page_index": index,
                            "metadata_outcome_id": metadata_id,
                            "offset": offset,
                            "raw_hex": chunk.hex(),
                            "raw_sha256": sha(chunk),
                        },
                        kind="reconciliation",
                    )
                )
            expected.append(
                {
                    "role": role,
                    "metadata": metadata_id,
                    "parts": part_ids,
                    "body_sha256": sha(page.raw_body),
                }
            )
        manifest = {
            "version": "ctcc.native_read_manifest.v1",
            "capture_id": capture_id,
            "reservation_id": state["reservation_id"],
            "pages": expected,
            "assessment_sha256": None,
            "computation": "not_evaluated",
            "durable_lineage_sha256": state["target"].facts.durable_lineage_sha256,
            "execution_authority": False,
            "order_retry_authority": False,
        }
        if state["target"].current_state == "reconciled_flat":
            closure = state.get("verified_flat_closure") or state.get(
                "verified_filled_flat_closure"
            )
            need(state["kind"] == "recovery" and closure is not None)
            manifest["recovery_closed_outcome_id"] = closure[0]
        manifest_id = await _append(binding, parts, manifest, kind="reconciliation")
        write_metadata = parts[-1]
    async with _transaction(binding) as parts:
        stored = await _outcomes(parts[3], state["target"])
        need(parts[-1][8] != write_metadata[8])
        by_id = {row.outcome_id: row for row in stored}
        need(
            manifest_id in by_id
            and decode(by_id[manifest_id].capture_json.encode(), 140000) == manifest
        )
        reconstructed = []
        from app.exchange.okx.native_reconciliation_observation_v1 import (
            DemoReconciliationObservationV1,
        )

        for item in expected:
            page = decode(by_id[item["metadata"]].capture_json.encode(), 140000)
            raw, offset = bytearray(), 0
            for part_id in item["parts"]:
                chunk = decode(by_id[part_id].capture_json.encode(), 140000)
                value = bytes.fromhex(chunk["raw_hex"])
                need(
                    chunk["metadata_outcome_id"] == item["metadata"]
                    and chunk["offset"] == offset
                    and sha(value) == chunk["raw_sha256"]
                )
                raw.extend(value)
                offset += len(value)
            need(
                len(raw) == page["body_bytes"]
                and sha(bytes(raw)) == item["body_sha256"] == page["body_sha256"]
            )
            reconstructed.append(
                (
                    item["role"],
                    DemoReconciliationObservationV1(
                        page["path"],
                        tuple(tuple(pair) for pair in page["parameters"]),
                        page["request_url"],
                        page["request_signature_sha256"],
                        page["read_session_id"],
                        datetime.fromisoformat(page["started_at"]),
                        page["started_monotonic_ns"],
                        datetime.fromisoformat(page["headers_at"]),
                        page["headers_monotonic_ns"],
                        datetime.fromisoformat(page["eof_at"]),
                        page["eof_monotonic_ns"],
                        page["status"],
                        tuple(
                            (bytes.fromhex(k), bytes.fromhex(v))
                            for k, v in page["raw_headers_hex"]
                        ),
                        bytes(raw),
                        page["certificate_sha256"],
                        page["tls_hostname"],
                    ),
                )
            )
        need(tuple(reconstructed) == observations)
    state["durable_raw_manifest_id"] = manifest_id
    state["durable_raw_manifest"] = manifest
    state["durable_observations"] = tuple(reconstructed)
    return manifest_id


async def record_native_assessment_v1(binding):
    """Join the real computation only AFTER raw pages have durable readback."""
    state, _ = _source(binding)
    actual = computation.assess_native_reconciliation_v1(
        state["target"].facts, state["durable_observations"]
    )
    need(actual.receipt_json == state["assessment"].receipt_json)
    manifest = {
        **state["durable_raw_manifest"],
        "computation": "completed",
        "assessment_sha256": sha(actual.receipt_json),
        "raw_manifest_outcome_id": state["durable_raw_manifest_id"],
    }
    async with _transaction(binding) as parts:
        outcome = await _append(binding, parts, manifest, kind="reconciliation")
        metadata = parts[-1]
    async with _transaction(binding) as parts:
        rows = await _outcomes(parts[3], state["target"])
        need(
            rows[-1].outcome_id == outcome
            and parts[-1][8] != metadata[8]
            and decode(rows[-1].capture_json.encode(), 140000) == manifest
        )
    state["durable_readback_manifest_id"] = outcome
    state["durable_assessment"] = actual
    return outcome


async def retain_native_failed_attempt_v1(binding):
    """Bounded best-effort adverse DATA, never EOF/completeness or release.

    Uses only the ORIGINAL remaining read budget. Initial uncertainty and risk
    survive if that budget is exhausted. Unsafe/incomplete JSON prefixes stay
    in strongly held native memory; their count/hash/times are recorded when
    possible. No scanner bypass stores a possibly escaped credential as hex.
    """
    from app.exchange.okx import native_reconciliation_observation_v1 as read

    state, source = _source(binding)
    journals = getattr(source.client, "_native_reconciliation_read_journals_v1", ())
    need(
        type(journals) in (tuple, list)
        and len(journals) <= read.MAX_REQUESTS
        and all(type(item) is read.ReadJournalV1 for item in journals)
    )
    if not journals:
        return None
    retained = []
    async with _transaction(binding) as parts:
        for index, item in enumerate(journals):
            need(
                type(item.chunks) is list
                and all(type(chunk) is bytes for chunk in item.chunks)
            )
            raw = b"".join(item.chunks)
            need(len(raw) == item.body_bytes <= read.MAX_BODY)
            # Metadata is safe independently of whether a response body is a
            # complete JSON document. No provider error text/header/body enters.
            metadata = {
                "version": "ctcc.native_failed_read_page.v1",
                "page_index": index,
                "path": item.path,
                "parameters": list(item.parameters),
                "read_session_id": item.read_session_id,
                "started_at": item.started[0].isoformat(),
                "started_monotonic_ns": item.started[1],
                "headers_at": None
                if item.headers_stamp is None
                else item.headers_stamp[0].isoformat(),
                "headers_monotonic_ns": None
                if item.headers_stamp is None
                else item.headers_stamp[1],
                "eof_at": None
                if item.eof_stamp is None
                else item.eof_stamp[0].isoformat(),
                "eof_monotonic_ns": None
                if item.eof_stamp is None
                else item.eof_stamp[1],
                "http_status": item.status,
                "received_bytes": len(raw),
                "received_prefix_sha256": sha(raw),
                "faults": tuple(item.faults),
                "raw_retention": "native_memory_only",
                "execution_authority": False,
                "order_retry_authority": False,
            }
            metadata_id = await _append(binding, parts, metadata, kind="reconciliation")
            raw_ids = []
            can_store = bool(raw) and item.eof_stamp is not None
            if can_store:
                try:
                    await _scan(binding, raw)
                except Exception:  # noqa: BLE001 - raw rejection stays private; reauthenticate source before metadata-only retention.
                    # Revalidate the genuine registry/factory/budget: only a
                    # rejected RAW scan may become metadata-only. A changed or
                    # expired source cannot authorize this continuation.
                    _source(binding)
                    can_store = False
            if can_store:
                for offset in range(0, len(raw), _CHUNK):
                    chunk = raw[offset : offset + _CHUNK]
                    raw_ids.append(
                        await _append(
                            binding,
                            parts,
                            {
                                "version": "ctcc.native_failed_read_raw_part.v1",
                                "metadata_outcome_id": metadata_id,
                                "page_index": index,
                                "offset": offset,
                                "raw_hex": chunk.hex(),
                                "raw_sha256": sha(chunk),
                                "execution_authority": False,
                                "order_retry_authority": False,
                            },
                            kind="reconciliation",
                        )
                    )
            retained.append(
                {
                    "metadata": metadata_id,
                    "parts": raw_ids,
                    "raw_retention": "complete_received_bytes"
                    if raw_ids
                    else "native_memory_only",
                }
            )
        manifest = {
            "version": "ctcc.native_failed_read_attempt.v1",
            "reservation_id": state["reservation_id"],
            "safe_reason": state.get(
                "unknown_reason", "native_postsubmit_reconciliation_unknown"
            ),
            "prior_raw_manifest": state.get("durable_raw_manifest_id"),
            "pages": retained,
            "computation": "unresolved",
            "risk_hold_retained": True,
            "execution_authority": False,
            "order_retry_authority": False,
        }
        result = await _append(binding, parts, manifest, kind="reconciliation")
        metadata = parts[-1]
    async with _transaction(binding) as parts:
        rows = await _outcomes(parts[3], state["target"])
        need(
            rows[-1].outcome_id == result
            and parts[-1][8] != metadata[8]
            and decode(rows[-1].capture_json.encode(), 140000) == manifest
        )
    state["durable_failure_manifest_id"] = result
    return result


def _read_manifest(rows, manifest_id):
    """Reconstruct historical RAW bytes, not a hash-only completeness claim."""
    from app.exchange.okx.native_reconciliation_observation_v1 import (
        DemoReconciliationObservationV1,
    )

    by_id = {row.outcome_id: row for row in rows}
    need(len(by_id) == len(rows) and manifest_id in by_id)
    manifest_row = by_id[manifest_id]
    manifest = decode(manifest_row.capture_json.encode(), 140000)
    need(
        manifest_row.observation_kind == "reconciliation"
        and manifest_row.status == "uncertain"
        and manifest["version"] == "ctcc.native_read_manifest.v1"
        and manifest["computation"] == "completed"
        and type(manifest["pages"]) is list
        and 1 <= len(manifest["pages"]) <= 256
    )
    reconstructed = []
    for index, item in enumerate(manifest["pages"]):
        need(
            type(item) is dict
            and type(item["parts"]) is list
            and 1 <= len(item["parts"]) <= 8
        )
        metadata_row = by_id[item["metadata"]]
        page = decode(metadata_row.capture_json.encode(), 140000)
        need(
            metadata_row.observation_kind == "reconciliation"
            and metadata_row.status == "uncertain"
            and metadata_row.sequence < manifest_row.sequence
            and page["version"] == "ctcc.native_read_page_metadata.v1"
            and page["capture_id"] == manifest["capture_id"]
            and page["page_index"] == index
            and page["role"] == item["role"]
            and page["status"] == 200
        )
        raw = bytearray()
        for part_id in item["parts"]:
            part_row = by_id[part_id]
            part = decode(part_row.capture_json.encode(), 140000)
            value = bytes.fromhex(part["raw_hex"])
            need(
                part_row.observation_kind == "reconciliation"
                and part_row.status == "uncertain"
                and metadata_row.sequence < part_row.sequence < manifest_row.sequence
                and part["version"] == "ctcc.native_read_raw_part.v1"
                and part["capture_id"] == manifest["capture_id"]
                and part["page_index"] == index
                and part["metadata_outcome_id"] == metadata_row.outcome_id
                and part["offset"] == len(raw)
                and 0 < len(value) <= _CHUNK
                and sha(value) == part["raw_sha256"]
            )
            raw.extend(value)
            need(len(raw) <= 524288)
        body = bytes(raw)
        need(
            len(body) == page["body_bytes"]
            and sha(body) == page["body_sha256"] == item["body_sha256"]
        )
        reconstructed.append(
            (
                item["role"],
                DemoReconciliationObservationV1(
                    page["path"],
                    tuple(tuple(pair) for pair in page["parameters"]),
                    page["request_url"],
                    page["request_signature_sha256"],
                    page["read_session_id"],
                    datetime.fromisoformat(page["started_at"]),
                    page["started_monotonic_ns"],
                    datetime.fromisoformat(page["headers_at"]),
                    page["headers_monotonic_ns"],
                    datetime.fromisoformat(page["eof_at"]),
                    page["eof_monotonic_ns"],
                    page["status"],
                    tuple(
                        (bytes.fromhex(k), bytes.fromhex(v))
                        for k, v in page["raw_headers_hex"]
                    ),
                    body,
                    page["certificate_sha256"],
                    page["tls_hostname"],
                ),
            )
        )
    return manifest, tuple(reconstructed)


def _zero_closure_outcome_v2(rows, reservation_id):
    """Immutable historical selection; a later cleanup ACK is not a closure."""
    need(
        type(rows) is tuple
        and all(
            type(row) is Outcome and row.reservation_id == reservation_id
            for row in rows
        )
    )
    closures = [
        row
        for row in rows
        if decode(row.capture_json.encode(), 140000).get("version")
        == "ctcc.native_zero_fok_flat_closure.v1"
    ]
    need(
        len(closures) == 1
        and closures[0].observation_kind == "reconciliation"
        and closures[0].status == "rejected"
    )
    return closures[0]


async def _flat_readback_locked(binding, parts):
    from app.domain import native_zero_fok_closure_schema_v55 as schema

    state, source, repository, session, scope_row, _, _, metadata = parts
    target = state["target"]
    current = await _target_locked(
        binding, source, repository, session, scope_row, metadata
    )
    need(
        current.core_json == target.core_json
        and current.current_state == "reconciled_flat"
        and current.current_state_revision == 4
    )
    rows = await _outcomes(session, target)
    _source(binding)
    # Later cleanup/outbox DATA never replaces or restamps the immutable zero
    # closure. A different event may also advance the same scope's ledger.
    final = _zero_closure_outcome_v2(rows, target.reservation_id)
    witness = decode(final.capture_json.encode(), 140000)
    need(
        witness["version"] == "ctcc.native_zero_fok_flat_closure.v1"
        and witness["reservation_id"] == target.reservation_id
        and witness["request_sha256"] == sha(target.request_json.encode())
        and witness["intent_sha256"] == target.intent.sha256
        and witness["issued_row_sha256"] == target.request.issued_row_sha256
        and witness["original_credential_session_sha256"]
        == target.request.credential_session_sha256
        and witness["original_main_uid"] == target.facts.main_uid
        and witness["original_origin"] == target.facts.origin
        and sha(witness["exact_order_json"].encode()) == final.exchange_request_sha256
        and witness["closure_body_sha256"] == schema.CURRENT_BODY_SHA256
        and witness["execution_authority"] is False
        and witness["order_retry_authority"] is False
    )
    transition = await session.scalar(
        select(Transition).filter_by(
            reservation_id=target.reservation_id, state_revision=4
        )
    )
    _source(binding)
    record = await session.get(
        Reservation, target.reservation_id, populate_existing=True
    )
    _source(binding)
    need(
        type(transition) is Transition
        and type(record) is Reservation
        and transition.occurred_at == record.updated_at == final.recorded_at
        and transition.reason_code == "post_submit_native_zero_fok_flat_v1"
        and decode(transition.evidence_json.encode())
        == {
            "version": "ctcc.native_zero_fok_flat_transition.v1",
            "outcome_id": final.outcome_id,
            "manifest_outcome_id": witness["manifest_outcome_id"],
            "assessment_sha256": witness["assessment_sha256"],
            "closure_body_sha256": schema.CURRENT_BODY_SHA256,
            "execution_authority": False,
            "order_retry_authority": False,
        }
    )
    manifest, observations = _read_manifest(rows, witness["manifest_outcome_id"])
    need(
        manifest["reservation_id"] == target.reservation_id
        and manifest["assessment_sha256"] == witness["assessment_sha256"]
    )
    for _, page in observations:
        await _scan(binding, page.raw_body)
    assessment = computation.assess_native_reconciliation_v1(target.facts, observations)
    need(
        assessment.classification == "terminal_zero_fill_flat"
        and assessment.receipt_json.decode() == witness["assessment_json"]
        and sha(assessment.receipt_json) == witness["assessment_sha256"]
    )
    return final.outcome_id, assessment, observations, metadata


async def read_native_flat_closure_v1(binding):
    """Already-closed restart DATA; does not mutate/release another event."""
    state, _ = _source(binding)
    async with _transaction(binding) as parts:
        first = await _flat_readback_locked(binding, parts)
    async with _transaction(binding) as parts:
        second = await _flat_readback_locked(binding, parts)
        need(first[:3] == second[:3] and first[3][8] != second[3][8])
    state["verified_flat_closure"] = second
    return second


async def finish_previously_closed_recovery_v1(binding):
    """Commit and replay fresh verification for the exact spent zero-FOK event."""
    from app.trade_qualification import native_postsubmit_reconciliation_v1 as native

    state, _ = _source(binding)
    need(state["kind"] == "recovery")
    previous = state["verified_flat_closure"]
    observations = tuple(state["observations"])
    fresh = computation.assess_previously_closed_zero_v1(
        state["target"].facts,
        observations,
        closure_outcome_id=previous[0],
        historical_assessment=previous[1],
    )
    await retain_native_readback_v1(binding)
    need(state["durable_observations"] == observations)
    manifest = {
        **state["durable_raw_manifest"],
        "computation": "completed",
        "purpose": "native_previously_closed_zero_recovery.v1",
        "raw_manifest_outcome_id": state["durable_raw_manifest_id"],
        "assessment_sha256": sha(fresh),
        "assessment_json": fresh.decode(),
    }
    async with _transaction(binding) as parts:
        current = await _flat_readback_locked(binding, parts)
        need(current[:3] == previous[:3])
        outcome = await _append(binding, parts, manifest, kind="reconciliation")
        written_xid = parts[-1][8]
    async with _transaction(binding) as parts:
        current = await _flat_readback_locked(binding, parts)
        need(current[:3] == previous[:3] and parts[-1][8] != written_xid)
        rows = await _outcomes(parts[3], state["target"])
        saved, pages = _read_manifest(rows, outcome)
        need(saved == manifest and pages == observations)
        replay = computation.assess_previously_closed_zero_v1(
            state["target"].facts,
            pages,
            closure_outcome_id=current[0],
            historical_assessment=current[1],
        )
        need(replay == fresh)
        read_metadata = parts[-1]
    result = native.NativeReconciliationResultV1(
        state["reservation_id"],
        "terminal_zero_fill_flat_previously_reconciled",
        current[0],
        sha(replay),
        False,
    )
    state["verified_previously_closed_zero_recovery"] = (
        outcome,
        replay,
        pages,
        read_metadata,
    )
    state["owned_flat_result"] = result
    return result


async def finish_native_reconciliation_v1(binding):
    """Only exact zero FOK uses the new SQL predicate; filled positions HOLD."""
    from app.domain import native_zero_fok_closure_schema_v55 as schema
    from app.trade_qualification import native_postsubmit_reconciliation_v1 as native

    state, _ = _source(binding)
    actual = state["durable_assessment"]
    need(
        type(actual) is computation.NativeReconciliationAssessmentV1
        and actual.receipt_json == state["assessment"].receipt_json
    )
    if actual.classification == "terminal_zero_fill_flat":
        async with _transaction(binding) as parts:
            current = await _target_locked(
                binding, parts[1], parts[2], parts[3], parts[4], parts[-1]
            )
            need(
                current.current_state == "uncertain"
                and current.current_state_revision == 3
            )
            target = state["target"]
            inner = decode(
                decode(target.intent.canonical_json.encode(), 16 * 1024 * 1024)[
                    "intent_json"
                ].encode()
            )
            witness = {
                "version": "ctcc.native_zero_fok_flat_closure.v1",
                "reservation_id": target.reservation_id,
                "request_sha256": sha(target.request_json.encode()),
                "intent_sha256": target.intent.sha256,
                "issued_row_sha256": target.request.issued_row_sha256,
                "original_credential_session_sha256": target.request.credential_session_sha256,
                "original_main_uid": target.facts.main_uid,
                "original_origin": target.facts.origin,
                "exact_order_json": canonical(inner["exact_order"]).decode(),
                "manifest_outcome_id": state["durable_readback_manifest_id"],
                "assessment_json": actual.receipt_json.decode(),
                "assessment_sha256": sha(actual.receipt_json),
                "closure_body_sha256": schema.CURRENT_BODY_SHA256,
                "execution_authority": False,
                "order_retry_authority": False,
            }
            outcome = await _append(
                binding, parts, witness, kind="reconciliation", status="rejected"
            )
            final = await parts[3].get(Outcome, outcome, populate_existing=True)
            _source(binding)
            record = await parts[3].get(
                Reservation, target.reservation_id, populate_existing=True
            )
            _source(binding)
            need(
                type(final) is Outcome
                and record.state == "uncertain"
                and record.state_revision == 3
            )
            record.state, record.state_revision, record.updated_at = (
                "reconciled_flat",
                4,
                final.recorded_at,
            )
            parts[2]._journal(
                parts[3],
                record,
                "uncertain",
                "post_submit_native_zero_fok_flat_v1",
                final.recorded_at,
                canonical(
                    {
                        "version": "ctcc.native_zero_fok_flat_transition.v1",
                        "outcome_id": outcome,
                        "manifest_outcome_id": state["durable_readback_manifest_id"],
                        "assessment_sha256": sha(actual.receipt_json),
                        "closure_body_sha256": schema.CURRENT_BODY_SHA256,
                        "execution_authority": False,
                        "order_retry_authority": False,
                    }
                ).decode(),
            )
            await parts[3].flush()
            _source(binding)
            write_metadata = parts[-1]
        closed = await read_native_flat_closure_v1(binding)
        need(
            closed[0] == outcome
            and closed[1].receipt_json == actual.receipt_json
            and closed[3][8] != write_metadata[8]
        )
        # A result is DATA. Genuine owner settlement requires its own private
        # result-identity/closure/resource cleanup operation; never this bool.
        result = native.NativeReconciliationResultV1(
            state["reservation_id"],
            actual.classification,
            outcome,
            sha(actual.receipt_json),
            False,
        )
        state["owned_flat_result"] = result
        return result
    async with _transaction(binding) as parts:
        outcome = await _append(
            binding,
            parts,
            {
                "version": "ctcc.native_verified_reconciliation_data.v1",
                "manifest_outcome_id": state["durable_readback_manifest_id"],
                "assessment": decode(actual.receipt_json, 140000),
                "risk_hold_retained": True,
                "filled_lifecycle_pending": True,
                "execution_authority": False,
                "order_retry_authority": False,
            },
            kind="reconciliation",
        )
        metadata = parts[-1]
    async with _transaction(binding) as parts:
        rows = await _outcomes(parts[3], state["target"])
        need(rows[-1].outcome_id == outcome and parts[-1][8] != metadata[8])
        # Reconstruct the actual complete committed pages in this independent
        # transaction, rather than promoting a returned DATA classification.
        manifest, observations = _read_manifest(
            rows, state["durable_readback_manifest_id"]
        )
        replay = computation.assess_native_reconciliation_v1(
            state["target"].facts, observations
        )
        need(
            manifest["assessment_sha256"] == sha(replay.receipt_json)
            and replay.receipt_json == actual.receipt_json
        )
        readback_metadata = parts[-1]
    result = native.NativeReconciliationResultV1(
        state["reservation_id"],
        actual.classification,
        outcome,
        sha(actual.receipt_json),
        True,
    )
    if replay.filled_and_protected is True:
        state["verified_filled_readback"] = (
            outcome,
            replay,
            observations,
            readback_metadata,
        )
        state["owned_filled_result"] = result
    return result


async def record_native_close_preflight_v1(binding):
    """Same raw-parts journal, independently recomputed negative preflight."""
    from app.trade_qualification import native_close_contracts_v1 as close

    state, _ = _source(binding)
    need(state["kind"] == "controlled_close")
    entry = state["close_entry_view"].filled_result_view
    actual = close.assess_native_close_preflight_v1(
        state["target"].facts, entry.readback[2], state["durable_observations"]
    )
    need(
        actual.receipt_json == state["close_preflight"].receipt_json
        and actual.exact_order_json == state["close_preflight"].exact_order_json
    )
    manifest = {
        **state["durable_raw_manifest"],
        "computation": "completed",
        "assessment_sha256": sha(actual.receipt_json),
        "raw_manifest_outcome_id": state["durable_raw_manifest_id"],
        "purpose": "native_controlled_close_preflight.v1",
        "original_filled_outcome_id": entry.readback_outcome_id,
        "original_filled_receipt_sha256": entry.readback_receipt_sha256,
        "preflight_json": actual.receipt_json.decode(),
        "exact_close_order_json": actual.exact_order_json.decode(),
        "exact_close_order_sha256": actual.exact_order_sha256,
    }
    async with _transaction(binding) as parts:
        outcome = await _append(binding, parts, manifest, kind="reconciliation")
        write_metadata = parts[-1]
    async with _transaction(binding) as parts:
        rows = await _outcomes(parts[3], state["target"])
        need(rows[-1].outcome_id == outcome and parts[-1][8] != write_metadata[8])
        saved, observations = _read_manifest(rows, outcome)
        need(saved == manifest)
        replay = close.assess_native_close_preflight_v1(
            state["target"].facts, entry.readback[2], observations
        )
        need(
            replay.receipt_json == actual.receipt_json
            and replay.exact_order_json == actual.exact_order_json
        )
        metadata = parts[-1]
    state["durable_close_preflight"] = (outcome, replay, observations, metadata)
    return outcome


async def _require_close_intent_schema_v55(binding, session):
    from app.domain import native_close_intent_schema_v55 as schema

    metadata = await verify_actual_issuer_catalog_v2(session)
    _source(binding)
    need(
        type(metadata) is dict
        and metadata.get("native_baseline_admission") is True
        and metadata.get("native_close_intent_index_sha256")
        == schema.INDEX_SPEC_SHA256,
        "native_close_intent55_unsealed_or_schema_unavailable",
    )


async def create_native_close_intent_v1(binding):
    """Unique committed close request before any physical owner can be minted.

    Outcome columns retain the original entry anchor. The immutable capture
    explicitly records the SECOND close request and unique client ID separately.
    """
    from app.trade_qualification import native_close_contracts_v1 as close

    state, source = _source(binding)
    need(state["kind"] == "controlled_close" and state.get("close_budget") is None)
    preflight = state["durable_close_preflight"]
    need(
        type(preflight) is tuple
        and len(preflight) == 4
        and type(preflight[1]) is close.NativeClosePreflightDataV1
    )
    started = native_stamp()
    budget = {
        "started_utc_ns": started["utc_ns"],
        "started_monotonic_ns": started["monotonic_ns"],
        "expires_utc_ns": min(
            state["utc_deadline_ns"], started["utc_ns"] + 10_000_000_000
        ),
        "expires_monotonic_ns": min(
            state["mono_deadline_ns"], started["monotonic_ns"] + 10_000_000_000
        ),
        "maximum_seconds": 10,
        "entry_lease_renewed": False,
    }
    need(
        budget["expires_utc_ns"] > started["utc_ns"]
        and budget["expires_monotonic_ns"] > started["monotonic_ns"]
    )
    state["close_budget"] = budget
    remaining = min(
        (budget["expires_utc_ns"] - started["utc_ns"]) / 1_000_000_000,
        (budget["expires_monotonic_ns"] - started["monotonic_ns"]) / 1_000_000_000,
    )
    import asyncio

    async with asyncio.timeout_at(asyncio.get_running_loop().time() + remaining):
        async with _transaction(binding) as parts:
            await _require_close_intent_schema_v55(binding, parts[3])
            rows = await _outcomes(parts[3], state["target"])
            need(
                not any(
                    decode(row.capture_json.encode(), 140000).get("version")
                    == "ctcc.native_controlled_close_intent.v1"
                    for row in rows
                ),
                "native_close_intent_already_retained_no_retry",
            )
            manifest, observations = _read_manifest(rows, preflight[0])
            actual = close.assess_native_close_preflight_v1(
                state["target"].facts,
                state["close_entry_view"].filled_result_view.readback[2],
                observations,
            )
            need(
                manifest["purpose"] == "native_controlled_close_preflight.v1"
                and actual.receipt_json == preflight[1].receipt_json
                and actual.exact_order_json == preflight[1].exact_order_json
            )
            payload, facts_document = (
                decode(actual.exact_order_json),
                decode(actual.receipt_json),
            )
            document = {
                "version": "ctcc.native_controlled_close_intent.v1",
                "reservation_id": state["reservation_id"],
                "original_event_key": state["target"].request.origin.original_event_key,
                "original_entry_request_sha256": sha(
                    state["target"].request_json.encode()
                ),
                "original_entry_intent_sha256": state["target"].intent.sha256,
                "original_entry_order_id": facts_document["entry_order_id"],
                "original_entry_response_sha256": sha(
                    state[
                        "close_entry_view"
                    ].filled_result_view.response_data.received_journal.body_raw
                ),
                "original_filled_outcome_id": state[
                    "close_entry_view"
                ].filled_result_view.readback_outcome_id,
                "position_id": facts_document["position_id"],
                "protection_algo_id": facts_document["protection_algo_id"],
                "protection_client_order_id": facts_document[
                    "protection_client_order_id"
                ],
                "position_mode": facts_document["position_mode"],
                "closing_semantics": facts_document["closing_semantics"],
                "preflight_manifest_outcome_id": preflight[0],
                "preflight_sha256": sha(actual.receipt_json),
                "close_client_order_id": payload["clOrdId"],
                "exact_close_order_json": actual.exact_order_json.decode(),
                "close_request_sha256": actual.exact_order_sha256,
                "negative_budget": budget,
                "read_credential_session_sha256": source.credential_session_sha256,
                "original_credential_session_sha256": state[
                    "target"
                ].request.credential_session_sha256,
                "execution_authority": False,
                "order_retry_authority": False,
            }
            outcome = await _append(binding, parts, document, kind="reconciliation")
            saved = await parts[3].get(Outcome, outcome, populate_existing=True)
            _source(binding)
            need(type(saved) is Outcome)
            snapshot = canonical(_row(saved))
            written_metadata = parts[-1]
        async with _transaction(binding) as parts:
            await _require_close_intent_schema_v55(binding, parts[3])
            rows = await _outcomes(parts[3], state["target"])
            matches = [
                row
                for row in rows
                if decode(row.capture_json.encode(), 140000).get("version")
                == "ctcc.native_controlled_close_intent.v1"
            ]
            need(
                len(matches) == 1
                and matches[0].outcome_id == outcome
                and canonical(_row(matches[0])) == snapshot
                and matches[0].capture_json.encode() == canonical(document)
                and parts[-1][8] != written_metadata[8]
            )
            metadata = parts[-1]
        completed = native_stamp()
        need(
            started["utc_ns"] <= completed["utc_ns"] < budget["expires_utc_ns"]
            and started["monotonic_ns"]
            <= completed["monotonic_ns"]
            < budget["expires_monotonic_ns"]
        )
        state["durable_close_intent"] = (
            snapshot,
            canonical(document),
            outcome,
            metadata,
            budget,
        )
        return outcome


async def read_native_close_intent_locked_v1(
    view, *, session, scope_row, control_state, control_event_sha256, key
):
    """Actual same-session readback; caller must already hold UID->Control->scope."""
    from app.trade_qualification import native_controlled_close_v1 as close
    from app.trade_qualification import native_postsubmit_reconciliation_v1 as post

    need(key is close._DB_KEY_V1 and type(view) is post.NativeCloseIntentForOwnerV1)
    retained = post._replay_native_close_intent_for_owner_v1(
        view, key=close._SOURCE_KEY_V1
    )
    need(
        retained is view
        and type(session) is AsyncSession
        and session.in_transaction()
        and session.get_bind()
        is view.source_context.session_factory.kw["bind"].sync_engine
    )
    await _require_close_intent_schema_v55(view.binding, session)
    _source_state, source = _source(view.binding)
    repo = ledger.QualificationLedgerRepository(source.session_factory, clock=_now)
    metadata = tuple((await session.execute(_META)).one())
    _source(view.binding)
    need(
        len(metadata) == 10
        and metadata[0] == metadata[1]
        and metadata[2] == "none"
        and all(value is False for value in metadata[4:7])
        and metadata[8] not in {view.intent_readback[3][8]}
        and type(metadata[3]) is int
        and metadata[3] > 0
    )
    target = await _target_locked(
        view.binding, source, repo, session, scope_row, metadata
    )
    need(
        target.core_json == view.target.core_json
        and target.current_state == "uncertain"
        and target.current_state_revision == 3
    )
    permitted = await session.scalar(
        text(
            "SELECT pg_catalog.has_table_privilege(current_user,"
            "'public.demo_account_capture_events','SELECT')"
        )
    )
    _source(view.binding)
    need(permitted is True)
    reader = AccountCaptureJournalRepository(source.session_factory, clock=_now)
    chain = await reader._read_chain_locked(
        session, source.scope, target.current_capture_id
    )
    _source(view.binding)
    need(
        type(chain) is tuple
        and chain
        and journal.digest(chain[-1].event.event_json) == target.current_head_sha256
    )
    header = journal.checked_event(chain[0].event)
    lineage = decode(
        decode(target.lineage_row_json, 32 * 1024 * 1024)["lineage_json"].encode()
    )
    need(
        header["kind"] == "capture_start"
        and observed.reference_document(observed.source_reference(chain))
        == lineage["source_reference"]
    )
    plan = header["data"]["plan"]
    need(
        plan["expected_uid"] == source.scope.account_id
        and plan["environment"] == "demo"
        and plan["settlement_currency"] == source.scope.settlement_currency
        and plan["origin"] == source.origin
        and sha(plan["session_binding_id"].encode("ascii"))
        == target.request.credential_session_sha256
    )
    factual = computation.NativeReconciliationTargetV1(
        target.request,
        target.request_json,
        target.intent.canonical_json,
        target.intent.sha256,
        target.reservation_id,
        plan["expected_main_uid"],
        plan["origin"],
        target.request.credential_session_sha256,
        sha(
            canonical(
                {
                    "core_sha256": sha(target.core_json),
                    "original_capture_head": target.current_head_sha256,
                    "original_plan_sha256": header["data"]["plan_sha256"],
                }
            )
        ),
    )
    need(factual == view.target.facts)
    target = replace(target, facts=factual)
    rows = await _outcomes(session, target)
    _source(view.binding)
    intents = [
        row
        for row in rows
        if decode(row.capture_json.encode(), 140000).get("version")
        == "ctcc.native_controlled_close_intent.v1"
    ]
    need(
        len(intents) == 1
        and intents[0] is rows[-1]
        and canonical(_row(intents[0])) == view.intent_readback[0]
        and intents[0].capture_json.encode() == view.intent_readback[1]
        and intents[0].outcome_id == view.intent_readback[2]
    )
    document = decode(view.intent_readback[1])
    manifest, observations = _read_manifest(
        rows, document["preflight_manifest_outcome_id"]
    )
    from app.trade_qualification import native_close_contracts_v1 as contracts

    preflight = contracts.assess_native_close_preflight_v1(
        target.facts, view.filled_result_view.readback[2], observations
    )
    need(
        manifest["purpose"] == "native_controlled_close_preflight.v1"
        and sha(preflight.receipt_json) == document["preflight_sha256"]
        and preflight.exact_order_json.decode() == document["exact_close_order_json"]
        and preflight.exact_order_sha256 == document["close_request_sha256"]
    )
    issued_document = decode(target.issued_row_json, 32 * 1024 * 1024)
    need(
        scope_row.account_revision == target.request.expected_account_revision
        and scope_row.ledger_revision == intents[0].ledger_revision
        and scope_row.claims_json == issued_document["claims_json"]
        and scope_row.claims_sha256 == issued_document["claims_sha256"]
        and sha(scope_row.claims_json.encode()) == scope_row.claims_sha256
    )
    original = control.decode(
        decode(target.intent.canonical_json.encode())["consume_control_state_json"]
    )
    need(
        control_state.scope == original.scope
        and control_state.owner_sha256 == original.owner_sha256
        and control_state.owner_epoch == original.owner_epoch
    )
    await DemoControlRepositoryV2.verify_historical_binding(
        session, control_state, control_event_sha256
    )
    _source(view.binding)
    # Read-only/negative close may follow disarm/EStop/entry expiry. Ownership,
    # current Control lineage and held original risk remain exact; no Arm rises.
    return {
        "ledger_revision": intents[0].ledger_revision,
        "claims_sha256": scope_row.claims_sha256,
        "close_row_sha256": sha(view.intent_readback[0]),
        "metadata": metadata,
        "control_state_json": control.document(control_state),
        "control_event_sha256": control_event_sha256,
    }


def _recovery_close_history_from_rows_v1(target, rows, metadata):
    """Rebuild the original and preflight source without a surviving process cap."""
    from app.trade_qualification import native_close_contracts_v1 as close

    need(type(target) is NativeTargetReadbackV1 and type(rows) is tuple)
    matches = [
        row
        for row in rows
        if decode(row.capture_json.encode(), 140000).get("version")
        == "ctcc.native_controlled_close_intent.v1"
    ]
    need(len(matches) <= 1, "native_recovery_duplicate_close_intent")
    if not matches:
        return None
    row = matches[0]
    need(
        row.observation_kind == "reconciliation"
        and row.status == "uncertain"
        and rows
        and rows[0].observation_kind == "initial"
    )
    document = decode(row.capture_json.encode(), 140000)
    need(
        document["reservation_id"] == target.reservation_id
        and document["original_event_key"] == target.request.origin.original_event_key
        and document["original_entry_request_sha256"]
        == sha(target.request_json.encode())
        and document["original_entry_intent_sha256"] == target.intent.sha256
        and document["original_credential_session_sha256"]
        == target.request.credential_session_sha256
        and document["execution_authority"] is False
        and document["order_retry_authority"] is False
    )
    planned, prior = _read_manifest(rows, document["preflight_manifest_outcome_id"])
    need(
        planned["purpose"] == "native_controlled_close_preflight.v1"
        and planned["original_filled_outcome_id"]
        == document["original_filled_outcome_id"]
    )
    by_id = {item.outcome_id: item for item in rows}
    original_row = by_id.get(document["original_filled_outcome_id"])
    need(type(original_row) is Outcome and original_row.sequence < row.sequence)
    original_document = decode(original_row.capture_json.encode(), 140000)
    need(original_document["version"] == "ctcc.native_verified_reconciliation_data.v1")
    manifest, original = _read_manifest(rows, original_document["manifest_outcome_id"])
    assessment = computation.assess_native_reconciliation_v1(target.facts, original)
    need(
        assessment.filled_and_protected is True
        and original_document["assessment"] == decode(assessment.receipt_json)
        and manifest["assessment_sha256"]
        == planned["original_filled_receipt_sha256"]
        == sha(assessment.receipt_json)
    )
    preflight = close.assess_native_close_preflight_v1(target.facts, original, prior)
    measured = decode(preflight.receipt_json)
    payload = decode(preflight.exact_order_json)
    need(
        planned["preflight_json"].encode() == preflight.receipt_json
        and planned["assessment_sha256"]
        == document["preflight_sha256"]
        == sha(preflight.receipt_json)
        and planned["exact_close_order_json"].encode()
        == document["exact_close_order_json"].encode()
        == preflight.exact_order_json
        and planned["exact_close_order_sha256"]
        == document["close_request_sha256"]
        == preflight.exact_order_sha256
        and document["close_client_order_id"] == payload["clOrdId"]
        and all(
            document[name] == measured[name]
            for name in (
                "position_id",
                "protection_algo_id",
                "protection_client_order_id",
                "position_mode",
                "closing_semantics",
            )
        )
        and document["original_entry_order_id"] == measured["entry_order_id"]
    )
    retained = tuple(canonical(_row(item)) for item in rows)
    need(
        sum(map(len, retained)) <= 16 * 1024 * 1024,
        "native_recovery_retained_history_bound",
    )
    return NativeRecoveryCloseHistoryV1(
        row.outcome_id,
        canonical(_row(row)),
        row.capture_json.encode(),
        original_row.outcome_id,
        original_document["manifest_outcome_id"],
        assessment,
        original,
        document["preflight_manifest_outcome_id"],
        preflight,
        prior,
        retained,
        rows[0].outcome_id,
        metadata,
    )


async def read_native_recovery_close_history_v1(binding):
    """Two actual new-XID reads of the unique durable close; never mint a write owner."""
    state, _ = _source(binding)
    need(
        state["kind"] == "recovery"
        and state.get("recovery_history_loaded") is not True
        and binding not in _RECOVERY_CLOSE_HISTORIES_V1
    )
    snapshots, retained_candidates = [], ()
    for _ in range(2):
        async with _transaction(binding) as parts:
            await _require_close_intent_schema_v55(binding, parts[3])
            current = await _target_locked(
                binding, parts[1], parts[2], parts[3], parts[4], parts[-1]
            )
            need(current.core_json == state["target"].core_json)
            rows = await _outcomes(parts[3], state["target"])
            _source(binding)
            actual = _recovery_close_history_from_rows_v1(
                state["target"], rows, parts[-1]
            )
            row_buffers = (
                actual.retained_rows
                if actual is not None
                else tuple(canonical(_row(item)) for item in rows)
            )
            received_roots = _recovery_received_roots_from_rows_v1(rows)
            roots = (
                *_recovery_history_data_roots_v1(actual, row_buffers),
                *received_roots,
            )
            retained_candidates = (*retained_candidates, *roots)
            _RECOVERY_HISTORY_BUFFERS_V1[binding] = retained_candidates
            state["recovery_history_retained"] = retained_candidates
            # Both independent-read copies remain charged while retained.
            _source(binding)
            snapshots.append(
                (actual is None, tuple(sha(raw) for raw in row_buffers), parts[-1])
            )
    first, second = snapshots
    need(
        first[1] == second[1] and first[2][8] != second[2][8] and first[0] is second[0]
    )
    result = actual
    state["recovery_history_loaded"] = True
    state["recovery_close_history"] = result
    if result is not None:
        need(
            type(result) is NativeRecoveryCloseHistoryV1
            and (state["target"].current_state, state["target"].current_state_revision)
            in {("uncertain", 3), ("reconciled_flat", 4)}
        )
        _RECOVERY_CLOSE_HISTORIES_V1[binding] = result
        state["initial_outcome_id"] = result.initial_outcome_id
        # This source replay charges the retained complete history together
        # with actual new GET/credential buffers, before any next request.
        _source(binding)
    # The first transaction's reconstructed objects are no longer retained.
    retained_candidates = (
        *_recovery_history_data_roots_v1(result, row_buffers),
        *received_roots,
    )
    _RECOVERY_HISTORY_BUFFERS_V1[binding] = retained_candidates
    state["recovery_history_retained"] = retained_candidates
    _source(binding)
    for raw in native_recovery_history_buffers_v1(binding):
        await _scan(binding, raw)
    return result


def native_recovery_history_buffers_v1(binding):
    """Private DATA roots, authenticated by the exact actual repository result."""
    state, _ = _source(binding)
    need(state["kind"] == "recovery")
    roots = _RECOVERY_HISTORY_BUFFERS_V1.get(binding)
    need(type(roots) is tuple and state.get("recovery_history_retained") is roots)
    return roots


def _recovery_history_data_roots_v1(value, row_buffers):
    need(type(row_buffers) is tuple and all(type(raw) is bytes for raw in row_buffers))
    if value is None:
        return row_buffers
    need(type(value) is NativeRecoveryCloseHistoryV1)
    return (
        value.close_row_json,
        value.close_intent_json,
        value.original_assessment.receipt_json,
        value.preflight_assessment.receipt_json,
        *row_buffers,
        *(
            page.raw_body
            for _, page in value.original_observations + value.preflight_observations
        ),
    )


def _recovery_received_roots_from_rows_v1(rows):
    """Recover stored completed response bytes, not intentional outgoing auth."""
    roots, seen_pages, total = [], set(), 0
    by_id = {row.outcome_id: row for row in rows}
    need(len(by_id) == len(rows))
    for row in rows:
        document = decode(row.capture_json.encode(), 140000)
        version = document.get("version")
        if version in {
            "ctcc.native_initial_transport_data.v1",
            "ctcc.native_close_transport_response.v1",
        }:
            encoded = document.get("raw_body_hex")
            need(encoded is None or type(encoded) is str)
            if encoded:
                need(
                    len(encoded) <= 2 * _CHUNK
                    and len(encoded) % 2 == 0
                    and all(char in "0123456789abcdef" for char in encoded)
                )
                body = bytes.fromhex(encoded)
                need(
                    document.get("received_eof") is True
                    and document.get("received_overflow") is False
                    and sha(body) == document["raw_body_sha256"]
                )
                if version == "ctcc.native_close_transport_response.v1":
                    need(
                        document.get("received_body_complete") is True
                        and len(body) == document["raw_body_bytes"]
                    )
                # Stored nonempty bodies were complete-document scanned before
                # append. Missing/metadata-only ACK custody is never fabricated.
                decode(body, _CHUNK)
                roots.append(body)
            if version == "ctcc.native_close_transport_response.v1":
                headers = document["raw_headers_hex"]
                need(
                    type(headers) is list
                    and len(headers) <= 256
                    and sha(canonical(headers)) == document["raw_headers_sha256"]
                )
                for pair in headers:
                    need(
                        type(pair) is list
                        and len(pair) == 2
                        and all(
                            type(part) is str and len(part) <= 131072 for part in pair
                        )
                    )
                    roots.append(
                        bytes.fromhex(pair[0]) + b": " + bytes.fromhex(pair[1])
                    )
        elif (
            version == "ctcc.native_read_manifest.v1"
            and document.get("computation") == "completed"
        ):
            pages = document["pages"]
            need(type(pages) is list and 1 <= len(pages) <= 256)
            maximum = 0
            for item in pages:
                metadata = decode(by_id[item["metadata"]].capture_json.encode(), 140000)
                count = metadata["body_bytes"]
                need(type(count) is int and 0 < count <= 524288)
                maximum += count
                need(
                    maximum <= 16 * 1024 * 1024,
                    "native_recovery_retained_history_bound",
                )
            _, observations = _read_manifest(rows, row.outcome_id)
            for item, (_, page) in zip(pages, observations, strict=True):
                if item["metadata"] not in seen_pages:
                    seen_pages.add(item["metadata"])
                    roots.append(page.raw_body)
                    roots.extend(key + b": " + value for key, value in page.raw_headers)
        total = sum(len(raw) for raw in roots)
        need(total <= 16 * 1024 * 1024, "native_recovery_retained_history_bound")
    return tuple(roots)


def _close_history_context_v1(binding):
    state, _ = _source(binding)
    if state["kind"] == "recovery":
        value = _RECOVERY_CLOSE_HISTORIES_V1.get(binding)
        need(
            type(value) is NativeRecoveryCloseHistoryV1
            and state.get("recovery_close_history") is value
            and state.get("recovery_history_loaded") is True
        )
        return (
            value.close_outcome_id,
            value.close_row_json,
            value.close_intent_json,
            value.original_outcome_id,
            value.original_assessment.receipt_json,
            value.original_observations,
        )
    need(state["kind"] == "controlled_close_post")
    view = state["close_response_view"].close_intent_view
    return (
        view.intent_readback[2],
        view.intent_readback[0],
        view.intent_readback[1],
        view.filled_result_view.readback_outcome_id,
        view.filled_result_view.readback[1].receipt_json,
        view.filled_result_view.readback[2],
    )


async def read_native_recovered_filled_closure_v1(binding):
    state, _ = _source(binding)
    need(state["kind"] == "recovery")
    _close_history_context_v1(binding)
    async with _transaction(binding) as parts:
        first = await _read_native_filled_flat_closure_locked_v1(binding, parts)
    async with _transaction(binding) as parts:
        second = await _read_native_filled_flat_closure_locked_v1(binding, parts)
        need(first[:3] == second[:3] and first[3][8] != second[3][8])
    state["verified_filled_flat_closure"] = second
    return second


async def finish_native_recovered_filled_reconciliation_v1(binding):
    """Resolve only the exact durable event, without old Root/socket/Arm custody."""
    from app.trade_qualification import native_postsubmit_reconciliation_v1 as post

    state, _ = _source(binding)
    need(state["kind"] == "recovery")
    _close_history_context_v1(binding)
    if state["target"].current_state == "uncertain":
        result = await finish_native_filled_flat_reconciliation_v1(binding)
        # Financial/entry/exit forensics are reconstructed from committed
        # manifests. Missing old response/socket custody stays explicitly absent.
        await record_native_closed_forensics_v1(binding)
        classification = "own_entry_and_close_filled_flat_recovered"
    else:
        need(
            state["target"].current_state == "reconciled_flat"
            and state["target"].current_state_revision == 4
        )
        previous = state["verified_filled_flat_closure"]
        current = await read_native_recovered_filled_closure_v1(binding)
        need(current[:3] == previous[:3])
        actual = state["verified_closed_trade_readback"]
        result = post.NativeReconciliationResultV1(
            state["reservation_id"],
            "own_entry_and_close_filled_flat_previously_reconciled",
            current[0],
            sha(actual[1].receipt_json),
            False,
        )
        classification = result.classification
    # No owned_filled_flat_result/Root cap is created. Surviving local resource
    # records remain held; a restart cannot attest to the old process's sockets.
    result = post.NativeReconciliationResultV1(
        result.reservation_id,
        classification,
        result.outcome_id,
        result.receipt_sha256,
        False,
    )
    state["recovered_filled_flat_result"] = result
    return result


async def _recompute_native_closed_trade_locked_v1(binding, parts, manifest_id):
    """Rebuild ALL three actual committed read groups in one UID transaction."""
    from app.trade_qualification import native_close_final_facts_v1 as closing

    state, _ = _source(binding)
    need(state["kind"] in {"controlled_close_post", "recovery"})
    target = await _target_locked(
        binding, parts[1], parts[2], parts[3], parts[4], parts[-1]
    )
    need(
        target.core_json == state["target"].core_json
        and (target.current_state, target.current_state_revision)
        in {("uncertain", 3), ("reconciled_flat", 4)}
    )
    if target.current_state == "reconciled_flat":
        await _require_filled_flat_schema_v55(binding, parts[3])
        transition = await parts[3].scalar(
            select(Transition).filter_by(
                reservation_id=target.reservation_id, state_revision=4
            )
        )
        _source(binding)
        need(
            type(transition) is Transition
            and transition.reason_code == "post_submit_native_filled_flat_v1"
        )
    rows = await _outcomes(parts[3], state["target"])
    _source(binding)
    (
        close_id,
        close_row_json,
        close_raw,
        original_id,
        original_receipt,
        original_held_pages,
    ) = _close_history_context_v1(binding)
    close_rows = [
        row
        for row in rows
        if decode(row.capture_json.encode(), 140000).get("version")
        == "ctcc.native_controlled_close_intent.v1"
    ]
    need(
        len(close_rows) == 1
        and close_rows[0].outcome_id == close_id
        and canonical(_row(close_rows[0])) == close_row_json
        and close_rows[0].capture_json.encode() == close_raw
    )
    close_document = decode(close_rows[0].capture_json.encode(), 140000)
    planned, preflight_pages = _read_manifest(
        rows, close_document["preflight_manifest_outcome_id"]
    )
    by_id = {row.outcome_id: row for row in rows}
    original_row = by_id.get(planned["original_filled_outcome_id"])
    need(type(original_row) is Outcome and original_row.outcome_id == original_id)
    original_document = decode(original_row.capture_json.encode(), 140000)
    need(original_document["version"] == "ctcc.native_verified_reconciliation_data.v1")
    original_manifest, original_pages = _read_manifest(
        rows, original_document["manifest_outcome_id"]
    )
    original = computation.assess_native_reconciliation_v1(
        state["target"].facts, original_pages
    )
    need(
        original.filled_and_protected is True
        and original_document["assessment"] == decode(original.receipt_json)
        and sha(original.receipt_json) == planned["original_filled_receipt_sha256"]
        and original_manifest["assessment_sha256"] == sha(original.receipt_json)
        and original.receipt_json == original_receipt
        and original_pages == original_held_pages
    )
    final_manifest, final_pages = _read_manifest(rows, manifest_id)
    actual = closing.assess_native_closed_trade_v1(
        state["target"].facts,
        original_pages,
        preflight_pages,
        close_rows[0].capture_json.encode(),
        final_pages,
    )
    need(len(actual.receipt_json) <= 16384)
    return final_manifest, actual, final_pages


async def record_native_closed_trade_readback_v1(binding):
    """Existing DB0018 DATA append/readback, explicitly retaining held risk.

    This records the independently reconstructed terminal evidence. It does
    not call a reservation transition or claim the pending SQL witness exists.
    """
    state, _ = _source(binding)
    need(state["kind"] in {"controlled_close_post", "recovery"})
    async with _transaction(binding) as parts:
        (
            raw_manifest,
            actual,
            observations,
        ) = await _recompute_native_closed_trade_locked_v1(
            binding, parts, state["durable_raw_manifest_id"]
        )
        need(
            observations == state["durable_observations"]
            and actual.receipt_json == state["closed_trade_assessment"].receipt_json
        )
        manifest = {
            **raw_manifest,
            "computation": "completed",
            "raw_manifest_outcome_id": state["durable_raw_manifest_id"],
            "assessment_sha256": sha(actual.receipt_json),
            "purpose": "native_controlled_close_final.v1",
            "close_intent_outcome_id": _close_history_context_v1(binding)[0],
        }
        closure_pending = state["target"].current_state == "uncertain"
        manifest_id = await _append(binding, parts, manifest, kind="reconciliation")
        outcome = await _append(
            binding,
            parts,
            {
                "version": "ctcc.native_closed_trade_readback.v1",
                "manifest_outcome_id": manifest_id,
                "assessment_json": actual.receipt_json.decode(),
                "assessment_sha256": sha(actual.receipt_json),
                "risk_hold_retained": closure_pending,
                "closure_transition_pending": closure_pending,
                "execution_authority": False,
                "order_retry_authority": False,
            },
            kind="reconciliation",
        )
        write_metadata = parts[-1]
    async with _transaction(binding) as parts:
        manifest, replay, observations = await _recompute_native_closed_trade_locked_v1(
            binding, parts, manifest_id
        )
        rows = await _outcomes(parts[3], state["target"])
        _source(binding)
        need(
            rows[-1].outcome_id == outcome
            and parts[-1][8] != write_metadata[8]
            and manifest["assessment_sha256"] == sha(replay.receipt_json)
            and replay.receipt_json == actual.receipt_json
        )
        saved = decode(rows[-1].capture_json.encode(), 140000)
        need(
            saved["version"] == "ctcc.native_closed_trade_readback.v1"
            and saved["manifest_outcome_id"] == manifest_id
            and saved["assessment_json"].encode() == replay.receipt_json
            and saved["risk_hold_retained"] is closure_pending
            and saved["closure_transition_pending"] is closure_pending
        )
        read_metadata = parts[-1]
    state["verified_closed_trade_readback"] = (
        outcome,
        replay,
        observations,
        read_metadata,
        manifest_id,
    )
    return outcome


async def _require_filled_flat_schema_v55(binding, session):
    from app.domain import native_filled_flat_closure_schema_v55 as schema

    actual = await verify_actual_issuer_catalog_v2(session)
    _source(binding)
    need(
        type(actual) is dict
        and actual.get("native_baseline_admission") is True
        and actual.get("native_closure_body_sha256") == schema.CURRENT_BODY_SHA256
        and actual.get("native_zero_closure_body_sha256") == schema.ZERO_BODY_SHA256
        and actual.get("native_filled_flat_body_sha256") == schema.FILLED_BODY_SHA256,
        "native_filled_flat55_unsealed_or_schema_unavailable",
    )


async def _read_native_filled_flat_closure_locked_v1(binding, parts):
    from app.domain import native_filled_flat_closure_schema_v55 as schema

    state, _ = _source(binding)
    await _require_filled_flat_schema_v55(binding, parts[3])
    target = await _target_locked(
        binding, parts[1], parts[2], parts[3], parts[4], parts[-1]
    )
    need(
        target.current_state == "reconciled_flat" and target.current_state_revision == 4
    )
    rows = await _outcomes(parts[3], state["target"])
    _source(binding)
    matches = [
        row
        for row in rows
        if decode(row.capture_json.encode(), 140000).get("version")
        == schema.WITNESS_VERSION
    ]
    need(len(matches) == 1)
    row = matches[0]
    witness = decode(row.capture_json.encode(), 140000)
    valid = await parts[3].scalar(
        text(
            "SELECT public.qualification_native_filled_flat_data_v55(:reservation,:at)"
        ),
        {"reservation": target.reservation_id, "at": row.recorded_at},
    )
    _source(binding)
    need(
        valid is True
        and row.status == "acknowledged"
        and row.observation_kind == "reconciliation"
        and witness["closure_body_sha256"] == schema.CURRENT_BODY_SHA256
    )
    manifest, actual, observations = await _recompute_native_closed_trade_locked_v1(
        binding, parts, witness["manifest_outcome_id"]
    )
    need(
        manifest["purpose"] == "native_controlled_close_final.v1"
        and witness["assessment_sha256"] == sha(actual.receipt_json)
        and manifest["assessment_sha256"] == sha(actual.receipt_json)
        and witness["close_intent_outcome_id"] == _close_history_context_v1(binding)[0]
    )
    by_id = {item.outcome_id: item for item in rows}
    terminal = by_id.get(witness["terminal_data_outcome_id"])
    need(
        type(terminal) is Outcome
        and terminal.sequence + 1 == row.sequence
        and decode(terminal.capture_json.encode(), 140000)["assessment_json"].encode()
        == actual.receipt_json
    )
    return (
        row.outcome_id,
        actual,
        observations,
        parts[-1],
        witness["manifest_outcome_id"],
        witness["terminal_data_outcome_id"],
        witness["close_intent_outcome_id"],
    )


async def finish_native_filled_flat_reconciliation_v1(binding):
    """Same DB0017/18 exact 3->4 transition; new SQL independently replays it."""
    from app.domain import native_filled_flat_closure_schema_v55 as schema
    from app.trade_qualification import native_postsubmit_reconciliation_v1 as post

    state, _ = _source(binding)
    need(state["kind"] in {"controlled_close_post", "recovery"})
    terminal = state["verified_closed_trade_readback"]
    async with _transaction(binding) as parts:
        await _require_filled_flat_schema_v55(binding, parts[3])
        _, actual, observations = await _recompute_native_closed_trade_locked_v1(
            binding, parts, terminal[4]
        )
        rows = await _outcomes(parts[3], state["target"])
        _source(binding)
        need(
            rows[-1].outcome_id == terminal[0]
            and actual.receipt_json == terminal[1].receipt_json
            and observations == terminal[2]
        )
        target = state["target"]
        inner = decode(
            decode(target.intent.canonical_json.encode())["intent_json"].encode()
        )
        witness = {
            "version": schema.WITNESS_VERSION,
            "reservation_id": target.reservation_id,
            "request_sha256": sha(target.request_json.encode()),
            "intent_sha256": target.intent.sha256,
            "original_credential_session_sha256": target.request.credential_session_sha256,
            "issued_row_sha256": target.request.issued_row_sha256,
            "original_main_uid": target.facts.main_uid,
            "original_origin": target.facts.origin,
            "exact_order_json": canonical(inner["exact_order"]).decode(),
            "close_intent_outcome_id": _close_history_context_v1(binding)[0],
            "terminal_data_outcome_id": terminal[0],
            "manifest_outcome_id": terminal[4],
            "assessment_sha256": sha(actual.receipt_json),
            "closure_body_sha256": schema.CURRENT_BODY_SHA256,
            "execution_authority": False,
            "order_retry_authority": False,
        }
        outcome = await _append(
            binding, parts, witness, kind="reconciliation", status="acknowledged"
        )
        row = await parts[3].get(Outcome, outcome, populate_existing=True)
        record = await parts[3].get(
            Reservation, target.reservation_id, populate_existing=True
        )
        _source(binding)
        need(
            type(row) is Outcome
            and type(record) is Reservation
            and record.state == "uncertain"
            and record.state_revision == 3
        )
        record.state, record.state_revision, record.updated_at = (
            "reconciled_flat",
            4,
            row.recorded_at,
        )
        parts[2]._journal(
            parts[3],
            record,
            "uncertain",
            schema.TRANSITION_REASON,
            row.recorded_at,
            canonical(
                {
                    "version": schema.WITNESS_VERSION,
                    "outcome_id": outcome,
                    "terminal_data_outcome_id": terminal[0],
                    "manifest_outcome_id": terminal[4],
                    "assessment_sha256": sha(actual.receipt_json),
                    "closure_body_sha256": schema.CURRENT_BODY_SHA256,
                    "execution_authority": False,
                    "order_retry_authority": False,
                }
            ).decode(),
        )
        await parts[3].flush()
        _source(binding)
        written_metadata = parts[-1]
    async with _transaction(binding) as parts:
        proof = await _read_native_filled_flat_closure_locked_v1(binding, parts)
        need(
            proof[0] == outcome
            and proof[1].receipt_json == actual.receipt_json
            and proof[3][8] != written_metadata[8]
        )
    state["verified_filled_flat_closure"] = proof
    result = post.NativeReconciliationResultV1(
        state["reservation_id"],
        "own_entry_and_close_filled_flat",
        outcome,
        sha(proof[1].receipt_json),
        False,
    )
    if state["kind"] == "controlled_close_post":
        state["owned_filled_flat_result"] = result
    else:
        # Recovery never constructs the lost process's Root response owner.
        state["recovered_filled_flat_result"] = result
    return result


async def record_native_closed_forensics_v1(binding):
    """Actual all-three-manifest reconstruction into existing immutable DATA."""
    from app.trade_evidence import native_closed_forensics_v55 as forensics

    state, _ = _source(binding)
    closure = state["verified_filled_flat_closure"]
    _, _, close_raw, _, _, _ = _close_history_context_v1(binding)
    document = decode(close_raw, 140000)
    async with _transaction(binding) as parts:
        current = await _read_native_filled_flat_closure_locked_v1(binding, parts)
        need(current[:3] == closure[:3])
        rows = await _outcomes(parts[3], state["target"])
        _source(binding)
        by_id = {item.outcome_id: item for item in rows}
        initial = decode(
            by_id[document["original_filled_outcome_id"]].capture_json.encode(), 140000
        )
        _, original = _read_manifest(rows, initial["manifest_outcome_id"])
        _, preflight = _read_manifest(rows, document["preflight_manifest_outcome_id"])
        _, final = _read_manifest(rows, closure[4])
        actual = forensics.reconstruct_native_closed_forensics_v55(
            state["target"].facts, original, preflight, close_raw, final
        )
        need(
            type(actual) is forensics.NativeClosedForensicsDataV55
            and 0 < len(actual.receipt_json) <= 32768
        )
        capture = {
            "version": "ctcc.native_closed_trade_forensics_readback.v1",
            "closure_outcome_id": closure[0],
            "terminal_data_outcome_id": closure[5],
            "close_intent_outcome_id": closure[6],
            "original_manifest_outcome_id": initial["manifest_outcome_id"],
            "preflight_manifest_outcome_id": document["preflight_manifest_outcome_id"],
            "final_manifest_outcome_id": closure[4],
            "forensics_json": actual.receipt_json.decode(),
            "forensics_sha256": sha(actual.receipt_json),
            "execution_authority": False,
            "order_retry_authority": False,
        }
        outcome = await _append(
            binding, parts, capture, kind="reconciliation", status="acknowledged"
        )
        written = parts[-1]
    async with _transaction(binding) as parts:
        current = await _read_native_filled_flat_closure_locked_v1(binding, parts)
        rows = await _outcomes(parts[3], state["target"])
        _source(binding)
        need(
            current[:3] == closure[:3]
            and rows[-1].outcome_id == outcome
            and rows[-1].capture_json.encode() == canonical(capture)
            and parts[-1][8] != written[8]
        )
        # The mapper has already consumed independently reconstructed raw
        # groups; this new session proves the exact immutable stored bytes.
        saved = (
            outcome,
            actual,
            canonical(_row(rows[-1])),
            parts[-1],
            canonical(capture),
        )
    state["verified_closed_forensics"] = saved
    return outcome


async def record_native_zero_cleanup_v2(binding):
    """After actual zero/Control/Root/R7 retirement; immutable DATA, no spool."""
    from app.trade_qualification import native_postsubmit_reconciliation_v1 as post

    state, _ = _source(binding)
    need(
        post._ZERO_PERSISTENCE_V2.get(binding) is state
        and state.get("zero_ledger_finalized") is True
        and state.get("source_retirement_call_returned") is True
        and state.get("root_finalization_returned") is True
    )
    flat = state["root_flat_settlement_view"]
    retired = state["ledger_retirement_receipt"]
    finalized = state["root_finalization_receipt"]
    need(
        type(retired) is tuple
        and len(retired) == 4
        and retired[0] == "ctcc.native_flat_ledger_resources_retired.v1"
        and type(finalized) is tuple
        and finalized
        == (
            "ctcc.native_transport_resources_finalized.v1",
            flat.closure_outcome_id,
            flat.closure_receipt_sha256,
        )
    )
    resources = canonical(
        {
            "version": "ctcc.native_zero_resources_closed.v2",
            "closure_outcome_id": flat.closure_outcome_id,
            "closure_receipt_sha256": flat.closure_receipt_sha256,
            "socket_observation": state["closed_transport_view"].socket_observation,
            "execution_authority": False,
            "order_retry_authority": False,
        }
    )
    receipts = {
        "source_retirement": state["zero_source_retirement_json"],
        "root_resources": resources,
        "ledger_retirement": canonical(
            {
                "version": retired[0],
                "reservation_id": retired[1],
                "original_event_key": retired[2],
                "request_sha256": retired[3],
            }
        ),
        "root_finalized": canonical(
            {
                "version": finalized[0],
                "closure_outcome_id": finalized[1],
                "closure_receipt_sha256": finalized[2],
            }
        ),
    }
    capture = {
        "version": "ctcc.native_zero_trade_cleanup.v2",
        "reservation_id": state["reservation_id"],
        "request_sha256": sha(state["target"].request_json.encode()),
        "intent_sha256": state["target"].intent.sha256,
        "closure_outcome_id": flat.closure_outcome_id,
        "closure_receipt_sha256": flat.closure_receipt_sha256,
        "entry_response_body_sha256": sha(flat.response_data.received_journal.body_raw),
        "execution_authority": False,
        "order_retry_authority": False,
    }
    for name, raw in receipts.items():
        need(
            type(raw) is bytes
            and 0 < len(raw) <= 32768
            and canonical(decode(raw, 32768)) == raw
        )
        capture[name + "_json"], capture[name + "_sha256"] = raw.decode(), sha(raw)
    async with _transaction(binding) as parts:
        proof = await _flat_readback_locked(binding, parts)
        need(proof[:3] == state["verified_flat_closure"][:3])
        outcome = await _append(
            binding, parts, capture, kind="reconciliation", status="acknowledged"
        )
        stored = await parts[3].get(Outcome, outcome, populate_existing=True)
        _source(binding)
        need(type(stored) is Outcome)
        snapshot, written = canonical(_row(stored)), parts[-1]
    async with _transaction(binding) as parts:
        proof = await _flat_readback_locked(binding, parts)
        rows = await _outcomes(parts[3], state["target"])
        _source(binding)
        matches = [row for row in rows if row.outcome_id == outcome]
        need(
            proof[:3] == state["verified_flat_closure"][:3]
            and len(matches) == 1
            and canonical(_row(matches[0])) == snapshot
            and matches[0].capture_json.encode() == canonical(capture)
            and parts[-1][8] != written[8]
            and 0 < len(snapshot) <= 320000
        )
        raw_capture = matches[0].capture_json.encode()
        await _scan(binding, raw_capture)
        await _scan(binding, snapshot)
        metadata = parts[-1]
    state["verified_zero_cleanup_readback"] = (outcome, snapshot, raw_capture, metadata)
    return outcome


async def record_native_filled_cleanup_v1(binding):
    """Append final actual receipts AFTER both Root/source/ledger retirements."""
    state, _ = _source(binding)
    need(
        state.get("filled_cleanup_status") == "RETIRED"
        and state.get("filled_root_retirement_taken") is True
        and state.get("filled_source_retired") is True
        and state.get("filled_close_retired") is True
        and state.get("filled_ledger_finalized") is True
        and state.get("verified_filled_cleanup_readback") is None
    )
    flat = state["filled_root_result_view"]
    intent = state["close_response_view"].close_intent_view
    close_response = decode(state["close_response_readback"][2], 140000)
    resources = canonical(
        {
            "version": "ctcc.native_filled_pair_resources_closed.v1",
            "reservation_id": state["reservation_id"],
            "closure_outcome_id": flat.closure_outcome_id,
            "closure_receipt_sha256": flat.closure_receipt_sha256,
            "socket_observations": state["closed_transports_view"].socket_observations,
            "entry_request_sha256": flat.response_data.transport_facts.exact_order_sha256,
            "close_request_sha256": decode(intent.intent_readback[1])[
                "close_request_sha256"
            ],
            "execution_authority": False,
            "order_retry_authority": False,
        }
    )
    finalized, retired, ledger_retired = (
        state["filled_root_finalization_receipt"],
        state["filled_root_retirement_receipt"],
        state["filled_ledger_retirement"],
    )
    receipts = {
        "root_resource_receipt": resources,
        "source_cleanup_receipt": state["filled_source_retirement_json"],
        "ledger_retirement": canonical(
            {
                "version": ledger_retired[0],
                "reservation_id": ledger_retired[1],
                "original_event_key": ledger_retired[2],
                "request_sha256": ledger_retired[3],
            }
        ),
        "close_retirement": state["filled_close_retirement_json"],
        "root_finalized_receipt": canonical(
            {
                "version": finalized[0],
                "closure_outcome_id": finalized[1],
                "closure_receipt_sha256": finalized[2],
                "prepared_cleanup_sha256": finalized[3],
            }
        ),
        "root_retirement_receipt": canonical(
            {
                "version": retired[0],
                "closure_outcome_id": retired[1],
                "closure_receipt_sha256": retired[2],
                "retirement_receipt_sha256": retired[3],
            }
        ),
    }
    capture = {
        "version": "ctcc.native_closed_trade_cleanup.v1",
        "reservation_id": state["reservation_id"],
        "request_sha256": sha(state["target"].request_json.encode()),
        "intent_sha256": state["target"].intent.sha256,
        "closure_outcome_id": flat.closure_outcome_id,
        "closure_receipt_sha256": flat.closure_receipt_sha256,
        "terminal_data_outcome_id": flat.closure_readback[5],
        "close_intent_outcome_id": flat.closure_readback[6],
        "close_response_outcome_id": state["close_response_readback"][0],
        "entry_response_body_sha256": sha(flat.response_data.received_journal.body_raw),
        "close_response_body_sha256": close_response["raw_body_sha256"],
        "execution_authority": False,
        "order_retry_authority": False,
    }
    for name, raw in receipts.items():
        need(
            type(raw) is bytes
            and type(decode(raw, 32768)) is dict
            and canonical(decode(raw, 32768)) == raw
        )
        capture[name + "_json"], capture[name + "_sha256"] = raw.decode(), sha(raw)
    async with _transaction(binding) as parts:
        closure = await _read_native_filled_flat_closure_locked_v1(binding, parts)
        need(closure[:3] == flat.closure_readback[:3])
        outcome = await _append(
            binding, parts, capture, kind="reconciliation", status="acknowledged"
        )
        written = parts[-1]
    async with _transaction(binding) as parts:
        rows = await _outcomes(parts[3], state["target"])
        _source(binding)
        matches = [
            row
            for row in rows
            if decode(row.capture_json.encode(), 140000).get("version")
            == capture["version"]
        ]
        need(
            len(matches) == 1
            and matches[0].outcome_id == outcome
            and matches[0].capture_json.encode() == canonical(capture)
            and parts[-1][8] != written[8]
        )
        row_json = canonical(_row(matches[0]))
        need(0 < len(row_json) <= 320000)
        readback = (outcome, row_json, canonical(capture), parts[-1])
    state["verified_filled_cleanup_readback"] = readback
    return outcome


async def record_native_filled_report_ready_v1(binding):
    """Native witness + existing spool; SQL derives eligibility independently."""
    from app.database.models.submission_reporting import (
        QualificationReportSpool as Spool,
    )
    from app.database.repositories import native_closed_reporting_v55 as reporting
    from app.database.repositories import submission_reporting as shared
    from app.domain import native_closed_report_schema_v55 as policy
    from app.trade_evidence import outbox
    from app.trade_evidence.submission_reporting import policy_record, queue

    state, source = _source(binding)
    cleanup, forensic = (
        state["verified_filled_cleanup_readback"],
        state["verified_closed_forensics"],
    )
    need(
        state.get("filled_terminal_spool") is None
        and state.get("filled_report_policy_result") is None
    )
    _, policy_json, policy_sha = policy_record(outbox.OutboxPolicy())
    namespace = queue(state["filled_report_namespace"])
    report_bytes, report_sha = forensic[1].receipt_json, sha(forensic[1].receipt_json)
    ready = {
        "version": "ctcc.native_closed_trade_report_ready.v1",
        "reservation_id": state["reservation_id"],
        "request_sha256": sha(state["target"].request_json.encode()),
        "intent_sha256": state["target"].intent.sha256,
        "closure_outcome_id": state["verified_filled_flat_closure"][0],
        "cleanup_outcome_id": cleanup[0],
        "forensics_outcome_id": forensic[0],
        "report_sha256": report_sha,
        "policy_sha256": policy_sha,
        "report_policy_version": policy.REPORT_POLICY_VERSION,
        "report_policy_sha256": policy.REPORT_POLICY_SHA256,
        "execution_authority": False,
        "order_retry_authority": False,
    }
    async with _transaction(binding) as parts:
        outcome = await _append(
            binding, parts, ready, kind="reconciliation", status="acknowledged"
        )
        permitted = await parts[3].scalar(
            text(
                "SELECT public.qualification_native_report_eligible_v55(:oid,:rid,:report,:policy)"
            ),
            {
                "oid": outcome,
                "rid": state["reservation_id"],
                "report": report_sha,
                "policy": policy_sha,
            },
        )
        _source(binding)
        need(type(permitted) is bool)
        if permitted:
            row = await parts[3].get(Outcome, outcome, populate_existing=True)
            _source(binding)
            spool = Spool(
                spool_id=shared.spool_identity(
                    outcome, report_sha, policy_sha, namespace
                ),
                outcome_id=outcome,
                reservation_id=state["reservation_id"],
                report_id=state["target"].consumed.report_id,
                report_bytes=report_bytes,
                report_sha256=report_sha,
                policy_json=policy_json,
                policy_sha256=policy_sha,
                queue_namespace=namespace,
                eligible=True,
                created_at=row.recorded_at,
            )
            parts[3].add(spool)
            await parts[3].flush()
            _source(binding)
            spool_id = spool.spool_id
        else:
            spool_id = None  # No legacy ineligible spool or caller flag fallback.
        written = parts[-1]
    async with _transaction(binding) as parts:
        rows = await _outcomes(parts[3], state["target"])
        _source(binding)
        need(
            rows[-1].outcome_id == outcome
            and rows[-1].capture_json.encode() == canonical(ready)
            and parts[-1][8] != written[8]
        )
        if spool_id is not None:
            saved = await parts[3].get(Spool, spool_id, populate_existing=True)
            _source(binding)
            need(
                type(saved) is Spool
                and saved.report_bytes == report_bytes
                and saved.report_sha256 == report_sha
                and saved.policy_json == policy_json
                and saved.policy_sha256 == policy_sha
                and saved.eligible is True
                and saved.outcome_id == outcome
            )
            repository = shared.SubmissionReportingRepository(
                source.session_factory, clock=_now
            )
            await reporting.verify_native_terminal_spool_v55(
                repository, parts[3], saved
            )
            _source(binding)
            state["filled_terminal_spool"] = canonical(_row(saved))
        state["filled_report_policy_result"] = (outcome, permitted, spool_id, parts[-1])
    return spool_id
