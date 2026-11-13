"""Native PostgreSQL and TLS acceptance for the fitted Gate 3 custody boundary."""

from __future__ import annotations

import asyncio
import io
import json
import os
import re
import secrets
import socket
import ssl
import sys
import tarfile
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from importlib import import_module
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import event, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.database.models  # noqa: F401 - register the full migration metadata.
from app.core import native_frame_reader_0056_v1 as held_frame56
from app.core import native_role_preflight_0056_full_diagnostic_v1 as observation
from app.core import native_writer_observation_0056_v1 as writer_observation
from app.database.base import Base
from app.database.native_pg_tls_v1 import verified_native_pg_tls_context_v1
from app.database.repositories import public_runtime_bundle_custody as raw_custody
from app.demo_automation.native_server_selectors_v2 import (
    ConfiguredNativeDemoFactoriesV2,
)
from app.domain.source_primitives import canonical, sha
from app.mie.validation.artifact import freeze_prospective_holdout_receipt
from app.mie.validation.controlled_fitted_holdout_reader_v1 import (
    ControlledFittedHoldoutReadV1,
    read_fitted_prospective_holdout_once,
)
from app.mie.validation.gate3_fitted_candidate_custody_v1 import (
    FittedCandidateInputsV1,
    Gate3FittedCandidateCustodyV1,
    Gate3FittedCustodyError,
)
from app.mie.validation.prospective import (
    Gate3ProspectiveHoldoutReceipt,
    Gate3ProspectivePreregistration,
    ProspectiveAccessOutcome,
)
from app.mie.validation.prospective_capture_schedule import (
    ProspectiveCoordinatePlanV1,
    build_capture_schedule,
)
from app.mie.validation.prospective_devval_campaign_plan_v1 import (
    ProspectiveDevvalCampaignPlanV1,
)
from app.mie.validation.prospective_devval_fitted_binding_v1 import (
    build_devval_candidate_binding,
)
from scripts import current0055_runtime_grants as core_acl
from scripts import current0056_fitted_catalog_measurement_v1 as fitted_measurement
from scripts import current0056_fitted_delta_catalog_v1 as fitted_delta
from scripts import current0056_native_owner_pregrant_v1 as owner_pregrant
from scripts import current0056_postgrant_acl_measurement_v1 as postgrant_acl
from scripts import current_contract_source_scopes_v1 as source_scopes
from scripts import devval_campaign_physical_catalog_v1 as devval_catalog
from scripts import native_owner_grant_0056_draft_v1 as owner_grant_draft
from scripts import native_owner_route_preflight_0056_v1 as owner_route
from scripts import native_owner_upgrade_0056_v1 as owner_upgrade
from scripts import verify_final_hermetic as archive_checks
from scripts.evidence_json import read_evidence_json
from tests.integration.test_gate3_canonical_schedule_claim import migrate, pin_record
from tests.integration.test_gate3_committed_preregistration_seal import (
    append_pin,
    append_seal,
    seal_record,
)
from tests.native_owner_migration_lab_0056_v1 import (
    migrate_fresh_lab_owner_0056_v1,
)
from tests.unit.mie.test_devval_campaign_batch import synthetic_grid
from tests.unit.mie.test_devval_candidate_trials import _inputs
from tests.unit.mie.test_devval_fitted_binding import (
    _formal_preregistration,
    aligned_synthetic_case,
)
from tests.unit.mie.test_prospective_devval_campaign_plan_v1 import plan_input

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
_SOURCE_ROOT = Path(__file__).resolve().parents[2]
# Diagnostic component pins from the independent N234 postgrant pair readback
# (receipt SHA256 81889b574f0ad6eb95ccca507a7a7ee07b7b78f8c7c43c24830ec9b332ee1571).
# These do not seal a native startup reference or grant execution authority.
_N234_INHERITED_NATIVE_SHA256 = (
    "6243338abe9cfcbb4eb613bc3d21cd7adbaf8059644e8b7a1c2df9755ac340c7"
)
_N234_POSTGRANT_ACL_SHAPE_SHA256 = (
    "6f4cd2d534400a6bd571d8b4ca17b0488c6b00c9f2d63a2b1ea0cd0e335ab5ab"
)
_N234_ROLE_OBSERVER_SOURCE_SHA256 = (
    "0d8984cddbc46a724f5d93dba88af7392276eb0a42e560334914686ce0717aa9"
)
# The newer inert candidate has separate exact source pins. N234 remains intact.
_CANDIDATE_0056_SOURCE_PINS = {
    "app/core/native_role_witness_0056_v1.py": (
        "164ba609ecff5e7df4d77689dcfdf34c4ff0506ff5587b7494cd1e15a833488a"
    ),
    "app/core/native_startup_candidate_0056_v1.py": (
        "69458555dea8f36c6b7773dfe86fd7598222b69220c1b6a6a64bc8ca632eef48"
    ),
}
_STABLE_ROLE_IDENTITY_V2_SOURCE_SHA256 = (
    "e309e6847ca4cd0d94e932fe837fb70b95e184d6d8d30f822ca0cfed72e59290"
)
_READ = "gate3_fitted_binding_read(text)"
_ACK_READ = "gate3_fitted_binding_ack_read(text)"
_ROLES = {
    "seal": ("gate3_prereg_seal_append(jsonb)", "gate3_prereg_seal_read(text)"),
    "seal_ack": (
        "gate3_prereg_seal_read(text)",
        "gate3_prereg_seal_ack_append(text)",
        "gate3_prereg_seal_ack_read(text)",
    ),
    "schedule": (
        "gate3_capture_schedule_claim_append(jsonb)",
        "gate3_capture_schedule_claim_read(text)",
    ),
    "publisher": ("gate3_fitted_binding_append(text,text,text)", _READ),
    "observer": (_READ, "gate3_fitted_binding_ack_append(text,text)", _ACK_READ),
    "reader": (_READ, _ACK_READ),
    "evaluator": (
        _READ,
        _ACK_READ,
        "gate3_fitted_evaluation_append(text,text,text,text,text,text)",
        "gate3_fitted_evaluation_read(text)",
    ),
}


def _migrate_fitted(connection, direction):
    module = import_module("migrations.versions.0056_gate3_fitted_candidate_custody")
    with Operations.context(MigrationContext.configure(connection)):
        getattr(module, direction)()


async def _grant(owner, names, keys):
    async with owner.begin() as connection:
        for key in keys:
            role = names[key]
            await connection.execute(text(f"GRANT USAGE ON SCHEMA public TO {role}"))
            for function in _ROLES[key]:
                await connection.execute(
                    text(f"GRANT EXECUTE ON FUNCTION public.{function} TO {role}")
                )


def _isolated_tls_database_config():
    raw_url = os.environ.get("CTCC_DEVVAL_CUSTODY_TEST_DATABASE_URL")
    ca_file = os.environ.get("CTCC_DEVVAL_CUSTODY_TEST_TLS_CA_FILE")
    ca_sha256 = os.environ.get("CTCC_DEVVAL_CUSTODY_TEST_TLS_CA_SHA256")
    if raw_url is None and ca_file is None and ca_sha256 is None:
        pytest.skip("explicit isolated Gate 3 PostgreSQL TLS lab required")
    if not raw_url or not ca_file or not ca_sha256:
        pytest.fail("isolated Gate 3 PostgreSQL TLS lab requires all inputs")
    url = make_url(raw_url)
    if (
        url.drivername != "postgresql+asyncpg"
        or not url.username
        or not url.database
        or url.password is not None
        or url.query
        or not re.fullmatch(r"ctcc-final-[a-f0-9]{12}-postgres", url.host or "")
    ):
        pytest.fail(
            "fitted custody acceptance requires the isolated trust-auth TLS lab"
        )
    return url, verified_native_pg_tls_context_v1(Path(ca_file), ca_sha256)


async def test_actual0056_verified_ca_rejects_wrong_server_hostname():
    """A second internal DNS alias reaches the same server but fails TLS SAN."""
    url, tls = _isolated_tls_database_config()
    wrong_host = f"{url.host}-wrong-host"
    assert re.fullmatch(r"ctcc-final-[a-f0-9]{12}-postgres-wrong-host", wrong_host)
    assert socket.gethostbyname(wrong_host) == socket.gethostbyname(url.host)
    connection = None
    try:
        connection = await asyncpg.connect(
            host=wrong_host,
            port=url.port or 5432,
            user=url.username,
            database=url.database,
            ssl=tls,
            timeout=5,
        )
    except (OSError, asyncpg.PostgresError) as error:
        cursor = error
        while cursor is not None and not isinstance(
            cursor, ssl.SSLCertVerificationError
        ):
            cursor = cursor.__cause__ or cursor.__context__
        # OpenSSL X509_V_ERR_HOSTNAME_MISMATCH. A bad CA or expired
        # certificate must not satisfy this specific negative check.
        if cursor is None or cursor.verify_code != 62:
            raise AssertionError("wrong_hostname_tls_denial_unconfirmed") from None
    else:
        raise AssertionError("wrong_hostname_tls_unexpectedly_accepted")
    finally:
        if connection is not None:
            await connection.close()


async def test_actual0056_fresh_owner_migration_has_independent_source_and_tls_readback():
    """The admitted archive pin and a second physical TLS connection both matter."""
    url, admin_tls = _isolated_tls_database_config()
    identity, files, files_raw = fitted_measurement._read_hermetic_inputs()
    source = fitted_measurement.source_identity(identity, files, files_raw)
    admitted_manifest = os.environ.get(
        "CTCC_DEVVAL_CUSTODY_TEST_SOURCE_MANIFEST_SHA256"
    )
    assert admitted_manifest == source["manifest_sha256"]
    database = f"ctcc_native_obs56_{uuid4().hex[:12]}"
    admin = create_async_engine(
        url, poolclass=NullPool, connect_args={"ssl": admin_tls}
    )
    created = False
    try:
        async with admin.connect() as connection:
            await connection.execution_options(isolation_level="AUTOCOMMIT")
            await connection.execute(
                text(f"CREATE DATABASE {_delta56_identifier(database)}")
            )
            created = True
        result = await migrate_fresh_lab_owner_0056_v1(
            owner_url=url.set(database=database),
            ca_file=Path(os.environ["CTCC_DEVVAL_CUSTODY_TEST_TLS_CA_FILE"]),
            ca_sha256=os.environ["CTCC_DEVVAL_CUSTODY_TEST_TLS_CA_SHA256"],
            manifest_sha256=admitted_manifest,
        )
        assert result.manifest_sha256 == admitted_manifest
        assert result.migration_sha256 == source["migration_inventory_sha256"]
        assert result.database_head == "0056"
        assert result.actual_lab_connection_observed is True
        assert result.startup_admission is result.execution_authority is False
    finally:
        try:
            if created:
                async with admin.connect() as connection:
                    await connection.execution_options(isolation_level="AUTOCOMMIT")
                    await connection.execute(
                        text(
                            f"DROP DATABASE {_delta56_identifier(database)} WITH (FORCE)"
                        )
                    )
        finally:
            await admin.dispose()


def _migrate_owned_empty_to_0055(connection):
    scope = source_scopes.database0055_source_scope(_SOURCE_ROOT)
    config = Config(str(_SOURCE_ROOT / "alembic.ini"))
    config.set_main_option("path_separator", "os")
    config.set_main_option("script_location", str(_SOURCE_ROOT / "migrations"))
    script = ScriptDirectory.from_config(config)
    assert tuple(script.get_heads()) == ("0056",)
    assert script.get_revision("0056").down_revision == "0055"
    migration = MigrationContext.configure(
        connection,
        opts={
            "fn": lambda heads, _: script._upgrade_revs("0055", heads),
            "target_metadata": Base.metadata,
            "compare_type": True,
        },
    )
    assert migration.get_current_heads() == ()
    with Operations.context(migration), migration.begin_transaction():
        migration.run_migrations()
    assert (
        source_scopes.verify_database_scope(connection, _SOURCE_ROOT, scope)[
            "database_head"
        ]
        == "0055"
    )


def _reconstruct_owned_lab_archive(tmp_path, identity, files):
    """Test-only exact-tree archive from admitted in-container source bytes."""
    path = tmp_path / "source-exact-lab.tar"
    with tarfile.open(path, "w", pax_headers={"comment": identity["commit"]}) as tar:
        for name, item in sorted(files.items()):
            raw = (_SOURCE_ROOT / name).read_bytes()
            assert sha(raw) == item["sha256"]
            header = tarfile.TarInfo(name)
            header.mode = 0o755 if item["executable"] else 0o644
            header.size = len(raw)
            tar.addfile(header, io.BytesIO(raw))
    with tarfile.open(path, "r:") as tar:
        assert archive_checks.archive_tree(tar) == identity["tree"]
    return path, sha(path.read_bytes())


async def test_actual0056_non_superuser_owner_upgrades_0055_once_with_fresh_readback(
    tmp_path,
):
    """Historical superuser genesis, then a fresh restricted 0056 owner session."""
    url, tls = _isolated_tls_database_config()
    identity, files, files_raw = fitted_measurement._read_hermetic_inputs()
    source = fitted_measurement.source_identity(identity, files, files_raw)
    assert source["manifest_sha256"] == os.environ.get(
        "CTCC_DEVVAL_CUSTODY_TEST_SOURCE_MANIFEST_SHA256"
    )
    archive, archive_pin = _reconstruct_owned_lab_archive(tmp_path, identity, files)
    ca_file = Path(os.environ["CTCC_DEVVAL_CUSTODY_TEST_TLS_CA_FILE"])
    ca_sha = os.environ["CTCC_DEVVAL_CUSTODY_TEST_TLS_CA_SHA256"]
    database = f"ctcc_native_obs56_{uuid4().hex[:12]}"
    login = f"ctcc_ownerroute56_{uuid4().hex[:12]}"
    password = secrets.token_hex(24)
    secret_file = tmp_path / "owner-dsn"
    owner_url = url.set(database=database, username=login, password=password)
    secret_file.write_bytes(owner_url.render_as_string(hide_password=False).encode())
    secret_file.chmod(0o600)
    admin = create_async_engine(
        url, poolclass=NullPool, connect_args={"ssl": tls}, hide_parameters=True
    )
    owner = None
    role_created = database_created = control_granted = False
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
                text(
                    "INSERT INTO pg_temp.ctcc_owner_setup(name,password) "
                    "VALUES (:name,:password)"
                ),
                {"name": login, "password": password},
            )
            await connection.exec_driver_sql(
                """DO $ctcc$
                DECLARE role_name text; role_password text;
                BEGIN
                  SELECT name,password INTO STRICT role_name,role_password
                  FROM pg_temp.ctcc_owner_setup;
                  EXECUTE pg_catalog.format('CREATE ROLE %I LOGIN SUPERUSER PASSWORD %L',
                                            role_name,role_password);
                END $ctcc$"""
            )
        role_created = True
        async with admin.connect() as connection:
            await connection.execution_options(isolation_level="AUTOCOMMIT")
            await connection.exec_driver_sql(
                f"CREATE DATABASE {_delta56_identifier(database)} "
                f"OWNER {_delta56_identifier(login)}"
            )
        database_created = True
        async with admin.begin() as connection:
            await connection.exec_driver_sql(
                "GRANT EXECUTE ON FUNCTION pg_catalog.pg_control_system() "
                f"TO {_delta56_identifier(login)}"
            )
        control_granted = True
        owner = create_async_engine(
            owner_url,
            poolclass=NullPool,
            connect_args={"ssl": tls, "timeout": 10},
            hide_parameters=True,
        )
        async with owner.begin() as connection:
            await connection.run_sync(_migrate_owned_empty_to_0055)
        # Immutable 0050 requires the historical parent owner to be a
        # superuser. End that lab-only genesis session before the 0056 actor;
        # the actor must open a new, non-superuser physical connection.
        await owner.dispose()
        owner = None
        async with admin.begin() as connection:
            await connection.exec_driver_sql(
                f"ALTER ROLE {_delta56_identifier(login)} NOSUPERUSER"
            )
        owner = create_async_engine(
            owner_url,
            poolclass=NullPool,
            connect_args={"ssl": tls, "timeout": 10},
            hide_parameters=True,
        )
        async with owner.connect() as connection:
            row = tuple(
                (
                    await connection.execute(
                        text("""SELECT d.oid::bigint,d.datdba::bigint,
                                 r.rolsuper,r.rolcreatedb,r.rolcreaterole,
                                 (SELECT system_identifier::text
                                  FROM pg_catalog.pg_control_system()),
                                 current_user,session_user
                          FROM pg_catalog.pg_database d
                          JOIN pg_catalog.pg_roles r ON r.oid=d.datdba
                          WHERE d.datname=pg_catalog.current_database()""")
                    )
                ).one()
            )
        assert row[2:5] == (False, False, False)
        assert row[6:] == (login, login)
        route = owner_route.OwnerRoutePinsV1(
            source_manifest_sha256=source["manifest_sha256"],
            ca_sha256=ca_sha,
            cluster_system_identifier_sha256=sha(row[5].encode("ascii")),
            host=url.host,
            port=url.port,
            database=database,
            owner=login,
            database_oid=row[0],
            owner_oid=row[1],
            database_head="0055",
        )
        pins = owner_upgrade.OwnerUpgradePinsV1(
            source_commit=identity["commit"],
            source_tree=identity["tree"],
            source_archive_sha256=archive_pin,
            route=route,
        )
        async with owner.begin() as competing:
            await competing.execute(owner_upgrade._OWNER_LOCK)
            with pytest.raises(owner_upgrade.NativeOwnerUpgradeRefusal):
                await owner_upgrade.upgrade_owner_0055_to_0056_v1(
                    pins,
                    owner_dsn_file=secret_file,
                    ca_file=ca_file,
                    source_archive_file=archive,
                )
        async with owner.connect() as connection:
            assert (
                await connection.scalar(
                    text("SELECT version_num FROM public.alembic_version")
                )
                == "0055"
            )
        result = await owner_upgrade.upgrade_owner_0055_to_0056_v1(
            pins,
            owner_dsn_file=secret_file,
            ca_file=ca_file,
            source_archive_file=archive,
        )
        assert result["status"] == "PASS"
        assert result["database_head_before"] == "0055"
        assert result["database_head_after"] == "0056"
        assert result["independent_postcommit_readonly_observations"] == 3
        assert result["grants_installed"] is False
        assert result["startup_admission"] is result["execution_authority"] is False
        assert password not in json.dumps(result)
        with pytest.raises(owner_upgrade.NativeOwnerUpgradeRefusal):
            await owner_upgrade.upgrade_owner_0055_to_0056_v1(
                pins,
                owner_dsn_file=secret_file,
                ca_file=ca_file,
                source_archive_file=archive,
            )
    finally:
        try:
            if owner is not None:
                await owner.dispose()
        finally:
            try:
                if role_created:
                    async with admin.begin() as connection:
                        await connection.exec_driver_sql(
                            f"ALTER ROLE {_delta56_identifier(login)} NOSUPERUSER"
                        )
            finally:
                try:
                    if database_created:
                        async with admin.connect() as connection:
                            await connection.execution_options(
                                isolation_level="AUTOCOMMIT"
                            )
                            await connection.exec_driver_sql(
                                f"DROP DATABASE {_delta56_identifier(database)} WITH (FORCE)"
                            )
                finally:
                    try:
                        if control_granted:
                            async with admin.begin() as connection:
                                await connection.exec_driver_sql(
                                    "REVOKE EXECUTE ON FUNCTION pg_catalog.pg_control_system() "
                                    f"FROM {_delta56_identifier(login)}"
                                )
                    finally:
                        try:
                            if role_created:
                                async with admin.begin() as connection:
                                    await connection.exec_driver_sql(
                                        f"DROP ROLE {_delta56_identifier(login)}"
                                    )
                        finally:
                            try:
                                await admin.dispose()
                            finally:
                                secret_file.unlink(missing_ok=True)


