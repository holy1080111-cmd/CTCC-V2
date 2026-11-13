"""Persist and reopen the exact configured Demo account's restart stop."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from typing import Literal

from app.core import native_locked_demo_control_0056_v1 as locked
from app.core.native_stopped_control_genesis_0056_v1 import _require_role_witness
from app.demo_automation import native_server_selectors_v2 as selectors
from app.exchange.okx.private_rest import OkxDemoPrivateRestClient
from app.trade_qualification import demo_control as control


class NativeAccountRestartRecovery0056Error(ValueError):
    """Redacted refusal without private account state or database details."""


class NativeAccountRestartRecovery0056Uncertain(NativeAccountRestartRecovery0056Error):
    """An attempted commit or its readback requires independent observation."""


@dataclass(frozen=True, slots=True)
class NativeAccountRestartRecovery0056V1:
    status: Literal["STOP_PERSISTED", "STOP_OBSERVED"]
    revision: int
    control_event_sha256: str
    database_head: Literal["0056"] = field(default="0056", init=False)
    emergency_stop: Literal[True] = field(default=True, init=False)
    armed: Literal[False] = field(default=False, init=False)
    positions_reconciled: Literal[False] = field(default=False, init=False)
    startup_admission: Literal[False] = field(default=False, init=False)
    execution_authority: Literal[False] = field(default=False, init=False)
    admission: Literal["DENY"] = field(default="DENY", init=False)


def _need(value: bool) -> None:
    if value is not True:
        raise NativeAccountRestartRecovery0056Error(
            "db0056_account_restart_recovery_refused"
        ) from None


def _uncertain() -> None:
    raise NativeAccountRestartRecovery0056Uncertain(
        "db0056_account_restart_recovery_uncertain_reconcile_only"
    ) from None


def _stopped(state: control.ControlState) -> bool:
    return (
        state.emergency_stop is True
        and state.arm_request_id is None
        and state.arm_expires_at is None
    )


async def _stop_locked(session, factories, roles, scope):
    async with locked._verified_locked_demo_control_0056(
        factories, session, scope
    ) as proof:
        row, catalog, witness, started_at, check_held = proof
        _require_role_witness(roles, catalog, witness)
        before, previous_event = await locked.DemoControlRepositoryV2._snapshot(
            session, row
        )
        check_held()
        _need(before.scope == scope)
        if _stopped(before):
            return False, before, previous_event
        observed_at = await session.scalar(locked._CLOCK)
        check_held()
        _need(observed_at >= started_at and observed_at >= before.updated_at)
        command = control.digest(
            control.canonical(
                ["ctcc.demo.account_restart_recovery.0056.v1", previous_event]
            )
        )
        stop = replace(
            before,
            revision=before.revision + 1,
            emergency_stop=True,
            arm_request_id=None,
            arm_expires_at=None,
            lease_until=max(observed_at, before.lease_until),
            updated_at=observed_at,
        )
        _need(
            _stopped(stop)
            and stop.revision == before.revision + 1
            and stop.owner_epoch == before.owner_epoch
            and stop.owner_sha256 == before.owner_sha256
            and stop.pins == before.pins
        )
        event = await locked.DemoControlRepository._store(
            session,
            row,
            stop,
            command_id=command,
            action="estop",
            previous_sha256=previous_event,
        )
        check_held()
        await locked.DemoControlRepositoryV2.verify_historical_binding(
            session, stop, event
        )
        check_held()
        return True, stop, event


async def _observe_stop(factories, roles, scope):
    async with factories.writer_factory() as session, session.begin():
        proof = await locked.read_locked_demo_control_0056_v1(factories, session, scope)
        _require_role_witness(
            roles,
            proof.writer_catalog,
            (proof.server_incarnation_sha256, proof.role_route_sha256),
        )
        observation = proof.control
        _need(
            observation.state.scope == scope
            and _stopped(observation.state)
            and proof.issuance_authority is proof.execution_authority is False
        )
        return observation


async def _recover_selected(selection, *, reconcile_only: bool):
    selected = selectors._record(selection)
    factories = selected["factories"]
    roles = selection.source_roles_0056
    scope = control.ControlScope("demo", selection.scope_config.expected_uid)
    expected = None
    attempted = False
    try:
        if not reconcile_only:
            async with factories.writer_factory() as session:
                tx = await session.begin()
                changed, state, event = await _stop_locked(
                    session, factories, roles, scope
                )
                _need(selectors._record(selection) is selected)
                expected = (state, event)
                if changed:
                    attempted = True
                    await tx.commit()
                else:
                    await tx.rollback()
        readback = await _observe_stop(factories, roles, scope)
        _need(selectors._record(selection) is selected)
        actual = (readback.state, readback.event_sha256)
        _need(expected is None or actual == expected)
        return NativeAccountRestartRecovery0056V1(
            "STOP_PERSISTED" if attempted else "STOP_OBSERVED",
            readback.state.revision,
            readback.event_sha256,
        )
    except (Exception, asyncio.CancelledError):
        if attempted:
            _uncertain()
        raise


async def recover_configured_account_restart_0056_v1(
    instrument_id: str, *, reconcile_only: bool
) -> NativeAccountRestartRecovery0056V1:
    """Use the original signed selector; external position checks stay separate."""
    from app.demo_automation.native_source_only_v1 import _dispose, _settings

    _need(type(reconcile_only) is bool)
    selection = factories = result = None
    failure = False
    try:
        settings = _settings(instrument_id)
        client = OkxDemoPrivateRestClient(settings=settings)
        with client.pinned_read_session() as lease:
            selection = await selectors.select_configured_demo_source_identity_0056_v1(
                private_client=client,
                read_session=lease,
                instrument_ids=(instrument_id,),
            )
            factories = selectors._SELECTIONS[selection]["factories"]
            result = await _recover_selected(selection, reconcile_only=reconcile_only)
    except (NativeAccountRestartRecovery0056Error, asyncio.CancelledError):
        failure = True
        raise
    except Exception:  # noqa: BLE001 -- redact private source and route data
        failure = True
        if result is not None and result.status == "STOP_PERSISTED":
            _uncertain()
        _need(False)
    finally:
        try:
            await _dispose(selection, factories)
        except (Exception, asyncio.CancelledError):  # noqa: BLE001 -- preserve primary outcome
            if not failure:
                if result is not None and result.status == "STOP_PERSISTED":
                    _uncertain()
                _need(False)
    return result
