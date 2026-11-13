"""Replay one Demo Control lineage under an existing restricted DB0056 lock."""

from __future__ import annotations

import asyncio
import ssl
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import native_role_identity_0056_v2 as identity56
from app.core import native_role_preflight_0056_full_diagnostic_v1 as roles56
from app.core import native_writer_observation_0056_v1 as writer56
from app.core.native_role_preflight_v1 import _SETTINGS, _exact_existing_acl
from app.core.native_role_witness_0056_v1 import (
    RestrictedNativeRoles0056WitnessV1,
    runtime_witness_0056,
)
from app.core.native_startup_control_observation_0056_v1 import _factory_pins
from app.database.models.demo_control import DemoAccountControl, DemoControlJournal
from app.database.models.demo_operator_acknowledgements_v55 import (
    DemoControlClearAcknowledgementV2,
)
from app.database.repositories.demo_control import (
    ControlObservation,
    DemoControlRepository,
)
from app.database.repositories.demo_control_current55_v2 import DemoControlRepositoryV2
from app.database.repositories.public_runtime_bundle_custody import _closure
from app.demo_automation.native_server_selectors_v2 import (
    ConfiguredNativeDemoFactoriesV2,
    _database_route,
)
from app.trade_qualification.demo_control import ControlScope, checked
from scripts.current0056_fitted_delta_catalog_v1 import pinned_migration

_SOURCE = Path(__file__).resolve().parents[2]
_CLOCK = text("SELECT pg_catalog.clock_timestamp()")
_LOCAL_SETTINGS = (
    *_SETTINGS,
    "SET LOCAL row_security = 'off'",
)
_CONTROL_MODELS = (
    DemoAccountControl,
    DemoControlJournal,
    DemoControlClearAcknowledgementV2,
)


class NativeLockedDemoControl0056Error(ValueError):
    """Fixed refusal without a UID, a database route, or stored contents."""


@dataclass(frozen=True, slots=True, repr=False)
class NativeLockedDemoControl0056V1:
    """Historical observation taken under the caller's still-held transaction."""

    control: ControlObservation
    writer_catalog: writer56.NativeWriter0056ObservationV1
    server_incarnation_sha256: str
    role_route_sha256: str
    database_head: Literal["0056"] = field(default="0056", init=False)
    issuance_authority: Literal[False] = field(default=False, init=False)
    execution_authority: Literal[False] = field(default=False, init=False)


class NativeWriterControlPurpose0056(Enum):
    RECOVERY_STOP = "recovery_stop"
    PREPARE_OBSERVATION = "prepare_observation"


@dataclass(frozen=True, slots=True, repr=False)
class HeldNativeWriterControl0056V1:
    """One account's exact Control observation within the writer context."""

    observation: NativeLockedDemoControl0056V1
    purpose: NativeWriterControlPurpose0056
    _check_active: Callable[[], None]
    _recovery_row: DemoAccountControl | None
    startup_admission: Literal[False] = field(default=False, init=False)
    execution_authority: Literal[False] = field(default=False, init=False)
    admission: Literal["DENY"] = field(default="DENY", init=False)

    def check_held(self) -> None:
        self._check_active()

    def _control_row_for_restart(self) -> DemoAccountControl:
        self.check_held()
        _require(
            self.purpose is NativeWriterControlPurpose0056.RECOVERY_STOP
            and type(self._recovery_row) is DemoAccountControl
        )
        return self._recovery_row


def _require(condition: bool) -> None:
    if condition is not True:
        raise NativeLockedDemoControl0056Error(
            "native_locked_demo_control_0056_unavailable"
        ) from None


def _require_session(session, engine, transaction):
    writer56._require_same_transaction(session, engine, transaction)
    _require(
        session.autoflush is False
        and not (session.new or session.dirty or session.deleted)
        and all(
            session.get_bind(mapper=model) is engine.sync_engine
            for model in _CONTROL_MODELS
        )
        and all(
            session.get_bind(clause=statement) is engine.sync_engine
            for statement in (roles56.current55._IDENTITY_SQL, _CLOCK)
        )
    )