async def test_actual0056_password_bearing_owner_route_preflight_is_readonly(
    tmp_path, monkeypatch
):
    """A lab owner, owned DB, CA and two physical reads; trust is not SCRAM."""
    url, tls = _isolated_tls_database_config()
    identity, files, files_raw = fitted_measurement._read_hermetic_inputs()
    source = fitted_measurement.source_identity(identity, files, files_raw)
    admitted_manifest = os.environ.get(
        "CTCC_DEVVAL_CUSTODY_TEST_SOURCE_MANIFEST_SHA256"
    )
    assert admitted_manifest == source["manifest_sha256"]
    ca_file = Path(os.environ["CTCC_DEVVAL_CUSTODY_TEST_TLS_CA_FILE"])
    ca_sha256 = os.environ["CTCC_DEVVAL_CUSTODY_TEST_TLS_CA_SHA256"]
    database = f"ctcc_native_obs56_{uuid4().hex[:12]}"
    login = f"ctcc_ownerroute56_{uuid4().hex[:12]}"
    password = secrets.token_hex(24)
    quoted_database = _delta56_identifier(database)
    quoted_login = _delta56_identifier(login)
    secret_file = tmp_path / "owner-dsn"
    secret_file.write_bytes(
        url.set(database=database, username=login, password=password)
        .render_as_string(hide_password=False)
        .encode("utf-8")
    )
    secret_file.chmod(0o600)
    admin = create_async_engine(
        url, poolclass=NullPool, connect_args={"ssl": tls}, hide_parameters=True
    )
    role_created = database_created = control_granted = False
    try:
        # The secret is a bound value in a transaction-local temp table. The
        # fixed DO statement never places its value in client SQL or JUnit.
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
                await connection.exec_driver_sql(
                    """
                    DO $ctcc$
                    DECLARE role_name text; role_password text;
                    BEGIN
                      SELECT name, password INTO STRICT role_name, role_password
                      FROM pg_temp.ctcc_owner_setup;
                      EXECUTE pg_catalog.format(
                        'CREATE ROLE %I LOGIN PASSWORD %L',
                        role_name, role_password
                      );
                    END $ctcc$
                    """
                )
        except Exception:  # noqa: BLE001 -- private lab password redaction
            raise AssertionError("owner_route_lab_role_setup_failed") from None
        role_created = True
        async with admin.connect() as connection:
            await connection.execution_options(isolation_level="AUTOCOMMIT")
            await connection.exec_driver_sql(
                f"CREATE DATABASE {quoted_database} OWNER {quoted_login}"
            )
        database_created = True
        migrated = await migrate_fresh_lab_owner_0056_v1(
            owner_url=url.set(database=database),
            ca_file=ca_file,
            ca_sha256=ca_sha256,
            manifest_sha256=admitted_manifest,
        )
        assert migrated.database_head == "0056"
        async with admin.begin() as connection:
            await connection.exec_driver_sql(
                "GRANT EXECUTE ON FUNCTION pg_catalog.pg_control_system() "
                f"TO {quoted_login}"
            )
        control_granted = True
        target = create_async_engine(
            url.set(database=database),
            poolclass=NullPool,
            connect_args={"ssl": tls},
        )
        try:
            async with target.begin() as connection:
                await connection.exec_driver_sql(
                    f"GRANT USAGE ON SCHEMA public TO {quoted_login}"
                )
                await connection.exec_driver_sql(
                    f"GRANT SELECT ON TABLE public.alembic_version TO {quoted_login}"
                )
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
        pins = owner_route.OwnerRoutePinsV1(
            source_manifest_sha256=admitted_manifest,
            ca_sha256=ca_sha256,
            cluster_system_identifier_sha256=sha(cluster_id.encode("ascii")),
            host=url.host,
            port=url.port,
            database=database,
            owner=login,
            database_oid=db_oid,
            owner_oid=role_oid,
            database_head="0056",
        )
        physical = []
        original_create = owner_route.create_async_engine

        def traced_create(*args, **kwargs):
            engine = original_create(*args, **kwargs)

            @event.listens_for(engine.sync_engine, "connect")
            def capture(dbapi_connection, _record):
                physical.append(dbapi_connection)

            return engine

        with monkeypatch.context() as patch:
            patch.setattr(owner_route, "create_async_engine", traced_create)
            receipt = await owner_route.preflight_owner_route_0056_v1(
                pins, owner_dsn_file=secret_file, ca_file=ca_file
            )
        assert len(physical) == 2 and physical[0] is not physical[1]
        assert receipt["status"] == "PASS"
        assert receipt["source_manifest_sha256"] == admitted_manifest
        assert receipt["database_owner_verified"] is True
        assert receipt["independent_readonly_observations"] == 2
        assert receipt["password_authentication_verified"] is False
        assert receipt["schema_drift_verified"] is False
        assert receipt["startup_admission"] is receipt["execution_authority"] is False
        assert password not in json.dumps(receipt)
        with pytest.raises(owner_route.OwnerRoutePreflightError):
            await owner_route.preflight_owner_route_0056_v1(
                replace(pins, cluster_system_identifier_sha256="0" * 64),
                owner_dsn_file=secret_file,
                ca_file=ca_file,
            )
        with pytest.raises(owner_route.OwnerRoutePreflightError):
            await owner_route.preflight_owner_route_0056_v1(
                replace(pins, owner_oid=role_oid + 1),
                owner_dsn_file=secret_file,
                ca_file=ca_file,
            )
        with pytest.raises(owner_route.OwnerRoutePreflightError):
            await owner_route.preflight_owner_route_0056_v1(
                replace(pins, ca_sha256="0" * 64),
                owner_dsn_file=secret_file,
                ca_file=ca_file,
            )
    finally:
        try:
            if database_created:
                async with admin.connect() as connection:
                    await connection.execution_options(isolation_level="AUTOCOMMIT")
                    await connection.exec_driver_sql(
                        f"DROP DATABASE {quoted_database} WITH (FORCE)"
                    )
            if control_granted:
                async with admin.begin() as connection:
                    await connection.exec_driver_sql(
                        "REVOKE EXECUTE ON FUNCTION pg_catalog.pg_control_system() "
                        f"FROM {quoted_login}"
                    )
            if role_created:
                async with admin.begin() as connection:
                    await connection.exec_driver_sql(f"DROP ROLE {quoted_login}")
        finally:
            await admin.dispose()
            secret_file.unlink(missing_ok=True)


@pytest.fixture
async def fitted_database():
    url, tls = _isolated_tls_database_config()
    database = "ctcc_g3f_" + uuid4().hex
    names = {key: "ctcc_g3f_" + uuid4().hex for key in _ROLES}

    def make_engine(target):
        return create_async_engine(
            target, poolclass=NullPool, connect_args={"ssl": tls}
        )

    admin = make_engine(url)
    created_database = False
    created_roles = []
    engines = {}
    try:
        async with admin.connect() as connection:
            await connection.execution_options(isolation_level="AUTOCOMMIT")
            await connection.execute(text(f"CREATE DATABASE {database}"))
        created_database = True
        async with admin.begin() as connection:
            for role in names.values():
                await connection.execute(text(f"CREATE ROLE {role} LOGIN"))
        created_roles = list(names.values())
        db_url = url.set(database=database)
        owner = engines["owner"] = make_engine(db_url)
        async with owner.begin() as connection:
            for revision in ("0024", "0026", "0028", "0029", "0030", "0031", "0032"):
                await connection.run_sync(migrate, revision, "upgrade")
            await connection.run_sync(_migrate_fitted, "upgrade")
        await _grant(owner, names, _ROLES)
        for key, role in names.items():
            engines[key] = make_engine(db_url.set(username=role))
        async with owner.connect() as connection:
            db_oid = await connection.scalar(
                text(
                    "SELECT oid FROM pg_catalog.pg_database WHERE datname = current_database()"
                )
            )
            role_oids = {}
            for key, role in names.items():
                role_oids[key] = await connection.scalar(
                    text("SELECT oid FROM pg_catalog.pg_roles WHERE rolname=:role"),
                    {"role": role},
                )
        repos = {
            key: Gate3FittedCandidateCustodyV1(
                async_sessionmaker(engines[key], expire_on_commit=False),
                role=key,
                expected_host=db_url.host,
                expected_port=db_url.port or 5432,
                expected_database_name=database,
                expected_login_name=names[key],
                expected_database_oid=db_oid,
                expected_login_oid=role_oids[key],
                expected_publisher_oid=role_oids["publisher"],
                expected_observer_oid=role_oids["observer"],
                expected_evaluator_oid=role_oids["evaluator"],
            )
            for key in ("publisher", "observer", "reader", "evaluator")
        }
        yield engines, repos, names
    finally:
        for engine in reversed(tuple(engines.values())):
            await engine.dispose()
        if created_database:
            async with admin.connect() as connection:
                await connection.execution_options(isolation_level="AUTOCOMMIT")
                await connection.execute(text(f"DROP DATABASE {database}"))
        if created_roles:
            async with admin.begin() as connection:
                for role in created_roles:
                    await connection.execute(text(f"DROP ROLE {role}"))
        await admin.dispose()


def _migrate_runtime_custody_head(connection):
    config = Config(str(_SOURCE_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_SOURCE_ROOT / "migrations"))
    script = ScriptDirectory.from_config(config)
    scope = source_scopes.actual_current_source_scope(_SOURCE_ROOT)
    assert scope.actual_source_head == scope.database_target == "0056"
    assert script.get_current_head() == "0056"
    assert script.get_revision("0056").down_revision == "0055"
    context = MigrationContext.configure(
        connection,
        opts={
            "fn": lambda heads, _: script._upgrade_revs("0056", heads),
            "target_metadata": Base.metadata,
            "compare_type": True,
        },
    )
    assert context.get_current_heads() == ()
    with Operations.context(context), context.begin_transaction():
        context.run_migrations()
    readback = source_scopes.verify_database_scope(connection, _SOURCE_ROOT, scope)
    assert readback["source_head"] == readback["database_head"] == "0056"


@pytest.fixture
async def runtime_custody_actual56():
    url, tls = _isolated_tls_database_config()
    assert (url.username, url.database, url.port) == ("ctcc", "ctcc", 5432)
    assert tls.check_hostname and tls.verify_mode == ssl.CERT_REQUIRED

    def engine(target):
        return create_async_engine(
            target, poolclass=NullPool, connect_args={"ssl": tls}
        )

    database = f"ctcc_g3_raw_{uuid4().hex}"
    login = f"ctcc_g3_raw_{uuid4().hex}"
    target = url.set(database=database)
    provisioner, owner, restricted = (
        engine(url),
        engine(target),
        engine(target.set(username=login)),
    )
    created_database = created_role = False
    try:
        async with provisioner.connect() as connection:
            await connection.execution_options(isolation_level="AUTOCOMMIT")
            assert await connection.scalar(
                text("SELECT current_setting('server_version_num')::int")
            ) in range(170000, 180000)
            await connection.execute(text(f'CREATE DATABASE "{database}"'))
            created_database = True
        async with provisioner.begin() as connection:
            await connection.execute(text(f'CREATE ROLE "{login}" LOGIN'))
        created_role = True
        async with owner.begin() as connection:
            await connection.run_sync(_migrate_runtime_custody_head)
            for grant in (
                "USAGE ON SCHEMA public",
                "SELECT ON TABLE public.alembic_version",
                "EXECUTE ON FUNCTION public.public_runtime_bundle_v1_append(text,text,text,bytea)",
                "EXECUTE ON FUNCTION public.public_runtime_bundle_v1_read(text)",
            ):
                await connection.execute(text(f'GRANT {grant} TO "{login}"'))
        yield owner, restricted, database, login
    finally:
        await restricted.dispose()
        await owner.dispose()
        try:
            if created_database:
                async with provisioner.connect() as connection:
                    await connection.execution_options(isolation_level="AUTOCOMMIT")
                    await connection.execute(
                        text(f'DROP DATABASE "{database}" WITH (FORCE)')
                    )
        finally:
            try:
                if created_role:
                    async with provisioner.begin() as connection:
                        await connection.execute(text(f'DROP ROLE "{login}"'))
            finally:
                await provisioner.dispose()


async def test_actual56_runtime_raw_custody_seals_and_reads_under_tls(
    runtime_custody_actual56,
):
    owner, restricted, database, login = runtime_custody_actual56
    async with restricted.connect() as connection:
        identity = (
            await connection.execute(
                text("""
                    SELECT pg_catalog.current_database(), SESSION_USER::text,
                           SESSION_USER = CURRENT_USER,
                           pg_catalog.inet_server_port(),
                           EXISTS (SELECT 1 FROM pg_catalog.pg_stat_ssl
                                   WHERE pid = pg_catalog.pg_backend_pid() AND ssl)
                    """)
            )
        ).one()
        assert tuple(identity) == (database, login, True, 5432, True)
        assert (
            await connection.scalar(
                text("SELECT version_num FROM public.alembic_version")
            )
            == "0056"
        )
        with pytest.raises(DBAPIError) as denied:
            await connection.execute(
                text("SELECT 1 FROM public.public_runtime_raw_bundles_v1")
            )
        assert denied.value.orig.sqlstate == "42501"

    factory = async_sessionmaker(restricted, autoflush=False, expire_on_commit=False)
    repository = raw_custody.RuntimeBundleCustodyRepository(factory)
    await repository.verify_deployment()
    bundle = b'{"declared_synthetic":"current0056_runtime_raw_custody"}'
    pins = {
        "expected_sha256": sha(bundle),
        "proof_envelope_sha256": sha(b"synthetic-current0056-raw-custody-proof"),
    }
    receipt = await repository.seal_bundle(bundle_raw=bundle, **pins)
    committed = raw_custody._consume_runtime_bundle_custody_receipt(
        receipt,
        expected_bundle_sha256=pins["expected_sha256"],
        expected_proof_envelope_sha256=pins["proof_envelope_sha256"],
    )
    assert committed.bundle_raw == bundle
    assert committed.append_transaction_id != committed.readback_transaction_id

    fresh = raw_custody.RuntimeBundleCustodyRepository(factory)
    await fresh.verify_deployment()
    retained = await fresh.read_retained_bundle_by_pins(**pins)
    assert retained.bundle_id == committed.bundle_id
    assert retained.bundle_raw == bundle
    assert retained.bundle_sha256 == pins["expected_sha256"]
    assert retained.proof_envelope_sha256 == pins["proof_envelope_sha256"]
    assert retained.readback_transaction_id not in {
        None,
        committed.append_transaction_id,
        committed.readback_transaction_id,
    }
    assert retained.execution_authority is False

    async with owner.begin() as connection:
        await connection.execute(
            text("""
                CREATE TABLE public.ctcc_g3_raw_extra_parent (
                    bundle_id varchar(32) REFERENCES public.public_runtime_raw_bundles_v1(bundle_id)
                )
                """)
        )
    with pytest.raises(
        raw_custody.RuntimeBundleCustodyError,
        match="runtime_bundle_custody_unavailable",
    ):
        await fresh.read_retained_bundle_by_pins(**pins)
    with pytest.raises(
        raw_custody.RuntimeBundleCustodyError,
        match="runtime_bundle_custody_unavailable",
    ):
        await raw_custody.RuntimeBundleCustodyRepository(factory).verify_deployment()


@pytest.fixture(scope="module")
def fitted_inputs_source():
    plan = ProspectiveDevvalCampaignPlanV1(**plan_input())
    offset = datetime(2026, 10, 1, tzinfo=UTC) - plan.development.start_at

    def move(window):
        return window.model_copy(
            update={
                "start_at": window.start_at + offset,
                "end_at": window.end_at + offset,
            }
        )

    source_plan = ProspectiveDevvalCampaignPlanV1.model_validate(
        plan.model_copy(
            update={
                "planned_at": plan.planned_at + offset,
                "development": move(plan.development),
                "validation": move(plan.validation),
            }
        ).model_dump()
    )
    return aligned_synthetic_case.__wrapped__(synthetic_grid(source_plan))


async def _now(engine):
    async with engine.connect() as connection:
        return await connection.scalar(text("SELECT pg_catalog.clock_timestamp()"))


async def _fitted_proposal(engines, fitted_inputs_source):
    batch, study, trials = fitted_inputs_source
    now = await _now(engines["owner"])
    start = now.replace(second=0, microsecond=0) + timedelta(minutes=3)
    end = start + timedelta(minutes=1)
    formal = _formal_preregistration(batch, study, trials, holdout_start=start)
    coordinate = ProspectiveCoordinatePlanV1(
        holdout_id=formal.prospective_holdout.holdout_id,
        origin="https://openapi.okx.com",
        instrument_ids=formal.training_dataset.instrument_ids,
        start_at=start,
        end_at=end,
        expected_rows=2,
    )
    window = formal.prospective_holdout.model_copy(
        update={
            "end_at": end,
            "first_permitted_access_at": end,
            "publication_lag_seconds": 0,
            "expected_rows": 2,
            "expected_artifact_count": 2,
            "coordinate_plan_sha256": coordinate.canonical_sha256(),
        }
    )
    seal = Gate3ProspectivePreregistration.model_validate(
        formal.model_copy(update={"prospective_holdout": window}).model_dump()
    )
    seal_pin = seal.canonical_sha256()
    archive_pin = sha(b"synthetic TLS custody source archive")
    binding = build_devval_candidate_binding(
        study,
        batch=batch,
        trial_result=trials,
        preregistration=seal,
        expected_preregistration_sha256=seal_pin,
        source_archive_sha256=archive_pin,
    )
    _, legacy, _ = _inputs(study)
    inputs = FittedCandidateInputsV1(
        binding=binding,
        expected_binding_sha256=binding.sha256,
        batch=batch,
        study=study,
        trial_result=trials,
        legacy_scores=legacy,
        preregistration=seal,
        expected_preregistration_sha256=seal_pin,
        source_archive_sha256=archive_pin,
    )
    schedule = build_capture_schedule(
        seal=seal, coordinate_plan=coordinate, planned_at=now
    )
    await append_seal(engines["seal"], seal_record(seal, schedule))
    return inputs, coordinate


async def _append(engine, statement, **params):
    async with engine.begin() as connection:
        await connection.execute(text(statement), params)


async def _accept_seal(engines, inputs):
    await _append(
        engines["seal_ack"],
        "SELECT public.gate3_prereg_seal_ack_append(:seal)",
        seal=inputs.expected_preregistration_sha256,
    )


