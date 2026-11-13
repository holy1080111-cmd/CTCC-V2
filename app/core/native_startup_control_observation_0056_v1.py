"""Read the existing Demo control in a DB0056 restricted writer snapshot."""

from __future__ import annotations

import asyncio
import ssl
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import native_role_preflight_0056_full_diagnostic_v1 as roles56
from app.core.native_only_lifespan import (
    _CONTROL_SQL,
    _RUNNING_SOAK_SQL,
    ExistingControl,
    _checked_control,
)
from app.core.native_role_preflight_v1 import _SETTINGS, _exact_existing_acl
from app.database.repositories.collector_current_0055_multirole_catalog_v1 import (
    _factory_route,
)
from app.database.repositories.public_runtime_bundle_custody import _closure
from app.demo_automation.native_server_selectors_v2 import (
    ConfiguredNativeDemoFactoriesV2,
    _database_route,
)
from scripts.current0056_fitted_delta_catalog_v1 import pinned_migration

_SOURCE = Path(__file__).resolve().parents[2]
_STARTUP_SETTINGS = (
    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY",
    *_SETTINGS,
    "SET LOCAL row_security = 'off'",
)
_SNAPSHOT_SQL = text("""
SELECT pg_catalog.pg_backend_pid(), pg_catalog.transaction_timestamp(),
       pg_catalog.current_database()::text,
       pg_catalog.current_setting('transaction_isolation') = 'repeatable read',
       pg_catalog.current_setting('transaction_read_only') = 'on',
       pg_catalog.current_setting('session_replication_role') = 'origin',
       pg_catalog.current_setting('row_security') = 'off',
       NOT pg_catalog.pg_is_in_recovery()
""")


class NativeStartupControl0056Error(ValueError):
    """Fixed refusal without database or control details."""


@dataclass(frozen=True, slots=True)
class NativeStartupControl0056ObservationV1:
    control: ExistingControl
    database_head: Literal["0056"] = field(default="0056", init=False)
    startup_admission: Literal[False] = field(default=False, init=False)
    execution_authority: Literal[False] = field(default=False, init=False)


def _require(condition: bool) -> None:
    if condition is not True:
        raise NativeStartupControl0056Error("native_startup_control_0056_unavailable")


def _factory_pins(selected):
    _require(type(selected) is ConfiguredNativeDemoFactoriesV2)
    factories = (
        selected.writer_factory,
        selected.custody_factory,
        selected.frame_reader_factory,
    )
    routes = tuple(_factory_route(factory) for factory in factories)
    names = tuple(route[3] for route in routes)
    engines = tuple(factory.kw["bind"] for factory in factories)
    url_routes = tuple(_database_route(engine.url) for engine in engines)
    _require(
        len(set(names)) == 3
        and len(set(url_routes)) == 1
        and all(route[2] == routes[0][2] for route in routes[1:])
        and tuple(engine.url.username for engine in engines) == names
        and engines[1] is selected.custody_engine
        and engines[2] is selected.frame_reader_engine
        and factories[0].kw["autoflush"] is False
    )
    writer = engines[0]
    parameters = _closure(writer.sync_engine.pool._creator)["cparams"]
    tls = parameters.get("ssl")
    _require(
        type(tls) is ssl.SSLContext
        and tls.verify_mode == ssl.CERT_REQUIRED
        and tls.check_hostname is True
    )
    return factories[0], writer, names, routes


def _require_session(session, writer, transaction):
    _require(
        type(session) is AsyncSession
        and session.bind is writer
        and session.sync_session.bind is writer.sync_engine
        and session.get_bind() is writer.sync_engine
        and all(
            session.get_bind(clause=statement) is writer.sync_engine
            for statement in (
                roles56.current55._IDENTITY_SQL,
                roles56._HEAD,
                _SNAPSHOT_SQL,
                roles56._FITTED_DEMO_ACCESS,
                _CONTROL_SQL,
                _RUNNING_SOAK_SQL,
            )
        )
        and transaction is not None
        and session.sync_session.get_transaction() is transaction
        and transaction.is_active
        and session.sync_session.get_nested_transaction() is None
        and not any(
            len(getattr(session.sync_session.dispatch, event))
            for event in session.sync_session.dispatch._event_names
        )
    )


async def _identity(session, writer, name):
    actor = await roles56._identity_at_0056(session, name)
    snapshot = tuple((await session.execute(_SNAPSHOT_SQL)).one())
    _require(
        len(snapshot) == 8
        and type(snapshot[0]) is int
        and snapshot[0] > 0
        and type(snapshot[1]) is datetime
        and snapshot[1].utcoffset() is not None
        and snapshot[1] >= actor[5]
        and snapshot[2] == writer.url.database
        and all(value is True for value in snapshot[3:])
    )
    return actor, snapshot


@asynccontextmanager
async def _restricted_read_snapshot_0056(selected):
    try:
        writer_factory, writer, names, routes = _factory_pins(selected)
        pinned_migration(_SOURCE)
        async with asyncio.timeout(15), writer_factory() as session, session.begin():
            _require(type(session) is AsyncSession)
            transaction = session.sync_session.get_transaction()
            _require_session(session, writer, transaction)
            for statement in _STARTUP_SETTINGS:
                await session.execute(text(statement))
            first = await _identity(session, writer, names[0])
            _require_session(session, writer, transaction)
            await _exact_existing_acl(await session.connection(), names, first[0][3])
            await roles56._require_no_effective_demo_fitted_execute(session, names)
            _require_session(session, writer, transaction)
            yield session
            _require(await _identity(session, writer, names[0]) == first)
            _require_session(session, writer, transaction)
        _require(_factory_pins(selected) == (writer_factory, writer, names, routes))
    except asyncio.CancelledError:
        raise
    except NativeStartupControl0056Error:
        raise
    except Exception:  # noqa: BLE001 -- Never expose private rows, SQL, or route facts.
        _require(False)


async def observe_startup_control_0056_v1(selected):
    """Observe a pre-provisioned writer without taking recovery or API authority."""
    async with _restricted_read_snapshot_0056(selected) as session:
        row = (await session.execute(_CONTROL_SQL)).mappings().one_or_none()
        soak = (await session.execute(_RUNNING_SOAK_SQL)).scalar_one_or_none()
        control = _checked_control(None if row is None else dict(row), soak)
    return NativeStartupControl0056ObservationV1(control)
