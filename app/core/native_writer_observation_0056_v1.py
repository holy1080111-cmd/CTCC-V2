"""Read the inherited DB0056 catalog in an existing restricted transaction."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import native_role_preflight_0056_full_diagnostic_v1 as diagnostic
from app.database.repositories.public_runtime_bundle_custody import _native_engine
from app.domain import collector_current_0056_inherited_catalog_v1 as inherited
from app.domain.source_primitives import canonical, sha

_SESSION_SQL = text("""
SELECT ARRAY(SELECT version_num::text FROM public.alembic_version LIMIT 2),
       CURRENT_USER::text, SESSION_USER::text,
       pg_catalog.current_setting('role'), pg_catalog.current_database()::text,
       (SELECT oid::bigint FROM pg_catalog.pg_roles WHERE rolname=SESSION_USER),
       (SELECT oid::bigint FROM pg_catalog.pg_database
        WHERE datname=pg_catalog.current_database()),
       pg_catalog.pg_backend_pid(), pg_catalog.pg_postmaster_start_time(),
       pg_catalog.transaction_timestamp(),
       pg_catalog.current_setting('transaction_read_only'),
       EXISTS(SELECT 1 FROM pg_catalog.pg_stat_ssl
              WHERE pid=pg_catalog.pg_backend_pid() AND ssl)
""")
_ACTOR_CATALOG_SQL = text(inherited.ACTOR_CATALOG_SQL)
_HEX = re.compile(r"[0-9a-f]{64}\Z")


class NativeWriter0056ObservationError(ValueError):
    """Fixed refusal without catalog or connection details."""


@dataclass(frozen=True, slots=True)
class NativeWriter0056ObservationV1:
    native_definitions_sha256: str
    actor_catalog_sha256: str
    schema: Literal["ctcc.current0056.restricted_readwrite_writer_observation.v1"] = (
        field(
            default="ctcc.current0056.restricted_readwrite_writer_observation.v1",
            init=False,
        )
    )
    database_head: Literal["0056"] = field(default="0056", init=False)
    native_baseline_admission: Literal[False] = field(default=False, init=False)
    startup_admission: Literal[False] = field(default=False, init=False)
    account_complete: Literal[False] = field(default=False, init=False)
    execution_authority: Literal[False] = field(default=False, init=False)
    admission: Literal["DENY"] = field(default="DENY", init=False)


def _deny():
    raise NativeWriter0056ObservationError(
        "native_writer_0056_observation_unavailable"
    ) from None


def _require_same_transaction(session, engine, transaction):
    sync = session.sync_session
    if (
        not _native_engine(engine)
        or session.bind is not engine
        or sync.bind is not engine.sync_engine
        or session.get_bind() is not engine.sync_engine
        or session.get_bind(clause=_SESSION_SQL) is not engine.sync_engine
        or session.get_bind(clause=_ACTOR_CATALOG_SQL) is not engine.sync_engine
        or session.get_bind(clause=diagnostic._FITTED_DEMO_ACCESS)
        is not engine.sync_engine
        or transaction is None
        or sync.get_transaction() is not transaction
        or not transaction.is_active
        or sync.get_nested_transaction() is not None
        or any(
            len(getattr(sync.dispatch, event)) for event in sync.dispatch._event_names
        )
    ):
        _deny()


async def _session_identity(session, engine):
    row = tuple((await session.execute(_SESSION_SQL)).one())
    if (
        len(row) != 12
        or row[0] != ["0056"]
        or type(engine.url.username) is not str
        or not engine.url.username
        or row[1:5]
        != (engine.url.username, engine.url.username, "none", engine.url.database)
        or type(row[4]) is not str
        or not row[4]
        or any(type(row[index]) is not int or row[index] <= 0 for index in (5, 6, 7))
        or any(
            type(row[index]) is not datetime or row[index].utcoffset() is None
            for index in (8, 9)
        )
        or row[9] < row[8]
        or row[10] != "off"
        or row[11] is not True
    ):
        _deny()
    return row


async def observe_restricted_readwrite_writer_0056_v1(session):
    """Read the actual writer; this observation never grants native admission."""
    try:
        if type(session) is not AsyncSession:
            _deny()
        engine = session.bind
        transaction = session.sync_session.get_transaction()
        _require_same_transaction(session, engine, transaction)
        first = await _session_identity(session, engine)
        _require_same_transaction(session, engine, transaction)
        value = (await session.execute(_ACTOR_CATALOG_SQL)).scalar_one()
        _require_same_transaction(session, engine, transaction)
        observed = inherited.observe_readwrite_writer_0056(value)
        definitions = observed["native_definitions_sha256"]
        if (
            observed["schema"] != inherited.OBSERVATION_SCHEMA
            or observed["database_head"] != "0056"
            or observed["role_oid"] != first[5]
            or value["role"]["name"] != first[1]
            or value["issued"]["server"]["database"] != first[4]
            or observed["native_baseline_admission"] is not False
            or observed["account_complete"] is not False
            or observed["execution_authority"] is not False
            or type(definitions) is not str
            or _HEX.fullmatch(definitions) is None
        ):
            _deny()
        await diagnostic._require_no_effective_demo_fitted_execute(session, (first[1],))
        _require_same_transaction(session, engine, transaction)
        if await _session_identity(session, engine) != first:
            _deny()
        _require_same_transaction(session, engine, transaction)
        return NativeWriter0056ObservationV1(definitions, sha(canonical(value)))
    except asyncio.CancelledError:
        raise
    except NativeWriter0056ObservationError:
        raise
    except Exception:  # noqa: BLE001 -- Redact SQL, connection, and catalog details.
        _deny()
