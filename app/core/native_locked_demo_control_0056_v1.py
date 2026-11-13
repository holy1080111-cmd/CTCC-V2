"""Replay one Demo Control lineage under an existing restricted DB0056 lock."""

from __future__ import annotations

import asyncio
import ssl
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import native_role_preflight_0056_full_diagnostic_v1 as roles56
from app.core import native_writer_observation_0056_v1 as writer56
from app.core.native_role_preflight_v1 import _SETTINGS, _exact_existing_acl
from app.core.native_role_witness_0056_v1 import runtime_witness_0056
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
            catalog = await writer56.observe_restricted_readwrite_writer_0056_v1(
                session
            )
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
            state, event = await DemoControlRepositoryV2._snapshot(session, row)
            _require_session(session, engine, transaction)
            _require(state.scope == scope)
            observed_at = await session.scalar(_CLOCK)
            control = ControlObservation(state, event, observed_at)
            _require(observed_at >= first[9])

            _require(await roles56.current55._identity(session, names[0]) == actor)
            _require(await writer56._session_identity(session, engine) == first)
            _require_session(session, engine, transaction)
            _require(
                _factory_pins(selected) == pins
                and _direct_tls_route(engine) == url_route
            )
            # All configured routes have already been checked to be identical.
            witness = runtime_witness_0056(actor, names, (url_route,) * 3)
        return NativeLockedDemoControl0056V1(control, catalog, *witness)
    except asyncio.CancelledError:
        raise
    except NativeLockedDemoControl0056Error:
        raise
    except Exception:  # noqa: BLE001 -- Do not expose stored control or SQL details.
        _require(False)
