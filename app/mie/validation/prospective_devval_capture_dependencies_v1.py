"""Read the actual source database roles before creating any campaign journal."""

from __future__ import annotations

import asyncio
import ssl
from dataclasses import dataclass
from datetime import datetime, timedelta

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import text

from app.database.repositories.public_runtime_bundle_custody import (
    RuntimeBundleCustodyRepository,
    _closure,
)
from app.mie.validation.post_publication_availability_v4 import (
    PostReadDatabaseIdentityV4,
)
from app.mie.validation.public_checkpoint_service import _source_database_pins

_IDENTITY = text("""
    SELECT d.oid::bigint AS database_oid, r.oid::bigint AS login_oid,
           d.datname::text AS database_name, r.rolname::text AS login_name,
           c.relowner::bigint AS owner_oid,
           pg_catalog.inet_server_addr()::text AS server_address,
           pg_catalog.inet_server_port() AS server_port,
           pg_catalog.pg_postmaster_start_time() AS server_started,
           pg_catalog.current_setting('server_version_num') AS server_version_num,
           pg_catalog.version() AS server_build,
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
    FROM pg_catalog.pg_database AS d
    CROSS JOIN pg_catalog.pg_roles AS r
    CROSS JOIN pg_catalog.pg_class AS c
    WHERE d.datname = pg_catalog.current_database() AND r.rolname = SESSION_USER
      AND c.oid = pg_catalog.to_regclass(:relation)
""")
_SOURCE_RELATIONS = (
    "public.public_receipt_witness_revisions",
    "public.public_receipt_post_read_observations",
    "public.public_runtime_raw_bundles_v1",
)
_SERVER_BUILD = TypeAdapter(
    PostReadDatabaseIdentityV4.model_fields["server_build"].rebuild_annotation(),
    config=PostReadDatabaseIdentityV4.model_config,
)


class DevvalCaptureDependencyError(ValueError):
    """Refuse unresolved readiness without a connection locator or SQL output."""


@dataclass(frozen=True, slots=True)
class _SourceScope:
    database_oid: int
    database_name: str
    server_address: str
    server_port: int
    server_started: datetime
    server_version_num: str
    server_build: str
    owner_oid: int


@dataclass(frozen=True, slots=True)
class _RoleReadback:
    scope: _SourceScope
    login_oid: int


async def _read_role(factory, guard, relation) -> _RoleReadback:
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
        await session.execute(
            text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        )
        await session.execute(text("SET LOCAL search_path = pg_catalog, pg_temp"))
        await session.execute(text("SET LOCAL synchronous_commit = on"))
        await session.execute(text("SET LOCAL statement_timeout = '8s'"))
        await session.execute(text("SET LOCAL lock_timeout = '2s'"))
        row = (
            (await session.execute(_IDENTITY, {"relation": relation})).mappings().one()
        )
        if (
            any(
                row[key] is not True
                for key in ("tls", "direct_login", "read_only", "durable_primary")
            )
            or (row["database_name"], row["login_name"]) != (url.database, url.username)
            or any(
                type(row[key]) is not int or not 0 < row[key] < 2**32
                for key in ("database_oid", "login_oid", "owner_oid")
            )
            or row["owner_oid"] == row["login_oid"]
            or type(row["server_address"]) is not str
            or not row["server_address"]
            or type(row["server_port"]) is not int
            or not 0 < row["server_port"] <= 65535
            or type(row["server_started"]) is not datetime
            or row["server_started"].utcoffset() != timedelta(0)
            or row["server_version_num"] != "170011"
        ):
            raise DevvalCaptureDependencyError("devval_capture_role_readback_invalid")
        try:
            _SERVER_BUILD.validate_python(row["server_build"])
        except ValidationError:
            raise DevvalCaptureDependencyError(
                "devval_capture_role_readback_invalid"
            ) from None
        await guard(session)
        return _RoleReadback(
            _SourceScope(
                **{key: row[key] for key in _SourceScope.__dataclass_fields__}
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
        for factory, guard, relation in zip(
            (*pins.factories, custody_factory),
            (witness._role_guard, observation._role_guard, custody._catalog),
            _SOURCE_RELATIONS,
            strict=True,
        ):
            roles.append(await _read_role(factory, guard, relation))
        # V6 capture binds raw custody to the same database/server/owner as
        # the clock custody pair. Check this before a durable dispatch.
        if any(role.scope != roles[0].scope for role in roles[1:]) or len(
            {role.login_oid for role in roles}
        ) != len(roles):
            raise DevvalCaptureDependencyError("devval_capture_role_topology_changed")
        _source_database_pins(witness, observation, pins)
        custody._check_factory()
    except DevvalCaptureDependencyError:
        raise
    except Exception:  # noqa: BLE001 - Never surface a DSN, SQL or private host name.
        raise DevvalCaptureDependencyError(
            "devval_capture_dependencies_unresolved"
        ) from None
