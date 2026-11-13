"""Isolated SCRAM owner-route acceptance; never uses a deployment credential."""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy import event, text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.database.native_pg_tls_v1 import (
    _read_bounded_regular_file_v1,
    verified_native_pg_tls_context_v1,
)
from app.domain.source_primitives import sha
from scripts import native_owner_route_preflight_0056_v1 as preflight
from scripts.current_contract_source_scopes_v1 import actual_current_source_scope
from tests.integration.test_gate3_fitted_candidate_custody_pg import (
    _migrate_runtime_custody_head,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
_SOURCE = Path(__file__).resolve().parents[2]
_HOST = re.compile(r"ctcc-owner-scram-[a-f0-9]{12}-postgres\Z", re.ASCII)
_EVIDENCE = Path("/validation-results/owner-scram-lab.json")
_CREATE_ROLE = """
DO $ctcc$
DECLARE role_name text; role_password text;
BEGIN
  SELECT name, password INTO STRICT role_name, role_password
  FROM pg_temp.ctcc_owner_setup;
  EXECUTE pg_catalog.format(
    'CREATE ROLE %I LOGIN PASSWORD %L', role_name, role_password
  );
END $ctcc$
"""


def _inputs():
    names = (
        "CTCC_OWNER_SCRAM_LAB_HOST",
        "CTCC_OWNER_SCRAM_LAB_SECRET_FILE",
        "CTCC_OWNER_SCRAM_LAB_CA_FILE",
        "CTCC_OWNER_SCRAM_LAB_CA_SHA256",
        "CTCC_OWNER_SCRAM_LAB_MANIFEST_SHA256",
    )
    values = tuple(os.environ.get(name) for name in names)
    if all(value is None for value in values):
        pytest.skip("isolated owner SCRAM lab required")
    if any(not value for value in values):
        pytest.fail("isolated_owner_scram_lab_inputs_incomplete", pytrace=False)
    host, secret_file, ca_file, ca_sha, manifest_sha = values
    if _HOST.fullmatch(host) is None:
        pytest.fail("isolated_owner_scram_lab_host_invalid", pytrace=False)
    return host, Path(secret_file), Path(ca_file), ca_sha, manifest_sha


def _identifier(value: str) -> str:
    assert re.fullmatch(r"ctcc_owner_scram_[0-9a-f]{12}", value)
    return '"' + value + '"'


def _publish_evidence(evidence: dict) -> None:
    with _EVIDENCE.open("x", encoding="utf-8") as stream:
        json.dump(evidence, stream, sort_keys=True, separators=(",", ":"))
        stream.write("\n")


@pytest.mark.asyncio
async def test_actual0056_scram_owner_good_and_wrong_password_route_preflight(
    tmp_path, monkeypatch
):
    host, root_secret, ca_file, ca_sha, manifest_sha = _inputs()
    scope = actual_current_source_scope(_SOURCE)
    assert scope.actual_source_head == "0056"
    assert scope.manifest_sha256 == manifest_sha
    tls = verified_native_pg_tls_context_v1(ca_file, ca_sha)
    root_password = _read_bounded_regular_file_v1(root_secret, 8192).decode("ascii")
    assert re.fullmatch(r"[0-9a-f]{64}", root_password)
    admin_url = URL.create(
        "postgresql+asyncpg",
        username="ctcc",
        password=root_password,
        host=host,
        port=5432,
        database="ctcc",
    )
    admin = create_async_engine(
        admin_url,
        poolclass=NullPool,
        connect_args={"ssl": tls},
        hide_parameters=True,
    )
    database = "ctcc_owner_scram_" + uuid4().hex[:12]
    login = "ctcc_owner_scram_" + uuid4().hex[:12]
    database_sql = _identifier(database)
    login_sql = _identifier(login)
    password = secrets.token_hex(32)
    owner_secret = tmp_path / "owner-dsn"
    wrong_secret = tmp_path / "wrong-owner-dsn"
    owner_url = admin_url.set(database=database, username=login, password=password)
    owner_secret.write_bytes(
        owner_url.render_as_string(hide_password=False).encode("utf-8")
    )
    wrong_secret.write_bytes(
        owner_url.set(password=password[:-1] + ("0" if password[-1] != "0" else "1"))
        .render_as_string(hide_password=False)
        .encode("utf-8")
    )
    owner_secret.chmod(0o600)
    wrong_secret.chmod(0o600)
    role_created = database_created = control_granted = False
    try:
        try:
            unexpected = await asyncpg.connect(
                host=host,
                port=5432,
                user="ctcc",
                password=root_password[:-1]
                + ("0" if root_password[-1] != "0" else "1"),
                database="ctcc",
                ssl=tls,
                timeout=5,
            )
        except asyncpg.InvalidPasswordError as error:
            assert error.sqlstate == "28P01"
        else:
            await unexpected.close()
            pytest.fail("scram_wrong_password_was_accepted", pytrace=False)
        # Create a password-bearing NOSUPERUSER role without a secret literal
        # in client SQL. The admin engine suppresses parameter values on error.
        try:
            async with admin.begin() as connection:
                await connection.exec_driver_sql(
                    "SET LOCAL password_encryption='scram-sha-256'"
                )
                await connection.exec_driver_sql(
                    "CREATE TEMP TABLE ctcc_owner_setup "
                    "(name text NOT NULL, password text NOT NULL) ON COMMIT DROP"
                )
                await connection.execute(
                    text("""
                    INSERT INTO pg_temp.ctcc_owner_setup(name,password)
                    VALUES (:name,:password)
                    """),
                    {"name": login, "password": password},
                )
                await connection.exec_driver_sql(_CREATE_ROLE)
        except Exception:  # noqa: BLE001 -- never expose DBAPI parameters
            pytest.fail("scram_role_setup_refused", pytrace=False)
        role_created = True
        async with admin.connect() as connection:
            await connection.execution_options(isolation_level="AUTOCOMMIT")
            await connection.exec_driver_sql(
                f"CREATE DATABASE {database_sql} OWNER {login_sql}"
            )
        database_created = True
        target = create_async_engine(
            admin_url.set(database=database),
            poolclass=NullPool,
            connect_args={"ssl": tls},
            hide_parameters=True,
        )
        try:
            async with target.begin() as connection:
                await connection.run_sync(_migrate_runtime_custody_head)
                await connection.exec_driver_sql(
                    f"GRANT USAGE ON SCHEMA public TO {login_sql}"
                )
                await connection.exec_driver_sql(
                    f"GRANT SELECT ON TABLE public.alembic_version TO {login_sql}"
                )
            async with admin.begin() as connection:
                await connection.exec_driver_sql(
                    "GRANT EXECUTE ON FUNCTION pg_catalog.pg_control_system() "
                    f"TO {login_sql}"
                )
            control_granted = True
            async with target.connect() as connection:
                row = (
                    await connection.execute(
                        text("""
                        SELECT d.oid::bigint, d.datdba::bigint, r.oid::bigint,
                               r.rolsuper, r.rolcreatedb, r.rolcreaterole,
                               EXISTS(SELECT 1 FROM pg_catalog.pg_authid a
                                      WHERE a.oid=r.oid
                                      AND a.rolpassword LIKE 'SCRAM-SHA-256$%'),
                               pg_catalog.has_function_privilege(
                                   r.oid, 'pg_catalog.pg_control_system()', 'EXECUTE'
                               ),
                               (SELECT system_identifier::text
                                FROM pg_catalog.pg_control_system())
                        FROM pg_catalog.pg_database d
                        JOIN pg_catalog.pg_roles r ON r.rolname=:role
                        WHERE d.datname=:database
                        """),
                        {"role": login, "database": database},
                    )
                ).one()
        finally:
            await target.dispose()
        db_oid, db_owner_oid, role_oid = row[:3]
        assert db_owner_oid == role_oid
        assert row[3:8] == (False, False, False, True, True)
        cluster_id = row[8]
        assert type(cluster_id) is str and cluster_id.isdecimal()
        pins = preflight.OwnerRoutePinsV1(
            source_manifest_sha256=manifest_sha,
            ca_sha256=ca_sha,
            cluster_system_identifier_sha256=sha(cluster_id.encode("ascii")),
            host=host,
            port=5432,
            database=database,
            owner=login,
            database_oid=db_oid,
            owner_oid=role_oid,
            database_head="0056",
        )
        physical = []
        original_create = preflight.create_async_engine

        def traced_create(*args, **kwargs):
            engine = original_create(*args, **kwargs)

            @event.listens_for(engine.sync_engine, "connect")
            def capture(dbapi_connection, _record):
                physical.append(dbapi_connection)

            return engine

        with monkeypatch.context() as patch:
            patch.setattr(preflight, "create_async_engine", traced_create)
            receipt = await preflight.preflight_owner_route_0056_v1(
                pins, owner_dsn_file=owner_secret, ca_file=ca_file
            )
        assert len(physical) == 2 and physical[0] is not physical[1]
        assert receipt["status"] == "PASS"
        assert receipt["database_owner_verified"] is True
        assert receipt["password_authentication_verified"] is False
        assert receipt["startup_admission"] is receipt["execution_authority"] is False
        with pytest.raises(preflight.OwnerRoutePreflightError):
            await preflight.preflight_owner_route_0056_v1(
                pins, owner_dsn_file=wrong_secret, ca_file=ca_file
            )
        try:
            unexpected = await asyncpg.connect(
                host=host,
                port=5432,
                user=login,
                password=password[:-1] + ("0" if password[-1] != "0" else "1"),
                database=database,
                ssl=tls,
                timeout=5,
            )
        except asyncpg.InvalidPasswordError as error:
            assert error.sqlstate == "28P01"
        else:
            await unexpected.close()
            pytest.fail("owner_wrong_password_was_accepted", pytrace=False)
        evidence = {
            "schema": "ctcc.native_owner_0056.scram_lab.v1",
            "status": "PASS",
            "source_manifest_sha256": manifest_sha,
            "ca_sha256": ca_sha,
            "auth_method": "SCRAM-SHA-256",
            "wrong_password_sqlstate": "28P01",
            "physical_preflight_connections": 2,
            "owner_nonsuperuser": True,
            "database_owner_verified": True,
            "product_route_preflight_passed": True,
            "startup_admission": False,
            "execution_authority": False,
        }
        await asyncio.to_thread(_publish_evidence, evidence)
    finally:
        try:
            if database_created:
                async with admin.connect() as connection:
                    await connection.execution_options(isolation_level="AUTOCOMMIT")
                    await connection.exec_driver_sql(
                        f"DROP DATABASE {database_sql} WITH (FORCE)"
                    )
            if control_granted:
                async with admin.begin() as connection:
                    await connection.exec_driver_sql(
                        "REVOKE EXECUTE ON FUNCTION pg_catalog.pg_control_system() "
                        f"FROM {login_sql}"
                    )
            if role_created:
                async with admin.begin() as connection:
                    await connection.exec_driver_sql(f"DROP ROLE {login_sql}")
        finally:
            await admin.dispose()
            owner_secret.unlink(missing_ok=True)
            wrong_secret.unlink(missing_ok=True)
