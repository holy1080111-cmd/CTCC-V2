"""Explicit read-only owner route preflight for a future DB0056 migration actor.

This command never migrates, grants, starts the API, or confers execution
authority. Operator pins must come from a separately reviewed deployment
record. A matching revision label is only route identity, not schema proof.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import re
import ssl
from contextlib import redirect_stdout
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.database.native_pg_tls_v1 import (
    _read_bounded_regular_file_v1,
    reviewed_native_pg_tls_route_v1,
    verified_native_pg_tls_context_v1,
)
from scripts.current_contract_source_scopes_v1 import actual_current_source_scope

_SOURCE_ROOT = Path(__file__).resolve().parents[1]
_SHA256 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_CLUSTER_ID = re.compile(r"[1-9][0-9]{0,19}\Z", re.ASCII)
_SECRET_LIMIT = 8192
_PROFILE_SQL = text("""
SELECT current_user::text, session_user::text,
       pg_catalog.current_database()::text,
       pg_catalog.inet_server_port(),
       (SELECT oid::bigint FROM pg_catalog.pg_roles WHERE rolname=current_user),
       (SELECT oid::bigint FROM pg_catalog.pg_database
        WHERE datname=pg_catalog.current_database()),
       (SELECT datdba::bigint FROM pg_catalog.pg_database
        WHERE datname=pg_catalog.current_database()),
       (SELECT ssl FROM pg_catalog.pg_stat_ssl
        WHERE pid=pg_catalog.pg_backend_pid()),
       (SELECT version::text FROM pg_catalog.pg_stat_ssl
        WHERE pid=pg_catalog.pg_backend_pid()),
       pg_catalog.current_setting('transaction_read_only'),
       (SELECT version_num::text FROM public.alembic_version),
       (SELECT system_identifier::text FROM pg_catalog.pg_control_system())
