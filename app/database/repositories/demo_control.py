"""Demo control transitions under the existing qualification UID lock.

Successful commit plus independent-session readback records only control intent.
There is no dependency on legacy Arm state or any execution/credential client.
"""

import json
from dataclasses import dataclass, replace

from sqlalchemy import func, select

from app.database.models.demo_control import DemoAccountControl, DemoControlJournal
from app.trade_qualification.demo_control import (
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
    async def verify_historical_binding(session, state, event_sha256):
        """Replay an immutable past control event; does not require current Arm."""
        checked(state, ControlState)
        sha(event_sha256)
        event = await session.get(
            DemoControlJournal,
            (state.scope.environment, state.scope.account_id, state.revision),
        )
        expected_state = canonical(document(state))
        if event is None or (
            event.event_sha256 != event_sha256
            or digest(event.event_json) != event_sha256
            or event.state_sha256 != digest(expected_state)
            or event.occurred_at != state.updated_at
            or event.event_json
            != canonical(
                {
                    "version": "ctcc.demo_control_event.v1",
                    "command_id": event.command_id,
                    "action": event.action,
                    "previous_sha256": event.previous_sha256,
                    "state": document(state),
                }
            )
        ):
            raise DemoControlError("control_historical_binding_invalid")
        prior = (
            await session.get(
                DemoControlJournal,
                (state.scope.environment, state.scope.account_id, state.revision - 1),
            )
            if state.revision > 1
            else None
        )
        if (None if prior is None else prior.event_sha256) != event.previous_sha256:
            raise DemoControlError("control_historical_chain_invalid")

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