async def test_fitted_binding_role_separation_and_immutable_readback(
    fitted_database, fitted_inputs_source
):
    engines, repos, names = fitted_database
    inputs, coordinate = await _fitted_proposal(engines, fitted_inputs_source)
    seal = inputs.preregistration
    with pytest.raises(Gate3FittedCustodyError):
        await repos["publisher"].publish(inputs)
    await _accept_seal(engines, inputs)
    before_ack = build_capture_schedule(
        seal=seal, coordinate_plan=coordinate, planned_at=await _now(engines["owner"])
    )
    with pytest.raises(DBAPIError, match="gate3_fitted"):
        await append_pin(engines["schedule"], pin_record(seal, before_ack))
    committed = await repos["publisher"].publish(inputs)
    with pytest.raises(DBAPIError):
        await _append(
            engines["publisher"],
            "SELECT public.gate3_fitted_binding_ack_append(:seal,:binding)",
            seal=seal.canonical_sha256(),
            binding=inputs.binding.sha256,
        )
    ack = await repos["observer"].acknowledge(inputs)
    observed = await repos["reader"].read_acknowledged(
        inputs, expected_ack_sha256=ack.ack_sha256
    )
    assert observed.ack_sha256 == ack.ack_sha256
    assert ack.binding_sha256 == committed.binding_sha256 == inputs.binding.sha256
    assert ack.publication_xid != ack.acknowledgement_xid
    after_ack = build_capture_schedule(
        seal=seal, coordinate_plan=coordinate, planned_at=await _now(engines["owner"])
    )
    await append_pin(engines["schedule"], pin_record(seal, after_ack))
    with pytest.raises(DBAPIError, match="gate3_fitted"):
        await _append(
            engines["owner"],
            "UPDATE public.gate3_fitted_candidate_acks SET binding_sha256=binding_sha256",
        )
    await _append(
        engines["owner"],
        f"GRANT SELECT ON public.gate3_fitted_candidate_acks TO {names['reader']}",
    )
    with pytest.raises(Gate3FittedCustodyError):
        await repos["reader"].read_acknowledged(
            inputs, expected_ack_sha256=ack.ack_sha256
        )


async def test_fitted_ack_recovery_reads_one_committed_ack_under_tls(
    fitted_database, fitted_inputs_source, monkeypatch, tmp_path
):
    engines, repos, _ = fitted_database
    inputs, _ = await _fitted_proposal(engines, fitted_inputs_source)
    await _accept_seal(engines, inputs)
    committed = await repos["publisher"].publish(inputs)

    async def lost_readback(*_args, **_kwargs):
        raise Gate3FittedCustodyError("TEST_ACK_RECEIPT_LOST_AFTER_COMMIT")

    observer = repos["observer"]
    reader = repos["reader"]
    with monkeypatch.context() as scoped:
        scoped.setattr(observer, "_read_acknowledged", lost_readback)
        with pytest.raises(
            Gate3FittedCustodyError, match="TEST_ACK_RECEIPT_LOST_AFTER_COMMIT"
        ):
            await observer.acknowledge(inputs)

    async def counts():
        async with engines["owner"].connect() as connection:
            result = await connection.execute(
                text(
                    "SELECT (SELECT count(*) FROM public.gate3_fitted_candidate_bindings), "
                    "(SELECT count(*) FROM public.gate3_fitted_candidate_acks), "
                    "(SELECT count(*) FROM public.gate3_fitted_candidate_evaluations)"
                )
            )
            return tuple(result.one())

    assert await counts() == (1, 1, 0)
    with pytest.raises(Gate3FittedCustodyError, match="^FITTED_CUSTODY_ROLE_REFUSED$"):
        await observer.recover_acknowledgement(inputs)
    with monkeypatch.context() as scoped:
        # The real observer retains its ACK append grant under this app label.
        scoped.setattr(observer, "_role", "reader")
        with pytest.raises(
            Gate3FittedCustodyError, match="^FITTED_CUSTODY_RECOVERY_ROLE_REFUSED$"
        ):
            await observer.recover_acknowledgement(inputs)

    recovered = await reader.recover_acknowledgement(inputs)
    observed = recovered.acknowledgement
    assert observed.seal_sha256 == inputs.expected_preregistration_sha256
    assert observed.binding_sha256 == committed.binding_sha256 == inputs.binding.sha256
    assert observed.publication_xid == committed.publication_xid
    assert observed.acknowledgement_xid != committed.publication_xid
    assert not recovered.independent_ack_pin_verified
    assert not recovered.predictive_oos_eligible and not recovered.execution_authority

    pin_path = tmp_path / "reviewed-ack.sha256"
    with pin_path.open("x", encoding="ascii") as stream:
        stream.write(observed.ack_sha256 + "\n")
    pinned = await reader.read_acknowledged(
        inputs, expected_ack_sha256=pin_path.read_text(encoding="ascii").strip()
    )
    assert pinned.ack_sha256 == observed.ack_sha256
    assert pinned.acknowledgement_xid == observed.acknowledgement_xid
    assert pinned.database_read_at >= observed.database_read_at
    assert await counts() == (1, 1, 0)


async def test_fitted_evaluation_is_once_and_after_permitted_access(
    fitted_database, fitted_inputs_source, tmp_path
):
    engines, repos, _ = fitted_database
    inputs, _ = await _fitted_proposal(engines, fitted_inputs_source)
    await _accept_seal(engines, inputs)
    await repos["publisher"].publish(inputs)
    ack = await repos["observer"].acknowledge(inputs)
    seal = inputs.preregistration
    permitted = seal.prospective_holdout.first_permitted_access_at
    manifest = b"synthetic holdout manifest"
    dataset = b"synthetic holdout bytes"
    holdout_dataset = seal.training_dataset.model_copy(
        update={
            "dataset_id": "dataset:synthetic:fitted-holdout:v1",
            "manifest_sha256": sha(manifest),
            "content_sha256": sha(dataset),
            "expected_rows": 2,
            "first_event_at": seal.prospective_holdout.end_at,
            "last_event_at": seal.prospective_holdout.end_at,
            "frozen_at": permitted,
            "event_time_field": "closed_at",
        }
    )
    receipt = freeze_prospective_holdout_receipt(
        Gate3ProspectiveHoldoutReceipt(
            receipt_id="receipt:synthetic:fitted-holdout:v1",
            recorded_at=permitted,
            preregistration=seal,
            preregistration_sha256=seal.canonical_sha256(),
            first_accessed_at=permitted,
            holdout_dataset=holdout_dataset,
            acquisition_plan_sha256=seal.prospective_holdout.coordinate_plan_sha256,
            artifact_count=2,
            all_artifacts_verified=True,
            access_policy_compliant=True,
            access_outcome=ProspectiveAccessOutcome.SEALED_UNREAD,
            candidate_changed_after_preregistration=False,
            predictive_oos_eligible=True,
        )
    )
    with pytest.raises(DBAPIError, match="gate3_fitted"):
        await _append(
            engines["evaluator"],
            "SELECT public.gate3_fitted_evaluation_append(:seal,:binding,:ack,:receipt,:dataset,:attempt)",
            seal=seal.canonical_sha256(),
            binding=inputs.binding.sha256,
            ack=ack.acknowledgement_xid,
            receipt=receipt.sha256,
            dataset=sha(dataset),
            attempt=sha(b"early"),
        )
    manifest_path, dataset_path = tmp_path / "manifest", tmp_path / "dataset"
    manifest_path.write_bytes(manifest)
    dataset_path.write_bytes(dataset)
    async with asyncio.timeout(250):
        while await _now(engines["owner"]) < permitted:
            await asyncio.sleep(1)

    evaluator = repos["evaluator"]
    db_now = await _now(engines["owner"])
    with pytest.raises(
        Gate3FittedCustodyError, match="^FITTED_CUSTODY_GLOBAL_TIME_NOT_REACHED$"
    ):
        await evaluator.reserve_evaluation(
            inputs,
            expected_ack_sha256=ack.ack_sha256,
            receipt_sha256=receipt.sha256,
            dataset_sha256=sha(dataset),
            not_before=db_now + timedelta(hours=1),
        )

    async def reader():
        return await read_fitted_prospective_holdout_once(
            custody=evaluator,
            inputs=inputs,
            expected_ack_sha256=ack.ack_sha256,
            receipt_bytes=receipt.payload,
            expected_receipt_sha256=receipt.sha256,
            manifest_path=manifest_path,
            dataset_path=dataset_path,
        )

    results = await asyncio.gather(reader(), reader(), return_exceptions=True)
    reads = [
        result
        for result in results
        if isinstance(result, ControlledFittedHoldoutReadV1)
    ]
    assert (
        len(reads) == 1
        and sum(isinstance(result, Gate3FittedCustodyError) for result in results) == 1
    )
    observed = reads[0]
    assert (observed.manifest_bytes, observed.dataset_bytes) == (manifest, dataset)
    assert observed.reservation.reserved_at >= permitted
    assert observed.reservation.receipt_sha256 == receipt.sha256
    assert observed.reservation.dataset_sha256 == sha(dataset)
    assert (
        not observed.independent_first_access_verified
        and not observed.execution_authority
    )
    # The later SQL gate now reaches the append function. The durable unique
    # reservation still rejects the second attempt on this exact seal.
    with pytest.raises(
        Gate3FittedCustodyError, match="^FITTED_CUSTODY_TRANSACTION_UNRESOLVED$"
    ):
        await evaluator.reserve_evaluation(
            inputs,
            expected_ack_sha256=ack.ack_sha256,
            receipt_sha256=receipt.sha256,
            dataset_sha256=sha(dataset),
            not_before=permitted,
        )
    async with engines["owner"].connect() as connection:
        assert (
            await connection.scalar(
                text("SELECT count(*) FROM public.gate3_fitted_candidate_evaluations")
            )
            == 1
        )


async def test_fitted_binding_refuses_changed_bytes_and_empty_downgrade(
    fitted_database, fitted_inputs_source
):
    engines, repos, names = fitted_database
    owner = engines["owner"]
    async with owner.begin() as connection:
        await connection.run_sync(_migrate_fitted, "downgrade")
        assert (
            await connection.scalar(
                text(
                    "SELECT pg_catalog.to_regclass('public.gate3_fitted_candidate_bindings')"
                )
            )
            is None
        )
        await connection.run_sync(_migrate_fitted, "upgrade")
    await _grant(owner, names, ("publisher", "observer", "reader", "evaluator"))
    inputs, _ = await _fitted_proposal(engines, fitted_inputs_source)
    await _accept_seal(engines, inputs)
    raw = inputs.binding.payload
    changed = json.loads(raw)
    changed["selected_trial_configuration_sha256"] = sha(b"wrong selected trial")
    for payload in (b" " + raw, canonical(changed)):
        with pytest.raises(DBAPIError, match="gate3_fitted"):
            await _append(
                engines["publisher"],
                "SELECT public.gate3_fitted_binding_append(:seal,:binding,:raw)",
                seal=inputs.expected_preregistration_sha256,
                binding=sha(payload),
                raw=payload.decode("utf-8"),
            )
    await repos["publisher"].publish(inputs)
    with pytest.raises(DBAPIError, match="gate3_fitted"):
        async with owner.begin() as connection:
            await connection.run_sync(_migrate_fitted, "downgrade")


# DB0056 fitted-delta physical catalog: diagnostic only, no native startup seal.
def _delta56_identifier(value: str) -> str:
    assert value.isascii() and value.replace("_", "").isalnum()
    assert value.startswith("ctcc_") and len(value) < 63
    return '"' + value + '"'


@pytest.fixture
async def _delta56_two_fresh_databases():
    url, tls = _isolated_tls_database_config()
    assert (url.username, url.database, url.port) == ("ctcc", "ctcc", 5432)
    assert tls.check_hostname and tls.verify_mode == ssl.CERT_REQUIRED
    fitted = {
        key: f"ctcc_g3f_{key}_{uuid4().hex[:12]}"
        for key in ("publisher", "observer", "reader", "evaluator")
    }
    demo = tuple(
        f"ctcc_native_{key}_{uuid4().hex[:12]}"
        for key in ("writer", "custody", "frame")
    )
    names = (*fitted.values(), *demo)
    assert len({*names, "ctcc"}) == 8
    databases = tuple(f"ctcc_g3fcat_{uuid4().hex[:12]}" for _ in range(2))
    assert len(set(databases)) == 2
    admin = create_async_engine(url, poolclass=NullPool, connect_args={"ssl": tls})
    engines = []
    created_roles = []
    created_databases = []
    try:
        fitted_delta.pinned_migration(_SOURCE_ROOT)
        for name in names:
            async with admin.begin() as connection:
                await connection.execute(
                    text(f"CREATE ROLE {_delta56_identifier(name)} LOGIN")
                )
            created_roles.append(name)
        for database in databases:
            async with admin.connect() as connection:
                await connection.execution_options(isolation_level="AUTOCOMMIT")
                await connection.execute(
                    text(f"CREATE DATABASE {_delta56_identifier(database)}")
                )
            created_databases.append(database)
            engine = create_async_engine(
                url.set(database=database),
                poolclass=NullPool,
                connect_args={"ssl": tls},
            )
            engines.append(engine)
            async with engine.begin() as connection:
                await connection.run_sync(_migrate_runtime_custody_head)
                for role in names:
                    await connection.execute(
                        text(
                            "GRANT USAGE ON SCHEMA public TO "
                            f"{_delta56_identifier(role)}"
                        )
                    )
                for kind, role in fitted.items():
                    for signature in _ROLES[kind]:
                        await connection.execute(
                            text(
                                "GRANT EXECUTE ON FUNCTION public."
                                f"{signature} TO {_delta56_identifier(role)}"
                            )
                        )
        yield engines, fitted, demo, databases
    finally:
        for engine in reversed(engines):
            await engine.dispose()
        try:
            for database in reversed(created_databases):
                async with admin.connect() as connection:
                    await connection.execution_options(isolation_level="AUTOCOMMIT")
                    await connection.execute(
                        text(
                            f"DROP DATABASE {_delta56_identifier(database)} WITH (FORCE)"
                        )
                    )
        finally:
            try:
                for role in reversed(created_roles):
                    async with admin.begin() as connection:
                        await connection.execute(
                            text(f"DROP ROLE {_delta56_identifier(role)}")
                        )
            finally:
                await admin.dispose()


async def _delta56_read_catalog(
    engine, *, capture_cluster_identity=False
) -> tuple[dict, str, int, str | None]:
    async with engine.connect() as connection, connection.begin():
        await connection.execute(
            text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        )
        await connection.execute(text("SET LOCAL search_path TO pg_catalog, pg_temp"))
        result = await connection.execute(text(fitted_delta.CATALOG_SQL))
        value = result.scalar_one()
        if isinstance(value, str):
            value = json.loads(value)
        assert type(value) is dict
        identity = (
            await connection.execute(
                text("""
                SELECT pg_catalog.current_database(), d.oid
                FROM pg_catalog.pg_database d
                WHERE d.datname=pg_catalog.current_database()
            """)
            )
        ).one()
        assert type(identity[0]) is str and type(identity[1]) is int
        cluster_identifier = None
        if capture_cluster_identity:
            cluster_identifier = await connection.scalar(
                text(
                    "SELECT system_identifier::text FROM pg_catalog.pg_control_system()"
                )
            )
            assert type(cluster_identifier) is str and cluster_identifier.isdecimal()
        return value, identity[0], identity[1], cluster_identifier


async def _delta56_owner_change(engine, sql: str) -> None:
    async with engine.begin() as connection:
        await connection.execute(text(sql))


async def test_actual0056_two_fresh_fitted_catalogs_and_drift_refusal(
    _delta56_two_fresh_databases,
):
    engines, fitted, demo, databases = _delta56_two_fresh_databases
    roles = {"owner": "ctcc", "demo_roles": demo, "fitted_roles": fitted}
    retain_measurement = os.environ.get(devval_catalog.HERMETIC_CATALOG_ENV) == "1"
    first, first_name, first_oid, cluster_identifier = await _delta56_read_catalog(
        engines[0], capture_cluster_identity=retain_measurement
    )
    second, second_name, second_oid, _ = await _delta56_read_catalog(engines[1])
    assert (first_name, second_name) == databases and first_oid != second_oid
    expected_commit_guards = [
        ("gate3_fitted_ack_commit_guard", "gate3_fitted_candidate_acks"),
        ("gate3_fitted_binding_commit_guard", "gate3_fitted_candidate_bindings"),
        ("gate3_fitted_evaluation_commit_guard", "gate3_fitted_candidate_evaluations"),
    ]
    for document in (first, second):
        observed_commit_guards = sorted(
            (row["name"], row["table"])
            for row in document["constraints"]
            if row["type"] == "t"
            and row["validated"] is True
            and row["deferrable"] is True
            and row["deferred"] is True
        )
        assert observed_commit_guards == expected_commit_guards
    digest = fitted_delta.inspect_catalog_document(first, **roles)
    assert fitted_delta.inspect_catalog_document(second, **roles) == digest
    assert sha(canonical(first)) == digest
    if retain_measurement:
        assert cluster_identifier is not None
        fitted_measurement.retain_hermetic_measurement(
            catalog=first,
            **roles,
            cluster_system_identifier=cluster_identifier,
            database_name=first_name,
            database_oid=first_oid,
        )
    with pytest.raises(
        fitted_delta.CatalogRefusal, match="fitted_catalog_native_reference_unsealed"
    ):
        fitted_delta.require_native_reference(first, **roles)

    trials = (
        (
            (
                "ALTER FUNCTION public.gate3_fitted_binding_read(text) "
                "SET search_path=pg_catalog,public,pg_temp"
            ),
            (
                "ALTER FUNCTION public.gate3_fitted_binding_read(text) "
                "SET search_path=pg_catalog,pg_temp"
            ),
            "fitted_catalog_function_shape_changed",
        ),
        (
            (
                "GRANT EXECUTE ON FUNCTION public.gate3_fitted_binding_read(text) "
                f"TO {_delta56_identifier(demo[0])}"
            ),
            (
                "REVOKE EXECUTE ON FUNCTION public.gate3_fitted_binding_read(text) "
                f"FROM {_delta56_identifier(demo[0])}"
            ),
            "fitted_catalog_function_acl_changed",
        ),
        (
            (
                "ALTER TABLE public.gate3_capture_schedule_key_claims "
                "DISABLE TRIGGER gate3_fitted_candidate_claim_guard"
            ),
            (
                "ALTER TABLE public.gate3_capture_schedule_key_claims "
                "ENABLE ALWAYS TRIGGER gate3_fitted_candidate_claim_guard"
            ),
            "fitted_catalog_trigger_changed",
        ),
        (
            (
                "GRANT SELECT ON TABLE public.gate3_fitted_candidate_acks "
                f"TO {_delta56_identifier(fitted['reader'])}"
            ),
            (
                "REVOKE SELECT ON TABLE public.gate3_fitted_candidate_acks "
                f"FROM {_delta56_identifier(fitted['reader'])}"
            ),
            "fitted_catalog_table_acl_changed",
        ),
    )
    for mutation, undo, refusal in trials:
        await _delta56_owner_change(engines[1], mutation)
        try:
            changed, name, oid, _ = await _delta56_read_catalog(engines[1])
            assert (name, oid) == (second_name, second_oid)
            with pytest.raises(fitted_delta.CatalogRefusal, match=refusal):
                fitted_delta.inspect_catalog_document(changed, **roles)
            assert changed != first
        finally:
            await _delta56_owner_change(engines[1], undo)
        restored, name, oid, _ = await _delta56_read_catalog(engines[1])
        assert (name, oid) == (second_name, second_oid)
        assert fitted_delta.inspect_catalog_document(restored, **roles) == digest


