"""Exact current55 Control reads, preserving historical54 APIs and denial.

Only this versioned repository reads the v2 clear-stop journal/action and its
immutable acknowledgements. The old DemoControlRepository is unchanged.
Native operator writers are added below only through opaque source hooks;
ordinary DTO-based clear_stop remains denied by the inherited API.
"""

import asyncio
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.demo_control import DemoAccountControl, DemoControlJournal
from app.database.models.demo_operator_acknowledgements_v55 import (
    DemoControlClearAcknowledgementV2 as ClearRow,
)
from app.database.repositories import (
    collector_current_0055_multirole_catalog_v1 as current55,
)
from app.database.repositories.demo_control import (
    DemoControlRepository as HistoricalControlRepository,
)
from app.trade_qualification import demo_control as legacy
from app.trade_qualification import demo_control_clear_v2 as policy

_SOURCE_KEY = object()


def _deny(code):
    raise legacy.DemoControlError(code) from None


def _validated_clear_row_v2(row):
    if type(row) is not ClearRow:
        _deny("control55_exact_clear_row_required")
    ack = policy.decode_clear_ack_v2(row.ack_json)
    state = legacy.decode(row.state_json)
    event = policy.clear_event_document_v2(
        state,
        command_id=ack.command_id,
        previous_sha256=ack.stopped_event_sha256,
        ack_sha256=ack.ack_sha256,
    )
    if (
        (
            row.ack_sha256,
            row.environment,
            row.account_id,
            row.settlement_currency,
            row.command_id,
            row.stopped_control_revision,
            row.stopped_event_sha256,
            row.stopped_state_sha256,
            row.clear_control_revision,
            row.clear_state_sha256,
            row.clear_event_sha256,
            row.source_lineage_sha256,
            row.account_revision,
            row.ledger_revision,
            row.claims_sha256,
            row.owner_sha256,
            row.owner_epoch,
            row.original_lease_until,
            row.owner_sampled_at,
            row.original_expires_at,
            row.ack_json,
            row.state_json,
            row.event_json,
        )
        != (
            ack.ack_sha256,
            ack.scope.environment,
            ack.scope.account_id,
            ack.settlement_currency,
            ack.command_id,
            ack.stopped_control_revision,
            ack.stopped_event_sha256,
            ack.stopped_state_sha256,
            ack.stopped_control_revision + 1,
            ack.clear_state_sha256,
            legacy.digest(legacy.canonical(event)),
            ack.source_lineage_sha256,
            ack.account_revision,
            ack.ledger_revision,
            ack.claims_sha256,
            ack.owner_sha256,
            ack.owner_epoch,
            ack.original_lease_until,
            ack.owner_sampled_at,
            ack.original_expires_at,
            legacy.canonical(policy.clear_ack_document_v2(ack)),
            legacy.canonical(legacy.document(state)),
            legacy.canonical(event),
        )
        or legacy.digest(row.state_json) != ack.clear_state_sha256
        or state.scope != ack.scope
        or state.revision != ack.stopped_control_revision + 1
        or state.owner_sha256 != ack.owner_sha256
        or state.owner_epoch != ack.owner_epoch
        or state.lease_until != ack.original_lease_until
        or state.emergency_stop is not False
        or state.arm_request_id is not None
        or state.arm_expires_at is not None
        or type(row.db_recorded_at) is not datetime
        or row.db_recorded_at.tzinfo is not UTC
        or not ack.owner_sampled_at <= row.db_recorded_at < ack.original_expires_at
    ):
        _deny("control55_clear_actual_columns_changed")
    return ack


@dataclass(frozen=True, slots=True, repr=False)
class ControlLineageReadbackV2:
    lineage: policy.ControlLineageV2 = field(repr=False)
    current_state: legacy.ControlState = field(repr=False)
    current_event_sha256: str
    receipt_json: str = field(repr=False)
    current_lineage: policy.ControlLineageV2 = field(repr=False)
    execution_authority: bool = field(default=False, init=False)

    @property
    def receipt_sha256(self):
        return legacy.digest(self.receipt_json)


