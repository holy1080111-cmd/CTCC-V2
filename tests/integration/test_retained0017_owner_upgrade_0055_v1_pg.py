"""Future genuine isolated0017->55 workflow; literal baseline must be sealed.

EMPTY/PENDING is a test refusal, never an approved skip or a synthetic baseline.
Register/run this suite only after independently accepted two-reference source
evidence. All databases are new disposable TLS labs, never a retained host DB.
"""

import asyncio
import os
import secrets
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncTransaction, create_async_engine
from sqlalchemy.pool import NullPool

from scripts import current0056_fitted_catalog_measurement_v1 as measurement
from scripts import retained0017_owner_upgrade_0055_v1 as subject
from tests.integration.test_gate3_fitted_candidate_custody_pg import (
    _isolated_tls_database_config,
)
from tests.native_private_temp_v1 import _private_root_path

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _build_canonical17(connection):
    config = Config(str(subject.ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(subject.ROOT / "migrations"))
    graph = ScriptDirectory.from_config(config)
    migration = MigrationContext.configure(
        connection,
        opts={
            "fn": lambda heads, _: graph._upgrade_revs("0017", heads),
            "target_metadata": subject.Base.metadata,
            "compare_type": True,
        },
    )
    with Operations.context(migration), migration.begin_transaction():
        migration.run_migrations()
    assert migration.get_current_heads() == ("0017",)


@pytest.fixture
async def _canonical_upgrade17_lab(tmp_path):
    # Before even disposable DDL: no monkeypatch, caller pin or equal pair may
    # install the missing literal schema proof on behalf of this test.
    subject._require_reference_seal()
    url, tls = _isolated_tls_database_config()
    nonce = uuid4().hex[:12]
    name, owner_name = "ctcc_upgrade17_" + nonce, "ctcc_upgrade17_owner_" + nonce
    admin = create_async_engine(
        url, poolclass=NullPool, connect_args={"ssl": tls}, hide_parameters=True
    )
    owner, created, role_created = None, False, False
    try:
        async with admin.connect() as connection:
            await connection.execution_options(isolation_level="AUTOCOMMIT")
            # Frozen0050 requires an actual superowner. A named role with a
            # newly allocated OID retains the reviewed catalog ownership edges.
            await connection.exec_driver_sql(
                f'CREATE ROLE "{owner_name}" LOGIN SUPERUSER NOINHERIT NOCREATEDB '
                "NOCREATEROLE NOREPLICATION NOBYPASSRLS"
            )
            role_created = True
            await connection.exec_driver_sql(
                f'CREATE DATABASE "{name}" OWNER "{owner_name}" TEMPLATE template0'
            )
            created = True
        owner = create_async_engine(
            url.set(database=name, username=owner_name),
            poolclass=NullPool,
            connect_args={"ssl": tls},
            hide_parameters=True,
        )
        async with owner.begin() as connection:
            await connection.run_sync(_build_canonical17)
            await connection.execute(
                text("""INSERT INTO public.demo_automation_state
 (id,armed,emergency_stop,locked,lock_reasons,session_date,baseline_equity,peak_equity,daily_pnl,
 trades_today,consecutive_losses,risk_peak_equity,realized_pnl_events)
 VALUES(1,false,true,true,'["unknown_retained_reason","unknown_retained_reason"]',current_date,
 300,325,-12.5,13,3,330,'[{"net_pnl":"-12.5","source":"declared-synthetic-history"}]')""")
            )
            audit_id = uuid4()
            await connection.execute(
                text(
                    "INSERT INTO public.audit_logs(id,actor,action,resource_type,resource_id,\"before\") VALUES(:id,'declared_lab','null_flip','synthetic','retained17-upgrade',NULL)"
                ),
                {"id": audit_id},
            )
            actor = tuple((await connection.execute(subject._PROFILE_SQL)).one())
            assert actor[0] == owner_name and actor[4] >= 16384
            settings = await connection.scalar(
                subject._SETTINGS_HASH,
                {"owner_oid": actor[4], "database_oid": actor[5]},
            )
            legacy_catalog = await connection.scalar(subject.original.CATALOG_SQL)
            legacy_catalog_sha = subject.original._sha(
                subject.original._canonical(
                    subject.read_evidence_json(legacy_catalog.encode())
                )
            )
        identity, files, source_map = measurement._read_hermetic_inputs()
        archive = Path("/ctcc-admitted-test-source.tar")
        if (
            identity.get("runner_contract")
            == "ctcc.source58.retained17_direct_authority_tls_probe.v1"
        ):
            from scripts.run_retained0017_reference_probe_v1 import (
                bound_authority_fixture_source_v1,
            )

            source = bound_authority_fixture_source_v1(
                identity, files, source_map, archive
            )
        else:
            source = measurement.source_identity(identity, files, source_map)
        assert (
            archive.is_file()
            and subject.baseline.sha(archive.read_bytes()) == identity["archive_sha256"]
        )
        retained = subject.original.RetainedOwnerPins0017V1(
            source["commit"],
            source["tree"],
            identity["archive_sha256"],
            subject.baseline.sha(
                b"declared synthetic disposable17 lab; not a restored host backup"
            ),
            subject.original.RetainedOwnerRoute0017V1(
                source["manifest_sha256"],
                os.environ["CTCC_DEVVAL_CUSTODY_TEST_TLS_CA_SHA256"],
                subject.baseline.sha(actor[11].encode("ascii")),
                owner.url.host,
                owner.url.port or 5432,
                actor[2],
                actor[0],
                actor[5],
                actor[4],
            ),
            expected_catalog_sha256=legacy_catalog_sha,
        )
        pins = subject.Retained0017UpgradePinsV1(retained, settings)
        private = _private_root_path(tmp_path)
        dsn = private / "owner-dsn"
        # The isolated cluster has declared trust authentication. This tests
        # private route parsing and TLS, not a password authentication claim.
        dsn.write_text(
            owner.url.set(
                port=retained.route.port, password=secrets.token_hex(24)
            ).render_as_string(hide_password=False),
            encoding="utf-8",
        )
        dsn.chmod(0o600)
        arguments = {
            "owner_dsn_file": dsn,
            "ca_file": Path(os.environ["CTCC_DEVVAL_CUSTODY_TEST_TLS_CA_FILE"]),
            "source_archive_file": archive,
        }
        prepared = subject._prepared(pins, **arguments)
        yield owner, pins, prepared, arguments, audit_id, private
    finally:
        if owner is not None:
            await owner.dispose()
        try:
            if created or role_created:
                async with admin.connect() as connection:
                    await connection.execution_options(isolation_level="AUTOCOMMIT")
                    if created:
                        await connection.exec_driver_sql(
                            f'DROP DATABASE "{name}" WITH (FORCE)'
                        )
                    if role_created:
                        await connection.exec_driver_sql(f'DROP ROLE "{owner_name}"')
        finally:
            await admin.dispose()


async def test_actual0017_direct_confirmed_apply_refuses_with_valid_readonly_route(
    _canonical_upgrade17_lab,
):
    owner, pins, _prepared, arguments, _audit, _private = _canonical_upgrade17_lab
    before = await subject.upgrade_preserved_owner_0017_to_0055_v1(pins, **arguments)
    assert before["status"] == "READ_ONLY_ELIGIBILITY_DATA"
    assert before["database_head"] == "0017"
    assert before["migration_performed"] is before["execution_authority"] is False
    assert before["schema_upgrade_authorized"] is False
    assert before["exchange_flat"] is None
    with pytest.raises(
        subject.Retained0017UpgradeRefused,
        match=r"^retained0017_upgrade0055_apply_lineage_pending$",
    ):
        await subject.upgrade_preserved_owner_0017_to_0055_v1(
            pins, confirmation=subject.CONFIRMATION, **arguments
        )
    after = await subject.upgrade_preserved_owner_0017_to_0055_v1(pins, **arguments)
    assert after == before
    async with owner.connect() as connection:
        assert (
            await connection.scalar(
                text("SELECT version_num FROM public.alembic_version")
            )
            == "0017"
        )


async def test_actual0017_upgrade0055_genuine_outer_rehearsal_commit_and_two_readbacks(
    _canonical_upgrade17_lab,
):
    owner, pins, _prepared, arguments, _audit, _private = _canonical_upgrade17_lab
    result = await subject.upgrade_preserved_owner_0017_to_0055_v1(
        pins, confirmation=subject.CONFIRMATION, **arguments
    )
    assert result["status"] == "COMMITTED_STOPPED_0055_READBACK_VERIFIED"
    assert (
        result["rehearsal_rolled_back_verified"] is True
        and result["independent_readbacks"] == 2
    )
    assert result["control_revision"] == 0 and result["execution_authority"] is False
    assert result["exchange_flat"] is None and result["account_complete"] is False
    async with owner.connect() as connection:
        assert (
            await connection.scalar(
                text("SELECT version_num FROM public.alembic_version")
            )
            == "0055"
        )
        added = sorted(
            subject._target_tables()
            - set(subject.baseline.source17_contract_v1()["columns"])
        )
        namespaces = tuple(
            (
                await connection.execute(
                    text(
                        "SELECT n.nspname::pg_catalog.text,pg_catalog.count(*) FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE c.relkind='r' AND c.relname=ANY(CAST(:names AS pg_catalog.text[])) GROUP BY n.nspname ORDER BY n.nspname"
                    ),
                    {"names": added},
                )
            ).all()
        )
        assert namespaces == (("public", len(added)),)
        assert tuple(
            (
                await connection.execute(
                    text(
                        "SELECT n.nspname::pg_catalog.text FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace WHERE p.proname='demo_automation_control_revision_guard'"
                    )
                )
            ).scalars()
        ) == ("public",)
    with pytest.raises(subject.Retained0017UpgradeRefused):
        await subject.upgrade_preserved_owner_0017_to_0055_v1(
            pins, confirmation=subject.CONFIRMATION, **arguments
        )


@pytest.mark.parametrize("phase", (1, 2))
async def test_actual0017_upgrade0055_rehearsal_or_final_fault_restores_rows_and_catalog(
    _canonical_upgrade17_lab, monkeypatch, phase
):
    owner, pins, prepared, _args, _audit, _private = _canonical_upgrade17_lab
    before = await subject._readonly_before(owner, pins)
    real, calls = subject._migrate, []

    def fail(connection, script):
        real(connection, script)
        calls.append(1)
        if len(calls) == phase:
            raise RuntimeError("declared isolated migration fault")

    monkeypatch.setattr(subject, "_migrate", fail)
    with pytest.raises(subject.Retained0017UpgradeRefused):
        await subject._commit_once(owner, pins, prepared, before)
    assert len(calls) == phase and await subject._readonly_before(owner, pins) == before


@pytest.mark.parametrize("phase", (1, 2))
async def test_actual0017_upgrade0055_null_flip_has_json_only_zero_delta_but_refuses(
    _canonical_upgrade17_lab, monkeypatch, phase
):
    owner, pins, prepared, _args, audit_id, _private = _canonical_upgrade17_lab
    before = await subject._readonly_before(owner, pins)
    real, calls, old_equal = subject._migrate, [], []

    def corrupt(connection, script):
        real(connection, script)
        calls.append(1)
        if len(calls) == phase:
            old = connection.scalar(
                text(
                    "SELECT pg_catalog.to_jsonb(a)::pg_catalog.text FROM public.audit_logs a WHERE id=:id"
                ),
                {"id": audit_id},
            )
            connection.execute(
                text(
                    "UPDATE public.audit_logs SET \"before\"='null'::pg_catalog.jsonb WHERE id=:id"
                ),
                {"id": audit_id},
            )
            old_equal.append(
                old
                == connection.scalar(
                    text(
                        "SELECT pg_catalog.to_jsonb(a)::pg_catalog.text FROM public.audit_logs a WHERE id=:id"
                    ),
                    {"id": audit_id},
                )
            )

    monkeypatch.setattr(subject, "_migrate", corrupt)
    with pytest.raises(subject.Retained0017UpgradeRefused):
        await subject._commit_once(owner, pins, prepared, before)
    assert old_equal == [True] and len(calls) == phase
    assert await subject._readonly_before(owner, pins) == before


async def test_actual0017_upgrade0055_real_commit_then_lost_result_never_reapplies(
    _canonical_upgrade17_lab, monkeypatch
):
    owner, pins, prepared, _args, _audit, _private = _canonical_upgrade17_lab
    before = await subject._readonly_before(owner, pins)
    real_commit, commits = AsyncTransaction.commit, []

    async def lost(transaction):
        await real_commit(transaction)
        commits.append(1)
        raise OSError("declared lost commit acknowledgement")

    monkeypatch.setattr(AsyncTransaction, "commit", lost)
    with pytest.raises(subject.Retained0017UpgradeCommitUnknown):
        await subject._commit_once(owner, pins, prepared, before)
    assert commits == [1]
    monkeypatch.setattr(AsyncTransaction, "commit", real_commit)
    async with owner.connect() as connection:
        assert (
            await connection.scalar(
                text("SELECT version_num FROM public.alembic_version")
            )
            == "0055"
        )
    with pytest.raises(subject.Retained0017UpgradeRefused):
        await subject._commit_once(owner, pins, prepared, before)
    assert commits == [1]


async def test_actual0017_upgrade0055_lost_independent_readback_reports_committed_uncertain(
    _canonical_upgrade17_lab, monkeypatch
):
    owner, pins, _prepared, arguments, _audit, _private = _canonical_upgrade17_lab
    real, reads = subject._after, []

    async def unavailable(*args, **kwargs):
        if kwargs["read_only"]:
            reads.append(1)
            raise OSError("declared independent readback failure")
        return await real(*args, **kwargs)

    monkeypatch.setattr(subject, "_after", unavailable)
    with pytest.raises(subject.Retained0017UpgradeReadbackRequired):
        await subject.upgrade_preserved_owner_0017_to_0055_v1(
            pins, confirmation=subject.CONFIRMATION, **arguments
        )
    assert reads == [1]
    async with owner.connect() as connection:
        assert (
            await connection.scalar(
                text("SELECT version_num FROM public.alembic_version")
            )
            == "0055"
        )


async def test_actual0017_upgrade0055_unresolved_legacy_intent_is_retained_and_no_ddl_runs(
    _canonical_upgrade17_lab,
):
    owner, pins, _prepared, arguments, _audit, _private = _canonical_upgrade17_lab
    async with owner.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO public.okx_live_execution_intents(idempotency_key,request_hash,action,status,instrument_id,detail_codes) VALUES('declared_retained_unknown',:sha,'place_order','ambiguous','BTC-USDT-SWAP','[\"unknown_result\"]')"
            ),
            {"sha": "a" * 64},
        )
    with pytest.raises(
        ValueError, match=r"^native_legacy_live_uncertain_or_tracked_exposure$"
    ):
        await subject._readonly_before(owner, pins)
    with pytest.raises(
        subject.Retained0017UpgradeRefused, match=r"^retained0017_upgrade0055_refused$"
    ):
        await subject.upgrade_preserved_owner_0017_to_0055_v1(pins, **arguments)
    async with owner.connect() as connection:
        assert (
            await connection.scalar(
                text("SELECT version_num FROM public.alembic_version")
            )
            == "0017"
        )
        assert (
            await connection.scalar(
                text(
                    "SELECT status FROM public.okx_live_execution_intents WHERE idempotency_key='declared_retained_unknown'"
                )
            )
            == "ambiguous"
        )


