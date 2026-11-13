"""Exact configured DB0056 Control lifecycle and independent readback."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace

from sqlalchemy import text

from app.core import native_locked_demo_control_0056_v1 as locked
from app.core.native_role_witness_0056_v1 import RestrictedNativeRoles0056WitnessV1
from app.database.models.demo_control import DemoAccountControl
from app.database.repositories import account_revision_collector_lineage as lineage
from app.database.repositories.demo_control import (
    ControlObservation,
    DemoControlRepository,
)
from app.database.repositories.demo_control_current55_v2 import DemoControlRepositoryV2
from app.demo_automation.native_server_selectors_v2 import (
    ConfiguredNativeDemoFactoriesV2,
)
from app.trade_qualification import demo_control as control

_SYNC = text("SET LOCAL synchronous_commit = 'on'")
_FENCE = text(
    "LOCK TABLE public.demo_account_controls, public.demo_control_journal, "
    "public.demo_control_clear_acknowledgements_v2 "
    "IN ROW EXCLUSIVE MODE NOWAIT"
)


class DemoControlRepository0056Error(control.DemoControlError):
    """Fixed denial without role, account or journal contents."""


class DemoControlRepository0056Uncertain(DemoControlRepository0056Error):
    """Commit or independent readback must be reconciled."""


def _need(condition: bool) -> None:
    if condition is not True:
        raise DemoControlRepository0056Error(
            "native_control_repository_0056_refused"
        ) from None


@dataclass(frozen=True, slots=True, repr=False)
class _Read:
    state: control.ControlState
    event: str
    metadata: tuple
    witness: tuple[str, str]

    def result(self):
        return ControlObservation(self.state, self.event, self.metadata[9])


class DemoControlRepository0056V1:
    """Account-scoped policy repository, with DB authorization and timing held.

    Arm and clear-stop remain owned by their independent application paths.
    This repository reuses the deployed trigger, canonical transition and full
    lifetime replay for disarmed ownership and safety tightening only.
    """

    def __init__(self, selected, *, roles, scope, clock, expected_acquire_event=None):
        _need(
            type(selected) is ConfiguredNativeDemoFactoriesV2
            and type(roles) is RestrictedNativeRoles0056WitnessV1
            and callable(clock)
        )
        self._selected = selected
        self._factory = selected.writer_factory
        self._roles = roles
        self._scope = control.checked(scope, control.ControlScope)
        self._clock = clock
        self._expected_acquire_event = (
            None
            if expected_acquire_event is None
            else control.sha(expected_acquire_event)
        )

    @property
    def session_factory(self):
        return self._factory

    @property
    def clock(self):
        return self._clock

    def _check_scope(self, scope):
        _need(control.checked(scope, control.ControlScope) == self._scope)
        _need(self._selected.writer_factory is self._factory)

    @asynccontextmanager
    async def _held(self, session, scope, *, mutate):
        self._check_scope(scope)
        _, engine, _, _ = locked._factory_pins(self._selected)
        locked._require_session(session, engine, session.sync_session.get_transaction())
        _need(
            all(
                session.get_bind(clause=statement) is engine.sync_engine
                for statement in (_SYNC, _FENCE, lineage._META_SQL)
            )
        )
        await session.execute(_SYNC)
        async with locked._verified_locked_demo_control_0056(
            self._selected, session, scope
        ) as pin:
            row, catalog, witness, started_at, check = pin
            await locked._compare_held_role_pins(
                self._selected, session, self._roles, catalog, witness, check
            )
            if mutate:
                await session.execute(_FENCE)
                check()
                _need(
                    await locked.writer56.observe_restricted_readwrite_writer_0056_v1(
                        session
                    )
                    == catalog
                )
                check()
            state, event = await DemoControlRepositoryV2._snapshot(session, row)
            check()
            metadata = await lineage._metadata(session)
            check()
            _need(
                type(row) is DemoAccountControl
                and state.scope == scope
                and started_at <= metadata[9]
                and state.updated_at <= metadata[9]
            )
            yield row, _Read(state, event, metadata, witness), check
            check()
            if mutate:
                _need(
                    await locked.writer56.observe_restricted_readwrite_writer_0056_v1(
                        session
                    )
                    == catalog
                )
                check()
            self._check_scope(scope)

    async def _read(self, scope):
        async with (
            self._factory() as session,
            session.begin(),
            self._held(session, scope, mutate=False) as (_, read, _),
        ):
            return read

    async def read(self, scope):
        try:
            return (await self._read(scope)).result()
        except (asyncio.CancelledError, DemoControlRepository0056Error):
            raise
        except Exception:  # noqa: BLE001 -- protect account and database details.
            _need(False)

    async def verify_native_deployment_v2(self):
        # Scope is already selected. Obtain a full current lineage from the
        # actual readwrite database, with the selected role and UID locks.
        return await self.read(self._scope)

    async def _mutate(self, scope, *, action, command_id, transition):
        attempted = False
        try:
            control.sha(command_id)
            self._check_scope(scope)
            async with self._factory() as session:
                tx = await session.begin()
                async with self._held(session, scope, mutate=True) as (
                    row,
                    before,
                    check,
                ):
                    if action == "acquire" and self._expected_acquire_event is not None:
                        _need(before.event == self._expected_acquire_event)
                    state = transition(before.state, before.metadata[9])
                    _need(
                        state.scope == scope
                        and state.revision == before.state.revision + 1
                        and state.arm_request_id is state.arm_expires_at is None
                        and not (
                            before.state.emergency_stop and not state.emergency_stop
                        )
                    )
                    event = await DemoControlRepository._store(
                        session,
                        row,
                        state,
                        command_id=command_id,
                        action=action,
                        previous_sha256=before.event,
                    )
                    check()
                    exact = await DemoControlRepositoryV2._snapshot(session, row)
                    check()
                    _need(exact == (state, event))
                    after = await lineage._metadata(session)
                    check()
                    _need(
                        after[:9] == before.metadata[:9]
                        and after[9] >= state.updated_at
                        and (
                            action not in {"acquire", "renew"}
                            or after[9] < state.lease_until
                        )
                    )
                self._check_scope(scope)
                attempted = True
                await tx.commit()
            actual = await self._read(scope)
            _need(
                actual.state == state
                and actual.event == event
                and actual.witness == before.witness
                and actual.metadata[:7] == after[:7]
                and actual.metadata[8] != after[8]
                and actual.metadata[9] >= after[9]
                and (
                    action not in {"acquire", "renew"}
                    or actual.metadata[9] < state.lease_until
                )
            )
            return actual.result()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 -- no SQL, endpoint or private record output.
            if attempted:
                raise DemoControlRepository0056Uncertain(
                    "native_control_repository_0056_uncertain"
                ) from None
            _need(False)

    async def acquire(self, scope, *, owner_token, pins, command_id):
        return await self._mutate(
            scope,
            action="acquire",
            command_id=command_id,
            transition=lambda previous, now: control.acquire(
                previous,
                scope=scope,
                owner_sha256=control.owner_binding(owner_token),
                pins=pins,
                now=now,
            ),
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
        _need(type(action) is str and action in {"renew", "release", "disarm", "estop"})
        _need(arm_expires_at is None and pins is None)

        def transition(previous, now):
            # A separate established owner must revoke a retained Arm before
            # renew. This slice can never extend an existing trading window.
            _need(action != "renew" or previous.arm_request_id is None)
            return control.advance(
                previous,
                owner_sha256=control.owner_binding(owner_token),
                owner_epoch=owner_epoch,
                expected_revision=expected_revision,
                action=action,
                command_id=command_id,
                now=now,
            )

        return await self._mutate(
            scope, action=action, command_id=command_id, transition=transition
        )

    async def _tighten(self, scope, *, command_id, stop):
        return await self._mutate(
            scope,
            action="estop" if stop else "disarm",
            command_id=command_id,
            transition=lambda previous, now: replace(
                previous,
                revision=previous.revision + 1,
                emergency_stop=previous.emergency_stop or stop,
                arm_request_id=None,
                arm_expires_at=None,
                lease_until=max(previous.lease_until, now),
                updated_at=now,
            ),
        )

    async def latch_stop(self, scope, *, command_id):
        return await self._tighten(scope, command_id=command_id, stop=True)

    async def revoke_arm(self, scope, *, command_id):
        return await self._tighten(scope, command_id=command_id, stop=False)

    async def clear_stop(self, _scope, **_untrusted):
        _need(False)