_ROLE_OBS56_IDENTITY = text("""
SELECT current_user::text, session_user::text,
       pg_catalog.current_setting('role'), pg_catalog.pg_backend_pid(),
       (SELECT oid::bigint FROM pg_catalog.pg_roles WHERE rolname=current_user),
       EXISTS(SELECT 1 FROM pg_catalog.pg_stat_ssl
              WHERE pid=pg_catalog.pg_backend_pid() AND ssl),
       pg_catalog.current_setting('server_version_num'),
       (SELECT oid::bigint FROM pg_catalog.pg_database
        WHERE datname=pg_catalog.current_database()),
       pg_catalog.pg_postmaster_start_time(),
       pg_catalog.inet_server_addr()::text,
       pg_catalog.inet_server_port()
""")
_ROLE_OBS56_REFUSAL = "^native_roles_0056_observation_unavailable$"


_GRANT_LOCK_0056 = text("SELECT pg_catalog.pg_advisory_xact_lock(5883109675522810055)")
_GRANT_TX_SETTINGS = (
    "SET TRANSACTION ISOLATION LEVEL READ COMMITTED",
    "SET TRANSACTION READ WRITE",
    *owner_pregrant._OWNER_SETTINGS[2:],
)


async def _read_test_fitted_write_graph(connection, owner_name, demo):
    await connection.exec_driver_sql("SET LOCAL search_path TO pg_catalog, pg_temp")
    document = await connection.scalar(text(fitted_delta.OWNER_READWRITE_CATALOG_SQL))
    fitted_delta.inspect_owner_readwrite_catalog(
        document, owner=owner_name, demo_roles=demo
    )
    await connection.exec_driver_sql(
        "SET LOCAL search_path TO pg_catalog, public, pg_temp"
    )
    # Compare only physical facts across profiles after validating each actual
    # transaction separately. Neither digest becomes a startup reference.
    return {
        key: value
        for key, value in document.items()
        if key not in {"read_only", "read_committed"}
    }


async def _install_test_demo_grants(owner, demo):
    names = dict(zip(("writer", "custody", "frame_reader"), demo, strict=True))
    sql = owner_pregrant.exact_42_grant_sql_0056(**names)
    owner_name = owner.url.username
    assert re.fullmatch(r"ctcc_native_obs56_[0-9a-f]{12}", owner.url.database)
    fitted_delta.pinned_migration(_SOURCE_ROOT)
    owner_factory = async_sessionmaker(owner, autoflush=False, expire_on_commit=False)
    async with owner_factory() as session, session.begin():
        for setting in observation._OWNER_SETTINGS:
            await session.execute(text(setting))
        read_identity = await observation._identity_at_0056(session, owner_name)
        read_graph = await observation._catalog(session, fitted_delta.CATALOG_SQL)
        fitted_delta.inspect_catalog_document(
            read_graph, owner=owner_name, demo_roles=demo, fitted_roles=None
        )
    fitted_physical = {
        key: value
        for key, value in read_graph.items()
        if key not in {"read_only", "repeatable"}
    }
    plan = owner_pregrant._grant_objects(**names)
    relations = ", ".join(
        f"public.{name}"
        for name in sorted(
            {item.target for item in plan if item.kind == "TABLE"} | fitted_delta.TABLES
        )
    )

    async with owner.begin() as connection:
        for setting in _GRANT_TX_SETTINGS:
            await connection.exec_driver_sql(setting)
        await connection.execute(_GRANT_LOCK_0056)
        assert (
            await observation._identity_at_0056(connection, owner_name) == read_identity
        )
        await connection.exec_driver_sql(
            f"LOCK TABLE {relations} IN ACCESS EXCLUSIVE MODE NOWAIT"
        )
        owner_oid = read_identity[3]
        assert (
            await connection.scalar(observation.current55._OWNER_OID_SQL) == owner_oid
        )
        assert tuple(
            (
                await connection.execute(
                    core_acl._OWNER_POLICY_SQL, {"owner": owner_oid}
                )
            ).one()
        ) == (True, True)
        inherited = (
            owner_pregrant.inherited56.observe_owner_readwrite_installation_0056(
                await connection.scalar(
                    text(owner_pregrant.inherited56.OWNER_CATALOG_SQL)
                )
            )
        )
        assert inherited["role_oid"] == owner_oid
        assert inherited["native_definitions_sha256"] == (
            "6243338abe9cfcbb4eb613bc3d21cd7adbaf8059644e8b7a1c2df9755ac340c7"
        )
        assert (
            await _read_test_fitted_write_graph(connection, owner_name, demo)
            == fitted_physical
        )
        role_oids = await core_acl._roles(connection, demo, owner_oid)
        await observation._require_no_effective_demo_fitted_execute(connection, demo)
        before = await owner_pregrant._exact_acl_phase(
            connection, role_oids, owner_oid, **names, granted=False
        )
        for statement in sql:
            await connection.exec_driver_sql(statement)
        assert (
            await owner_pregrant._exact_acl_phase(
                connection, role_oids, owner_oid, **names, granted=True
            )
            == before
        )
        await observation._require_no_effective_demo_fitted_execute(connection, demo)
        assert (
            await _read_test_fitted_write_graph(connection, owner_name, demo)
            == fitted_physical
        )
        assert await core_acl._roles(connection, demo, owner_oid) == role_oids
        assert (
            await observation._identity_at_0056(connection, owner_name) == read_identity
        )


async def _verify_actual_tls_roles(engines):
    async with AsyncExitStack() as stack:
        connections = [
            await stack.enter_async_context(engine.connect()) for engine in engines
        ]
        observed = [
            tuple((await connection.execute(_ROLE_OBS56_IDENTITY)).one())
            for connection in connections
        ]
    names = tuple(engine.url.username for engine in engines)
    assert len(set(names)) == 4
    assert tuple(row[:3] for row in observed) == tuple(
        (name, name, "none") for name in names
    )
    assert len({row[3] for row in observed}) == len({row[4] for row in observed}) == 4
    assert all(row[5] is True and row[6] == "170011" for row in observed)
    assert len({row[7:] for row in observed}) == 1


async def test_actual0056_configured_product_tls_child_is_source_pinned_and_read_back(
    _actual_0056_role_observation_lab,
):
    """Import the configured product engines afresh, then read three TLS roles."""
    _, _, selected, _ = _actual_0056_role_observation_lab
    identity, source_files, source_files_raw = (
        fitted_measurement._read_hermetic_inputs()
    )
    source = fitted_measurement.source_identity(
        identity, source_files, source_files_raw
    )
    ca_file = os.environ["CTCC_DEVVAL_CUSTODY_TEST_TLS_CA_FILE"]
    ca_sha256 = os.environ["CTCC_DEVVAL_CUSTODY_TEST_TLS_CA_SHA256"]
    factories = (
        selected.writer_factory,
        selected.custody_factory,
        selected.frame_reader_factory,
    )
    urls = tuple(factory.kw["bind"].url for factory in factories)
    assert len({url.username for url in urls}) == 3
    assert all(url.password is None and not url.query for url in urls)
    child_env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "PYTHONPATH": str(_SOURCE_ROOT),
        "PYTHONDONTWRITEBYTECODE": "1",
        "ENVIRONMENT": "test",
        "TRADING_MODE": "analysis_only",
        "CTCC_NATIVE_ONLY_LIFESPAN_V1": "true",
        "DATABASE_URL": urls[0].render_as_string(hide_password=False),
        "OKX_DEMO_NATIVE_CUSTODY_DATABASE_URL": urls[1].render_as_string(
            hide_password=False
        ),
        "OKX_DEMO_NATIVE_FRAME_READER_DATABASE_URL": urls[2].render_as_string(
            hide_password=False
        ),
        "CTCC_NATIVE_PG_TLS_CA_FILE": ca_file,
        "CTCC_NATIVE_PG_TLS_CA_SHA256": ca_sha256,
        "CTCC_DEVVAL_EXPECTED_SOURCE_MANIFEST_SHA256": source["manifest_sha256"],
    }

    async def child(changes):
        env = {**child_env, **changes}
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-B",
            "-m",
            "tests.integration.native_pg_tls_configured_child_v1",
            cwd=str(_SOURCE_ROOT),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            async with asyncio.timeout(60):
                stdout, stderr = await process.communicate()
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
        assert len(stdout) <= 4096 and stderr == b""
        return process.returncode, read_evidence_json(stdout.strip())

    code, positive = await child({})
    assert code == 0
    assert positive == {
        "schema": "ctcc.native_pg_tls.configured_child.diagnostic.v1",
        "status": "PASS",
        "source_manifest_sha256": source["manifest_sha256"],
        "distinct_restricted_roles": 3,
        "shared_tls_context": True,
        "actual_ssl_readback": True,
        "database_head": "0056",
        "startup_admission": False,
        "execution_authority": False,
    }
    for changed in (
        {"CTCC_DEVVAL_EXPECTED_SOURCE_MANIFEST_SHA256": "0" * 64},
        {"CTCC_NATIVE_PG_TLS_CA_SHA256": "0" * 64},
    ):
        code, negative = await child(changed)
        assert code == 2
        assert negative == {"status": "FAIL", "code": "native_pg_tls_child_denied"}


@asynccontextmanager
async def _open_actual_0056_role_observation_lab(*, grant_demo: bool):
    url, tls = _isolated_tls_database_config()
    assert (url.username, url.database, url.port) == ("ctcc", "ctcc", 5432)
    assert tls.check_hostname and tls.verify_mode == ssl.CERT_REQUIRED
    database = f"ctcc_native_obs56_{uuid4().hex[:12]}"
    demo = tuple(
        f"ctcc_obs56_{role}_{uuid4().hex[:12]}"
        for role in ("writer", "custody", "frame")
    )
    database_sql = _delta56_identifier(database)
    assert len(set(demo)) == 3

    def engine(target):
        return create_async_engine(
            target, poolclass=NullPool, connect_args={"ssl": tls}
        )

    target = url.set(database=database)
    admin = engine(url)
    owner = engine(target)
    role_engines = tuple(engine(target.set(username=name)) for name in demo)
    created_database = False
    created_roles = False
    try:
        async with admin.connect() as connection:
            await connection.execution_options(isolation_level="AUTOCOMMIT")
            initial = tuple((await connection.execute(_ROLE_OBS56_IDENTITY)).one())
            assert initial[:3] == ("ctcc", "ctcc", "none")
            assert initial[5] is True and initial[6] == "170011"
            await connection.execute(text(f"CREATE DATABASE {database_sql}"))
            created_database = True
        async with admin.begin() as connection:
            for name in demo:
                await connection.execute(
                    text(f"CREATE ROLE {_delta56_identifier(name)} LOGIN")
                )
        created_roles = True
        async with owner.begin() as connection:
            await connection.run_sync(_migrate_runtime_custody_head)
        owner_factory = async_sessionmaker(
            owner, autoflush=False, expire_on_commit=False
        )
        pregrant = None
        if os.environ.get(devval_catalog.HERMETIC_CATALOG_ENV) == "1":
            pregrant = await owner_pregrant.observe_owner_pregrant_diagnostic_0056(
                owner_factory,
                source=_SOURCE_ROOT,
                writer=demo[0],
                custody=demo[1],
                frame_reader=demo[2],
            )
        if grant_demo:
            await _install_test_demo_grants(owner, demo)
        await _verify_actual_tls_roles((owner, *role_engines))
        factories = tuple(
            async_sessionmaker(bound, autoflush=False, expire_on_commit=False)
            for bound in (owner, *role_engines)
        )
        owner_factory, writer_factory, custody_factory, frame_factory = factories
        yield (
            owner,
            owner_factory,
            ConfiguredNativeDemoFactoriesV2(
                writer_factory,
                custody_factory,
                frame_factory,
                role_engines[1],
                role_engines[2],
            ),
            pregrant,
        )
    finally:
        try:
            async with AsyncExitStack() as stack:
                for candidate in (owner, *role_engines):
                    stack.push_async_callback(candidate.dispose)
        finally:
            try:
                if created_database:
                    async with admin.connect() as connection:
                        await connection.execution_options(isolation_level="AUTOCOMMIT")
                        await connection.execute(
                            text(f"DROP DATABASE {database_sql} WITH (FORCE)")
                        )
            finally:
                try:
                    if created_roles:
                        async with admin.begin() as connection:
                            for name in reversed(demo):
                                await connection.execute(
                                    text(f"DROP ROLE {_delta56_identifier(name)}")
                                )
                finally:
                    await admin.dispose()


@pytest.fixture
async def _actual_0056_role_observation_lab():
    async with _open_actual_0056_role_observation_lab(grant_demo=True) as lab:
        yield lab


@pytest.fixture
async def _actual_0056_ungranted_lab():
    async with _open_actual_0056_role_observation_lab(grant_demo=False) as lab:
        yield lab


async def test_actual0056_owner_grant_lock_and_rollback(
    _actual_0056_ungranted_lab,
):
    owner, owner_factory, selected, _ = _actual_0056_ungranted_lab
    demo = tuple(
        factory.kw["bind"].url.username
        for factory in (
            selected.writer_factory,
            selected.custody_factory,
            selected.frame_reader_factory,
        )
    )
    names = dict(zip(("writer", "custody", "frame_reader"), demo, strict=True))
    before = await owner_pregrant.observe_owner_pregrant_diagnostic_0056(
        owner_factory, source=_SOURCE_ROOT, **names
    )
    async with owner.begin() as competing:
        await competing.execute(_GRANT_LOCK_0056)
        with pytest.raises(DBAPIError) as locked:
            await _install_test_demo_grants(owner, demo)
        assert locked.value.orig.sqlstate == "55P03"

    executed_grants = []

    def fail_last_grant(_conn, _cursor, statement, parameters, _ctx, _many):
        if statement.startswith("GRANT "):
            executed_grants.append(statement)
            if len(executed_grants) == 42:
                return "SELECT 1/0", parameters
        return statement, parameters

    event.listen(
        owner.sync_engine, "before_cursor_execute", fail_last_grant, retval=True
    )
    try:
        with pytest.raises(DBAPIError) as failed:
            await _install_test_demo_grants(owner, demo)
        assert failed.value.orig.sqlstate == "22012"
        assert tuple(executed_grants) == owner_pregrant.exact_42_grant_sql_0056(**names)
    finally:
        event.remove(owner.sync_engine, "before_cursor_execute", fail_last_grant)
    assert (
        await owner_pregrant.observe_owner_pregrant_diagnostic_0056(
            owner_factory, source=_SOURCE_ROOT, **names
        )
        == before
    )

    await _install_test_demo_grants(owner, demo)
    readback = await observation.observe_restricted_native_roles_0056_v1(
        selected, source=_SOURCE_ROOT
    )
    assert (
        readback.inherited_native_definitions_sha256
        == before.inherited_native_definitions_sha256
    )
    assert readback.startup_admission is readback.execution_authority is False


async def test_actual0056_draft_locked_owner_grant_and_separate_role_readback(
    _actual_0056_ungranted_lab,
    tmp_path,
    monkeypatch,
):
    """Run the attended grant operation against a disposable TLS cluster."""
    from types import SimpleNamespace

    from app.core.native_only_lifespan import _MUST_BE_FALSE
    from scripts import native_deployment_descriptor_0056_v1 as descriptor
    from scripts import native_owner_grant_0056_v2 as attended_grant
    from scripts import native_owner_postgrant_readback_0056_v1 as postgrant_readback

    owner, owner_factory, selected, _ = _actual_0056_ungranted_lab
    names = tuple(
        factory.kw["bind"].url.username
        for factory in (
            selected.writer_factory,
            selected.custody_factory,
            selected.frame_reader_factory,
        )
    )
    roles = dict(zip(("writer", "custody", "frame_reader"), names, strict=True))
    pregrant = await owner_pregrant.observe_owner_pregrant_diagnostic_0056(
        owner_factory, source=_SOURCE_ROOT, **roles
    )
    readwrite = await owner_pregrant._read_owner_pregrant_graph_0056(
        owner_factory, owner.url.username, names, readwrite=True
    )
    async with owner.begin() as connection:
        for setting in _GRANT_TX_SETTINGS:
            await connection.exec_driver_sql(setting)
        await connection.exec_driver_sql("SET LOCAL search_path TO pg_catalog, pg_temp")
        fitted_write = await connection.scalar(
            text(fitted_delta.OWNER_READWRITE_CATALOG_SQL)
        )
        fitted_delta.inspect_owner_readwrite_catalog(
            fitted_write, owner=owner.url.username, demo_roles=names
        )
    identity, files, _ = fitted_measurement._read_hermetic_inputs()
    source = source_scopes.actual_current_source_scope(_SOURCE_ROOT)
    async with owner.connect() as connection:
        profile = tuple(
            (await connection.execute(owner_grant_draft._PROFILE_SQL)).one()
        )
        role_oids = await core_acl._roles(connection, names, profile[4])
    pins = owner_grant_draft.OwnerGrantDraftPinsV1(
        source_commit=identity["commit"],
        source_tree=identity["tree"],
        source_archive_sha256=identity["archive_sha256"],
        route=owner_route.OwnerRoutePinsV1(
            source_manifest_sha256=source.manifest_sha256,
            ca_sha256=os.environ["CTCC_DEVVAL_CUSTODY_TEST_TLS_CA_SHA256"],
            cluster_system_identifier_sha256=sha(profile[11].encode("ascii")),
            host=owner.url.host,
            port=owner.url.port,
            database=owner.url.database,
            owner=owner.url.username,
            database_oid=profile[5],
            owner_oid=profile[4],
            database_head="0056",
        ),
        writer=names[0],
        custody=names[1],
        frame_reader=names[2],
        writer_oid=role_oids[names[0]],
        custody_oid=role_oids[names[1]],
        frame_reader_oid=role_oids[names[2]],
        portable_inherited_definitions_sha256=pregrant.inherited_native_definitions_sha256,
        portable_fitted_pregrant_normalized_sha256=pregrant.fitted_normalized_owner_catalog_sha256,
        raw_inherited_owner_readonly_sha256=pregrant.inherited_raw_owner_graph_sha256,
        raw_inherited_owner_readwrite_sha256=readwrite[2],
        raw_fitted_owner_readonly_sha256=pregrant.fitted_raw_owner_catalog_sha256,
        raw_fitted_owner_readwrite_sha256=sha(canonical(fitted_write)),
        exact_42_plan_sha256=pregrant.exact_grant_plan_sha256,
    )
    # The fixture supplies the isolated product-shaped role set at the
    # configuration boundary. The readback itself opens four fresh verified
    # TLS engines and queries the actual scoped PostgreSQL catalogs.
    ca_file = Path(os.environ["CTCC_DEVVAL_CUSTODY_TEST_TLS_CA_FILE"])
    archive, archive_pin = _reconstruct_owned_lab_archive(tmp_path, identity, files)
    private = tmp_path / "postgrant-owner-private"
    private.mkdir(mode=0o700)
    owner_dsn = private / "postgrant-owner-dsn"
    owner_dsn.write_bytes(
        owner.url.set(password=secrets.token_hex(24))
        .render_as_string(hide_password=False)
        .encode("utf-8")
    )
    owner_dsn.chmod(0o600)
    pins = replace(pins, source_archive_sha256=archive_pin)
    settings = SimpleNamespace(
        ctcc_native_pg_tls_ca_sha256=pins.route.ca_sha256,
        ctcc_native_pg_tls_ca_file=str(ca_file),
        ctcc_native_only_lifespan_v1=True,
        environment="production",
        trading_mode="okx_demo",
        web_concurrency=1,
        okx_demo_enabled=True,
        **dict.fromkeys(_MUST_BE_FALSE, False),
    )
    monkeypatch.setattr(attended_grant, "get_settings", lambda: settings)
    monkeypatch.setattr(postgrant_readback, "get_settings", lambda: settings)
    monkeypatch.setattr(
        postgrant_readback.selectors,
        "select_configured_native_demo_factories_v2",
        lambda: selected,
    )
    digest = postgrant_readback.target_plan._retain_private_plan(private, pins)
    confirmations = []
    monkeypatch.setattr(
        attended_grant, "confirm_owner_grant_0056_v1", confirmations.append
    )
    options = {
        "plan_file": private / "db0056-target-grant-pins.json",
        "plan_file_sha256": digest,
        "owner_dsn_file": owner_dsn,
        "ca_file": ca_file,
        "source_archive_file": archive,
        "journal_directory": private,
    }
    observed = await attended_grant.apply_attended_owner_grant_0056_v2(**options)
    assert observed["status"] == "GRANTED_AND_OBSERVED"
    assert observed["grant_statements"] == 42
    assert observed["operator_confirmation_received"] is True
    assert observed["external_approval_authenticated"] is False
    assert confirmations == [digest]
    restricted = await observation.observe_restricted_native_roles_0056_v1(
        selected, source=_SOURCE_ROOT
    )
    assert (
        observed["cluster_system_identifier_sha256"]
        == pins.route.cluster_system_identifier_sha256
    )
    assert (
        observed["inherited_native_definitions_sha256"]
        == pins.portable_inherited_definitions_sha256
    )
    assert (
        observed["granted_inherited_acl_shape_sha256"]
        == restricted.granted_inherited_acl_shape_sha256
    )
    assert observed["restricted_logins_observed"] == 3
    assert observed["separate_read_only_owner_observations"] == 2
    assert observed["password_authentication_verified"] is False
    assert observed["admission"] == "DENY"
    assert observed["startup_admission"] is observed["execution_authority"] is False
    with pytest.raises(attended_grant.OwnerGrantAttemptNeedsReconciliation):
        await attended_grant.apply_attended_owner_grant_0056_v2(**options)
    assert confirmations == [digest]
    reconciled = await attended_grant.reconcile_owner_grant_0056_v2(**options)
    assert reconciled["status"] == "EXACT_GRANTED_ACL_OBSERVED"
    assert reconciled["attempt_status"] == "OBSERVED_GRANTED"
    assert reconciled["commit_attributed_to_attempt"] is True
    assert reconciled["admission"] == "DENY"
    with pytest.raises(owner_grant_draft.OwnerGrantRefusal):
        await owner_grant_draft.grant_reviewed_owner_0056_v1(pins)

    descriptor_sha256 = await descriptor.export_owner_verified_descriptor_0056_v1(
        plan_file=options["plan_file"],
        plan_file_sha256=digest,
        owner_dsn_file=owner_dsn,
        ca_file=ca_file,
        source_archive_file=archive,
        descriptor_directory=private,
    )
    monkeypatch.setattr(descriptor, "get_settings", lambda: settings)
    # The restricted verifier now has no owner credential file to consult.
    owner_dsn.unlink()
    assert (
        await descriptor.verify_restricted_deployment_descriptor_0056_v1(
            descriptor_file=private / "db0056-deployment-descriptor.json",
            descriptor_sha256=descriptor_sha256,
            source_archive_file=archive,
        )
        == descriptor_sha256
    )