""")


class OwnerRoutePreflightError(ValueError):
    """A fixed refusal that does not contain private route or catalog values."""


def _deny() -> None:
    raise OwnerRoutePreflightError("native_owner_route_preflight_refused") from None


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _valid_sha(value: object) -> bool:
    return type(value) is str and _SHA256.fullmatch(value) is not None


@dataclass(frozen=True, slots=True, repr=False)
class OwnerRoutePinsV1:
    source_manifest_sha256: str
    ca_sha256: str
    cluster_system_identifier_sha256: str
    host: str
    port: int
    database: str
    owner: str
    database_oid: int
    owner_oid: int
    database_head: Literal["0055", "0056"]


@dataclass(frozen=True, slots=True, repr=False)
class _Prepared:
    url: URL = field(repr=False)
    tls: ssl.SSLContext = field(repr=False)
    manifest_sha256: str
    migration_sha256: str
    source_head: str
    route_sha256: str


def _private_owner_url(path: Path) -> URL:
    raw = _read_bounded_regular_file_v1(path, _SECRET_LIMIT)
    if raw.endswith(b"\r\n"):
        raw = raw[:-2]
    elif raw.endswith(b"\n"):
        raw = raw[:-1]
    if not raw or b"\n" in raw or b"\r" in raw or b"\x00" in raw:
        _deny()
    return reviewed_native_pg_tls_route_v1(raw.decode("utf-8"))


def _prepare(
    pins: OwnerRoutePinsV1, *, owner_dsn_file: Path, ca_file: Path
) -> _Prepared:
    """Validate external pins, actual source and private transport before SQL."""
    try:
        if (
            type(pins) is not OwnerRoutePinsV1
            or not all(
                _valid_sha(value)
                for value in (
                    pins.source_manifest_sha256,
                    pins.ca_sha256,
                    pins.cluster_system_identifier_sha256,
                )
            )
            or type(pins.host) is not str
            or type(pins.port) is not int
            or not 1 <= pins.port <= 65535
            or type(pins.database) is not str
            or not pins.database
            or type(pins.owner) is not str
            or not pins.owner
            or type(pins.database_oid) is not int
            or pins.database_oid <= 0
            or type(pins.owner_oid) is not int
            or pins.owner_oid <= 0
            or pins.database_head not in ("0055", "0056")
        ):
            _deny()
        with redirect_stdout(io.StringIO()):
            source = actual_current_source_scope(_SOURCE_ROOT)
        if (
            source.actual_source_head != "0056"
            or source.manifest_sha256 != pins.source_manifest_sha256
            or source.execution_authority is not False
        ):
            _deny()
        url = _private_owner_url(owner_dsn_file)
        if (
            url.host != pins.host
            or url.port != pins.port
            or url.database != pins.database
            or url.username != pins.owner
            or type(url.password) is not str
            or not url.password
        ):
            _deny()
        tls = verified_native_pg_tls_context_v1(ca_file, pins.ca_sha256)
        route = (pins.host, pins.port, pins.database, pins.owner)
        return _Prepared(
            url=url,
            tls=tls,
            manifest_sha256=source.manifest_sha256,
            migration_sha256=source.actual_migration_sha256,
            source_head=source.actual_source_head,
            route_sha256=_sha(json.dumps(route, separators=(",", ":")).encode("utf-8")),
        )
    except OwnerRoutePreflightError:
        raise
    except Exception:  # noqa: BLE001 -- private URL/path/catalog cannot escape
        _deny()


async def _read_one(engine, pins: OwnerRoutePinsV1) -> tuple[str, int, int]:
    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.exec_driver_sql(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
            )
            await connection.exec_driver_sql(
                "SET LOCAL search_path TO pg_catalog, pg_temp"
            )
            await connection.exec_driver_sql("SET LOCAL TIME ZONE 'UTC'")
            await connection.exec_driver_sql("SET LOCAL lock_timeout='1000ms'")
            await connection.exec_driver_sql("SET LOCAL statement_timeout='10000ms'")
            row = tuple((await connection.execute(_PROFILE_SQL)).one())
            if (
                len(row) != 12
                or row[0] != pins.owner
                or row[1] != pins.owner
                or row[2] != pins.database
                or row[3] != pins.port
                or row[4] != pins.owner_oid
                or row[5] != pins.database_oid
                or row[6] != pins.owner_oid
                or row[7] is not True
                or row[8] not in ("TLSv1.2", "TLSv1.3")
                or row[9] != "on"
                or row[10] != pins.database_head
                or type(row[11]) is not str
                or _CLUSTER_ID.fullmatch(row[11]) is None
                or _sha(row[11].encode("ascii"))
                != pins.cluster_system_identifier_sha256
            ):
                _deny()
            return (_sha(row[11].encode("ascii")), row[5], row[4])
        finally:
            await transaction.rollback()


async def preflight_owner_route_0056_v1(
    pins: OwnerRoutePinsV1, *, owner_dsn_file: Path, ca_file: Path
) -> dict[str, object]:
    """Two fresh physical read-only owner observations; no schema admission."""
    try:
        before = _prepare(pins, owner_dsn_file=owner_dsn_file, ca_file=ca_file)
        engine = create_async_engine(
            before.url,
            poolclass=NullPool,
            connect_args={"ssl": before.tls},
        )
        try:
            first = await _read_one(engine, pins)
            second = await _read_one(engine, pins)
            if first != second:
                _deny()
        finally:
            await engine.dispose()
        after = _prepare(pins, owner_dsn_file=owner_dsn_file, ca_file=ca_file)
        if (
            after.url != before.url
            or after.manifest_sha256 != before.manifest_sha256
            or after.migration_sha256 != before.migration_sha256
            or after.route_sha256 != before.route_sha256
        ):
            _deny()
    except OwnerRoutePreflightError:
        raise
    except Exception:  # noqa: BLE001 -- redact SQL/driver/owner details
        _deny()
    return {
        "schema": "ctcc.native_owner_0056.route_preflight.v1",
        "status": "PASS",
        "source_head": before.source_head,
        "source_manifest_sha256": before.manifest_sha256,
        "migration_sha256": before.migration_sha256,
        "database_head": pins.database_head,
        "route_sha256": before.route_sha256,
        "cluster_system_identifier_sha256": pins.cluster_system_identifier_sha256,
        "ca_sha256": pins.ca_sha256,
        "independent_readonly_observations": 2,
        "database_owner_verified": True,
        "password_authentication_verified": False,
        "schema_object_ownership_verified": False,
        "schema_drift_verified": False,
        "owner_provision_verified": False,
        "startup_admission": False,
        "execution_authority": False,
    }


class _PrivateArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        _deny()


def main(argv: list[str] | None = None) -> int:
    parser = _PrivateArgumentParser(add_help=False)
    parser.add_argument("--preflight-reviewed-native-owner", action="store_true")
    parser.add_argument("--owner-dsn-file", type=Path)
    parser.add_argument("--ca-file", type=Path)
    parser.add_argument("--ca-sha256")
    parser.add_argument("--source-manifest-sha256")
    parser.add_argument("--cluster-system-identifier-sha256")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--database")
    parser.add_argument("--owner")
    parser.add_argument("--database-oid", type=int)
    parser.add_argument("--owner-oid", type=int)
    parser.add_argument("--database-head")
    try:
        args = parser.parse_args(argv)
        if not args.preflight_reviewed_native_owner:
            _deny()
        pins = OwnerRoutePinsV1(
            source_manifest_sha256=args.source_manifest_sha256,
            ca_sha256=args.ca_sha256,
            cluster_system_identifier_sha256=args.cluster_system_identifier_sha256,
            host=args.host,
            port=args.port,
            database=args.database,
            owner=args.owner,
            database_oid=args.database_oid,
            owner_oid=args.owner_oid,
            database_head=args.database_head,
        )
        receipt = asyncio.run(
            preflight_owner_route_0056_v1(
                pins, owner_dsn_file=args.owner_dsn_file, ca_file=args.ca_file
            )
        )
    except Exception:  # noqa: BLE001 -- fixed refusal, including parser input
        print('{"status":"REFUSED","code":"native_owner_route_preflight_refused"}')
        return 1
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