async def test_actual0017_cli_valid_private_pins_read_only_and_confirmed_refusal(
    _canonical_upgrade17_lab,
):
    owner, pins, _prepared, arguments, _audit, private = _canonical_upgrade17_lab
    raw = subject.baseline.canonical({"schema": subject.PINS_SCHEMA, **asdict(pins)})
    pins_file = private / "reviewed-upgrade-pins.json"
    pins_file.write_bytes(raw)
    pins_file.chmod(0o600)
    assert subject._load_pins(pins_file, subject.baseline.sha(raw)) == pins
    import sys

    command = (
        sys.executable,
        "-B",
        "-m",
        "scripts.retained0017_owner_upgrade_0055_v1",
        "--pins-file",
        str(pins_file),
        "--pins-sha256",
        subject.baseline.sha(raw),
        "--owner-dsn-file",
        str(arguments["owner_dsn_file"]),
        "--ca-file",
        str(arguments["ca_file"]),
        "--source-archive-file",
        str(arguments["source_archive_file"]),
    )

    async def run_cli(*options):
        process = await asyncio.create_subprocess_exec(
            *command,
            *options,
            cwd=subject.ROOT,
            env={
                "PATH": os.environ["PATH"],
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONNOUSERSITE": "1",
            },
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=600)
        except BaseException:
            if process.returncode is None:
                process.kill()
                await process.wait()
            raise
        assert not stderr
        assert owner.url.host.encode() not in stdout
        assert str(arguments["owner_dsn_file"]).encode() not in stdout
        return process.returncode, subject.read_evidence_json(stdout)

    code, observed = await run_cli()
    assert code == 0 and observed["status"] == "READ_ONLY_ELIGIBILITY_DATA"
    assert observed["database_head"] == "0017"
    assert observed["migration_performed"] is observed["execution_authority"] is False
    code, observed = await run_cli("--confirm", subject.CONFIRMATION)
    assert code == 2
    assert observed == {"status": "REFUSED", "execution_authority": False}
    async with owner.connect() as connection:
        assert (
            await connection.scalar(
                text("SELECT version_num FROM public.alembic_version")
            )
            == "0017"
        )


