"""Held DB0056 preconditions for an internal qualification writer."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import ClassVar, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import native_locked_demo_control_0056_v1 as locked
from app.core.native_role_witness_0056_v1 import RestrictedNativeRoles0056WitnessV1
from app.database.models.account_issued_revision import (
    DemoCollectorIssuedAccountRevision,
)
from app.database.models.qualification_ledger import QualificationAccountScope
from app.database.repositories import account_revision_collector_lineage as lineage
from app.database.repositories import qualification_ledger as ledger
from app.database.repositories.qualification_native_checkpoint_v2 import (
    _finite_payload,
    _validate_held_finite_checkpoint_rows,
)
from app.domain import native_filled_flat_closure_schema_v55 as closure
from app.domain.source_primitives import canonical, decode, sha
from app.trade_qualification import control_bound_ledger as bound
from app.trade_qualification import demo_control as control
from app.trade_qualification.reservations import LedgerScope, checked_bootstrap

_SETUP = (
    text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED"),
    text("SET LOCAL synchronous_commit = 'on'"),
)
_CONTROL_FENCE = text(
    "LOCK TABLE public.demo_account_controls, public.demo_control_journal, "
    "public.demo_control_clear_acknowledgements_v2, "
    "public.demo_collector_current_revision_lineages IN ACCESS SHARE MODE NOWAIT"
)
_WRITER_FENCE = text(
    "LOCK TABLE public.qualification_account_scopes, "
    "public.qualification_reservations, public.qualification_reservation_transitions, "
    "public.demo_collector_issued_account_revisions IN ROW EXCLUSIVE MODE NOWAIT"
)


class NativeQualificationWriter0056Error(ValueError):
    """Fixed refusal without SQL, account or credential values."""


def _need(value: bool) -> None:
    if value is not True:
        raise NativeQualificationWriter0056Error(
            "native_qualification_writer_0056_refused"
        ) from None


class QualificationWritePurpose0056(Enum):
    ISSUE_ACCOUNT_REVISION = "issue_account_revision"
    RESERVE_QUALIFICATION = "reserve_qualification"


@dataclass(frozen=True, slots=True, repr=False)
class QualificationWriterPins0056V1:
    control: bound.ControlExpectation
    account_revision: int
    ledger_revision: int
    claims_sha256: str | None
    expires_at: datetime

    def __post_init__(self):
        bound.freeze_expectation(self.control)
        control.utc(self.expires_at)
        _need(
            type(self.account_revision) is type(self.ledger_revision) is int
            and 0 <= self.account_revision <= self.ledger_revision < 2**63 - 1
            and (self.account_revision == 0) == (self.claims_sha256 is None)
        )
        if self.claims_sha256 is not None:
            control.sha(self.claims_sha256)


@dataclass(frozen=True, slots=True, repr=False)
class QualificationWriterTransaction0056V1:
    role_oid: int
    backend_pid: int
    transaction_id: str
    observed_at: datetime


@dataclass(frozen=True, slots=True, repr=False)
class HeldQualificationWriter0056V1:
    """Transaction witness for the named operation only."""

    expectation: QualificationWriterPins0056V1
    observation: locked.NativeLockedDemoControl0056V1
    transaction: QualificationWriterTransaction0056V1
    purpose: QualificationWritePurpose0056
    _check: Callable[[], None]
    _recheck: Callable[[], Awaitable[datetime]]
    _accept_issue: Callable[[str, str], Awaitable[datetime]]
    _issue_row: QualificationAccountScope | None
    issuance_authority: ClassVar[Literal[False]] = False
    execution_authority: ClassVar[Literal[False]] = False
    admission: ClassVar[Literal["DENY"]] = "DENY"

    def check_held(self):
        self._check()

    async def recheck(self) -> datetime:
        return await self._recheck()

    async def recheck_issued(
        self, *, issuer_sha256: str, claims_sha256: str
    ) -> datetime:
        return await self._accept_issue(issuer_sha256, claims_sha256)

    def _scope_row_for_issue(self) -> QualificationAccountScope:
        self.check_held()
        _need(
            self.purpose is QualificationWritePurpose0056.ISSUE_ACCOUNT_REVISION
            and type(self._issue_row) is QualificationAccountScope
        )
        return self._issue_row


def _scope_values(row, scope: LedgerScope, *, now: datetime):
    _need(
        type(row) is QualificationAccountScope
        and (row.environment, row.account_id, row.settlement_currency)
        == (scope.environment, scope.account_id, scope.settlement_currency)
        and type(row.account_revision) is type(row.ledger_revision) is int
        and 0 <= row.account_revision <= row.ledger_revision < 2**63
        and (row.account_revision == 0) == (row.claims_json is None)
        and (row.claims_json is None) == (row.claims_sha256 is None)
        and control.utc(row.created_at) <= control.utc(row.updated_at) <= now
    )
    if row.claims_json is not None:
        _need(
            type(row.claims_json) is str
            and 0 < len(row.claims_json) <= 8 * 1024 * 1024
            and sha(row.claims_json.encode()) == row.claims_sha256
        )
    return row.account_revision, row.ledger_revision, row.claims_sha256


async def _actual_transaction(session, held):
    (
        _,
        _,
        _,
        role_oid,
        _,
        _,
        _,
        backend_pid,
        xact_id,
        observed_at,
    ) = await lineage._metadata(session)
    held()
    _need(
        type(role_oid) is type(backend_pid) is int
        and role_oid > 0
        and backend_pid > 0
        and type(xact_id) is str
        and xact_id.isascii()
        and xact_id.isdecimal()
        and 0 < int(xact_id) < 2**64
        and type(observed_at) is datetime
        and observed_at.tzinfo is UTC
    )
    return QualificationWriterTransaction0056V1(
        role_oid, backend_pid, xact_id, observed_at
    )


async def _verify_journal(session, held):
    await ledger._require_event_journal_session(session)
    held()
    rows = await ledger._read_event_journal_schema_rows(session)
    held()
    ledger._check_event_journal_schema_rows(
        *rows,
        {
            **ledger._EVENT_GUARD_FUNCTIONS,
            closure.FUNCTION: (
                closure.CURRENT_BODY_SHA256,
                ("search_path=pg_catalog, pg_temp",),
            ),
        },
    )


def _same_transaction(left, right):
    return (left.role_oid, left.backend_pid, left.transaction_id) == (
        right.role_oid,
        right.backend_pid,
        right.transaction_id,
    )


def _check_issued_row(issued, row, expected, writer, control_state, accepted, now):
    _need(type(issued) is DemoCollectorIssuedAccountRevision)
    issuer_sha, claims_sha = accepted
    proof = decode(issued.proof_json.encode(), 1024 * 1024)
    state_sha = control.digest(control.canonical(control.document(control_state)))
    _need(
        type(proof) is dict
        and canonical(proof) == issued.proof_json.encode()
        and sha(issued.proof_json.encode()) == issuer_sha == issued.issuer_sha256
        and issued.claims_sha256 == claims_sha == row.claims_sha256
        and issued.claims_json == row.claims_json
        and (issued.environment, issued.account_id, issued.settlement_currency)
        == (row.environment, row.account_id, row.settlement_currency)
        and (
            issued.previous_account_revision,
            issued.previous_ledger_revision,
            issued.previous_claims_sha256,
        )
        == (expected.account_revision, expected.ledger_revision, expected.claims_sha256)
        and (issued.account_revision, issued.ledger_revision)
        == (row.account_revision, row.ledger_revision)
        and issued.control_revision == expected.control.control_revision
        and issued.control_event_sha256 == expected.control.event_sha256
        and issued.control_state_sha256 == state_sha
        and proof.get("db_role_oid") == writer.role_oid
        and proof.get("db_backend_pid") == writer.backend_pid
        and proof.get("db_transaction_id") == writer.transaction_id
        and issued.original_expires_at == expected.expires_at
        and writer.observed_at
        <= control.utc(issued.db_recorded_at)
        <= now
        < expected.expires_at
        and writer.observed_at <= row.updated_at <= issued.db_recorded_at
    )


@asynccontextmanager
async def held_native_qualification_writer_0056_v1(
    selected,
    session: AsyncSession,
    scope: LedgerScope,
    *,
    roles: RestrictedNativeRoles0056WitnessV1,
    purpose: QualificationWritePurpose0056,
    expected: QualificationWriterPins0056V1,
):
    """Hold the exact account, Control, SQL schema and live owner pins.

    Start in a fresh configured writer transaction. The caller owns rollback
    and separate committed readback. No source, Arm, or order is created here.
    """
    try:
        _need(
            type(roles) is RestrictedNativeRoles0056WitnessV1
            and type(purpose) is QualificationWritePurpose0056
            and type(expected) is QualificationWriterPins0056V1
        )
        expected.__post_init__()
        expected = QualificationWriterPins0056V1(
            bound.freeze_expectation(expected.control),
            expected.account_revision,
            expected.ledger_revision,
            expected.claims_sha256,
            expected.expires_at,
        )
        scope = checked_bootstrap(scope, LedgerScope)
        control_scope = control.ControlScope(scope.environment, scope.account_id)
        _need(
            scope.settlement_currency == "USDT"
            and expected.control.scope == control_scope
        )
        factory, engine, _, _ = locked._factory_pins(selected)
        locked._require_session(session, engine, session.sync_session.get_transaction())
        _need(
            all(
                session.get_bind(clause=stmt) is engine.sync_engine
                for stmt in (*_SETUP, _CONTROL_FENCE, _WRITER_FENCE, lineage._META_SQL)
            )
            and all(
                session.get_bind(mapper=model) is engine.sync_engine
                for model in (
                    QualificationAccountScope,
                    DemoCollectorIssuedAccountRevision,
                )
            )
        )
        for stmt in _SETUP:
            await session.execute(stmt)
        async with locked.held_native_writer_control_0056_v1(
            selected,
            session,
            control_scope,
            roles=roles,
            purpose=locked.NativeWriterControlPurpose0056.PREPARE_OBSERVATION,
        ) as held:
            original = held.observation.control
            repo = ledger.QualificationLedgerRepository(
                factory, clock=lambda: datetime.now(UTC)
            )
            row = await repo._locked(session, scope)
            held.check_held()
            for stmt in (_WRITER_FENCE, _CONTROL_FENCE):
                await session.execute(stmt)
                held.check_held()
            catalog = await locked.writer56.observe_restricted_readwrite_writer_0056_v1(
                session
            )
            held.check_held()
            _need(catalog == held.observation.writer_catalog)
            await _verify_journal(session, held.check_held)
            opened = await _actual_transaction(session, held.check_held)
            frontier = opened.observed_at
            accepted_issue = None
            issue_checked = False
            previous = (
                expected.account_revision,
                expected.ledger_revision,
                expected.claims_sha256,
            )

            async def verify(*, own_issue=None):
                nonlocal frontier
                held.check_held()
                state, event = await locked.DemoControlRepositoryV2._snapshot(
                    session,
                    await locked.DemoControlRepository._locked(session, control_scope),
                )
                held.check_held()
                await session.refresh(row)
                held.check_held()
                issued = None
                if own_issue is not None:
                    issued = await session.get(
                        DemoCollectorIssuedAccountRevision,
                        own_issue[0],
                        populate_existing=True,
                    )
                    held.check_held()
                writer = await _actual_transaction(session, held.check_held)
                now = writer.observed_at
                _need(
                    _same_transaction(opened, writer)
                    and original.observed_at <= frontier <= now < expected.expires_at
                    and state.arm_expires_at is not None
                    and expected.expires_at
                    <= min(state.arm_expires_at, state.lease_until)
                    and (state, event) == (original.state, original.event_sha256)
                )
                bound.guard_current_control(
                    state, event, expected.control, scope, now=now
                )
                coordinates = _scope_values(row, scope, now=now)
                if own_issue is None:
                    _need(coordinates == previous)
                else:
                    _need(
                        coordinates == (previous[0] + 1, previous[1] + 1, own_issue[1])
                    )
                    _check_issued_row(
                        issued, row, expected, opened, state, own_issue, now
                    )
                frontier = now
                return now

            async def recheck():
                _need(not issue_checked or accepted_issue is not None)
                return await verify(own_issue=accepted_issue)

            def held_at_frontier():
                held.check_held()
                return frontier

            async def recheck_issued(issuer_sha256, claims_sha256):
                nonlocal accepted_issue, issue_checked
                held.check_held()
                _need(purpose is QualificationWritePurpose0056.ISSUE_ACCOUNT_REVISION)
                if issue_checked:
                    accepted_issue = None
                    _need(False)
                issue_checked = True
                own_issue = control.sha(issuer_sha256), control.sha(claims_sha256)
                await verify(own_issue=own_issue)
                _need(_finite_payload(row.claims_json))
                await _validate_held_finite_checkpoint_rows(
                    session, scope, row, check=held_at_frontier
                )
                now = await verify(own_issue=own_issue)
                accepted_issue = own_issue
                return now

            if _finite_payload(row.claims_json):
                await _validate_held_finite_checkpoint_rows(
                    session, scope, row, check=held_at_frontier
                )
            else:
                _need(purpose is QualificationWritePurpose0056.ISSUE_ACCOUNT_REVISION)
                if row.account_revision:
                    _need(repo._claims(row).scope == scope)
            await recheck()
            yield HeldQualificationWriter0056V1(
                expected,
                held.observation,
                opened,
                purpose,
                held.check_held,
                recheck,
                recheck_issued,
                row
                if purpose is QualificationWritePurpose0056.ISSUE_ACCOUNT_REVISION
                else None,
            )
            _need(
                await locked.writer56.observe_restricted_readwrite_writer_0056_v1(
                    session
                )
                == catalog
            )
            held.check_held()
            await recheck()
    except (NativeQualificationWriter0056Error, asyncio.CancelledError):
        raise
    except Exception:  # noqa: BLE001 -- redact driver, binding and ownership details.
        _need(False)