class DemoControlRepositoryV2(HistoricalControlRepository):
    """Same UID locks and original transactions; explicit head55 admission."""

    async def verify_native_deployment_v2(self):
        async with self.session_factory() as session, session.begin():
            return await current55.verify_actual_control_catalog_v2(session)

    @staticmethod
    async def _locked(session, scope):
        # An actual head/schema/role query chooses this exact contract. No
        # identity.json marker or caller head/pass flag participates.
        await session.execute(
            text("SET LOCAL search_path TO pg_catalog, public, pg_temp")
        )
        await session.execute(text("SET LOCAL TIME ZONE 'UTC'"))
        await session.execute(text("SET LOCAL lock_timeout='1000ms'"))
        await session.execute(text("SET LOCAL statement_timeout='1000ms'"))
        await current55.verify_actual_control_catalog_v2(session)
        return await HistoricalControlRepository._locked(session, scope)

    @staticmethod
    async def _read_lineage_prefix(session, state, event_sha256):
        legacy.checked(state, legacy.ControlState)
        legacy.sha(event_sha256)
        if state.revision > legacy.MAX_CONTROL_LINEAGE_ROWS:
            _deny("control55_complete_prefix_bound")
        predicate = (
            DemoControlJournal.environment == state.scope.environment,
            DemoControlJournal.account_id == state.scope.account_id,
            DemoControlJournal.control_revision <= state.revision,
        )
        count, byte_count = (
            await session.execute(
                select(
                    func.count(),
                    func.coalesce(
                        func.sum(func.octet_length(DemoControlJournal.event_json)), 0
                    ),
                ).where(*predicate)
            )
        ).one()
        if (
            type(count) is not int
            or count != state.revision
            or type(byte_count) is not int
            or not 1 <= byte_count <= legacy.MAX_CONTROL_LINEAGE_BYTES
        ):
            _deny("control55_complete_prefix_bound")
        models = tuple(
            (
                await session.scalars(
                    select(DemoControlJournal)
                    .where(*predicate)
                    .order_by(DemoControlJournal.control_revision)
                    .limit(legacy.MAX_CONTROL_LINEAGE_ROWS + 1)
                )
            ).all()
        )
        if len(models) != count or any(
            type(row) is not DemoControlJournal for row in models
        ):
            _deny("control55_complete_prefix_row_type")
        rows = tuple(
            legacy.ControlJournalSourceRowV1(
                legacy.ControlScope(row.environment, row.account_id),
                row.control_revision,
                row.command_id,
                row.action,
                row.previous_sha256,
                row.event_sha256,
                row.state_sha256,
                row.event_json,
                row.occurred_at,
            )
            for row in models
        )
        if sum(len(row.event_json.encode("ascii")) for row in rows) != byte_count:
            _deny("control55_complete_prefix_bytes_changed")
        ack_predicate = (
            ClearRow.environment == state.scope.environment,
            ClearRow.account_id == state.scope.account_id,
            ClearRow.clear_control_revision <= state.revision,
        )
        ack_count, ack_bytes = (
            await session.execute(
                select(
                    func.count(),
                    func.coalesce(func.sum(func.octet_length(ClearRow.ack_json)), 0),
                ).where(*ack_predicate)
            )
        ).one()
        if (
            type(ack_count) is not int
            or not 0 <= ack_count <= count
            or type(ack_bytes) is not int
            or ack_bytes < 0
            or byte_count + ack_bytes > legacy.MAX_CONTROL_LINEAGE_BYTES
        ):
            _deny("control55_ack_prefix_bound")
        saved = tuple(
            (
                await session.scalars(
                    select(ClearRow)
                    .where(*ack_predicate)
                    .order_by(ClearRow.clear_control_revision)
                    .limit(legacy.MAX_CONTROL_LINEAGE_ROWS + 1)
                )
            ).all()
        )
        if len(saved) != ack_count:
            _deny("control55_ack_prefix_count_changed")
        acknowledgements = tuple(_validated_clear_row_v2(row) for row in saved)
        if sum(len(row.ack_json.encode("ascii")) for row in saved) != ack_bytes:
            _deny("control55_ack_prefix_bytes_changed")
        return policy.replay_control_journal_lineage_v2(
            rows,
            scope=state.scope,
            target_state=state,
            expected_head_sha256=event_sha256,
            clear_ack_rows=acknowledgements,
        )

    @staticmethod
    async def verify_historical_binding(session, state, event_sha256):
        return await DemoControlRepositoryV2._read_lineage_prefix(
            session, state, event_sha256
        )

    @staticmethod
    async def _snapshot(session, row):
        if type(row) is not DemoAccountControl:
            _deny("control_scope_missing")
        state = legacy.decode(row.state_json)
        if row.state_sha256 != legacy.digest(row.state_json) or (
            row.environment,
            row.account_id,
            row.control_revision,
            row.owner_sha256,
            row.owner_epoch,
            row.lease_until,
            row.emergency_stop,
            row.arm_request_id,
            row.arm_expires_at,
            row.created_at,
            row.updated_at,
        ) != (
            state.scope.environment,
            state.scope.account_id,
            state.revision,
            state.owner_sha256,
            state.owner_epoch,
            state.lease_until,
            state.emergency_stop,
            state.arm_request_id,
            state.arm_expires_at,
            state.created_at,
            state.updated_at,
        ):
            _deny("control55_row_binding_invalid")
        event = await session.get(
            DemoControlJournal, (row.environment, row.account_id, row.control_revision)
        )
        if type(event) is not DemoControlJournal:
            _deny("control55_current_journal_missing")
        value = json.loads(event.event_json)
        expected = {
            "version": "ctcc.demo_control_event.v1",
            "command_id": event.command_id,
            "action": event.action,
            "previous_sha256": event.previous_sha256,
            "state": legacy.document(state),
        }
        if event.action == "clear_stop":
            saved = await session.scalar(
                select(ClearRow).where(
                    ClearRow.environment == row.environment,
                    ClearRow.account_id == row.account_id,
                    ClearRow.clear_control_revision == row.control_revision,
                )
            )
            ack = _validated_clear_row_v2(saved)
            expected = policy.clear_event_document_v2(
                state,
                command_id=event.command_id,
                previous_sha256=event.previous_sha256,
                ack_sha256=ack.ack_sha256,
            )
        if (
            legacy.canonical(value) != event.event_json
            or value != expected
            or legacy.digest(event.event_json) != event.event_sha256
            or event.state_sha256 != row.state_sha256
            or event.occurred_at != state.updated_at
        ):
            _deny("control55_current_event_binding_invalid")
        await DemoControlRepositoryV2._read_lineage_prefix(
            session, state, event.event_sha256
        )
        return state, event.event_sha256

    @classmethod
    async def read_in_transaction(cls, session, scope):
        if cls is not DemoControlRepositoryV2 or not session.in_transaction():
            _deny("control55_actual_transaction_required")
        row = await cls._locked(session, scope)
        return await cls._snapshot(session, row)

    async def _read_native_lineage_transaction(self, scope, through_revision):
        pin = self._native_lineage_factory_pin()
        async with pin[0]() as session, session.begin():
            if type(session) is not AsyncSession:
                raise legacy.DemoControlError("control_lineage_native_session_required")
            await session.execute(text("SET LOCAL lock_timeout='1000ms'"))
            await session.execute(text("SET LOCAL statement_timeout='1000ms'"))
            await session.execute(
                text("SET LOCAL search_path TO pg_catalog, public, pg_temp")
            )
            await session.execute(text("SET LOCAL TIME ZONE 'UTC'"))
            if (
                await session.scalar(
                    text("SELECT current_setting('transaction_isolation')")
                )
                != "read committed"
            ):
                raise legacy.DemoControlError(
                    "control_lineage_native_isolation_required"
                )
            row = await self._locked(session, scope)
            state, event_sha256 = await self._snapshot(session, row)
            # Even a historical request must validate the actual current full
            # lifetime. A historical prefix cannot conceal a malformed tail.
            current_lineage = await self._read_lineage_prefix(
                session, state, event_sha256
            )
            target, target_sha = state, event_sha256
            if through_revision is not None:
                if type(
                    through_revision
                ) is not int or not 1 <= through_revision <= min(
                    state.revision, legacy.MAX_CONTROL_LINEAGE_ROWS
                ):
                    raise legacy.DemoControlError(
                        "control_lineage_target_revision_invalid"
                    )
                original = await session.get(
                    DemoControlJournal,
                    (scope.environment, scope.account_id, through_revision),
                )
                if original is None:
                    raise legacy.DemoControlError(
                        "control_lineage_target_revision_missing"
                    )
                value = json.loads(original.event_json)
                target, target_sha = (
                    legacy.decode(legacy.canonical(value["state"])),
                    original.event_sha256,
                )
                if target.scope != scope or target.revision != through_revision:
                    raise legacy.DemoControlError(
                        "control_lineage_target_revision_conflict"
                    )
            lineage = (
                current_lineage
                if through_revision is None
                else policy.replay_control_journal_lineage_v2(
                    current_lineage.source_rows[:through_revision],
                    scope=scope,
                    target_state=target,
                    expected_head_sha256=target_sha,
                    clear_ack_rows=tuple(
                        ack
                        for ack in current_lineage.clear_ack_rows
                        if ack.stopped_control_revision + 1 <= through_revision
                    ),
                )
            )
            pid, transaction, observed_at = (
                await session.execute(
                    text(
                        "SELECT pg_catalog.pg_backend_pid(),pg_catalog.txid_current()::text,pg_catalog.clock_timestamp()"
                    )
                )
            ).one()
            if (
                type(pid) is not int
                or pid < 1
                or type(transaction) is not str
                or not transaction.isdigit()
                or legacy.utc(observed_at) < state.updated_at
                or self._native_lineage_factory_pin() != pin
            ):
                raise legacy.DemoControlError("control_lineage_native_readback_invalid")
        if self._native_lineage_factory_pin() != pin:
            raise legacy.DemoControlError("control_lineage_native_factory_changed")
        return (
            lineage,
            state,
            event_sha256,
            (pid, transaction, observed_at),
            current_lineage,
        )

    async def _read_lineage_v2(self, scope, *, through_revision=None):
        """Capture actual full prefix and exact independent-session readback.

        This performs no Control mutation and no automatic retry on a raced
        current head. Historical prefixes never become current owner permits.
        """
        legacy.checked(scope, legacy.ControlScope)
        if through_revision is not None and (
            type(through_revision) is not int
            or not 1 <= through_revision <= legacy.MAX_CONTROL_LINEAGE_ROWS
        ):
            raise legacy.DemoControlError("control_lineage_target_revision_invalid")
        original_pin = self._native_lineage_factory_pin()
        first = await self._read_native_lineage_transaction(scope, through_revision)
        if self._native_lineage_factory_pin() != original_pin:
            raise legacy.DemoControlError("control_lineage_native_factory_changed")
        second = await self._read_native_lineage_transaction(
            scope, first[0].state.revision
        )
        if self._native_lineage_factory_pin() != original_pin:
            raise legacy.DemoControlError("control_lineage_native_factory_changed")
        if (
            first[0].receipt_json != second[0].receipt_json
            or first[4].receipt_json != second[4].receipt_json
            or legacy.canonical(legacy.document(first[1]))
            != legacy.canonical(legacy.document(second[1]))
            or first[2] != second[2]
            or first[3][1] == second[3][1]
            or second[3][2] < first[3][2]
        ):
            raise legacy.DemoControlError(
                "control_lineage_independent_readback_conflict"
            )
        receipt = legacy.canonical(
            {
                "schema_version": "ctcc.demo_control_native_lineage_readback.v2",
                "lineage_receipt_sha256": second[0].receipt_sha256,
                "current_complete_lineage_receipt_sha256": second[4].receipt_sha256,
                "current_revision": second[1].revision,
                "current_event_sha256": second[2],
                "first": {
                    "backend_pid": first[3][0],
                    "transaction_id": first[3][1],
                    "database_observed_at": first[3][2].isoformat(),
                },
                "readback": {
                    "backend_pid": second[3][0],
                    "transaction_id": second[3][1],
                    "database_observed_at": second[3][2].isoformat(),
                },
                "execution_authority": False,
                "control_mutated": False,
            }
        )
        return ControlLineageReadbackV2(
            second[0], second[1], second[2], receipt, second[4]
        )

    async def read_lineage(self, scope, *, through_revision=None):
        """Privacy-safe entry; a failed read is not retried or made authoritative."""
        try:
            return await self._read_lineage_v2(scope, through_revision=through_revision)
        except asyncio.CancelledError:
            raise
        except legacy.DemoControlError:
            raise
        except Exception:  # noqa: BLE001 -- Driver/source parameters must stay private.
            raise legacy.DemoControlError(
                "control_lineage_native_read_unavailable"
            ) from None