async def test_actual0056_reviewed_owner_target_plan_has_no_grant(
    _actual_0056_ungranted_lab,
    tmp_path,
):
    """A separate exact-source owner CLI observes physical ACLs without grants."""
    from scripts import native_owner_target_plan_0056_v1 as target_plan

    owner, _, selected, _ = _actual_0056_ungranted_lab
    names = tuple(
        factory.kw["bind"].url.username
        for factory in (
            selected.writer_factory,
            selected.custody_factory,
            selected.frame_reader_factory,
        )
    )
    identity, files, files_raw = fitted_measurement._read_hermetic_inputs()
    source = fitted_measurement.source_identity(identity, files, files_raw)
    archive, archive_pin = _reconstruct_owned_lab_archive(tmp_path, identity, files)
    async with owner.connect() as connection:
        profile = tuple(
            (await connection.execute(owner_grant_draft._PROFILE_SQL)).one()
        )
    route = owner_route.OwnerRoutePinsV1(
        source_manifest_sha256=source["manifest_sha256"],
        ca_sha256=os.environ["CTCC_DEVVAL_CUSTODY_TEST_TLS_CA_SHA256"],
        cluster_system_identifier_sha256=sha(profile[11].encode("ascii")),
        host=owner.url.host,
        port=owner.url.port,
        database=owner.url.database,
        owner=owner.url.username,
        database_oid=profile[5],
        owner_oid=profile[4],
        database_head="0056",
    )
    private = tmp_path / "owner-private"
    private.mkdir(mode=0o700)
    dsn = private / "owner-dsn"
    # The disposable TLS fixture uses trust auth. It does not claim owner
    # password authentication, but the public CLI still requires a private DSN.
    fixture_url = owner.url.set(password="isolated-lab-owner-route-input")
    dsn.write_bytes(fixture_url.render_as_string(hide_password=False).encode())
    dsn.chmod(0o600)
    inputs = target_plan.OwnerTargetPlanInputsV1(
        owner_upgrade.OwnerUpgradePinsV1(
            identity["commit"], identity["tree"], archive_pin, route
        ),
        *names,
    )
    files = {
        "owner_dsn_file": dsn,
        "ca_file": Path(os.environ["CTCC_DEVVAL_CUSTODY_TEST_TLS_CA_FILE"]),
        "source_archive_file": archive,
    }
    first = await target_plan.observe_reviewed_owner_target_plan_0056_v1(
        inputs, **files
    )
    second = await target_plan.observe_reviewed_owner_target_plan_0056_v1(
        inputs, **files
    )
    assert first == second
    assert first["status"] == "OBSERVED_DENY" and first["grant_statements"] == 42
    assert first["portable_reference_matched"] is True
    assert first["grants_present"] is first["execution_authority"] is False
    assert all(name not in json.dumps(first) for name in names)


async def test_actual0056_full_native_role_observation_tls(
    _actual_0056_role_observation_lab,
):
    from app.database.repositories import (
        native_issuer_transaction as issuer_transaction,
    )

    _, owner_factory, selected, pregrant = _actual_0056_role_observation_lab
    first = await observation.observe_native_roles_0056_v1(
        selected, owner_factory=owner_factory, source=_SOURCE_ROOT
    )
    second = await observation.observe_native_roles_0056_v1(
        selected, owner_factory=owner_factory, source=_SOURCE_ROOT
    )
    assert first == second
    assert first.database_head == "0056"
    assert first.admission == "DENY"
    assert first.native_reference_sealed is first.native_baseline_admission is False
    assert first.startup_admission is first.execution_authority is False
    assert first.account_complete is False
    restricted = await observation.observe_restricted_native_roles_0056_v1(
        selected, source=_SOURCE_ROOT
    )
    assert (
        restricted.inherited_native_definitions_sha256,
        restricted.frame_writer_observation_sha256,
        restricted.raw_custody_catalog_sha256,
    ) == (
        first.inherited_native_definitions_sha256,
        first.frame_writer_observation_sha256,
        first.raw_custody_catalog_sha256,
    )
    assert restricted.startup_admission is restricted.execution_authority is False
    async with selected.writer_factory() as session, session.begin():
        await observation.current55._session_settings(session, frame=True)
        writer = await writer_observation.observe_restricted_readwrite_writer_0056_v1(
            session
        )
        catalog, write = await issuer_transaction.read_actual_native_issuer_transaction(
            session
        )
        (
            repeated_catalog,
            repeated,
        ) = await issuer_transaction.read_actual_native_issuer_transaction(session)
        assert repeated_catalog == catalog
        assert repeated[:9] == write[:9] and repeated[9] >= write[9]
    assert catalog.head == "0056" and catalog.role_oid == write[3]
    async with selected.writer_factory() as session, session.begin():
        await observation.current55._session_settings(session, frame=True)
        (
            read_catalog,
            readback,
        ) = await issuer_transaction.read_actual_native_issuer_transaction(session)
    assert read_catalog == catalog and readback[8] != write[8]
    assert readback[:7] == write[:7]
    async with selected.frame_reader_factory() as session, session.begin():
        await observation.current55._session_settings(session, frame=True)
        with pytest.raises(issuer_transaction.NativeIssuerTransactionError):
            await issuer_transaction.read_actual_native_issuer_transaction(session)
    assert writer.native_definitions_sha256 == first.inherited_native_definitions_sha256
    assert writer.startup_admission is writer.execution_authority is False
    if pregrant is not None:
        postgrant_acl.retain_hermetic_measurement(
            cluster_system_identifier_sha256=pregrant.cluster_system_identifier_sha256,
            full=first,
            restricted=restricted,
        )
        fitted_measurement.retain_hermetic_inherited_measurement(
            cluster_system_identifier_sha256=pregrant.cluster_system_identifier_sha256,
            owner_pregrant_inherited_sha256=pregrant.inherited_native_definitions_sha256,
            owner_pregrant_readwrite_inherited_sha256=(
                pregrant.inherited_readwrite_native_definitions_sha256
            ),
            owner_pregrant_fitted_raw_sha256=pregrant.fitted_raw_owner_catalog_sha256,
            owner_pregrant_fitted_normalized_sha256=pregrant.fitted_normalized_owner_catalog_sha256,
            postgrant_readonly_inherited_sha256=first.inherited_native_definitions_sha256,
            postgrant_readwrite_inherited_sha256=writer.native_definitions_sha256,
        )


async def test_actual0056_held_frame_reader_uses_same_transaction_and_real_catalog(
    _actual_0056_role_observation_lab,
):
    _, _, selected, _ = _actual_0056_role_observation_lab
    frame_factory = selected.frame_reader_factory
    writer_factory = selected.writer_factory
    custody_factory = selected.custody_factory

    class Fence:
        checks = 0

        def check(self):
            self.checks += 1

    fence = Fence()
    async with frame_factory() as session, session.begin():
        for statement in (
            "SET TRANSACTION ISOLATION LEVEL READ COMMITTED",
            "SET TRANSACTION READ WRITE",
            "SET LOCAL search_path TO pg_catalog,public,pg_temp",
            "SET LOCAL TIME ZONE 'UTC'",
            "SET LOCAL lock_timeout='1000ms'",
            "SET LOCAL statement_timeout='1000ms'",
        ):
            await session.execute(text(statement))
        held_transaction = session.sync_session.get_transaction()
        assert held_transaction is not None
        observed = json.loads(
            await held_frame56.verify_held_frame_catalog_0056_v1(
                session, fence, writer_factory, custody_factory, frame_factory
            )
        )
        assert session.sync_session.get_transaction() is held_transaction
        assert tuple(
            (
                await session.execute(
                    text(
                        "SELECT current_setting('transaction_isolation'), "
                        "current_setting('transaction_read_only')"
                    )
                )
            ).one()
        ) == ("read committed", "off")
        assert observed["schema"] == "ctcc.native_only.held_frame_catalog_0056.v1"
        assert observed["database_head"] == "0056"
        assert (
            observed["current0056"]["profile"]["actual_separate_roles_observed"] is True
        )
        assert observed["account_complete"] is observed["execution_authority"] is False
        assert observed["admission"] == "DENY"
        assert fence.checks >= 20
        with pytest.raises(held_frame56.NativeFrameReader0056Error):
            await held_frame56.verify_held_frame_catalog_0056_v1(
                session, fence, frame_factory, custody_factory, writer_factory
            )
        assert session.sync_session.get_transaction() is held_transaction

    async with frame_factory() as session, session.begin():
        await session.execute(text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED"))
        await session.execute(text("SET TRANSACTION READ ONLY"))
        with pytest.raises(held_frame56.NativeFrameReader0056Error):
            await held_frame56.verify_held_frame_catalog_0056_v1(
                session, fence, writer_factory, custody_factory, frame_factory
            )


async def test_actual0056_full_native_role_rejects_swapped_roles_and_extra_grant(
    _actual_0056_role_observation_lab,
):
    owner, owner_factory, selected, _ = _actual_0056_role_observation_lab
    args = {"owner_factory": owner_factory, "source": _SOURCE_ROOT}
    baseline = await observation.observe_native_roles_0056_v1(selected, **args)
    swapped = replace(
        selected,
        writer_factory=selected.frame_reader_factory,
        frame_reader_factory=selected.writer_factory,
        frame_reader_engine=selected.writer_factory.kw["bind"],
    )
    with pytest.raises(
        observation.NativeRoles0056ObservationError, match=_ROLE_OBS56_REFUSAL
    ):
        await observation.observe_native_roles_0056_v1(swapped, **args)

    writer = selected.writer_factory.kw["bind"]
    writer_name = _delta56_identifier(writer.url.username)
    relation = "public.gate3_fitted_candidate_bindings"
    async with owner.begin() as connection:
        await connection.execute(
            text(f"GRANT SELECT ON TABLE {relation} TO {writer_name}")
        )
    try:
        async with writer.connect() as connection:
            assert (
                await connection.scalar(
                    text(
                        "SELECT pg_catalog.has_table_privilege(current_user,:relation,'SELECT')"
                    ),
                    {"relation": relation},
                )
                is True
            )
        with pytest.raises(
            observation.NativeRoles0056ObservationError, match=_ROLE_OBS56_REFUSAL
        ):
            await observation.observe_native_roles_0056_v1(selected, **args)
    finally:
        async with owner.begin() as connection:
            await connection.execute(
                text(f"REVOKE SELECT ON TABLE {relation} FROM {writer_name}")
            )
    assert await observation.observe_native_roles_0056_v1(selected, **args) == baseline

    # PUBLIC EXECUTE is effective without an ACL directly naming a Demo role.
    routine = "public.gate3_fitted_binding_read(text)"
    demo_names = tuple(
        factory.kw["bind"].url.username
        for factory in (
            selected.writer_factory,
            selected.custody_factory,
            selected.frame_reader_factory,
        )
    )
    async with owner.begin() as connection:
        await connection.exec_driver_sql(
            f"GRANT EXECUTE ON FUNCTION {routine} TO PUBLIC"
        )
    try:
        async with writer.connect() as connection:
            assert (
                await connection.scalar(
                    text(
                        "SELECT pg_catalog.has_function_privilege(current_user,:routine,'EXECUTE')"
                    ),
                    {"routine": routine},
                )
                is True
            )
        async with selected.writer_factory() as session, session.begin():
            await observation.current55._session_settings(session, frame=False)
            writer_oid = await session.scalar(
                text("SELECT current_user::regrole::oid::bigint")
            )
            await observation.current55._exact_existing_acl(
                await session.connection(), demo_names, writer_oid
            )
            with pytest.raises(
                observation.NativeRoles0056ObservationError, match=_ROLE_OBS56_REFUSAL
            ):
                await observation._require_no_effective_demo_fitted_execute(
                    session, demo_names
                )
        with pytest.raises(
            observation.NativeRoles0056ObservationError, match=_ROLE_OBS56_REFUSAL
        ):
            await observation.observe_native_roles_0056_v1(selected, **args)
    finally:
        async with owner.begin() as connection:
            await connection.exec_driver_sql(
                f"REVOKE EXECUTE ON FUNCTION {routine} FROM PUBLIC"
            )
    assert await observation.observe_native_roles_0056_v1(selected, **args) == baseline


async def test_actual0056_between_snapshot_grant_denies_fresh_writer_recheck(
    _actual_0056_role_observation_lab, monkeypatch
):
    owner, _, selected, _ = _actual_0056_role_observation_lab
    baseline = await observation.observe_restricted_native_roles_0056_v1(
        selected, source=_SOURCE_ROOT
    )
    assert baseline.startup_admission is baseline.execution_authority is False
    writer_name = _delta56_identifier(selected.writer_factory.kw["bind"].url.username)
    relation = "public.gate3_fitted_candidate_bindings"
    original = observation._restricted_frame_writer
    injected = False

    async def grant_after_first_snapshot(*args):
        nonlocal injected
        first = await original(*args)
        # The first writer/frame transactions have closed. This independent
        # owner transaction commits an actual extra grant before fresh readback.
        async with owner.begin() as connection:
            await connection.execute(
                text(f"GRANT SELECT ON TABLE {relation} TO {writer_name}")
            )
        async with selected.writer_factory() as session, session.begin():
            assert (
                await session.scalar(
                    text(
                        "SELECT pg_catalog.has_table_privilege("
                        "current_user,:relation,'SELECT')"
                    ),
                    {"relation": relation},
                )
                is True
            )
        injected = True
        return first

    monkeypatch.setattr(
        observation, "_restricted_frame_writer", grant_after_first_snapshot
    )
    try:
        with pytest.raises(
            observation.NativeRoles0056ObservationError, match=_ROLE_OBS56_REFUSAL
        ):
            await observation.observe_restricted_native_roles_0056_v1(
                selected, source=_SOURCE_ROOT
            )
        assert injected
    finally:
        monkeypatch.setattr(observation, "_restricted_frame_writer", original)
        async with owner.begin() as connection:
            await connection.execute(
                text(f"REVOKE SELECT ON TABLE {relation} FROM {writer_name}")
            )
    assert (
        await observation.observe_restricted_native_roles_0056_v1(
            selected, source=_SOURCE_ROOT
        )
        == baseline
    )


async def test_actual0056_native_lifespan_denies_before_recovery_cas(
    _actual_0056_role_observation_lab, monkeypatch
):
    from unittest.mock import AsyncMock

    from app import main as app_main
    from app.config.settings import Settings
    from app.core import native_only_lifespan
    from app.core import native_startup_control_observation_0056_v1 as startup56
    from app.core.native_role_preflight_v1 import NativeRolePreflightError
    from app.database.models.demo_automation import DemoAutomationState

    _, owner_factory, selected, _ = _actual_0056_role_observation_lab
    writer_factory = selected.writer_factory
    writer_engine = writer_factory.kw["bind"]
    observed = await observation.observe_restricted_native_roles_0056_v1(
        selected, source=_SOURCE_ROOT
    )
    assert observed.database_head == "0056"
    assert observed.startup_admission is observed.execution_authority is False

    instant = datetime(2026, 1, 1, tzinfo=UTC)
    async with owner_factory.begin() as session:
        session.add(
            DemoAutomationState(
                id=1,
                control_revision=7,
                session_date=instant.date(),
                armed=True,
                restart_latch_required=True,
                active_trades={"synthetic-tracked-position": {"retained": True}},
                updated_at=instant,
            )
        )

    async def read_control():
        async with writer_factory() as session:
            return await session.scalar(
                text(
                    "SELECT pg_catalog.to_jsonb(s)::text FROM public.demo_automation_state s WHERE s.id=1"
                )
            )

    before = await read_control()
    before_state = json.loads(before)
    assert before_state["control_revision"] == 7 and before_state["armed"] is True
    observed_control = await startup56.observe_startup_control_0056_v1(selected)
    assert observed_control.database_head == "0056"
    assert observed_control.control == native_only_lifespan.ExistingControl(
        revision=7,
        armed=True,
        emergency_stop=False,
        locked=False,
        restart_latch_required=True,
    )
    assert (
        observed_control.startup_admission
        is observed_control.execution_authority
        is False
    )
    assert await read_control() == before

    settings = Settings(
        _env_file=None,
        ctcc_native_only_lifespan_v1=True,
        environment="production",
        trading_mode="okx_demo",
        web_concurrency=1,
        okx_demo_enabled=True,
        okx_demo_api_key="",
        okx_demo_api_secret="",
        okx_demo_api_passphrase="",
        **dict.fromkeys(native_only_lifespan._MUST_BE_FALSE, False),
    )
    forbidden_recovery = AsyncMock(side_effect=AssertionError("unexpected recovery"))
    automation = object()
    monkeypatch.setattr(app_main, "settings", settings)
    monkeypatch.setattr(app_main, "AsyncSessionFactory", writer_factory)
    monkeypatch.setattr(app_main, "engine", writer_engine)
    monkeypatch.setattr(app_main, "safe_demo_automation", automation)
    monkeypatch.setattr(app_main, "start_native_only", forbidden_recovery)
    monkeypatch.setattr(
        observation.selectors,
        "select_configured_native_demo_factories_v2",
        lambda: selected,
    )

    instance = app_main._build_app()
    with pytest.raises(
        NativeRolePreflightError, match="^native_role_preflight_unavailable$"
    ):
        async with app_main.lifespan(instance):
            pytest.fail("DB0056 unexpectedly served without startup admission")
    assert instance.state.native_only_recovery_confirmed is False
    forbidden_recovery.assert_not_awaited()
    assert await read_control() == before


async def test_actual0056_inert_startup_candidate_binds_source_roles_and_control(
    _actual_0056_role_observation_lab,
):
    from app.core import native_role_witness_0056_v1 as witness56
    from app.core import native_startup_candidate_0056_v1 as candidate56
    from app.core import native_startup_control_observation_0056_v1 as startup56
    from app.database.models.demo_automation import DemoAutomationState

    _, owner_factory, selected, _ = _actual_0056_role_observation_lab
    identity, files, files_raw = fitted_measurement._read_hermetic_inputs()
    source = fitted_measurement.source_identity(identity, files, files_raw)
    for relative, expected in _CANDIDATE_0056_SOURCE_PINS.items():
        assert files[relative]["sha256"] == expected
        assert sha((_SOURCE_ROOT / relative).read_bytes()) == expected
    assert (
        sha((_SOURCE_ROOT / "MANIFEST.sha256").read_bytes())
        == source["manifest_sha256"]
    )
    assert (
        sha(
            (
                _SOURCE_ROOT
                / "app/core/native_role_preflight_0056_full_diagnostic_v1.py"
            ).read_bytes()
        )
        == _N234_ROLE_OBSERVER_SOURCE_SHA256
    )

    instant = datetime(2026, 1, 1, tzinfo=UTC)
    async with owner_factory.begin() as session:
        session.add(
            DemoAutomationState(
                id=1,
                control_revision=7,
                session_date=instant.date(),
                armed=True,
                restart_latch_required=True,
                active_trades={"synthetic-tracked-position": {"retained": True}},
                updated_at=instant,
            )
        )
    # These independent lab reads supply diagnostic inputs only.
    measured = await witness56.observe_restricted_native_roles_0056_witness_v1(
        selected, source=_SOURCE_ROOT
    )
    measured_control = await startup56.observe_startup_control_0056_witness_v1(selected)
    assert (
        measured.server_incarnation_sha256 == measured_control.server_incarnation_sha256
    )
    assert measured.role_route_sha256 == measured_control.role_route_sha256
    pins = candidate56.NativeStartupCandidate0056PinsV1(
        source["manifest_sha256"],
        _N234_INHERITED_NATIVE_SHA256,
        _N234_POSTGRANT_ACL_SHAPE_SHA256,
        measured.observation.frame_writer_observation_sha256,
        measured.observation.raw_custody_catalog_sha256,
        measured.server_incarnation_sha256,
        measured.role_route_sha256,
    )
    result = await candidate56.observe_selected_native_startup_candidate_0056_v1(
        selected, source=_SOURCE_ROOT, pins=pins
    )
    assert result.control_revision == 7
    assert result.admission == "DENY"
    assert result.independent_reference_authenticated is False
    assert result.startup_admission is result.execution_authority is False
    assert (
        result.deployment_server_incarnation_sha256
        == measured.server_incarnation_sha256
    )
    assert result.deployment_role_route_sha256 == measured.role_route_sha256


async def test_actual0056_configured_startup_cli_is_readonly_and_denies_shell_gate(
    _actual_0056_role_observation_lab,
    tmp_path,
):
    """Run the separate configured process against the actual isolated TLS roles."""
    from app.core import native_role_witness_0056_v1 as witness56
    from app.core import native_startup_control_observation_0056_v1 as startup56
    from app.database.models.demo_automation import DemoAutomationState
    from scripts import native_startup_0056_diagnostic_cli_v1 as cli

    _, owner_factory, selected, _ = _actual_0056_role_observation_lab
    identity, files, files_raw = fitted_measurement._read_hermetic_inputs()
    source = fitted_measurement.source_identity(identity, files, files_raw)
    instant = datetime(2026, 1, 1, tzinfo=UTC)
    async with owner_factory.begin() as session:
        session.add(
            DemoAutomationState(
                id=1,
                control_revision=7,
                session_date=instant.date(),
                armed=True,
                restart_latch_required=True,
                updated_at=instant,
            )
        )
    measured = await witness56.observe_restricted_native_roles_0056_witness_v1(
        selected, source=_SOURCE_ROOT
    )
    control = await startup56.observe_startup_control_0056_witness_v1(selected)
    assert (
        control.observation.control.revision == 7
        and measured.server_incarnation_sha256 == control.server_incarnation_sha256
        and measured.role_route_sha256 == control.role_route_sha256
    )
    pins = {
        "schema": cli._PIN_SCHEMA,
        "source_manifest_sha256": source["manifest_sha256"],
        "inherited_native_definitions_sha256": _N234_INHERITED_NATIVE_SHA256,
        "granted_inherited_acl_shape_sha256": _N234_POSTGRANT_ACL_SHAPE_SHA256,
        "deployment_frame_writer_sha256": (
            measured.observation.frame_writer_observation_sha256
        ),
        "deployment_custody_sha256": measured.observation.raw_custody_catalog_sha256,
        "deployment_server_incarnation_sha256": measured.server_incarnation_sha256,
        "deployment_role_route_sha256": measured.role_route_sha256,
    }
    private = tmp_path / "private-pins"
    private.mkdir(mode=0o700)
    os.chmod(private, 0o700)
    pin_file = private / "caller-pins.json"
    raw = canonical(pins)
    descriptor = os.open(pin_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)
    assert pin_file.stat().st_mode & 0o777 == 0o600

    ca_file = os.environ["CTCC_DEVVAL_CUSTODY_TEST_TLS_CA_FILE"]
    ca_sha256 = os.environ["CTCC_DEVVAL_CUSTODY_TEST_TLS_CA_SHA256"]
    factories = (
        selected.writer_factory,
        selected.custody_factory,
        selected.frame_reader_factory,
    )
    urls = tuple(factory.kw["bind"].url for factory in factories)
    assert all(url.password is None and not url.query for url in urls)
    child_env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "PYTHONPATH": str(_SOURCE_ROOT),
        "PYTHONDONTWRITEBYTECODE": "1",
        "ENVIRONMENT": "production",
        "TRADING_MODE": "okx_demo",
        "CTCC_NATIVE_ONLY_LIFESPAN_V1": "true",
        "OKX_DEMO_ENABLED": "true",
        "PAPER_AUTO_TICKS": "false",
        "PAPER_PERSISTENCE_ENABLED": "false",
        "OKX_DEMO_OBSERVABILITY_ENABLED": "false",
        "OKX_DEMO_SOAK_ENABLED": "false",
        "OKX_DEMO_ALLOW_ORDER_WRITES": "false",
        "DATABASE_URL": urls[0].render_as_string(hide_password=False),
        "OKX_DEMO_NATIVE_CUSTODY_DATABASE_URL": urls[1].render_as_string(
            hide_password=False
        ),
        "OKX_DEMO_NATIVE_FRAME_READER_DATABASE_URL": urls[2].render_as_string(
            hide_password=False
        ),
        "CTCC_NATIVE_PG_TLS_CA_FILE": ca_file,
        "CTCC_NATIVE_PG_TLS_CA_SHA256": ca_sha256,
    }
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-B",
        "-m",
        "scripts.native_startup_0056_diagnostic_cli_v1",
        "--inspect-configured",
        "--pins-file",
        str(pin_file),
        "--pins-sha256",
        sha(raw),
        cwd=str(_SOURCE_ROOT),
        env=child_env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        async with asyncio.timeout(210):
            stdout, stderr = await process.communicate()
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    assert len(stdout) <= 4096 and len(stderr) == 0
    assert process.returncode == 2
    assert read_evidence_json(stdout.strip()) == cli._output("OBSERVED_DENY")
    async with owner_factory() as session:
        assert (await session.get(DemoAutomationState, 1)).control_revision == 7


