"""Replay stored policy-counter data under the original DB0056 account lock."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from sqlalchemy import select

from app.core import native_locked_demo_control_0056_v1 as locked
from app.core.native_stopped_control_genesis_0056_v1 import _require_role_witness
from app.database.models.demo_control import DemoControlJournal
from app.database.models.demo_operator_acknowledgements_v55 import (
    DemoPolicyCounterInitializationV1 as CounterRow,
)
from app.database.repositories import (
    demo_operator_acknowledgements_current55_v1 as original,
)
from app.domain.source_primitives import canonical, sha
from app.trade_qualification.demo_control import ControlScope, ControlState
from app.trade_qualification.reservations import LedgerScope


class NativePolicyCounterReadback0056Error(ValueError):
    """Fixed refusal without account contents, credentials, or SQL."""


@dataclass(frozen=True, slots=True, repr=False)
class NativePolicyCounterObservation0056V1:
    status: Literal["ABSENT", "OBSERVED_UNVERIFIED"]
    control_revision: int
    control_event_sha256: str
    counter_row_sha256: str | None
    observation_sha256: str
    database_head: Literal["0056"] = field(default="0056", init=False)
    source_authenticity_verified: Literal[False] = field(default=False, init=False)
    account_complete: Literal[False] = field(default=False, init=False)
    execution_authority: Literal[False] = field(default=False, init=False)
    admission: Literal["DENY"] = field(default="DENY", init=False)


@dataclass(frozen=True, slots=True, repr=False)
class _Read:
    state: ControlState
    event_sha256: str
    row: bytes | None
    transaction_id: str
    observed_at: datetime


@dataclass(frozen=True, slots=True, repr=False)
class _RetainedPolicy:
    selected_settings_json: bytes


@dataclass(frozen=True, slots=True, repr=False)
class _RetainedComparison:
    configured_policy: _RetainedPolicy
    original_measurement: bytes


def _need(condition: bool) -> None:
    if condition is not True:
        raise NativePolicyCounterReadback0056Error(
            "native_policy_counter_0056_readback_refused"
        ) from None


async def _read_counter_row(session, scope, state, check_held) -> bytes | None:
    rows = tuple(
        (
            await session.scalars(
                select(CounterRow)
                .where(
                    CounterRow.environment == scope.environment,
                    CounterRow.account_id == scope.account_id,
                    CounterRow.settlement_currency == scope.settlement_currency,
                )
                .limit(2)
            )
        ).all()
    )
    check_held()
    _need(len(rows) <= 1)
    if not rows:
        return None

    row = rows[0]
    _need(type(row) is CounterRow)
    # The existing validator checks canonical codecs, original timestamp and
    # byte identities. Here both comparison operands come from the stored row,
    # so the result only establishes its internal consistency.
    stored = _RetainedComparison(
        _RetainedPolicy(row.configured_policy_json.encode("ascii")),
        row.measured_genesis_json.encode("ascii"),
    )
    ack = original._validate_counter_row(row, stored)
    await _verify_row_control(session, scope, state, ack, check_held)
    return original._row_raw(row, original._COUNTER_COLUMNS)


async def _verify_row_control(session, scope, state, ack, check_held) -> None:
    _need(
        ack.scope == state.scope
        and ack.settlement_currency == scope.settlement_currency
        and ack.control_revision <= state.revision
    )
    event = await session.get(
        DemoControlJournal,
        (scope.environment, scope.account_id, ack.control_revision),
    )
    check_held()
    # The caller already replayed the full Control prefix. Compare the ACK's
    # historical reference to that actual immutable row, not the latest state.
    _need(
        type(event) is DemoControlJournal
        and event.event_sha256 == ack.control_event_sha256
        and event.state_sha256 == ack.control_state_sha256
    )


async def _read_once(factories, roles, scope, control_scope) -> _Read:
    async with (
        factories.writer_factory() as session,
        session.begin(),
        locked._verified_locked_demo_control_0056(
            factories, session, control_scope
        ) as proof,
    ):
        root, catalog, witness, started_at, check_held = proof
        _require_role_witness(roles, catalog, witness)
        _need(
            session.get_bind(mapper=CounterRow) is session.bind.sync_engine
            and session.get_bind(clause=original._META_SQL) is session.bind.sync_engine
        )
        state, event = await locked.DemoControlRepositoryV2._snapshot(session, root)
        check_held()
        _need(state.scope == control_scope)
        row = await _read_counter_row(session, scope, state, check_held)
        metadata = await original._metadata(session)
        check_held()
        _need(metadata[9] >= started_at and metadata[9] >= state.updated_at)
        return _Read(state, event, row, metadata[8], metadata[9])


async def observe_native_policy_counter_0056_v1(
    factories, roles, scope: LedgerScope
) -> NativePolicyCounterObservation0056V1:
    """Reopen the exact stored row twice, preserving an unknown effective counter.

    The two PostgreSQL transactions use the caller's original restricted
    factories and the original UID lock. A missing row is observed absence.
    A retained row still needs independent policy, genesis and source proofs.
    Neither finding changes the policy counter or admits a following action.
    """
    try:
        _need(
            type(scope) is LedgerScope
            and scope.environment == "demo"
            and scope.settlement_currency == "USDT"
        )
        control_scope = ControlScope(scope.environment, scope.account_id)
        first = await _read_once(factories, roles, scope, control_scope)
        second = await _read_once(factories, roles, scope, control_scope)
        _need(
            first.state == second.state
            and first.event_sha256 == second.event_sha256
            and first.row == second.row
            and first.transaction_id != second.transaction_id
            and first.observed_at <= second.observed_at
        )
        digest = sha(second.row) if second.row is not None else None
        status = "ABSENT" if digest is None else "OBSERVED_UNVERIFIED"
        observation = canonical(
            {
                "schema": "ctcc.demo.policy_counter_observation.0056.v1",
                "control_event_sha256": second.event_sha256,
                "control_revision": second.state.revision,
                "counter_row_sha256": digest,
                "first_transaction_id": first.transaction_id,
                "second_transaction_id": second.transaction_id,
                "status": status,
            }
        )
        return NativePolicyCounterObservation0056V1(
            status, second.state.revision, second.event_sha256, digest, sha(observation)
        )
    except (asyncio.CancelledError, NativePolicyCounterReadback0056Error):
        raise
    except Exception:  # noqa: BLE001 -- redact source values and database details.
        _need(False)