@pytest.mark.parametrize(
    "setting", ("session_preload_libraries", "local_preload_libraries")
)
async def test_actual0017_controlled_startup_blocks_preload_but_rejects_original_bad_default(
    _canonical_upgrade17_lab,
    monkeypatch,
    setting,
):
    from dataclasses import replace

    owner, pins, _prepared, arguments, _audit, _private = _canonical_upgrade17_lab
    assert setting in ("session_preload_libraries", "local_preload_libraries")
    async with owner.begin() as connection:
        await connection.exec_driver_sql(
            'ALTER DATABASE "'
            + owner.url.database
            + '" SET '
            + setting
            + "='ctcc_declared_missing_preload_never_loaded'"
        )
    # Causal fixture: an ordinary subsequent session really attempts startup
    # preload and fails before it can issue SQL. No executable module exists.
    with pytest.raises((asyncpg.UndefinedFileError, DBAPIError)) as uncontrolled:
        async with owner.connect():
            pytest.fail("uncontrolled missing preload unexpectedly connected")
    original_error = uncontrolled.value
    if isinstance(original_error, DBAPIError):
        original_error = original_error.orig
    assert getattr(original_error, "sqlstate", None) == "58P01"
    assert "ctcc_declared_missing_preload_never_loaded" in str(original_error)
    controlled = create_async_engine(
        owner.url,
        poolclass=NullPool,
        hide_parameters=True,
        connect_args={
            "ssl": _prepared[1],
            "server_settings": dict(subject._STARTUP_SETTINGS),
        },
    )
    try:
        async with controlled.connect() as connection:
            digest = await connection.scalar(
                subject._SETTINGS_HASH,
                {
                    "owner_oid": pins.retained.route.owner_oid,
                    "database_oid": pins.retained.route.database_oid,
                },
            )
        # Even a reviewed matching hash cannot bless this source-forbidden
        # original default merely because startup overrides made it inactive.
        reviewed_bad = replace(pins, maintenance_settings_sha256=digest)
        opened = []
        original_factory = subject.create_async_engine

        def watch(_conn, _cursor, sql, _parameters, _context, _executemany):
            if (
                "FROM public." in sql
                or "UPDATE public." in sql
                or "CREATE TABLE" in sql
            ):
                opened.append(True)

        def factory(*args, **kwargs):
            engine = original_factory(*args, **kwargs)
            event.listen(engine.sync_engine, "before_cursor_execute", watch)
            return engine

        monkeypatch.setattr(subject, "create_async_engine", factory)
        with pytest.raises(subject.Retained0017UpgradeRefused):
            await subject.upgrade_preserved_owner_0017_to_0055_v1(
                reviewed_bad, confirmation=subject.CONFIRMATION, **arguments
            )
        assert opened == []
        async with controlled.connect() as connection:
            assert (
                await connection.scalar(
                    text("SELECT version_num FROM public.alembic_version")
                )
                == "0017"
            )
    finally:
        await controlled.dispose()
    # The owning fixture drops only this new disposable database through its
    # separate admin database; its bad DB-specific setting is not propagated.