async def test_actual0056_stable_role_identity_on_distinct_connections(
    _actual_0056_role_observation_lab,
):
    from app.core import native_role_identity_0056_v2 as stable56

    _, _, selected, _ = _actual_0056_role_observation_lab
    identity, files, files_raw = fitted_measurement._read_hermetic_inputs()
    source = fitted_measurement.source_identity(identity, files, files_raw)
    relative = "app/core/native_role_identity_0056_v2.py"
    assert files[relative]["sha256"] == _STABLE_ROLE_IDENTITY_V2_SOURCE_SHA256
    assert sha((_SOURCE_ROOT / relative).read_bytes()) == (
        _STABLE_ROLE_IDENTITY_V2_SOURCE_SHA256
    )
    assert source["manifest_sha256"] == sha(
        (_SOURCE_ROOT / "MANIFEST.sha256").read_bytes()
    )
    factories = (
        selected.writer_factory,
        selected.custody_factory,
        selected.frame_reader_factory,
    )
    assert all(
        isinstance(factory.kw["bind"].sync_engine.pool, NullPool)
        for factory in factories
    )
    # Hold one backend while two independently opened V2 observations run.
    # This verifies distinct connections on one live PG incarnation, not a PG restart.
    async with selected.writer_factory() as held:
        held_pid = await held.scalar(text("SELECT pg_catalog.pg_backend_pid()"))
        held_start = await held.scalar(
            text("SELECT pg_catalog.pg_postmaster_start_time()")
        )
        first = await stable56.observe_restricted_role_identity_0056_v2(
            selected, source=_SOURCE_ROOT
        )
        second = await stable56.observe_restricted_role_identity_0056_v2(
            selected, source=_SOURCE_ROOT
        )
        async with selected.writer_factory() as probe:
            probe_pid = await probe.scalar(text("SELECT pg_catalog.pg_backend_pid()"))
            probe_start = await probe.scalar(
                text("SELECT pg_catalog.pg_postmaster_start_time()")
            )
        assert type(held_pid) is type(probe_pid) is int
        assert held_pid != probe_pid
        assert held_start == probe_start
    assert first.stable_identity_sha256 == second.stable_identity_sha256
    assert first.server_incarnation_sha256 == second.server_incarnation_sha256
    assert first.role_oids == second.role_oids
    assert first.role_names == second.role_names
    assert first.deployment_reference_authenticated is False
    assert first.startup_admission is first.execution_authority is False
    route = observation.selectors._database_route(
        selected.writer_factory.kw["bind"].url
    )
    expected = {
        "expected_database_oid": first.database_oid,
        "expected_owner_oid": first.owner_oid,
        "expected_role_names": first.role_names,
        "expected_role_oids": first.role_oids,
        "expected_route": route,
        "expected_sha256": first.stable_identity_sha256,
    }
    matched = stable56.compare_static_role_identity_0056_v2(first, **expected)
    assert matched["admission"] == "DENY"
    assert matched["startup_admission"] is matched["execution_authority"] is False
    changed = (
        first.role_oids[0],
        max(*first.role_oids, first.owner_oid) + 1,
        first.role_oids[2],
    )
    with pytest.raises(stable56.NativeRoleIdentity0056Error):
        stable56.compare_static_role_identity_0056_v2(
            first, **{**expected, "expected_role_oids": changed}
        )


async def test_actual0056_lab_only_recovery_cas_after_fresh_restricted_observations(
    _actual_0056_role_observation_lab, monkeypatch
):
    """Exercise the existing native-only lifespan in a disposable DB0056 lab.

    The production preflight still rejects DB0056. This test-only adapter
    proves the restricted observations and recovery CAS can operate on one
    actual role set; it is not a sealed reference or startup permission.
    """
    from decimal import Decimal

    from app import main as app_main
    from app.config.settings import Settings
    from app.core import native_only_lifespan
    from app.core import native_startup_control_observation_0056_v1 as startup56
    from app.database.models.demo_automation import (
        DemoAutomationRun,
        DemoAutomationState,
    )
    from app.database.repositories.demo_automation import DemoAutomationRepository
    from app.demo_automation.service import SafeDemoAutomation
    from app.domain.demo_automation import (
        DemoAutomationActiveTrade,
        DemoAutomationRunResult,
    )

    _, owner_factory, selected, _ = _actual_0056_role_observation_lab
    writer_factory = selected.writer_factory
    writer_engine = writer_factory.kw["bind"]
    repository = DemoAutomationRepository(writer_factory)
    instant = datetime.now(UTC)
    exposure = DemoAutomationActiveTrade(
        instrument_id="BTC-USDT-SWAP",
        settlement_currency="USDT",
        direction="long",
        client_order_id="labRecoveryFixture1",
        exchange_order_id="10001",
        protection_client_order_id="labRecoveryProtect1",
        contracts=Decimal(2),
        leverage=3,
        margin_mode="isolated",
        estimated_margin=Decimal(10),
        estimated_stop_loss_amount=Decimal(1),
        start_equity=Decimal(500),
        started_at=instant,
    )
    active_trades = {exposure.instrument_id: exposure.model_dump(mode="json")}
    run = DemoAutomationRunResult(
        trigger="manual",
        execute=False,
        started_at=instant,
        completed_at=instant,
        results=[],
    )
    async with owner_factory.begin() as session:
        session.add_all(
            [
                DemoAutomationState(
                    id=1,
                    control_revision=7,
                    session_date=instant.date(),
                    armed=True,
                    restart_latch_required=True,
                    equity_basis="fixture",
                    baseline_equity=Decimal(500),
                    peak_equity=Decimal(500),
                    risk_peak_equity=Decimal(500),
                    daily_pnl=Decimal(-4),
                    trades_today=1,
                    consecutive_losses=1,
                    active_trades=active_trades,
                    active_instrument_id=exposure.instrument_id,
                    active_client_order_id=exposure.client_order_id,
                    active_start_equity=exposure.start_equity,
                    active_started_at=exposure.started_at,
                    updated_at=instant,
                ),
                DemoAutomationRun(
                    trigger=run.trigger,
                    execute=run.execute,
                    started_at=run.started_at,
                    completed_at=run.completed_at,
                    result=run.model_dump(mode="json"),
                ),
            ]
        )
    before = await repository.load_state()
    assert before is not None
    assert before["_control_revision"] == 7 and before["armed"] is True

    settings = Settings(
        _env_file=None,
        ctcc_native_only_lifespan_v1=True,
        environment="production",
        trading_mode="okx_demo",
        web_concurrency=1,
        okx_demo_enabled=True,
        okx_demo_api_key="",
        okx_demo_api_secret="",
        okx_demo_api_passphrase="",
        **dict.fromkeys(native_only_lifespan._MUST_BE_FALSE, False),
    )
    absent = object()

    def new_automation():
        return SafeDemoAutomation(
            settings=settings,
            repository=DemoAutomationRepository(writer_factory),
            strategy_service=absent,
            risk_service=absent,
            demo_service=absent,
            public_client=absent,
            strategy_control_repository=absent,
            market_hub=absent,
            market_client=absent,
        )

    observations = []
    inspect = native_only_lifespan.inspect_existing_control

    async def lab_only_preflight(expected_writer_factory):
        # The production DB0055 preflight is replaced only inside this test.
        assert expected_writer_factory is writer_factory
        assert selected.writer_factory is expected_writer_factory
        assert (
            sha(
                (
                    _SOURCE_ROOT
                    / "app/core/native_role_preflight_0056_full_diagnostic_v1.py"
                ).read_bytes()
            )
            == _N234_ROLE_OBSERVER_SOURCE_SHA256
        )
        first = await observation.observe_restricted_native_roles_0056_v1(
            selected, source=_SOURCE_ROOT
        )
        control = await startup56.observe_startup_control_0056_v1(selected)
        second = await observation.observe_restricted_native_roles_0056_v1(
            selected, source=_SOURCE_ROOT
        )
        assert first == second
        assert first.database_head == control.database_head == "0056"
        assert (
            first.inherited_native_definitions_sha256 == _N234_INHERITED_NATIVE_SHA256
        )
        assert (
            first.granted_inherited_acl_shape_sha256 == _N234_POSTGRANT_ACL_SHAPE_SHA256
        )
        assert first.admission == "DENY"
        assert first.startup_admission is first.execution_authority is False
        assert control.startup_admission is control.execution_authority is False
        assert control.control == await inspect(expected_writer_factory)
        observations.append((first, control, second))
        return {"startup_admission": False, "execution_authority": False}

    monkeypatch.setattr(app_main, "settings", settings)
    monkeypatch.setattr(app_main, "AsyncSessionFactory", writer_factory)
    monkeypatch.setattr(app_main, "engine", writer_engine)
    automation = new_automation()
    monkeypatch.setattr(app_main, "safe_demo_automation", automation)
    monkeypatch.setattr(
        app_main, "preflight_configured_native_roles_v1", lab_only_preflight
    )
    instance = app_main._build_app()
    assert instance.openapi_url is None
    paths = instance.openapi()["paths"]
    assert set(paths) == {
        "/liveness",
        "/readiness",
        "/api/version",
        "/api/demo-automation/status",
        "/api/demo-automation/history",
    }
    assert all(set(operations) == {"get"} for operations in paths.values())

    async with app_main.lifespan(instance):
        assert len(observations) == 1
        assert instance.state.native_only_recovery_confirmed is True
        after = await repository.load_state()
        assert after is not None
        assert after["_control_revision"] == 8
        assert after["armed"] is False
        assert (
            after["emergency_stop"]
            is after["locked"]
            is after["_restart_latch_required"]
            is True
        )
        assert "restart_requires_explicit_clear_stop" in after["lock_reasons"]
        assert after["active_trades"] == active_trades
        assert all(
            after[key] == before[key]
            for key in (
                "active_instrument_id",
                "active_client_order_id",
                "active_start_equity",
                "active_started_at",
                "baseline_equity",
                "daily_pnl",
                "trades_today",
                "consecutive_losses",
            )
        )
        assert await automation.history() == [run]
        status = await automation.status()
        assert status.recovered and status.emergency_stop and status.locked
        assert not status.armed and not status.running
        assert status.active_trades == [exposure]
    assert instance.state.native_only_recovery_confirmed is False
    assert await repository.load_state() == after

    # Lifespan shutdown has disposed the writer pool. Start a fresh service
    # and app on the same database to prove recovery uses durable state.
    restarted_automation = new_automation()
    monkeypatch.setattr(app_main, "safe_demo_automation", restarted_automation)
    restarted_instance = app_main._build_app()
    async with app_main.lifespan(restarted_instance):
        assert len(observations) == 2
        assert observations[1][1].control.revision == 8
        assert observations[1][1].control.armed is False
        assert observations[1][1].control.emergency_stop is True
        assert restarted_instance.state.native_only_recovery_confirmed is True
        restarted = await repository.load_state()
        assert restarted is not None
        assert restarted["_control_revision"] == 9
        assert restarted["armed"] is False
        assert (
            restarted["emergency_stop"]
            is restarted["locked"]
            is restarted["_restart_latch_required"]
            is True
        )
        assert restarted["lock_reasons"] == after["lock_reasons"]
        assert restarted["active_trades"] == after["active_trades"]
        assert await restarted_automation.history() == [run]
        status = await restarted_automation.status()
        assert status.recovered and status.emergency_stop and status.locked
        assert not status.armed and not status.running
        assert status.active_trades == [exposure]
    assert restarted_instance.state.native_only_recovery_confirmed is False
    assert await repository.load_state() == restarted

    # A competing writer moves control after the startup snapshot. The next
    # boot must refuse before recovery can write another revision.
    async def snapshot_with_concurrent_update(factory):
        snapshot = await inspect(factory)
        assert snapshot.revision == 9
        await repository.latch_emergency_stop("lab_concurrent_stop")
        return snapshot

    monkeypatch.setattr(
        native_only_lifespan,
        "inspect_existing_control",
        snapshot_with_concurrent_update,
    )
    denied_automation = new_automation()
    monkeypatch.setattr(app_main, "safe_demo_automation", denied_automation)
    denied_instance = app_main._build_app()
    with pytest.raises(
        native_only_lifespan.NativeOnlyLifespanError,
        match="^native_only_preflight_unavailable$",
    ):
        async with app_main.lifespan(denied_instance):
            pytest.fail("concurrent control revision was incorrectly admitted")
    current = await repository.load_state()
    assert current is not None and current["_control_revision"] == 10
    assert current["active_trades"] == active_trades
    assert current["emergency_stop"] is current["locked"] is True
    assert "lab_concurrent_stop" in current["lock_reasons"]
    assert denied_instance.state.native_only_recovery_confirmed is False


