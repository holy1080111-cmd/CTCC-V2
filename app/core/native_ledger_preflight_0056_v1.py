"""Observe the DB0056 reservation journal under its real UID and SQL fences."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import select, text

from app.core import native_locked_demo_control_0056_v1 as locked
from app.core.native_stopped_control_genesis_0056_v1 import _require_role_witness
from app.database.models.account_issued_revision import (
    DemoCollectorIssuedAccountRevision,
)
from app.database.models.qualification_ledger import (
    QualificationAccountScope,
    QualificationReservation,
    QualificationReservationTransition,
)
from app.database.repositories import qualification_ledger as ledger
from app.database.repositories import qualification_native_checkpoint_v2 as checkpoint
from app.domain import native_filled_flat_closure_schema_v55 as closure
from app.trade_qualification import control_bound_ledger
from app.trade_qualification.demo_control import ControlScope
from app.trade_qualification.reservations import LedgerScope, checked_bootstrap

_READ_COMMITTED = text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
_JOURNAL_FENCE = text(
    "LOCK TABLE public.qualification_account_scopes, "
    "public.qualification_reservations, public.qualification_reservation_transitions "
    "IN SHARE MODE NOWAIT"
)
_ISSUED_FENCE = text(
    "LOCK TABLE public.demo_collector_issued_account_revisions IN ACCESS SHARE MODE NOWAIT"
)
_MODELS = (
    QualificationAccountScope,
    QualificationReservation,
    QualificationReservationTransition,
    DemoCollectorIssuedAccountRevision,
)
_EVENT_LIMIT = 2048


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
                    QualificationReservationTransition.evidence_json.is_not(None),
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
        has_evidence,
    ) in rows:
        _need(type(revision) is int)
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
                (reason == "consumed" and has_evidence is False)
                or (
                    reason
                    in {
                        control_bound_ledger.CONSUMED_REASON,
                        "consumed_with_submit_intent",
                    }
                    and has_evidence is True
                )
            )
        )
        if has_evidence:
            intents += 1
    return len(rows), intents


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
            unresolved, consumed = await _unresolved_under_uid_lock(
                session, scope, held
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
            )
    except (NativeLedgerPreflight0056Error, asyncio.CancelledError):
        raise
    except Exception:  # noqa: BLE001 -- redact private database or control detail.
        _need(False)