def _direct_tls_route(engine) -> tuple[str, str, int, str]:
    url_route = _database_route(engine.url)
    parameters = _closure(engine.sync_engine.pool._creator)["cparams"]
    tls = parameters["ssl"]
    _require(
        (parameters["host"], parameters["port"], parameters["database"])
        == url_route[1:]
        and type(tls) is ssl.SSLContext
        and tls.verify_mode == ssl.CERT_REQUIRED
        and tls.check_hostname is True
        and tls.minimum_version >= ssl.TLSVersion.TLSv1_2
        and tls.keylog_filename is None
    )
    return url_route


@asynccontextmanager
async def _verified_locked_demo_control_0056(
    selected: ConfiguredNativeDemoFactoriesV2,
    session: AsyncSession,
    scope: ControlScope,
):
    """Internal verified UID lock shared by the read and stopped genesis."""
    checked(scope, ControlScope)
    pins = _factory_pins(selected)
    _, engine, names, _ = pins
    url_route = _direct_tls_route(engine)
    _require(type(session) is AsyncSession)
    transaction = session.sync_session.get_transaction()
    _require_session(session, engine, transaction)
    # The V2 readers map SQL rows through this session's identity map.
    # Require a cold ORM view so a prior unbound read cannot supply a row.
    _require(not session.identity_map)
    pinned_migration(_SOURCE)

    async with asyncio.timeout(15):
        for setting in _LOCAL_SETTINGS:
            await session.execute(text(setting))
            _require_session(session, engine, transaction)

        first = await writer56._session_identity(session, engine)
        actor = await roles56.current55._identity(session, names[0])
        _require(actor[3:6] == (first[5], first[6], first[8]))
        _require_session(session, engine, transaction)
        catalog = await writer56.observe_restricted_readwrite_writer_0056_v1(session)
        _require_session(session, engine, transaction)
        await _exact_existing_acl(await session.connection(), names, actor[3])
        _require_session(session, engine, transaction)
        # The writer catalog checks its own login; cover both other
        # configured roles against the same installed fitted functions.
        await roles56._require_no_effective_demo_fitted_execute(session, names[1:])
        _require_session(session, engine, transaction)

        await session.execute(text("SET LOCAL statement_timeout='1000ms'"))
        _require_session(session, engine, transaction)
        row = await DemoControlRepository._locked(session, scope)
        _require_session(session, engine, transaction)
        # All configured routes have already been checked to be identical.
        witness = runtime_witness_0056(actor, names, (url_route,) * 3)

        def require_held_transaction():
            _require_session(session, engine, transaction)

        yield row, catalog, witness, first[9], require_held_transaction
        _require_session(session, engine, transaction)

        _require(await roles56.current55._identity(session, names[0]) == actor)
        _require(await writer56._session_identity(session, engine) == first)
        _require_session(session, engine, transaction)
        _require(
            _factory_pins(selected) == pins and _direct_tls_route(engine) == url_route
        )


async def _compare_held_role_pins(selected, session, roles, catalog, witness, held):
    _, engine, names, _ = _factory_pins(selected)
    _require(
        type(catalog) is writer56.NativeWriter0056ObservationV1
        and catalog.database_head == "0056"
        and catalog.admission == "DENY"
        and catalog.startup_admission is catalog.execution_authority is False
        and roles.startup_admission is roles.execution_authority is False
        and witness == (roles.server_incarnation_sha256, roles.role_route_sha256)
    )
    acl = roles56._granted_inherited_acl_shape_sha256(
        names, catalog.native_definitions_sha256
    )
    if type(roles) is RestrictedNativeRoles0056WitnessV1:
        _require(
            type(roles.observation) is roles56.RestrictedNativeRoles0056ObservationV1
            and roles.observation.database_head == "0056"
            and roles.observation.admission == "DENY"
            and roles.observation.inherited_native_definitions_sha256
            == catalog.native_definitions_sha256
            and roles.observation.granted_inherited_acl_shape_sha256 == acl
        )
        return

    _require(
        roles.inherited_native_definitions_sha256 == catalog.native_definitions_sha256
        and roles.granted_inherited_acl_shape_sha256 == acl
    )
    actor = await roles56.current55._identity(session, names[0])
    held()
    _require(
        runtime_witness_0056(actor, names, (_direct_tls_route(engine),) * 3) == witness
    )
    connection = await session.connection()
    held()
    owner = await connection.scalar(roles56.current55._OWNER_OID_SQL)
    held()
    role_oids = await roles56.current55.core_acl._roles(connection, names, owner)
    held()
    _require(role_oids[names[0]] == actor[3])
    identity56.compare_static_role_identity_0056_v2(
        roles,
        expected_database_oid=actor[4],
        expected_owner_oid=owner,
        expected_role_names=names,
        expected_role_oids=tuple(role_oids[name] for name in names),
        expected_route=_direct_tls_route(engine),
        expected_sha256=roles.stable_identity_sha256,
    )