async def test_actual0056_restricted_account_control_read_replays_exact_scope(
    _actual_0056_role_observation_lab,
):
    from app.core import native_account_control_observation_0056_v1 as account56
    from app.core.native_startup_control_observation_0056_v1 import (
        NativeStartupControl0056Error,
    )
    from app.database.repositories.demo_control import DemoControlRepository
    from app.trade_qualification.demo_control import ControlScope

    owner, owner_factory, selected, _ = _actual_0056_role_observation_lab
    seed = DemoControlRepository(owner_factory, clock=lambda: datetime.now(UTC))
    first_scope = ControlScope("demo", "000001")
    second_scope = ControlScope("demo", "1")
    await seed.latch_stop(first_scope, command_id="a" * 64)
    first = await seed.latch_stop(first_scope, command_id="b" * 64)
    second = await seed.latch_stop(second_scope, command_id="c" * 64)

    async def read(scope):
        return await account56.observe_account_control_0056_v1(selected, scope)

    # A second transaction holds the writer row lock. This observation must
    # replay the committed head without trying to lock the row for mutation.
    async with owner.begin() as connection:
        await connection.execute(
            text("""
            SELECT 1 FROM public.demo_account_controls
            WHERE environment='demo' AND account_id=:uid FOR UPDATE
            """),
            {"uid": first_scope.account_id},
        )
        actual = await read(first_scope)
        assert actual.control.state == first.state and first.state.revision == 2
        assert actual.control.event_sha256 == first.event_sha256
        assert actual.control.observed_at >= first.state.updated_at
        assert actual.database_head == "0056"
        assert actual.startup_admission is actual.execution_authority is False
        assert actual.control.execution_authority is False

    actual_second = await read(second_scope)
    assert actual_second.control.state == second.state and second.state.revision == 1
    assert actual_second.control.event_sha256 == second.event_sha256
    assert actual_second.control.event_sha256 != actual.control.event_sha256
    with pytest.raises(NativeStartupControl0056Error):
        await read(ControlScope("demo", "000002"))

    # A restricted role with an intact current row but a hidden lineage tail
    # may not turn that incomplete snapshot into a successful read.
    async with owner.begin() as connection:
        await connection.exec_driver_sql(
            "ALTER TABLE public.demo_control_journal ENABLE ROW LEVEL SECURITY"
        )
    try:
        with pytest.raises(NativeStartupControl0056Error):
            await read(first_scope)
    finally:
        async with owner.begin() as connection:
            await connection.exec_driver_sql(
                "ALTER TABLE public.demo_control_journal DISABLE ROW LEVEL SECURITY"
            )
    assert (await read(first_scope)).control.event_sha256 == first.event_sha256


async def test_actual0056_restricted_writer_control_replays_and_holds_uid_lock(
    _actual_0056_role_observation_lab,
):
    from app.core import native_locked_demo_control_0056_v1 as locked56
    from app.database.repositories.demo_control import DemoControlRepository
    from app.trade_qualification.demo_control import ControlScope, account_lock_key

    owner, owner_factory, selected, _ = _actual_0056_role_observation_lab
    seed = DemoControlRepository(owner_factory, clock=lambda: datetime.now(UTC))
    first_scope = ControlScope("demo", "000001")
    second_scope = ControlScope("demo", "1")
    await seed.latch_stop(first_scope, command_id="a" * 64)
    first = await seed.latch_stop(first_scope, command_id="b" * 64)
    second = await seed.latch_stop(second_scope, command_id="c" * 64)

    async def try_lock(connection, scope):
        return await connection.scalar(
            text("SELECT pg_catalog.pg_try_advisory_xact_lock(:key)"),
            {"key": account_lock_key(scope)},
        )

    async with selected.writer_factory() as session, session.begin():
        transaction = session.sync_session.get_transaction()
        actual = await locked56.read_locked_demo_control_0056_v1(
            selected, session, first_scope
        )
        assert actual.control.state == first.state and first.state.revision == 2
        assert actual.control.event_sha256 == first.event_sha256
        assert actual.control.state.emergency_stop is True
        assert actual.control.observed_at >= first.state.updated_at
        assert actual.database_head == "0056"
        assert actual.issuance_authority is actual.execution_authority is False
        assert session.sync_session.get_transaction() is transaction
        async with owner.begin() as competing:
            assert await try_lock(competing, first_scope) is False
            assert await try_lock(competing, second_scope) is True
            assert (
                await competing.scalar(
                    text("""
                SELECT account_id FROM public.demo_account_controls
                WHERE environment='demo' AND account_id=:uid
                FOR UPDATE SKIP LOCKED
                """),
                    {"uid": first_scope.account_id},
                )
                is None
            )

    async with owner.begin() as competing:
        assert await try_lock(competing, first_scope) is True
    async with selected.writer_factory() as session, session.begin():
        observed_second = await locked56.read_locked_demo_control_0056_v1(
            selected, session, second_scope
        )
        assert observed_second.control.state == second.state
        assert observed_second.control.event_sha256 != actual.control.event_sha256
        assert (
            observed_second.server_incarnation_sha256
            == actual.server_incarnation_sha256
        )

    async with owner_factory() as owner_session, owner_session.begin():
        with pytest.raises(locked56.NativeLockedDemoControl0056Error):
            await locked56.read_locked_demo_control_0056_v1(
                selected, owner_session, first_scope
            )
    with pytest.raises(locked56.NativeLockedDemoControl0056Error):
        async with selected.writer_factory() as session, session.begin():
            await session.execute(text("SET TRANSACTION READ ONLY"))
            await locked56.read_locked_demo_control_0056_v1(
                selected, session, first_scope
            )
    with pytest.raises(locked56.NativeLockedDemoControl0056Error):
        async with selected.writer_factory() as session, session.begin():
            await locked56.read_locked_demo_control_0056_v1(
                selected, session, ControlScope("demo", "000002")
            )

    async with owner.begin() as connection:
        await connection.exec_driver_sql(
            "ALTER TABLE public.demo_control_journal ENABLE ROW LEVEL SECURITY"
        )
    with pytest.raises(locked56.NativeLockedDemoControl0056Error):
        async with selected.writer_factory() as session, session.begin():
            await locked56.read_locked_demo_control_0056_v1(
                selected, session, first_scope
            )


async def test_actual0056_restricted_writer_retains_current_v7_page_chain(
    monkeypatch, _actual_0056_role_observation_lab
):
    from app.database.repositories.account_capture_journal import (
        AccountCaptureJournalRepository,
    )
    from app.trade_qualification.account_observation_index import source_reference
    from tests.integration.test_account_current_observation_repository import (
        original_v7,
    )

    _, _, selected, _ = _actual_0056_role_observation_lab
    writer = selected.writer_factory
    scope, reference = await original_v7(monkeypatch, (None, writer))
    fresh = AccountCaptureJournalRepository(writer, clock=lambda: datetime.now(UTC))
    chain = await fresh.read_chain(scope, reference.capture_id)
    assert source_reference(chain) == reference
    assert len(chain) > 8
    assert sum(item.event.packet_payload is not None for item in chain) == 1


async def test_actual0056_restricted_partition_reopens_existing_b3_prefix(
    monkeypatch, _actual_0056_role_observation_lab
):
    from app.core.native_role_witness_0056_v1 import (
        observe_restricted_native_roles_0056_witness_v1,
    )
    from app.database.repositories import (
        account_history_partition_current56_source_v1 as partition56,
    )
    from app.database.repositories.demo_control import DemoControlRepository
    from app.trade_qualification.demo_control import ControlScope
    from tests.integration.test_account_observation_index_repository import (
        additional_capture,
        now,
        stored,
    )

    owner, owner_factory, selected, _ = _actual_0056_role_observation_lab
    writer = selected.writer_factory
    index, ledger_scope, _, _, first = await stored(monkeypatch, (owner, writer))
    control_scope = ControlScope("demo", ledger_scope.account_id)
    control = await DemoControlRepository(owner_factory, clock=now).latch_stop(
        control_scope, command_id="b" * 64
    )
    scope = partition56.HistoryPartitionScopeV1("demo", ledger_scope.account_id, "USDT")
    roles = await observe_restricted_native_roles_0056_witness_v1(
        selected, source=_SOURCE_ROOT
    )
    reader = partition56.Db0056SourcePartitionReaderV1(
        selected, clock=now, expected_roles=roles
    )
    baseline = await reader.read(scope)
    assert baseline.head_sequence == 1
    assert baseline.head_sha256 == first.event_sha256
    assert baseline.b3_documents == (first.document_json,)
    assert baseline.control_revision == control.state.revision
    assert baseline.members == baseline.checkpoints == ()

    reference, window = await additional_capture(monkeypatch, index, ledger_scope, [])
    second = await index.append(ledger_scope, reference, window, expected_revision=1)
    readback = await reader.read(scope)
    reopened = await partition56.Db0056SourcePartitionReaderV1(
        selected, clock=now, expected_roles=roles
    ).read(scope)
    assert readback.head_sequence == reopened.head_sequence == 2
    assert readback.head_sha256 == reopened.head_sha256 == second.event_sha256
    assert readback.transaction_id != reopened.transaction_id
    assert (
        readback.b3_documents
        == reopened.b3_documents
        == (
            first.document_json,
            second.document_json,
        )
    )
    assert not readback.account_complete and not reopened.execution_authority
    with pytest.raises(partition56.Db0056SourcePartitionError):
        await reader.read(partition56.HistoryPartitionScopeV1("demo", "0", "USDT"))

    # The inherited read grant stays usable. Granting the old write RPC must
    # be diagnosed as drift before the reader can return another snapshot.
    legacy_append = "account_history_partition_v1_append"
    signature = partition56.original.policy.FUNCTION_SIGNATURES[legacy_append]
    role = _delta56_identifier(writer.kw["bind"].url.username)
    async with owner.begin() as connection:
        await connection.exec_driver_sql(
            f"GRANT EXECUTE ON FUNCTION public.{legacy_append}({signature}) TO {role}"
        )
    with pytest.raises(partition56.Db0056SourcePartitionError):
        await reader.read(scope)


async def test_actual0056_restricted_writer_creates_only_exact_stopped_genesis(
    monkeypatch, _actual_0056_role_observation_lab
):
    from types import SimpleNamespace

    from app.core import native_stopped_control_genesis_0056_v1 as genesis
    from app.core.native_role_witness_0056_v1 import (
        observe_restricted_native_roles_0056_witness_v1,
    )
    from app.database.repositories.demo_control import DemoControlRepository
    from app.trade_qualification.demo_control import ControlScope

    _, owner_factory, factories, _ = _actual_0056_role_observation_lab
    witness = await observe_restricted_native_roles_0056_witness_v1(
        factories, source=_SOURCE_ROOT
    )
    account = SimpleNamespace(expected_uid="730001")
    selection = SimpleNamespace(source_roles_0056=witness, scope_config=account)
    selected = {"factories": factories}
    # Only the exchange selection is synthetic. SQLAlchemy, role/ACL checks,
    # UID and row locks, transaction and both trigger checks are actual PG17.
    monkeypatch.setattr(genesis.selectors, "_record", lambda _: selected)

    with pytest.raises(genesis.locked.NativeLockedDemoControl0056Error):
        await genesis._operate_selected_genesis(selection, reconcile_only=True)
    created = await genesis._operate_selected_genesis(selection, reconcile_only=False)
    repeated = await genesis._operate_selected_genesis(selection, reconcile_only=False)
    reopened = await genesis._operate_selected_genesis(selection, reconcile_only=True)
    assert (created.status, repeated.status, reopened.status) == (
        "CREATED_STOPPED",
        "EXISTING_STOPPED",
        "EXISTING_STOPPED",
    )
    assert (
        created.control_event_sha256
        == repeated.control_event_sha256
        == reopened.control_event_sha256
    )
    assert reopened.control_revision == 1 and reopened.emergency_stop
    assert reopened.armed is reopened.execution_authority is False

    # Legitimate prior stop from another purpose is explicitly out of scope.
    account.expected_uid = "730002"
    scope = ControlScope("demo", account.expected_uid)
    owner_repository = DemoControlRepository(
        owner_factory, clock=lambda: datetime.now(UTC)
    )
    original = await owner_repository.latch_stop(scope, command_id="7" * 64)
    with pytest.raises(genesis.NativeStoppedControlGenesis0056Error):
        await genesis._operate_selected_genesis(selection, reconcile_only=False)
    preserved = await owner_repository.read(scope)
    assert preserved.state == original.state
    assert preserved.event_sha256 == original.event_sha256


async def test_actual0056_policy_counter_observes_absence_under_account_lock(
    monkeypatch, _actual_0056_role_observation_lab
):
    from sqlalchemy import func, select

    from app.core import native_policy_counter_readback_0056_v1 as policy_counter
    from app.core.native_role_witness_0056_v1 import (
        observe_restricted_native_roles_0056_witness_v1,
    )
    from app.database.repositories.demo_control import DemoControlRepository
    from app.trade_qualification.demo_control import ControlScope, account_lock_key
    from app.trade_qualification.reservations import LedgerScope

    _, owner_factory, factories, _ = _actual_0056_role_observation_lab
    witness = await observe_restricted_native_roles_0056_witness_v1(
        factories, source=_SOURCE_ROOT
    )
    scope = LedgerScope(account_id="730006", settlement_currency="USDT")
    control_scope = ControlScope(scope.environment, scope.account_id)
    repo = DemoControlRepository(owner_factory, clock=lambda: datetime.now(UTC))
    first_control = await repo.latch_stop(control_scope, command_id="a" * 64)
    original_read_row = policy_counter._read_counter_row
    original_once = policy_counter._read_once
    transactions = []

    async def verify_lock(session, selected_scope, state, check_held):
        async with owner_factory() as competing, competing.begin():
            held = await competing.scalar(
                select(func.pg_try_advisory_xact_lock(account_lock_key(control_scope)))
            )
        assert held is False
        return await original_read_row(session, selected_scope, state, check_held)

    async def record_read(*args):
        observed = await original_once(*args)
        transactions.append(observed.transaction_id)
        return observed

    monkeypatch.setattr(policy_counter, "_read_counter_row", verify_lock)
    monkeypatch.setattr(policy_counter, "_read_once", record_read)
    result = await policy_counter.observe_native_policy_counter_0056_v1(
        factories, witness, scope
    )
    assert result.status == "ABSENT"
    assert result.control_revision == first_control.state.revision
    assert result.control_event_sha256 == first_control.event_sha256
    assert result.counter_row_sha256 is None
    assert result.admission == "DENY"
    assert result.source_authenticity_verified is result.account_complete is False
    assert result.execution_authority is False
    assert len(set(transactions)) == 2

    with pytest.raises(policy_counter.NativePolicyCounterReadback0056Error):
        await policy_counter.observe_native_policy_counter_0056_v1(
            factories, witness, scope.model_copy(update={"account_id": "730007"})
        )

    first = True

    async def change_control_between_reads(*args):
        nonlocal first
        observed = await original_once(*args)
        if first:
            first = False
            await repo.latch_stop(control_scope, command_id="b" * 64)
        return observed

    monkeypatch.setattr(policy_counter, "_read_once", change_control_between_reads)
    with pytest.raises(policy_counter.NativePolicyCounterReadback0056Error):
        await policy_counter.observe_native_policy_counter_0056_v1(
            factories, witness, scope
        )


