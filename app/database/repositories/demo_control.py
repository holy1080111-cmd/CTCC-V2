"""Demo control transitions under the existing qualification UID lock.

Successful commit plus independent-session readback records only control intent.
There is no dependency on legacy Arm state or any execution/credential client.
"""

import asyncio
import json
from dataclasses import dataclass, field, replace

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.models.demo_control import DemoAccountControl, DemoControlJournal
from app.database.repositories.public_runtime_bundle_custody import _native_engine
from app.trade_qualification.demo_control import (
    MAX_CONTROL_LINEAGE_BYTES,
    MAX_CONTROL_LINEAGE_ROWS,
    ControlJournalSourceRowV1,
    ControlLineageV1,
    ControlScope,
    ControlState,
    DemoControlError,
    account_lock_key,
    acquire,
    advance,
    canonical,
    checked,
    decode,
    digest,
    document,
    owner_binding,
    replay_control_journal_lineage_v1,
    sha,
    stopped_genesis,
    utc,
)


@dataclass(frozen=True, slots=True, repr=False)
class ControlObservation:
    state: ControlState
    event_sha256: str
    observed_at: object

    def __post_init__(self):
        checked(self.state, ControlState)
        sha(self.event_sha256)
        utc(self.observed_at)
        if self.observed_at < self.state.updated_at:
            raise DemoControlError("control_observation_clock_regressed")

    @property
    def execution_authority(self):
        return False


@dataclass(frozen=True, slots=True, repr=False)
class ControlLineageReadbackV1:
    """Actual separate-transaction observation; not an owned Control session."""

    lineage: ControlLineageV1 = field(repr=False)
    current_state: ControlState = field(repr=False)
    current_event_sha256: str
    receipt_json: str = field(repr=False)
    current_lineage: ControlLineageV1 = field(repr=False)
    execution_authority: bool = field(default=False, init=False)

    @property
    def receipt_sha256(self):
        return digest(self.receipt_json)


