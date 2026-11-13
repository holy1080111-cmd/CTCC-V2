"""Explicit account-scoped stopped Control genesis for restricted DB0056."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Literal

from app.core import native_locked_demo_control_0056_v1 as locked
from app.core.native_role_witness_0056_v1 import RestrictedNativeRoles0056WitnessV1
from app.demo_automation import native_server_selectors_v2 as selectors
from app.exchange.okx.private_rest import OkxDemoPrivateRestClient
from app.trade_qualification.demo_control import (
    ControlScope,
    canonical,
    digest,
    document,
    stopped_genesis,
)


class NativeStoppedControlGenesis0056Error(ValueError):
    """Fixed refusal or uncertainty, without SQL, account data, or credentials."""


@dataclass(frozen=True, slots=True)
class StoppedControlGenesis0056V1:
    status: Literal["CREATED_STOPPED", "EXISTING_STOPPED"]
    control_event_sha256: str
    database_head: Literal["0056"] = field(default="0056", init=False)
    control_revision: Literal[1] = field(default=1, init=False)
    emergency_stop: Literal[True] = field(default=True, init=False)
    armed: Literal[False] = field(default=False, init=False)
    execution_authority: Literal[False] = field(default=False, init=False)
    admission: Literal["DENY"] = field(default="DENY", init=False)


def _need(condition: bool) -> None:
    if condition is not True:
        raise NativeStoppedControlGenesis0056Error(
            "db0056_stopped_control_genesis_refused"
        ) from None


def _command_id(scope):
    # Stable across an operator restart and unique to this explicit purpose and
    # exact UID. The underlying journal already scopes command IDs per account.
    return digest(
        canonical(
            ["ctcc.native_demo.stopped_genesis.0056.v1", "demo", scope.account_id]
        )
    )


def _exact_genesis_event(scope, state):
    _need(state == stopped_genesis(scope, state.created_at))
    return digest(
        canonical(
            {
                "version": "ctcc.demo_control_event.v1",
                "command_id": _command_id(scope),
                "action": "estop",
                "previous_sha256": None,
                "state": document(state),
            }
        )
    )


def _uncertain(stage: Literal["commit", "readback", "cleanup"]) -> None:
    raise NativeStoppedControlGenesis0056Error(
        f"db0056_stopped_control_{stage}_uncertain_reconcile_only"
    ) from None


def _require_role_witness(roles, catalog, witness) -> None:
    _need(
        type(roles) is RestrictedNativeRoles0056WitnessV1
        and roles.observation.database_head == "0056"
        and roles.observation.admission == "DENY"
        and roles.startup_admission is roles.execution_authority is False
        and catalog.native_definitions_sha256
        == roles.observation.inherited_native_definitions_sha256
        and witness == (roles.server_incarnation_sha256, roles.role_route_sha256)
    )


async def _stage_genesis(session, factories, roles, scope):
    async with locked._verified_locked_demo_control_0056(
        factories, session, scope
    ) as proof:
        row, catalog, witness, started_at, require_held_transaction = proof
        _require_role_witness(roles, catalog, witness)
        if row is not None:
            state, event_sha = await locked.DemoControlRepositoryV2._snapshot(
                session, row
            )
            require_held_transaction()
            _need(event_sha == _exact_genesis_event(scope, state))
            return False, state, event_sha

        now = await session.scalar(locked._CLOCK)
        require_held_transaction()
        _need(now >= started_at)
        state = stopped_genesis(scope, now)
        event_sha = await locked.DemoControlRepository._store(
            session,
            None,
            state,
            command_id=_command_id(scope),
            action="estop",
            previous_sha256=None,
        )
        require_held_transaction()
        _need(event_sha == _exact_genesis_event(scope, state))
        return True, state, event_sha


async def _read_committed_genesis(factories, roles, scope):
    async with factories.writer_factory() as session, session.begin():
        proof = await locked.read_locked_demo_control_0056_v1(factories, session, scope)
        _require_role_witness(
            roles,
            proof.writer_catalog,
            (proof.server_incarnation_sha256, proof.role_route_sha256),
        )
        _need(
            proof.issuance_authority is proof.execution_authority is False
            and proof.control.event_sha256
            == _exact_genesis_event(scope, proof.control.state)
        )
    return proof.control


async def _operate_selected_genesis(selection, *, reconcile_only):
    selected = selectors._record(selection)
    factories = selected["factories"]
    roles = selection.source_roles_0056
    scope = ControlScope("demo", selection.scope_config.expected_uid)
    _need(type(reconcile_only) is bool)
    expected_state = expected_event = None
    created = False
    if not reconcile_only:
        commit_started = False
        try:
            async with factories.writer_factory() as session:
                transaction = await session.begin()
                created, expected_state, expected_event = await _stage_genesis(
                    session, factories, roles, scope
                )
                _need(selectors._record(selection) is selected)
                if created:
                    commit_started = True
                    await transaction.commit()
                else:
                    await transaction.rollback()
        except (Exception, asyncio.CancelledError):
            if commit_started:
                _uncertain("commit")
            raise

    try:
        observed = await _read_committed_genesis(factories, roles, scope)
        _need(selectors._record(selection) is selected)
        if expected_state is not None:
            _need(
                observed.state == expected_state
                and observed.event_sha256 == expected_event
            )
    except (Exception, asyncio.CancelledError):
        if created:
            _uncertain("readback")
        raise

    return StoppedControlGenesis0056V1(
        "CREATED_STOPPED" if created else "EXISTING_STOPPED",
        observed.event_sha256,
    )


async def initialize_configured_stopped_control_0056_v1(
    instrument_id: str, *, reconcile_only: bool
) -> StoppedControlGenesis0056V1:
    """Only the live signed selector can establish the UID for this operation."""
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
            # Record factories before any replay can fail, so dispose can
            # always close both companion engines on refusal.
            factories = selectors._SELECTIONS[selection]["factories"]
            result = await _operate_selected_genesis(
                selection, reconcile_only=reconcile_only
            )
    except (NativeStoppedControlGenesis0056Error, asyncio.CancelledError):
        failure = True
        raise
    except Exception:  # noqa: BLE001 -- fixed refusal only
        failure = True
        if result is not None and result.status == "CREATED_STOPPED":
            _uncertain("cleanup")
        _need(False)
    finally:
        try:
            await _dispose(selection, factories)
        except (Exception, asyncio.CancelledError):  # noqa: BLE001 -- cleanup detail stays private
            if not failure:
                if result is not None and result.status == "CREATED_STOPPED":
                    _uncertain("cleanup")
                _need(False)
    return result