async def test_actual0056_restart_recovery_revokes_existing_account_arm(
    monkeypatch, _actual_0056_role_observation_lab
):
    from types import SimpleNamespace

    from app.core import native_account_restart_recovery_0056_v1 as recovery
    from app.core import native_locked_demo_control_0056_v1 as locked
    from app.core.native_role_identity_0056_v2 import (
        observe_restricted_role_identity_0056_v2,
    )
    from app.core.native_role_witness_0056_v1 import (
        observe_restricted_native_roles_0056_witness_v1,
    )
    from app.database.repositories import (
        native_issuer_transaction as issuer_transaction,
    )
    from app.database.repositories.demo_control import DemoControlRepository
    from app.database.repositories.qualification_ledger import (
        QualificationLedgerRepository,
    )
    from app.trade_qualification.control_bound_ledger import ControlExpectation
    from app.trade_qualification.demo_control import ControlPins, ControlScope
    from app.trade_qualification.native_prepare_owner_0056_v1 import (
        NativeQualificationWriter0056Error,
        QualificationWritePurpose0056,
        QualificationWriterPins0056V1,
        held_native_qualification_writer_0056_v1,
    )
    from app.trade_qualification.reservations import LedgerScope
    from scripts import read_demo_account_control_0056 as read_control

    owner_engine, owner_factory, factories, _ = _actual_0056_role_observation_lab
    witness = await observe_restricted_native_roles_0056_witness_v1(
        factories, source=_SOURCE_ROOT
    )
    scope = ControlScope("demo", "730005")
    selection = SimpleNamespace(
        source_roles_0056=witness,
        scope_config=SimpleNamespace(expected_uid=scope.account_id),
    )
    selected = {"factories": factories}
    monkeypatch.setattr(recovery.selectors, "_record", lambda _: selected)
    ledger_scope = LedgerScope(account_id=scope.account_id, settlement_currency="USDT")
    ledger = QualificationLedgerRepository(
        owner_factory, clock=lambda: datetime.now(UTC)
    )
    await ledger.initialize_capture_scope(ledger_scope)
    repository = DemoControlRepository(owner_factory, clock=lambda: datetime.now(UTC))
    token = b"c" * 32
    pins = ControlPins("a" * 64, "b" * 64, "c" * 64)
    owner = await repository.acquire(
        scope, owner_token=token, pins=pins, command_id="d" * 64
    )
    armed = await repository.change(
        scope,
        owner_token=token,
        owner_epoch=owner.state.owner_epoch,
        expected_revision=owner.state.revision,
        action="arm_requested",
        command_id="e" * 64,
        arm_expires_at=owner.state.lease_until,
    )
    expected = QualificationWriterPins0056V1(
        ControlExpectation(
            scope,
            token,
            armed.state.owner_epoch,
            armed.state.revision,
            armed.event_sha256,
            pins,
        ),
        0,
        0,
        None,
        armed.state.arm_expires_at,
    )
    async with (
        factories.writer_factory() as session,
        session.begin(),
        held_native_qualification_writer_0056_v1(
            factories,
            session,
            ledger_scope,
            roles=witness,
            purpose=QualificationWritePurpose0056.ISSUE_ACCOUNT_REVISION,
            expected=expected,
        ) as issued_fence,
    ):
        stamp = issued_fence.transaction
        assert (
            stamp.role_oid > 0
            and stamp.backend_pid > 0
            and stamp.transaction_id.isdecimal()
        )
        assert issued_fence.observation.control.state == armed.state
        assert issued_fence._scope_row_for_issue().account_revision == 0
        assert stamp.observed_at <= await issued_fence.recheck() < expected.expires_at
        assert (
            issued_fence.admission == "DENY"
            and issued_fence.execution_authority is False
        )
        async with owner_engine.begin() as competing:
            with pytest.raises(DBAPIError) as blocked:
                await competing.exec_driver_sql(
                    "LOCK TABLE public.demo_collector_issued_account_revisions IN ACCESS EXCLUSIVE MODE NOWAIT"
                )
            assert blocked.value.orig.sqlstate == "55P03"
    with pytest.raises(NativeQualificationWriter0056Error):
        async with (
            factories.writer_factory() as session,
            session.begin(),
            held_native_qualification_writer_0056_v1(
                factories,
                session,
                ledger_scope,
                roles=witness,
                purpose=QualificationWritePurpose0056.RESERVE_QUALIFICATION,
                expected=expected,
            ),
        ):
            pytest.fail("bootstrap claims reached reservation purpose")
    async with factories.writer_factory() as session, session.begin():
        await observation.current55._session_settings(session, frame=True)
        current, event = await issuer_transaction.read_actual_issuer_control_0056(
            session, scope
        )
        assert current == armed.state and event == armed.event_sha256
    stable_roles = await observe_restricted_role_identity_0056_v2(
        factories, source=_SOURCE_ROOT
    )
    async with factories.writer_factory() as session, session.begin():
        async with locked.held_native_writer_control_0056_v1(
            factories,
            session,
            scope,
            roles=stable_roles,
            purpose=locked.NativeWriterControlPurpose0056.PREPARE_OBSERVATION,
        ) as held:
            assert held.observation.control.state == armed.state
            assert held.observation.control.event_sha256 == armed.event_sha256
            assert held.execution_authority is False and held.admission == "DENY"
            with pytest.raises(locked.NativeLockedDemoControl0056Error):
                held._control_row_for_restart()
        with pytest.raises(locked.NativeLockedDemoControl0056Error):
            held.check_held()
    stable_drift = replace(stable_roles, database_oid=stable_roles.database_oid + 1)
    with pytest.raises(locked.NativeLockedDemoControl0056Error):
        async with (
            factories.writer_factory() as session,
            session.begin(),
            locked.held_native_writer_control_0056_v1(
                factories,
                session,
                scope,
                roles=stable_drift,
                purpose=locked.NativeWriterControlPurpose0056.PREPARE_OBSERVATION,
            ),
        ):
            pytest.fail("wrong stable database OID reached prepare observation")
    with pytest.raises(recovery.NativeAccountRestartRecovery0056Error):
        await recovery._recover_selected(selection, reconcile_only=True)
    stopped = await recovery._recover_selected(selection, reconcile_only=False)
    repeated = await recovery._recover_selected(selection, reconcile_only=False)
    readback = await recovery._recover_selected(selection, reconcile_only=True)
    assert (stopped.status, repeated.status, readback.status) == (
        "STOP_PERSISTED",
        "STOP_OBSERVED",
        "STOP_OBSERVED",
    )
    assert (
        stopped.revision
        == armed.state.revision + 1
        == readback.revision
        == repeated.revision
    )
    assert (
        stopped.control_event_sha256
        == readback.control_event_sha256
        == repeated.control_event_sha256
    )
    assert readback.emergency_stop and readback.admission == "DENY"
    assert (
        readback.armed
        is readback.positions_reconciled
        is readback.execution_authority
        is False
    )
    with pytest.raises(NativeQualificationWriter0056Error):
        async with (
            factories.writer_factory() as session,
            session.begin(),
            held_native_qualification_writer_0056_v1(
                factories,
                session,
                ledger_scope,
                roles=witness,
                purpose=QualificationWritePurpose0056.ISSUE_ACCOUNT_REVISION,
                expected=expected,
            ),
        ):
            pytest.fail("previous armed head survived durable stop")
    observed = await read_control._read_selected_control(
        factories, stable_roles, stopped.control_event_sha256
    )
    assert observed == {
        "control_event_sha256": stopped.control_event_sha256,
        "control_revision": stopped.revision,
        "emergency_stop": True,
        "arm_request_recorded": False,
    }
    with pytest.raises(
        read_control.startup.deployment.NativeDeploymentDescriptor0056Error
    ):
        await read_control._read_selected_control(
            factories, stable_roles, armed.event_sha256
        )


async def test_actual0056_stopped_portfolio_keeps_absent_and_legacy_unknown(
    _actual_0056_role_observation_lab,
):
    from app.core.native_role_witness_0056_v1 import (
        observe_restricted_native_roles_0056_witness_v1,
    )
    from app.database.repositories.demo_control import DemoControlRepository
    from app.demo_automation import native_restart_inventory_0056_v1 as restart
    from app.domain.source_primitives import decode
    from app.trade_qualification.demo_control import ControlScope

    _, owner_factory, factories, _ = _actual_0056_role_observation_lab
    roles = await observe_restricted_native_roles_0056_witness_v1(
        factories, source=_SOURCE_ROOT
    )
    scope = ControlScope("demo", "730006")

    def clock():
        return datetime.now(UTC)

    owner = DemoControlRepository(owner_factory, clock=clock)
    stop = await owner.latch_stop(scope, command_id="1" * 64)
    before, missing = await restart._observe_stopped_portfolio(
        factories, scope, roles, "USDT"
    )
    assert before.control.event_sha256 == stop.event_sha256 and missing is None
    later, local = await restart._observe_stopped_portfolio(
        factories, scope, roles, "USDT", initialize_capture=True
    )
    _, reopened = await restart._observe_stopped_portfolio(
        factories, scope, roles, "USDT"
    )
    assert local is not None and reopened is not None
    assert later.control.state == before.control.state
    assert later.control.event_sha256 == before.control.event_sha256
    assert local.document_json == reopened.document_json
    assert local.received_at <= reopened.observed_at
    value = decode(local.document_json)
    assert value["account_id"] == scope.account_id
    assert [state["account_revision"] for state in value["currency_states"]] == [0]
    assert value["future_writer_exclusion"] is value["execution_authority"] is False
    assert "legacy_reconciliation_initialization_unknown" in value["blocking_reasons"]


async def test_actual0056_restricted_ledger_preflight_holds_schema_fence(
    _actual_0056_role_observation_lab,
):
    from app.core import native_ledger_preflight_0056_v1 as preflight
    from app.core.native_role_witness_0056_v1 import (
        observe_restricted_native_roles_0056_witness_v1,
    )
    from app.database.repositories.demo_control import DemoControlRepository
    from app.database.repositories.qualification_ledger import (
        QualificationLedgerRepository,
    )
    from app.trade_qualification.demo_control import ControlScope, account_lock_key
    from app.trade_qualification.reservations import LedgerScope

    owner, owner_factory, factories, _ = _actual_0056_role_observation_lab
    roles = await observe_restricted_native_roles_0056_witness_v1(
        factories, source=_SOURCE_ROOT
    )
    scope = LedgerScope(
        environment="demo", account_id="730007", settlement_currency="USDT"
    )
    control_scope = ControlScope(scope.environment, scope.account_id)

    def clock():
        return datetime.now(UTC)

    control = DemoControlRepository(owner_factory, clock=clock)
    stopped = await control.latch_stop(control_scope, command_id="3" * 64)
    with pytest.raises(preflight.NativeLedgerPreflight0056Error):
        async with factories.writer_factory() as session, session.begin():
            await preflight.read_locked_native_ledger_preflight_0056_v1(
                factories, roles, session, scope
            )
    repository = QualificationLedgerRepository(owner_factory, clock=clock)
    await repository.initialize_capture_scope(scope)

    async with factories.writer_factory() as session, session.begin():
        initial = session.sync_session.get_transaction()
        observed = await preflight.read_locked_native_ledger_preflight_0056_v1(
            factories, roles, session, scope
        )
        assert session.sync_session.get_transaction() is initial
        assert observed.control_event_sha256 == stopped.event_sha256
        assert observed.emergency_stop is True and observed.stored_arm_present is False
        assert observed.scope_state == "BOOTSTRAP"
        assert observed.account_revision == observed.ledger_revision == 0
        assert (
            observed.unresolved_reservation_count
            == observed.unresolved_consumed_intent_count
            == 0
        )
        assert observed.database_head == "0056" and observed.admission == "DENY"
        assert (
            observed.reconciliation_verified
            is observed.account_complete
            is observed.execution_authority
            is False
        )
        async with owner.begin() as competing:
            assert (
                await competing.scalar(
                    text("SELECT pg_catalog.pg_try_advisory_xact_lock(:key)"),
                    {"key": account_lock_key(control_scope)},
                )
                is False
            )
            with pytest.raises(DBAPIError) as blocked:
                await competing.exec_driver_sql(
                    "LOCK TABLE public.qualification_reservation_transitions IN ROW EXCLUSIVE MODE NOWAIT"
                )
            assert blocked.value.orig.sqlstate == "55P03"
    active = await repository.read_bootstrap_checkpoint(scope)
    assert active.state.account_revision == active.state.ledger_revision == 0
    assert not active.state.active

    async with owner.begin() as competing:
        assert (
            await competing.scalar(
                text("SELECT pg_catalog.pg_try_advisory_xact_lock(:key)"),
                {"key": account_lock_key(control_scope)},
            )
            is True
        )
        await competing.exec_driver_sql(
            "ALTER TABLE public.qualification_reservation_transitions "
            "DISABLE TRIGGER qualification_transition_immutable"
        )
    try:
        with pytest.raises(preflight.NativeLedgerPreflight0056Error):
            async with factories.writer_factory() as session, session.begin():
                await preflight.read_locked_native_ledger_preflight_0056_v1(
                    factories, roles, session, scope
                )
    finally:
        async with owner.begin() as competing:
            await competing.exec_driver_sql(
                "ALTER TABLE public.qualification_reservation_transitions "
                "ENABLE TRIGGER qualification_transition_immutable"
            )

    async with factories.writer_factory() as session, session.begin():
        with pytest.raises(preflight.NativeLedgerPreflight0056Error):
            await preflight.read_locked_native_ledger_preflight_0056_v1(
                factories, replace(roles, role_route_sha256="f" * 64), session, scope
            )


async def test_actual0056_restricted_ledger_replays_retained_control_bound_intent(
    _actual_0056_role_observation_lab,
):
    from app.core import native_ledger_preflight_0056_v1 as preflight
    from app.core.native_role_witness_0056_v1 import (
        observe_restricted_native_roles_0056_witness_v1,
    )
    from app.database.models.qualification_ledger import (
        QualificationAccountScope,
        QualificationReservation,
        QualificationReservationTransition,
    )
    from app.database.repositories.demo_control import DemoControlRepository
    from app.trade_qualification import control_bound_ledger as bound
    from app.trade_qualification import demo_control as control
    from app.trade_qualification import reservations as reservation
    from app.trade_qualification.submission_intent import build_submission_intent
    from tests.unit.qualification_execution_binding_fixtures import (
        consumed_receipt,
        execution_binding,
    )
    from tests.unit.qualification_ledger_fixtures import ledger_fixture

    _, owner_factory, factories, _ = _actual_0056_role_observation_lab

    async def seed(account_id, *, corrupt):
        fixture = await asyncio.to_thread(ledger_fixture, account_id=account_id)
        request = fixture.request
        scope = request.scope
        control_scope = control.ControlScope(scope.environment, scope.account_id)
        binding = execution_binding(fixture)
        session_id = json.loads(binding.account_packet_json)["plan"][
            "session_binding_id"
        ]
        pins = control.ControlPins(
            "a" * 64,
            request.origin.original_policy_sha256,
            control.digest(session_id),
        )
        clock = fixture.now
        repo = DemoControlRepository(owner_factory, clock=lambda: clock)
        token = b"ctcc_unit_test_control_token_001"
        first = await repo.acquire(
            control_scope, owner_token=token, pins=pins, command_id="a" * 64
        )
        armed = await repo.change(
            control_scope,
            owner_token=token,
            owner_epoch=first.state.owner_epoch,
            expected_revision=first.state.revision,
            action="arm_requested",
            command_id="b" * 64,
            arm_expires_at=clock + timedelta(seconds=20),
        )
        consumed = consumed_receipt(fixture)
        reserved = consumed.model_copy(
            update={"state": "reserved", "state_revision": 1, "ledger_revision": 2}
        )
        reserve_raw = bound.build_reserved_evidence(
            request, reserved, armed.state, armed.event_sha256, checked_at=clock
        )
        inner = build_submission_intent(request, consumed, execution_binding=binding)
        intent = bound.build_control_bound_intent(
            request, inner, reserve_raw, armed.state, armed.event_sha256
        )
        clock += timedelta(seconds=30)
        stopped = await repo.latch_stop(control_scope, command_id="c" * 64)

        # Seed historical rows as owner of this disposable DB. The actual
        # reader receives only its production-defined restricted grants.
        coordinates = {
            "environment": scope.environment,
            "account_id": account_id,
            "settlement_currency": scope.settlement_currency,
        }
        async with owner_factory.begin() as session:
            session.add(
                QualificationAccountScope(
                    **coordinates,
                    account_revision=1,
                    ledger_revision=3,
                    claims_json=reservation.canonical(fixture.claims),
                    claims_sha256=reservation.digest(fixture.claims),
                    created_at=fixture.now,
                    updated_at=fixture.now,
                )
            )
            await session.flush()
            session.add(
                QualificationReservation(
                    **coordinates,
                    reservation_id=consumed.reservation_id,
                    original_event_key=consumed.original_event_key,
                    report_id=consumed.report_id,
                    instrument_id=consumed.instrument_id,
                    direction=consumed.direction,
                    correlation_group=consumed.correlation_group,
                    request_json=reservation.canonical(request),
                    request_sha256=consumed.request_sha256,
                    coverage_json=reservation.canonical(consumed.coverage),
                    risk_amount=consumed.coverage.risk_amount,
                    margin_amount=consumed.coverage.margin_amount,
                    notional_amount=consumed.coverage.notional_amount,
                    state=consumed.state,
                    state_revision=consumed.state_revision,
                    deadline=consumed.deadline,
                    created_at=consumed.created_at,
                    updated_at=consumed.updated_at,
                )
            )
            await session.flush()
            session.add_all(
                [
                    QualificationReservationTransition(
                        reservation_id=consumed.reservation_id,
                        state_revision=1,
                        from_state=None,
                        to_state="reserved",
                        reason_code=bound.RESERVED_REASON,
                        evidence_json="{}" if corrupt else reserve_raw,
                        occurred_at=reserved.updated_at,
                    ),
                    QualificationReservationTransition(
                        reservation_id=consumed.reservation_id,
                        state_revision=2,
                        from_state="reserved",
                        to_state="consumed",
                        reason_code=bound.CONSUMED_REASON,
                        evidence_json=intent.canonical_json,
                        occurred_at=consumed.updated_at,
                    ),
                ]
            )
        return scope, stopped, consumed.reservation_id

    roles = await observe_restricted_native_roles_0056_witness_v1(
        factories, source=_SOURCE_ROOT
    )
    scope, stopped, _ = await seed("730016", corrupt=False)
    async with factories.writer_factory() as session, session.begin():
        observed = await preflight.read_locked_native_ledger_preflight_0056_v1(
            factories, roles, session, scope
        )
        assert (
            observed.unresolved_reservation_count
            == observed.unresolved_consumed_intent_count
            == 1
        )
        assert observed.control_event_sha256 == stopped.event_sha256
        assert observed.emergency_stop and not observed.stored_arm_present
        assert observed.admission == "DENY" and not observed.execution_authority

    corrupt_scope, _, corrupt_id = await seed("730017", corrupt=True)
    with pytest.raises(
        preflight.NativeLedgerPreflight0056Error,
        match="^native_ledger_0056_preflight_refused$",
    ):
        async with factories.writer_factory() as session, session.begin():
            await preflight.read_locked_native_ledger_preflight_0056_v1(
                factories, roles, session, corrupt_scope
            )
    async with owner_factory() as session:
        assert (
            await session.get(QualificationReservation, corrupt_id)
        ).state == "consumed"


async def test_actual0056_terminal_report_reader_checks_grant_and_refuses_missing_spool(
    _actual_0056_role_observation_lab,
    monkeypatch,
):
    """Use the actual restricted writer and make no fake acceptable history."""
    from app.database.repositories import native_closed_reporting_0056_v1 as reporting

    _, owner_factory, selected, _ = _actual_0056_role_observation_lab
    missing = sha(b"ctcc-db0056-synthetic-missing-terminal-spool")
    async with selected.writer_factory() as session, session.begin():
        first = await session.get(reporting.QualificationReportSpool, missing)
        assert first is None
        assert await session.scalar(reporting._ELIGIBILITY_ACCESS) is True

    async with owner_factory.begin() as owner:
        counters = text("""
            SELECT (SELECT count(*) FROM public.qualification_reservations),
                   (SELECT count(*) FROM public.qualification_submission_outcomes),
                   (SELECT count(*) FROM public.qualification_report_spool),
                   (SELECT count(*) FROM public.qualification_report_projection_receipts)
        """)
        before = tuple((await owner.execute(counters)).one())

    readbacks = []
    original_get = reporting.AsyncSession.get

    async def observe_actual_get(session, model, identity, **options):
        value = await original_get(session, model, identity, **options)
        if model is reporting.QualificationReportSpool and identity == missing:
            assert session.bind is selected.writer_factory.kw["bind"]
            assert session.in_transaction()
            readbacks.append((session, value))
        return value

    with monkeypatch.context() as observer:
        observer.setattr(reporting.AsyncSession, "get", observe_actual_get)
        with pytest.raises(
            reporting.NativeTerminalReport0056Error,
            match="^native_terminal_report_0056_unavailable$",
        ):
            await reporting.observe_terminal_lineage_0056_v1(selected, spool_id=missing)
    assert len(readbacks) == 1
    reader, absent = readbacks[0]
    assert absent is None
    assert not reader.in_transaction()
    async with owner_factory.begin() as owner:
        assert tuple((await owner.execute(counters)).one()) == before
    async with selected.writer_factory() as session, session.begin():
        assert await session.get(reporting.QualificationReportSpool, missing) is None