class DemoControlRepository:
    def __init__(self, session_factory, *, clock):
        self.session_factory = session_factory
        self.clock = clock

    @staticmethod
    async def _locked(session, scope):
        checked(scope, ControlScope)
        if session.get_bind().dialect.name != "postgresql":
            raise DemoControlError("control_postgresql_required")
        # Byte-for-byte identical key and advisory-before-row ordering to DB0017.
        await session.execute(
            select(func.pg_advisory_xact_lock(account_lock_key(scope)))
        )
        return await session.scalar(
            select(DemoAccountControl)
            .filter_by(environment=scope.environment, account_id=scope.account_id)
            .with_for_update()
        )

    @classmethod
    async def read_in_transaction(cls, session, scope):
        """Current control under this transaction's UID then control-row lock.

        Does not open/commit another session. Used before the qualification scope
        row lock; the caller retains both locks through its journal transition.
        The returned state remains evidence, never dispatch authority.
        """
        if not session.in_transaction():
            raise DemoControlError("control_active_transaction_required")
        row = await cls._locked(session, scope)
        state, event_sha256 = await cls._snapshot(session, row)
        # The new binding additionally compares canonical typed journal bytes;
        # Python dictionary equality alone conflates nested bool/int values.
        await cls.verify_historical_binding(session, state, event_sha256)
        return state, event_sha256

    @staticmethod
    async def _read_lineage_prefix(session, state, event_sha256):
        """Read every original row, with a count/byte bound before bulk fetch."""
        checked(state, ControlState)
        sha(event_sha256)
        if state.revision > MAX_CONTROL_LINEAGE_ROWS:
            raise DemoControlError("control_lineage_complete_prefix_bound")
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
            or not 1 <= byte_count <= MAX_CONTROL_LINEAGE_BYTES
        ):
            raise DemoControlError("control_lineage_complete_prefix_bound")
        models = tuple(
            (
                await session.scalars(
                    select(DemoControlJournal)
                    .where(*predicate)
                    .order_by(DemoControlJournal.control_revision)
                    .limit(MAX_CONTROL_LINEAGE_ROWS + 1)
                )
            ).all()
        )
        if len(models) != count or any(
            type(row) is not DemoControlJournal for row in models
        ):
            raise DemoControlError("control_lineage_source_rows_invalid")
        rows = tuple(
            ControlJournalSourceRowV1(
                ControlScope(row.environment, row.account_id),
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
        if any(type(row.event_json) is not str for row in rows):
            raise DemoControlError("control_lineage_source_rows_invalid")
        try:
            actual_bytes = sum(len(row.event_json.encode("ascii")) for row in rows)
        except UnicodeError:
            raise DemoControlError("control_lineage_source_bytes_invalid") from None
        if actual_bytes != byte_count:
            raise DemoControlError("control_lineage_source_bytes_changed")
        return replay_control_journal_lineage_v1(
            rows,
            scope=state.scope,
            target_state=state,
            expected_head_sha256=event_sha256,
        )

    @staticmethod
    async def verify_historical_binding(session, state, event_sha256):
        """Verify a full immutable prefix, including genuine earlier owner epochs."""
        try:
            return await DemoControlRepository._read_lineage_prefix(
                session, state, event_sha256
            )
        except DemoControlError:
            raise DemoControlError("control_historical_binding_invalid") from None

    @staticmethod
    async def _snapshot(session, row):
        if row is None:
            raise DemoControlError("control_scope_missing")
        state = decode(row.state_json)
        if row.state_sha256 != digest(row.state_json) or (
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
            raise DemoControlError("control_row_binding_invalid")
        event = await session.get(
            DemoControlJournal, (row.environment, row.account_id, row.control_revision)
        )
        if event is None:
            raise DemoControlError("control_journal_missing")
        try:
            record = json.loads(event.event_json)
            if (
                canonical(record) != event.event_json
                or event.event_sha256 != digest(event.event_json)
                or event.state_sha256 != row.state_sha256
                or event.occurred_at != state.updated_at
                or record
                != {
                    "version": "ctcc.demo_control_event.v1",
                    "command_id": event.command_id,
                    "action": event.action,
                    "previous_sha256": event.previous_sha256,
                    "state": document(state),
                }
            ):
                raise DemoControlError("control_journal_binding_invalid")
        except (TypeError, ValueError):
            raise DemoControlError("control_journal_binding_invalid") from None
        prior = (
            await session.get(
                DemoControlJournal,
                (row.environment, row.account_id, row.control_revision - 1),
            )
            if row.control_revision > 1
            else None
        )
        if (None if prior is None else prior.event_sha256) != event.previous_sha256:
            raise DemoControlError("control_journal_chain_invalid")
        return state, event.event_sha256

    @staticmethod
    async def _store(session, row, state, *, command_id, action, previous_sha256):
        sha(command_id)
        raw = canonical(document(state))
        values = {
            "environment": state.scope.environment,
            "account_id": state.scope.account_id,
            "control_revision": state.revision,
            "owner_sha256": state.owner_sha256,
            "owner_epoch": state.owner_epoch,
            "lease_until": state.lease_until,
            "emergency_stop": state.emergency_stop,
            "arm_request_id": state.arm_request_id,
            "arm_expires_at": state.arm_expires_at,
            "state_json": raw,
            "state_sha256": digest(raw),
            "created_at": state.created_at,
            "updated_at": state.updated_at,
        }
        if row is None:
            row = DemoAccountControl(**values)
            session.add(row)
        else:
            for key, value in values.items():
                setattr(row, key, value)
        await session.flush()
        event_json = canonical(
            {
                "version": "ctcc.demo_control_event.v1",
                "command_id": command_id,
                "action": action,
                "previous_sha256": previous_sha256,
                "state": document(state),
            }
        )
        event_sha256 = digest(event_json)
        session.add(
            DemoControlJournal(
                environment=state.scope.environment,
                account_id=state.scope.account_id,
                control_revision=state.revision,
                command_id=command_id,
                action=action,
                previous_sha256=previous_sha256,
                event_sha256=event_sha256,
                state_sha256=digest(raw),
                event_json=event_json,
                occurred_at=state.updated_at,
            )
        )
        await session.flush()
        return event_sha256

    async def read(self, scope):
        async with self.session_factory() as session, session.begin():
            row = await self._locked(session, scope)
            state, event_sha256 = await self._snapshot(session, row)
            return ControlObservation(state, event_sha256, utc(self.clock()))

    def _native_lineage_factory_pin(self):
        factory = self.session_factory
        if (
            type(factory) is not async_sessionmaker
            or factory.class_ is not AsyncSession
            or type(factory.kw) is not dict
            or set(factory.kw) != {"bind", "autoflush", "expire_on_commit"}
            or type(factory.kw["autoflush"]) is not bool
            or type(factory.kw["expire_on_commit"]) is not bool
            or not _native_engine(factory.kw["bind"])
        ):
            raise DemoControlError("control_lineage_native_factory_required")
        return (
            factory,
            factory.kw["bind"],
            factory.kw["autoflush"],
            factory.kw["expire_on_commit"],
        )

    async def _read_native_lineage_transaction(self, scope, through_revision):
        pin = self._native_lineage_factory_pin()
        async with pin[0]() as session, session.begin():
            if type(session) is not AsyncSession:
                raise DemoControlError("control_lineage_native_session_required")
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
                raise DemoControlError("control_lineage_native_isolation_required")
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
                    state.revision, MAX_CONTROL_LINEAGE_ROWS
                ):
                    raise DemoControlError("control_lineage_target_revision_invalid")
                original = await session.get(
                    DemoControlJournal,
                    (scope.environment, scope.account_id, through_revision),
                )
                if original is None:
                    raise DemoControlError("control_lineage_target_revision_missing")
                value = json.loads(original.event_json)
                target, target_sha = (
                    decode(canonical(value["state"])),
                    original.event_sha256,
                )
                if target.scope != scope or target.revision != through_revision:
                    raise DemoControlError("control_lineage_target_revision_conflict")
            lineage = (
                current_lineage
                if through_revision is None
                else replay_control_journal_lineage_v1(
                    current_lineage.source_rows[:through_revision],
                    scope=scope,
                    target_state=target,
                    expected_head_sha256=target_sha,
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
                or utc(observed_at) < state.updated_at
                or self._native_lineage_factory_pin() != pin
            ):
                raise DemoControlError("control_lineage_native_readback_invalid")
        if self._native_lineage_factory_pin() != pin:
            raise DemoControlError("control_lineage_native_factory_changed")
        return (
            lineage,
            state,
            event_sha256,
            (pid, transaction, observed_at),
            current_lineage,
        )

    async def _read_lineage_v1(self, scope, *, through_revision=None):
        """Capture actual full prefix and exact independent-session readback.

        This performs no Control mutation and no automatic retry on a raced
        current head. Historical prefixes never become current owner permits.
        """
        checked(scope, ControlScope)
        if through_revision is not None and (
            type(through_revision) is not int
            or not 1 <= through_revision <= MAX_CONTROL_LINEAGE_ROWS
        ):
            raise DemoControlError("control_lineage_target_revision_invalid")
        original_pin = self._native_lineage_factory_pin()
        first = await self._read_native_lineage_transaction(scope, through_revision)
        if self._native_lineage_factory_pin() != original_pin:
            raise DemoControlError("control_lineage_native_factory_changed")
        second = await self._read_native_lineage_transaction(
            scope, first[0].state.revision
        )
        if self._native_lineage_factory_pin() != original_pin:
            raise DemoControlError("control_lineage_native_factory_changed")
        if (
            first[0].receipt_json != second[0].receipt_json
            or first[4].receipt_json != second[4].receipt_json
            or canonical(document(first[1])) != canonical(document(second[1]))
            or first[2] != second[2]
            or first[3][1] == second[3][1]
            or second[3][2] < first[3][2]
        ):
            raise DemoControlError("control_lineage_independent_readback_conflict")
        receipt = canonical(
            {
                "schema_version": "ctcc.demo_control_native_lineage_readback.v1",
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
        return ControlLineageReadbackV1(
            second[0], second[1], second[2], receipt, second[4]
        )

    async def read_lineage(self, scope, *, through_revision=None):
        """Privacy-safe entry; a failed read is not retried or made authoritative."""
        try:
            return await self._read_lineage_v1(scope, through_revision=through_revision)
        except asyncio.CancelledError:
            raise
        except DemoControlError:
            raise
        except Exception:  # noqa: BLE001 -- Driver/source parameters must stay private.
            raise DemoControlError("control_lineage_native_read_unavailable") from None

    def _now_after(self, previous):
        now = utc(self.clock())
        if now < previous:
            raise DemoControlError("control_commit_clock_regressed")
        return now

    async def _readback(self, scope, state, event_sha256, committed_not_before):
        result = await self.read(scope)
        if (
            result.state != state
            or result.event_sha256 != event_sha256
            or result.observed_at < committed_not_before
        ):
            raise DemoControlError("control_commit_readback_conflict")
        return result

    async def acquire(self, scope, *, owner_token, pins, command_id):
        owner_sha256 = owner_binding(owner_token)
        # Every mutation commits, closes that session, then reads through another.
        async with self.session_factory() as session, session.begin():
            row = await self._locked(session, scope)
            previous, previous_sha = (
                await self._snapshot(session, row) if row else (None, None)
            )
            state = acquire(
                previous,
                scope=scope,
                owner_sha256=owner_sha256,
                pins=pins,
                now=utc(self.clock()),
            )
            event_sha = await self._store(
                session,
                row,
                state,
                command_id=command_id,
                action="acquire",
                previous_sha256=previous_sha,
            )
            before_commit = self._now_after(state.updated_at)
        return await self._readback(
            scope, state, event_sha, self._now_after(before_commit)
        )

    async def change(
        self,
        scope,
        *,
        owner_token,
        owner_epoch,
        expected_revision,
        action,
        command_id,
        arm_expires_at=None,
        pins=None,
    ):
        owner_sha256 = owner_binding(owner_token)
        async with self.session_factory() as session, session.begin():
            row = await self._locked(session, scope)
            previous, previous_sha = await self._snapshot(session, row)
            state = advance(
                previous,
                owner_sha256=owner_sha256,
                owner_epoch=owner_epoch,
                expected_revision=expected_revision,
                action=action,
                command_id=command_id,
                now=utc(self.clock()),
                arm_expires_at=arm_expires_at,
                pins=pins,
            )
            event_sha = await self._store(
                session,
                row,
                state,
                command_id=command_id,
                action=action,
                previous_sha256=previous_sha,
            )
            before_commit = self._now_after(state.updated_at)
        return await self._readback(
            scope, state, event_sha, self._now_after(before_commit)
        )

    async def latch_stop(self, scope, *, command_id):
        """Safety tightening can stop a newer/expired owner; never grants ownership.

        This deliberately does not compare a caller's stale revision: concurrent
        Arm must not make an operator's EStop disappear through an optimistic-lock
        conflict. A subsequently running owner observes revocation on readback.
        """
        return await self._tighten(scope, command_id=command_id, action="estop")

    async def revoke_arm(self, scope, *, command_id):
        return await self._tighten(scope, command_id=command_id, action="disarm")

    async def _tighten(self, scope, *, command_id, action):
        async with self.session_factory() as session, session.begin():
            row = await self._locked(session, scope)
            now = utc(self.clock())
            if row is None and action == "estop":
                previous_sha = None
                state = stopped_genesis(scope, now)
            else:
                previous, previous_sha = await self._snapshot(session, row)
                if now < previous.updated_at:
                    raise DemoControlError("control_clock_regressed")
                state = replace(
                    previous,
                    revision=previous.revision + 1,
                    emergency_stop=previous.emergency_stop or action == "estop",
                    arm_request_id=None,
                    arm_expires_at=None,
                    lease_until=max(now, previous.lease_until),
                    updated_at=now,
                )
            event_sha = await self._store(
                session,
                row,
                state,
                command_id=command_id,
                action=action,
                previous_sha256=previous_sha,
            )
            before_commit = self._now_after(state.updated_at)
        return await self._readback(
            scope, state, event_sha, self._now_after(before_commit)
        )

    async def clear_stop(self, scope, **_untrusted):
        checked(scope, ControlScope)
        raise DemoControlError("control_trusted_reconciliation_producer_unavailable")
