"""Read the actual source database roles before creating any campaign journal."""

from __future__ import annotations

import asyncio
import ssl
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text

from app.database.repositories.public_runtime_bundle_custody import (
    RuntimeBundleCustodyRepository,
    _closure,
)
from app.mie.validation.public_checkpoint_service import _source_database_pins

_IDENTITY = text("""
    SELECT d.oid::bigint AS database_oid, r.oid::bigint AS login_oid,
           d.datname::text AS database_name, r.rolname::text AS login_name,
           pg_catalog.inet_server_addr()::text AS server_address,
           pg_catalog.inet_server_port() AS server_port,
           pg_catalog.pg_postmaster_start_time() AS server_started,
           SESSION_USER = CURRENT_USER AS direct_login,
           pg_catalog.current_setting('transaction_read_only') = 'on' AS read_only,
           pg_catalog.current_setting('fsync') = 'on'
             AND pg_catalog.current_setting('full_page_writes') = 'on'
             AND pg_catalog.current_setting('synchronous_commit') = 'on'
             AND pg_catalog.current_setting('session_replication_role') = 'origin'
             AND NOT pg_catalog.pg_is_in_recovery() AS durable_primary,
           EXISTS (
             SELECT 1 FROM pg_catalog.pg_stat_ssl
             WHERE pid = pg_catalog.pg_backend_pid() AND ssl
           ) AS tls
    FROM pg_catalog.pg_database AS d CROSS JOIN pg_catalog.pg_roles AS r
    WHERE d.datname = pg_catalog.current_database() AND r.rolname = SESSION_USER
""")


class DevvalCaptureDependencyError(ValueError):
    """Refuse unresolved readiness without a connection locator or SQL output."""


@dataclass(frozen=True, slots=True)
class _RoleReadback:
    database: tuple[str, int, datetime, int]
    login_oid: int


async def _read_role(factory, guard) -> _RoleReadback:
    # The repository constructors already check the pinned native driver.
    engine = factory.kw["bind"]
    url = engine.url
    parameters = _closure(engine.sync_engine.pool._creator)["cparams"]
    tls = parameters.get("ssl")
    if (
        not url.host
        or (url.host, url.database, url.username, url.port or 5432)
        != (
            parameters.get("host"),
            parameters.get("database"),
            parameters.get("user"),
            parameters.get("port", 5432),
        )
        or type(tls) is not ssl.SSLContext
        or tls.verify_mode != ssl.CERT_REQUIRED
        or tls.check_hostname is not True
    ):
        raise DevvalCaptureDependencyError("devval_capture_tls_required")
    async with asyncio.timeout(12), factory() as session, session.begin():
        await session.execute(text("SET TRANSACTION READ ONLY"))
        await session.execute(text("SET LOCAL search_path = pg_catalog, pg_temp"))
        await session.execute(text("SET LOCAL synchronous_commit = on"))
        await session.execute(text("SET LOCAL statement_timeout = '8s'"))
        await session.execute(text("SET LOCAL lock_timeout = '2s'"))
        row = (await session.execute(_IDENTITY)).mappings().one()
        if (
            any(
                row[key] is not True
                for key in ("tls", "direct_login", "read_only", "durable_primary")
            )
            or (row["database_name"], row["login_name"]) != (url.database, url.username)
            or any(
                type(row[key]) is not int or not 0 < row[key] < 2**32
                for key in ("database_oid", "login_oid")
            )
            or type(row["server_address"]) is not str
            or not row["server_address"]
            or type(row["server_port"]) is not int
            or not 0 < row["server_port"] <= 65535
            or type(row["server_started"]) is not datetime
            or row["server_started"].utcoffset() is None
        ):
            raise DevvalCaptureDependencyError("devval_capture_role_readback_invalid")
        await guard(session)
        return _RoleReadback(
            (
                row["server_address"],
                row["server_port"],
                row["server_started"],
                row["database_oid"],
            ),
            row["login_oid"],
        )


async def verify_devval_capture_dependencies(
    witness, observation, custody_factory
) -> None:
    try:
        pins = _source_database_pins(witness, observation)
        custody = RuntimeBundleCustodyRepository(custody_factory)
        # One read-only native transaction per login. These observations do
        # not create a witness, issue a custody receipt or authorize capture.
        roles = []
        for factory, guard in zip(
            (*pins.factories, custody_factory),
            (witness._role_guard, observation._role_guard, custody._catalog),
            strict=True,
        ):
            roles.append(await _read_role(factory, guard))
        logins = {(role.database[:-1], role.login_oid) for role in roles}
        if roles[0].database != roles[1].database or len(logins) != len(roles):
            raise DevvalCaptureDependencyError("devval_capture_role_topology_changed")
        _source_database_pins(witness, observation, pins)
        custody._check_factory()
    except DevvalCaptureDependencyError:
        raise
    except Exception:  # noqa: BLE001 - Never surface a DSN, SQL or private host name.
        raise DevvalCaptureDependencyError(
            "devval_capture_dependencies_unresolved"
        ) from None
