"""Observe the DB0056 reservation journal under its real UID and SQL fences."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import pairwise
from typing import Literal

from sqlalchemy import func, select, text

from app.core import native_locked_demo_control_0056_v1 as locked
from app.core.native_stopped_control_genesis_0056_v1 import _require_role_witness
from app.database.models.account_issued_revision import (
    DemoCollectorIssuedAccountRevision,
)
from app.database.models.account_revision_collector_lineage import (
    DemoCollectorCurrentRevisionLineage,
)
from app.database.models.qualification_ledger import (
    QualificationAccountScope,
    QualificationReservation,
    QualificationReservationTransition,
)
from app.database.repositories import qualification_ledger as ledger
from app.database.repositories import qualification_native_checkpoint_v2 as checkpoint
from app.domain import native_filled_flat_closure_schema_v55 as closure
from app.domain.source_primitives import canonical, decode, sha
from app.trade_qualification import control_bound_ledger, reservations
from app.trade_qualification.demo_control import ControlScope
from app.trade_qualification.reservations import LedgerScope, checked_bootstrap
from app.trade_qualification.submission_intent import replay_submission_intent

_READ_COMMITTED = text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
_JOURNAL_FENCE = text(
    "LOCK TABLE public.qualification_account_scopes, "
    "public.qualification_reservations, public.qualification_reservation_transitions "
    "IN SHARE MODE NOWAIT"
)
_ISSUED_FENCE = text(
    "LOCK TABLE public.demo_collector_issued_account_revisions, "
    "public.demo_collector_current_revision_lineages IN ACCESS SHARE MODE NOWAIT"
)
_MODELS = (
    QualificationAccountScope,
    QualificationReservation,
    QualificationReservationTransition,
    DemoCollectorIssuedAccountRevision,
    DemoCollectorCurrentRevisionLineage,
)
_EVENT_LIMIT = 2048
_CURRENCY_LIMIT = 128
_INTENT_REPLAY_BYTES = 64 * 1024 * 1024
_SHA256 = re.compile(r"[0-9a-f]{64}")


class NativeLedgerPreflight0056Error(ValueError):
    """Fixed refusal without UID, SQL, role, receipt or intent data."""


@dataclass(frozen=True, slots=True, repr=False)
class NativeLedgerPreflight0056V1:
    control_revision: int
    control_event_sha256: str
    emergency_stop: bool
    stored_arm_present: bool
    account_revision: int
    ledger_revision: int
    scope_state: Literal["BOOTSTRAP", "LEGACY_CLAIMS", "HISTORICAL_ISSUED"]
    unresolved_reservation_count: int
    unresolved_consumed_intent_count: int
    server_incarnation_sha256: str
    role_route_sha256: str
    native_definitions_sha256: str
    uid_ledger_checkpoint_sha256: str
    observed_at: datetime
    database_head: Literal["0056"] = field(default="0056", init=False)
    reconciliation_verified: Literal[False] = field(default=False, init=False)
    account_complete: Literal[False] = field(default=False, init=False)
    execution_authority: Literal[False] = field(default=False, init=False)
    admission: Literal["DENY"] = field(default="DENY", init=False)


def _need(value: bool) -> None:
    if value is not True:
        raise NativeLedgerPreflight0056Error("native_ledger_0056_preflight_refused")


async def _replay_historical_scope(session, repository, scope, row, held):
    _need(
        type(row) is QualificationAccountScope
        and type(row.account_revision) is type(row.ledger_revision) is int
        and 0 <= row.account_revision <= row.ledger_revision < 2**63
        and (row.environment, row.account_id, row.settlement_currency)
        == (scope.environment, scope.account_id, scope.settlement_currency)
    )

    def check():
        held()
        return repository._now(row)

    check()
    if row.account_revision == 0:
        _need(row.claims_json is row.claims_sha256 is None and row.ledger_revision == 0)
        return "BOOTSTRAP"
    if checkpoint._finite_payload(row.claims_json):
        await checkpoint._validate_held_finite_checkpoint_rows(
            session, scope, row, check=check
        )
        check()
        return "HISTORICAL_ISSUED"
    _need(repository._claims(row).scope == scope)
    return "LEGACY_CLAIMS"


async def _unresolved_under_uid_lock(session, scope, held):
    rows = tuple(
        (
            await session.execute(
                select(
                    QualificationReservation.reservation_id,
                    QualificationReservation.state,
                    QualificationReservation.state_revision,
                    QualificationReservationTransition.id,
                    QualificationReservationTransition.state_revision,
                    QualificationReservationTransition.from_state,
                    QualificationReservationTransition.reason_code,
                    func.pg_catalog.encode(
                        func.pg_catalog.sha256(
                            func.pg_catalog.convert_to(
                                QualificationReservationTransition.evidence_json,
                                "UTF8",
                            )
                        ),
                        "hex",
                    ),
                    QualificationReservation.request_sha256,
                )
                .outerjoin(
                    QualificationReservationTransition,
                    (
                        QualificationReservationTransition.reservation_id
                        == QualificationReservation.reservation_id
                    )
                    & (QualificationReservationTransition.to_state == "consumed"),
                )
                .where(
                    QualificationReservation.environment == scope.environment,
                    QualificationReservation.account_id == scope.account_id,
                    QualificationReservation.state != "reconciled_flat",
                )
                .order_by(QualificationReservation.reservation_id)
                .limit(_EVENT_LIMIT + 1)
            )
        ).all()
    )
    held()
    _need(len(rows) <= _EVENT_LIMIT and len({row[0] for row in rows}) == len(rows))
    intents = 0
    for (
        _,
        state,
        revision,
        transition_id,
        consumed_revision,
        previous,
        reason,
        evidence_sha256,
        request_sha256,
    ) in rows:
        _need(
            type(revision) is int
            and type(request_sha256) is str
            and _SHA256.fullmatch(request_sha256) is not None
            and (
                evidence_sha256 is None
                or type(evidence_sha256) is str
                and _SHA256.fullmatch(evidence_sha256) is not None
            )
        )
        if (state, revision) in {("reserved", 1), ("uncertain", 2)}:
            _need(transition_id is None)
            continue
        _need(
            (state, revision) in {("consumed", 2), ("uncertain", 3)}
            and type(transition_id) is int
            and transition_id > 0
            and consumed_revision == 2
            and previous == "reserved"
            and (
                (reason == "consumed" and evidence_sha256 is None)
                or (
                    reason
                    in {
                        control_bound_ledger.CONSUMED_REASON,
                        "consumed_with_submit_intent",
                    }
                    and evidence_sha256 is not None
                )
            )
        )
        if evidence_sha256 is not None:
            intents += 1
    return rows, intents


async def _verify_native_intent_issuance(session, request, control_state, event, held):
    from app.database.repositories import account_issued_revision_current55_v1 as issued

    issued_row = await session.get(
        DemoCollectorIssuedAccountRevision,
        request.issuer_sha256,
        populate_existing=True,
    )
    held()
    _need(type(issued_row) is DemoCollectorIssuedAccountRevision)
    issued_raw = issued._snapshot(
        issued_row, DemoCollectorIssuedAccountRevision, issued._ISSUED_COLUMNS
    )
    coordinates = (
        request.scope.environment,
        request.scope.account_id,
        request.scope.settlement_currency,
    )
    _need(
        sha(issued_raw) == request.issued_row_sha256
        and sha(issued_row.proof_json.encode()) == request.issuer_sha256
        and sha(issued_row.claims_json.encode()) == issued_row.claims_sha256
        and (
            issued_row.environment,
            issued_row.account_id,
            issued_row.settlement_currency,
        )
        == coordinates
        and issued_row.account_revision == request.expected_account_revision
        and issued_row.ledger_revision == request.expected_ledger_revision
        and issued_row.control_revision == control_state.revision
        and issued_row.control_event_sha256 == event
        and request.origin.deadline <= issued_row.original_expires_at
    )
    lineage = await session.get(
        DemoCollectorCurrentRevisionLineage,
        issued_row.source_lineage_sha256,
        populate_existing=True,
    )
    held()
    _need(
        type(lineage) is DemoCollectorCurrentRevisionLineage
        and sha(lineage.lineage_json.encode())
        == lineage.lineage_sha256
        == issued_row.source_lineage_sha256
        and (lineage.environment, lineage.account_id, lineage.settlement_currency)
        == coordinates
    )
    return sha(issued._lineage_snapshot_v1(lineage))


async def _replay_consumed_under_uid_lock(session, scope, held, currencies, rows):
    """Authenticate retained intent bytes in the fenced snapshot, without admission."""
    from app.trade_qualification import native_ledger_contracts_v2 as native

    expected_intents = [row for row in rows if row[7] is not None]
    currency_revisions = {
        currency: (account, revision, updated_at)
        for currency, account, revision, _, updated_at in currencies
    }
    byte_count = 0
    verified = []
    for (
        reservation_id,
        state,
        revision,
        transition_id,
        _,
        _,
        reason,
        intent_sha,
        request_sha,
    ) in expected_intents:
        record = await session.get(
            QualificationReservation, reservation_id, populate_existing=True
        )
        held()
        _need(
            type(record) is QualificationReservation
            and (record.environment, record.account_id)
            == (scope.environment, scope.account_id)
            and (record.state, record.state_revision, record.request_sha256)
            == (state, revision, request_sha)
        )
        chain = tuple(
            (
                await session.scalars(
                    select(QualificationReservationTransition)
                    .filter_by(reservation_id=reservation_id)
                    .order_by(QualificationReservationTransition.state_revision)
                    .limit(4)
                )
            ).all()
        )
        held()
        _need(
            len(chain) == revision
            and all(type(row) is QualificationReservationTransition for row in chain)
            and tuple(row.state_revision for row in chain)
            == tuple(range(1, revision + 1))
            and chain[0].from_state is None
            and chain[0].to_state == "reserved"
            and chain[0].occurred_at == record.created_at
            and chain[1].id == transition_id
            and chain[1].from_state == "reserved"
            and chain[1].to_state == "consumed"
            and chain[1].reason_code == reason
            and chain[-1].to_state == state
            and chain[-1].occurred_at == record.updated_at
            and all(
                left.to_state == right.from_state
                and left.occurred_at <= right.occurred_at
                for left, right in pairwise(chain)
            )
        )
        raw_request = record.request_json.encode()
        raw_evidence = tuple(
            row.evidence_json.encode() if row.evidence_json is not None else None
            for row in chain
        )
        byte_count += len(raw_request) + sum(
            len(raw) for raw in raw_evidence if raw is not None
        )
        _need(
            byte_count <= _INTENT_REPLAY_BYTES
            and sha(raw_request) == request_sha
            and raw_evidence[1] is not None
            and sha(raw_evidence[1]) == intent_sha
        )
        request = reservations.decode_reservation_request(record.request_json)
        _need(
            (
                request.scope.environment,
                request.scope.account_id,
                request.scope.settlement_currency,
            )
            == (record.environment, record.account_id, record.settlement_currency)
            and reservations.reservation_id(
                request.scope, request.origin.original_event_key
            )
            == reservation_id
        )
        lineage_sha = None
        is_native = native.is_native_request(request)
        if reason == control_bound_ledger.CONSUMED_REASON:
            _need(chain[0].reason_code == control_bound_ledger.RESERVED_REASON)
            envelope, consumed = control_bound_ledger.replay_control_bound_intent(
                chain[1].evidence_json, request, expected_sha256=intent_sha
            )
            _need(
                json.loads(envelope.canonical_json)["reservation_binding_json"]
                == chain[0].evidence_json
            )
            _, control_state, event = control_bound_ledger.replay_reserved_evidence(
                chain[0].evidence_json, request
            )
            held()
            await locked.DemoControlRepositoryV2.verify_historical_binding(
                session, control_state, event
            )
            held()
            if is_native:
                _need(
                    revision == 2
                    or chain[2].reason_code == "post_submit_native_uncertain_v1"
                )
                if revision == 3:
                    _need(raw_evidence[2] is not None)
                    uncertainty = decode(raw_evidence[2], 1024)
                    _need(
                        type(uncertainty) is dict
                        and set(uncertainty)
                        == {"version", "intent_sha256", "capture_sha256"}
                        and uncertainty["version"]
                        == "ctcc.native_uncertain_observation.v1"
                        and uncertainty["intent_sha256"] == intent_sha
                        and type(uncertainty["capture_sha256"]) is str
                        and _SHA256.fullmatch(uncertainty["capture_sha256"]) is not None
                    )
                lineage_sha = await _verify_native_intent_issuance(
                    session, request, control_state, event, held
                )
        else:
            _need(
                not is_native
                and reason == "consumed_with_submit_intent"
                and chain[0].reason_code == "risk_reserved"
                and chain[0].evidence_json is None
            )
            _, consumed = replay_submission_intent(
                chain[1].evidence_json, request, expected_sha256=intent_sha
            )
        account_revision, ledger_revision, scope_updated_at = currency_revisions[
            record.settlement_currency
        ]
        _need(
            consumed.scope == request.scope
            and consumed.reservation_id == reservation_id
            and consumed.updated_at == chain[1].occurred_at
            and record.updated_at <= scope_updated_at
            and request.expected_account_revision
            <= consumed.account_revision
            <= account_revision
            and request.expected_ledger_revision + 2
            <= consumed.ledger_revision
            <= ledger_revision
            and (
                not is_native
                or consumed.ledger_revision == request.expected_ledger_revision + 2
            )
            and all(
                getattr(record, field) == getattr(consumed, field)
                for field in (
                    "original_event_key",
                    "report_id",
                    "instrument_id",
                    "direction",
                    "correlation_group",
                    "request_sha256",
                    "deadline",
                    "created_at",
                )
            )
            and reservations.canonical(consumed.coverage) == record.coverage_json
            and (record.risk_amount, record.margin_amount, record.notional_amount)
            == (
                consumed.coverage.risk_amount,
                consumed.coverage.margin_amount,
                consumed.coverage.notional_amount,
            )
        )
        verified.append(
            [
                reservation_id,
                lineage_sha,
                [
                    [
                        row.id,
                        row.reason_code,
                        row.occurred_at.isoformat(),
                        sha(raw) if raw is not None else None,
                    ]
                    for row, raw in zip(chain, raw_evidence, strict=True)
                ],
            ]
        )
        held()
    return verified


async def _uid_ledger_checkpoint(session, scope, held, selected):
    currencies = tuple(
        (
            await session.execute(
                select(
                    QualificationAccountScope.settlement_currency,
                    QualificationAccountScope.account_revision,
                    QualificationAccountScope.ledger_revision,
                    QualificationAccountScope.claims_sha256,
                    QualificationAccountScope.updated_at,
                )
                .where(
                    QualificationAccountScope.environment == scope.environment,
                    QualificationAccountScope.account_id == scope.account_id,
                )
                .order_by(QualificationAccountScope.settlement_currency)
                .limit(_CURRENCY_LIMIT + 1)
            )
        ).all()
    )
    held()
    _need(
        0 < len(currencies) <= _CURRENCY_LIMIT
        and (
            scope.settlement_currency,
            selected.account_revision,
            selected.ledger_revision,
            selected.claims_sha256,
            selected.updated_at,
        )
        in currencies
    )
    unresolved, consumed = await _unresolved_under_uid_lock(session, scope, held)
    verified = await _replay_consumed_under_uid_lock(
        session, scope, held, currencies, unresolved
    )
    _need(len(verified) == consumed)
    checkpoint = {
        "schema": "ctcc.demo.db0056.uid_ledger_checkpoint.v2",
        "currencies": [
            [
                currency,
                account_revision,
                ledger_revision,
                claims,
                updated_at.isoformat(),
            ]
            for currency, account_revision, ledger_revision, claims, updated_at in currencies
        ],
        "unresolved": [list(row) for row in unresolved],
        "verified_consumed_intents": verified,
    }
    return len(unresolved), consumed, sha(canonical(checkpoint))


async def read_locked_native_ledger_preflight_0056_v1(
    selected, roles, session, scope: LedgerScope
) -> NativeLedgerPreflight0056V1:
    """Inspect existing local state while keeping caller transaction locks.

    Start with a fresh transaction from the configured restricted writer. The
    caller must roll back on refusal. Historical rows and an empty result carry
    no current replay, economic, portfolio, flat-start or execution authority.
    """
    try:
        scope = checked_bootstrap(scope, LedgerScope)
        _need(scope.settlement_currency == "USDT")
        writer, engine, _, _ = locked._factory_pins(selected)
        transaction = session.sync_session.get_transaction()
        locked._require_session(session, engine, transaction)
        _need(
            all(
                session.get_bind(clause=statement) is engine.sync_engine
                for statement in (_READ_COMMITTED, _JOURNAL_FENCE, _ISSUED_FENCE)
            )
            and all(
                session.get_bind(mapper=model) is engine.sync_engine
                for model in _MODELS
            )
        )
        await session.execute(_READ_COMMITTED)
        repo = ledger.QualificationLedgerRepository(
            writer, clock=lambda: datetime.now(UTC)
        )
        control_scope = ControlScope(scope.environment, scope.account_id)
        async with locked._verified_locked_demo_control_0056(
            selected, session, control_scope
        ) as proof:
            control_row, catalog, witness, started_at, held = proof
            _require_role_witness(roles, catalog, witness)
            state, event = await locked.DemoControlRepositoryV2._snapshot(
                session, control_row
            )
            held()
            _need(state.scope == control_scope)
            await locked.DemoControlRepositoryV2.verify_historical_binding(
                session, state, event
            )
            held()
            row = await repo._locked(session, scope)
            held()
            await session.execute(_JOURNAL_FENCE)
            held()
            await session.execute(_ISSUED_FENCE)
            held()
            repeated = (
                await locked.writer56.observe_restricted_readwrite_writer_0056_v1(
                    session
                )
            )
            held()
            _require_role_witness(roles, repeated, witness)
            _need(
                repeated.native_definitions_sha256 == catalog.native_definitions_sha256
            )
            await ledger._require_event_journal_session(session)
            held()
            schema = await ledger._read_event_journal_schema_rows(session)
            held()
            ledger._check_event_journal_schema_rows(
                *schema,
                {
                    **ledger._EVENT_GUARD_FUNCTIONS,
                    closure.FUNCTION: (
                        closure.CURRENT_BODY_SHA256,
                        ("search_path=pg_catalog, pg_temp",),
                    ),
                },
            )
            scope_state = await _replay_historical_scope(
                session, repo, scope, row, held
            )
            unresolved, consumed, uid_checkpoint = await _uid_ledger_checkpoint(
                session, scope, held, row
            )
            _need(scope_state != "BOOTSTRAP" or unresolved == 0)
            observed_at = await session.scalar(locked._CLOCK)
            held()
            _need(observed_at >= max(started_at, state.updated_at, row.updated_at))
            return NativeLedgerPreflight0056V1(
                state.revision,
                event,
                state.emergency_stop,
                state.arm_request_id is not None,
                row.account_revision,
                row.ledger_revision,
                scope_state,
                unresolved,
                consumed,
                *witness,
                catalog.native_definitions_sha256,
                uid_checkpoint,
                observed_at,
            )
    except (NativeLedgerPreflight0056Error, asyncio.CancelledError):
        raise
    except Exception:  # noqa: BLE001 -- redact private database or control detail.
        _need(False)