@asynccontextmanager
async def held_native_writer_control_0056_v1(
    selected: ConfiguredNativeDemoFactoriesV2,
    session: AsyncSession,
    scope: ControlScope,
    *,
    roles: RestrictedNativeRoles0056WitnessV1 | identity56.NativeRoleIdentity0056V2,
    purpose: NativeWriterControlPurpose0056,
):
    """Verify current Control within the original writer transaction and task.

    The caller owns the transaction and rolls it back on refusal. Recovery
    accepts the existing signed-selector role witness. A stable identity from
    the installed read-only monitor can only match a prepare observation.
    """
    try:
        _require(
            type(purpose) is NativeWriterControlPurpose0056
            and (
                type(roles) is RestrictedNativeRoles0056WitnessV1
                or (
                    type(roles) is identity56.NativeRoleIdentity0056V2
                    and purpose is NativeWriterControlPurpose0056.PREPARE_OBSERVATION
                )
            )
        )
        task = asyncio.current_task()
        _require(task is not None)
        async with _verified_locked_demo_control_0056(
            selected, session, scope
        ) as proof:
            row, catalog, witness, started_at, require_transaction = proof
            active = True

            def check_held():
                _require(active and asyncio.current_task() is task)
                require_transaction()

            try:
                await _compare_held_role_pins(
                    selected, session, roles, catalog, witness, check_held
                )
                state, event = await DemoControlRepositoryV2._snapshot(session, row)
                check_held()
                _require(state.scope == scope)
                observed_at = await session.scalar(_CLOCK)
                check_held()
                _require(observed_at >= started_at)
                observation = NativeLockedDemoControl0056V1(
                    ControlObservation(state, event, observed_at), catalog, *witness
                )
                yield HeldNativeWriterControl0056V1(
                    observation,
                    purpose,
                    check_held,
                    row
                    if purpose is NativeWriterControlPurpose0056.RECOVERY_STOP
                    else None,
                )
                check_held()
                if type(roles) is identity56.NativeRoleIdentity0056V2:
                    new_catalog = (
                        await writer56.observe_restricted_readwrite_writer_0056_v1(
                            session
                        )
                    )
                    check_held()
                    _require(new_catalog == catalog)
                    await _compare_held_role_pins(
                        selected, session, roles, new_catalog, witness, check_held
                    )
            finally:
                active = False
    except (asyncio.CancelledError, NativeLockedDemoControl0056Error):
        raise
    except Exception:  # noqa: BLE001 -- Never expose stored control or SQL details.
        _require(False)


async def read_locked_demo_control_0056_v1(
    selected: ConfiguredNativeDemoFactoriesV2,
    session: AsyncSession,
    scope: ControlScope,
) -> NativeLockedDemoControl0056V1:
    """Check the selected writer, then use the historical UID-before-row lock.

    The caller owns commit or rollback, including after a refusal. The caller
    must compare this exact scope to its independently sourced account and
    retain the transaction for any future operations relying on these locks.
    """
    try:
        async with _verified_locked_demo_control_0056(
            selected, session, scope
        ) as proof:
            row, catalog, witness, started_at, require_held_transaction = proof
            state, event = await DemoControlRepositoryV2._snapshot(session, row)
            require_held_transaction()
            _require(state.scope == scope)
            control = ControlObservation(state, event, await session.scalar(_CLOCK))
            _require(control.observed_at >= started_at)
        return NativeLockedDemoControl0056V1(control, catalog, *witness)
    except (asyncio.CancelledError, NativeLockedDemoControl0056Error):
        raise
    except Exception:  # noqa: BLE001 -- Never expose stored control or SQL details.
        _require(False)
