"""Real disposable PG17 component mechanics; never genuine native authority."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.domain import collector_current_0055_catalog_v1 as profile
from app.domain import native_closed_report_catalog_v55 as reports
from app.domain import native_closed_report_schema_v55 as report_schema
from app.domain import native_filled_flat_closure_schema_v55 as schema
from app.domain import native_terminal_catalog_v55 as helpers
from app.domain import native_zero_fok_closure_schema_v55 as zero
from app.domain.source_primitives import canonical
from tests.controlled_current55_fixtures import (
    controlled_current55_database as controlled_current55_database,  # noqa: PLC0414 -- Pytest collects this imported fixture.
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_actual_full55_observes_every_helper_and_report_guard_without_native_admission(
    controlled_current55_database,
):
    async with (
        controlled_current55_database.connect() as connection,
        connection.begin(),
    ):
        await connection.execute(text("SET TRANSACTION READ ONLY"))
        await connection.execute(
            text("SET LOCAL search_path = pg_catalog, public, pg_temp")
        )
        assert (
            await connection.scalar(text("SELECT current_setting('search_path')"))
            == "pg_catalog, public, pg_temp"
        )
        actual = await connection.scalar(text(profile.OWNER_CATALOG_SQL))
        observed = profile.observe_actual_owner_catalog(actual)
        owner = actual["issued"]["owner"]
        assert observed["native_baseline_admission"] is False
        assert observed["native_closure_body_sha256"] == schema.CURRENT_BODY_SHA256
        assert observed["native_zero_closure_body_sha256"] == zero.CURRENT_BODY_SHA256
        assert (
            len(helpers.observe_catalog(actual["native_terminal55"], owner=owner)) == 7
        )
        assert set(
            reports.observe_catalog(
                actual["native_report55"], owner=owner, actor=owner, owner_mode=True
            )
        ) == {"functions", "tables"}
        assert schema.CURRENT_SQL.replace(schema._BRANCH, "", 1) == zero.CURRENT_SQL


@pytest.mark.parametrize(
    "payload",
    ('{"a":1,"a":2}', '{"a":{"b":1,"b":2}}', '{"a":0.1}', "[1.0]"),
    ids=("duplicate_top", "duplicate_nested", "fractional_object", "fractional_array"),
)
async def test_real_sql_strict_decoder_rejects_duplicate_or_fractional_json(
    controlled_current55_database, payload
):
    async with controlled_current55_database.connect() as connection:
        with pytest.raises(DBAPIError):
            await connection.execute(
                text("SELECT public.qualification_native_plain_v55(:raw)"),
                {"raw": payload},
            )


async def test_real_sql_cursor_requires_actual_empty_terminal_page(
    controlled_current55_database,
):
    first = {
        "role": "fills",
        "meta": {"path": "/api/v5/trade/fills"},
        "query": {"limit": "100"},
        "data": [{"billId": "12"}, {"billId": "11"}],
    }
    terminal = {
        "role": "fills",
        "meta": {"path": "/api/v5/trade/fills"},
        "query": {"limit": "100", "after": "11"},
        "data": [],
    }
    statement = text(
        "SELECT public.qualification_native_rows_v55(CAST(:pages AS jsonb),'fills','/api/v5/trade/fills','{}'::jsonb,'billId')"
    )
    async with controlled_current55_database.connect() as connection:
        assert (
            await connection.scalar(
                statement, {"pages": canonical([first, terminal]).decode()}
            )
            == first["data"]
        )
        await connection.rollback()
        with pytest.raises(DBAPIError):
            await connection.scalar(statement, {"pages": canonical([first]).decode()})


@pytest.mark.parametrize(
    "mutation",
    (
        "helper_body",
        "helper_security",
        "report_guard",
        "eligible_index",
        "disable_spool_guard",
    ),
)
async def test_actual_catalog_rejects_helper_report_index_or_guard_drift(
    controlled_current55_database, mutation
):
    async with controlled_current55_database.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.execute(
                text("SET LOCAL search_path = pg_catalog, public, pg_temp")
            )
            assert (
                await connection.scalar(text("SELECT current_setting('search_path')"))
                == "pg_catalog, public, pg_temp"
            )
            baseline = await connection.scalar(text(profile.OWNER_CATALOG_SQL))
            # This is owner-only read/write DDL mechanics, not native admission.
            # Require the unchanged complete observer to accept the real healthy
            # context before testing a single controlled catalog mutation.
            assert (
                profile._observed(baseline, owner_mode=True, read_only="off")[
                    "native_baseline_admission"
                ]
                is False
            )
            if mutation == "helper_body":
                await connection.exec_driver_sql(
                    "CREATE OR REPLACE FUNCTION public.qualification_native_number_v55(j jsonb) RETURNS numeric AS $ctcc$ BEGIN RETURN 0; END; $ctcc$ LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp"
                )
            elif mutation == "helper_security":
                await connection.exec_driver_sql(
                    "ALTER FUNCTION public.qualification_native_filled_flat_data_v55(text,timestamptz) SECURITY DEFINER"
                )
            elif mutation == "report_guard":
                await connection.exec_driver_sql(report_schema.LEGACY_SPOOL_SQL)
            elif mutation == "eligible_index":
                await connection.exec_driver_sql(
                    "DROP INDEX public.uq_submission_eligible_reservation"
                )
                await connection.exec_driver_sql(
                    "CREATE UNIQUE INDEX uq_submission_eligible_reservation ON public.qualification_report_spool(reservation_id) WHERE true"
                )
            else:
                await connection.exec_driver_sql(
                    "ALTER TABLE public.qualification_report_spool DISABLE TRIGGER qualification_spool_binding_guard"
                )
            actual = await connection.scalar(text(profile.OWNER_CATALOG_SQL))
            with pytest.raises(ValueError):
                profile._observed(actual, owner_mode=True, read_only="off")
        finally:
            await transaction.rollback()


async def test_inert_ids_and_hashes_cannot_release_or_become_report_eligible(
    controlled_current55_database,
):
    async with (
        controlled_current55_database.connect() as connection,
        connection.begin(),
    ):
        assert (
            await connection.scalar(
                text(
                    "SELECT public.qualification_native_filled_flat_data_v55(:rid,:at)"
                ),
                {"rid": "a" * 64, "at": datetime.now(UTC)},
            )
            is False
        )
        assert (
            await connection.scalar(
                text(
                    "SELECT public.qualification_native_report_eligible_v55(:oid,:rid,:report,:policy)"
                ),
                {
                    "oid": "a" * 64,
                    "rid": "b" * 64,
                    "report": "c" * 64,
                    "policy": "d" * 64,
                },
            )
            is False
        )


async def test_configuration_ddl_matches_reviewed_sql_and_empty_account_is_not_blocked(
    controlled_current55_database,
):
    from hashlib import sha256

    from app.domain import native_leverage_configuration_enforcement_v55 as enforcement
    from app.domain import native_leverage_configuration_schema_v55 as configuration

    actual = configuration.statements() + enforcement.statements()
    expected = (
        (2421, "3ff12e5fd85d905a6fe0eb044b35a5603fc88a3d9290f5b69a9df74cb4329ba7"),
        (216, "a6bd01e419c40bdf191edcdba9194398ad3f957b68a20ac4e53a58fd5185e22f"),
        (1011, "ab80bc3b9c155182be27c7a65f4d7d966a9192605fd7a97b0328b467700d925e"),
        (1358, "a83eb71fc902b20a7c095710185d0cb9e4a76c3ccc4238d7c0bdc1d36121c199"),
        (374, "60593950c8e57ba438f460706aeab7a9df5e46e44bbcf3ed186ecc051de679bd"),
        (1449, "690fec33d520734c063ae1e7bc8d1a3098a0f04329d92b4cd84ee20b5f9b274c"),
        (1633, "c72dd43d0d2dc56b564af54014c6267f09bd754270bb04be4704ccffa543a829"),
        (14493, "80300362296d09a63c3f2d8f758ddf7f7cf456d62d4701a7ec5a9a866f1b88ff"),
        (1029, "06ad6d4329cd84700b737f44738085576a08f4ccfd892aed64b133deefc704a4"),
        (246, "11ca0d73c0342b32a919e6217b3fff47ddf53e43078842c7186e8834d7a85ae8"),
        (225, "fee1bece4e16ad6ec529d2da0f8aaf8c526bd70c784571ab875170d7d35f29df"),
        (240, "5db22c1cc187357c084afcbae6622ac64decec65cc3ce641bfc1845edfc5bff3"),
        (248, "0b5df544ec46d688e13f2d6a5a9e684d831c1e6140523ed3eb3ecfd501ccd4b7"),
        (227, "5f59738e7319a085134722364c451ab5dedb68097814df67568c51e0511ad295"),
        (242, "88e7beb40cb573b7b57634085c81a8f2419f60655fd2040a01557f044e88ba79"),
        (246, "c5b42c6a794314c7c077cb2b2e056aa54d981b0ea34764fb631549086d0b4ed8"),
        (225, "05e2931eb4d8f5cd6d95fb7c8694da674f468c4e1ae90eb63f86fa384fab33b0"),
        (240, "4ea81a3d91eb4181317f8753c296636fa6864d91406dd12dc2dd8744fc136d69"),
        (2564, "7b25aba4905e090767c85c3ecfcfde65c0fe254a3130b2ffc555e0b85e5751ef"),
        (196, "4a0c4ea6131e7ab793d0fe71fee92eb848cc11bee28e92eb221bd8095a9a3c2a"),
        (212, "2e2c2a4964dc7235d53ef3472358f81a483de01107edb186e804b8360f04c1ad"),
        (213, "a5b8f6a49860c8e3d969211dd9a2d5ef38c53a9c2b8b619cc065ac2f172af2af"),
        (209, "c1b6a915ba188534de31d3c6c22a62b5024eea21308f58e957718eb7ca171078"),
        (99, "25b415221d401aeb713c24d0427f052b6924f746dfd117eda8880e9968cb08fa"),
    )
    assert (
        tuple((len(sql.encode()), sha256(sql.encode()).hexdigest()) for sql in actual)
        == expected
    )
    async with controlled_current55_database.connect() as connection:
        assert (
            await connection.scalar(
                text(
                    "SELECT public.qualification_leverage_configuration_blocked_v55(:env,:uid)"
                ),
                {"env": "demo", "uid": "synthetic-component-only"},
            )
            is False
        )


@pytest.mark.parametrize("kind", ("intents", "attempts", "results"))
async def test_configuration_audit_rows_reject_truncate(
    controlled_current55_database, kind
):
    # Include the exact referencing tables so PostgreSQL reaches the
    # application trigger instead of rejecting the command at its FK precheck.
    suffixes = ("intents", "attempts", "results")
    tables = ", ".join(
        f"public.qualification_leverage_configuration_{name}_v1"
        for name in suffixes[suffixes.index(kind) :]
    )
    async with controlled_current55_database.connect() as connection:
        with pytest.raises(
            DBAPIError, match="native_configuration_immutable"
        ) as rejected:
            await connection.exec_driver_sql("TRUNCATE TABLE " + tables)
        assert rejected.value.orig.sqlstate == "P0001"


async def _observe_owner_current55(engine):
    from tests.integration.test_account_history_original_frame_repository_v1 import (
        settings,
    )

    async with engine.begin() as connection:
        await connection.execute(text("SET TRANSACTION READ ONLY"))
        await settings(connection)
        raw = await connection.scalar(text(profile.OWNER_CATALOG_SQL))
        observed = profile.observe_actual_owner_catalog(raw)
        admitted = profile.verify_actual_owner_catalog(raw)
        assert admitted["native_baseline_admission"] is True
        assert (
            admitted["native_definitions_sha256"]
            == observed["native_definitions_sha256"]
        )
        assert (
            admitted["account_complete"] is False
            and admitted["execution_authority"] is False
        )
        xid = await connection.scalar(text("SELECT txid_current()::text"))
        version = await connection.scalar(
            text("SELECT current_setting('server_version_num')::int")
        )
        assert (
            observed["database_head"] == "0055"
            and observed["native_baseline_admission"] is False
        )
        assert (
            observed["account_complete"] is False
            and observed["execution_authority"] is False
        )
        assert xid.isascii() and xid.isdecimal() and 170000 <= version < 180000
    return {
        "definitions_sha256": observed["native_definitions_sha256"],
        "xid": xid,
        "version": version,
    }


async def test_actual_current55_owner_catalog_survives_empty_downgrade_and_reupgrade(
    controlled_current55_database,
    request,
):
    from tests.integration.test_controlled_history_current55_v2_pg import _migrate

    engine = controlled_current55_database
    first = await _observe_owner_current55(engine)
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: _migrate(sync, "0054", down=True))
    async with engine.begin() as connection:
        assert (
            await connection.scalar(
                text("SELECT version_num FROM public.alembic_version")
            )
            == "0054"
        )
        assert (
            await connection.scalar(
                text(
                    "SELECT to_regclass('public.qualification_leverage_configuration_intents_v1')"
                )
            )
            is None
        )
        await connection.run_sync(lambda sync: _migrate(sync, "0055"))
    second = await _observe_owner_current55(engine)
    assert second["xid"] != first["xid"]
    assert second["version"] == first["version"]
    assert second["definitions_sha256"] == first["definitions_sha256"]
    receipt = {
        "schema": "ctcc.current55.actual_pg17_owner_reupgrade_observation.v1",
        "first": first,
        "second": second,
        "native_baseline_admission": False,
        "account_complete": False,
        "execution_authority": False,
    }
    request.node.user_properties.append(
        ("ctcc_pg55_owner_observation_v1", canonical(receipt).decode("ascii"))
    )


async def test_actual_current55_writer_login_matches_owner_and_rejects_missing_privilege(
    controlled_current55_database,
    request,
):
    from app.domain import controlled_history_current55_schema_v2 as history
    from tests.integration.test_account_history_original_frame_repository_v1 import (
        collector,
        settings,
    )

    engine = controlled_current55_database
    owner = await _observe_owner_current55(engine)
    async with (
        collector(engine, grant=True) as (raw, raw_oid),
        collector(engine) as (writer, writer_oid),
    ):
        raw_role, writer_role = raw.url.username, writer.url.username
        assert raw_oid != writer_oid
        table_grants = (
            (writer_role, "SELECT,INSERT", "demo_collector_current_revision_lineages"),
            (writer_role, "SELECT,INSERT", "demo_collector_issued_account_revisions"),
            (writer_role, "SELECT,INSERT,UPDATE", "demo_account_controls"),
            (writer_role, "SELECT,INSERT", "demo_control_journal"),
            (writer_role, "SELECT,INSERT", "demo_control_clear_acknowledgements_v2"),
            (writer_role, "SELECT,INSERT", "demo_policy_counter_initializations_v1"),
            (writer_role, "SELECT,INSERT", "qualification_submission_outcomes"),
            (writer_role, "SELECT,INSERT", "qualification_report_spool"),
            (writer_role, "SELECT,INSERT", "qualification_report_projection_receipts"),
            (writer_role, "SELECT", "qualification_reservations"),
            (
                writer_role,
                "SELECT,INSERT,UPDATE",
                "qualification_leverage_configuration_intents_v1",
            ),
            (
                writer_role,
                "SELECT,INSERT",
                "qualification_leverage_configuration_attempts_v1",
            ),
            (
                writer_role,
                "SELECT,INSERT",
                "qualification_leverage_configuration_results_v1",
            ),
            (writer_role, "SELECT", "alembic_version"),
            (raw_role, "SELECT", "alembic_version"),
        )
        function_grants = (
            (writer_role, history.APPEND, history.SIGNATURES[history.APPEND]),
            (raw_role, history.FRAME, history.SIGNATURES[history.FRAME]),
        )
        try:
            async with engine.begin() as connection:
                for role, rights, table in table_grants:
                    await connection.execute(
                        text(f'GRANT {rights} ON public.{table} TO "{role}"')
                    )
                for role, name, signature in function_grants:
                    await connection.execute(
                        text(
                            f'GRANT EXECUTE ON FUNCTION public.{name}({signature}) TO "{role}"'
                        )
                    )
            writer_reads = []
            for readonly in (False, True):
                async with writer.begin() as connection:
                    if readonly:
                        await connection.execute(text("SET TRANSACTION READ ONLY"))
                    await settings(connection)
                    raw_catalog = await connection.scalar(
                        text(profile.ACTOR_CATALOG_SQL)
                    )
                    assert raw_catalog["role"]["oid"] == writer_oid
                    observed = profile.observe_actual_writer_catalog(
                        raw_catalog, read_only="on" if readonly else "off"
                    )
                    verify = (
                        profile.verify_actual_readonly_writer_catalog
                        if readonly
                        else profile.verify_actual_writer_catalog
                    )
                    admitted = verify(raw_catalog)
                    assert admitted["native_baseline_admission"] is True
                    assert (
                        admitted["native_definitions_sha256"]
                        == observed["native_definitions_sha256"]
                    )
                    assert (
                        admitted["account_complete"] is False
                        and admitted["execution_authority"] is False
                    )
                    for relation in (
                        "qualification_report_spool",
                        "qualification_report_projection_receipts",
                    ):
                        rights = (
                            await connection.execute(
                                text(
                                    "SELECT has_table_privilege(current_user,:name,'SELECT'),"
                                    "has_table_privilege(current_user,:name,'INSERT'),has_table_privilege(current_user,:name,'UPDATE'),"
                                    "has_table_privilege(current_user,:name,'DELETE')"
                                ),
                                {"name": "public." + relation},
                            )
                        ).one()
                        assert tuple(rights) == (True, True, False, False)
                    xid = await connection.scalar(text("SELECT txid_current()::text"))
                    assert observed["native_baseline_admission"] is False
                    assert (
                        observed["account_complete"] is False
                        and observed["execution_authority"] is False
                    )
                    assert (
                        observed["native_definitions_sha256"]
                        == owner["definitions_sha256"]
                    )
                    writer_reads.append(
                        {
                            "readonly": readonly,
                            "xid": xid,
                            "definitions_sha256": observed["native_definitions_sha256"],
                        }
                    )
            assert len({owner["xid"], *(item["xid"] for item in writer_reads)}) == 3
            await _verify_actual_current55_raw_catalog_pair(
                raw, writer, raw_oid, writer_oid
            )
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "REVOKE INSERT ON public.qualification_report_projection_receipts "
                        f'FROM "{writer_role}"'
                    )
                )
            async with writer.begin() as connection:
                await settings(connection)
                raw_catalog = await connection.scalar(text(profile.ACTOR_CATALOG_SQL))
                with pytest.raises(profile.Current0055CatalogError):
                    profile.verify_actual_writer_catalog(raw_catalog)
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "GRANT INSERT ON public.qualification_report_projection_receipts "
                        f'TO "{writer_role}"'
                    )
                )
                await connection.execute(
                    text(
                        "REVOKE INSERT ON public.qualification_leverage_configuration_results_v1 "
                        f'FROM "{writer_role}"'
                    )
                )
            async with writer.begin() as connection:
                await settings(connection)
                raw_catalog = await connection.scalar(text(profile.ACTOR_CATALOG_SQL))
                with pytest.raises(profile.Current0055CatalogError):
                    profile.verify_actual_writer_catalog(raw_catalog)
        finally:
            async with engine.begin() as connection:
                for role, name, signature in reversed(function_grants):
                    await connection.execute(
                        text(
                            f'REVOKE EXECUTE ON FUNCTION public.{name}({signature}) FROM "{role}"'
                        )
                    )
                for role, rights, table in reversed(table_grants):
                    await connection.execute(
                        text(f'REVOKE {rights} ON public.{table} FROM "{role}"')
                    )
    receipt = {
        "schema": "ctcc.current55.actual_pg17_restricted_writer_observation.v1",
        "owner": owner,
        "writer_reads": writer_reads,
        "missing_result_insert_rejected": True,
        "missing_report_projection_insert_rejected": True,
        "native_baseline_admission": False,
        "account_complete": False,
        "execution_authority": False,
    }
    request.node.user_properties.append(
        ("ctcc_pg55_writer_observation_v1", canonical(receipt).decode("ascii"))
    )


async def test_actual_current55_configuration_model_matches_installed_schema(
    controlled_current55_database, request
):
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from app.database import models
    from app.database.base import Base
    from app.domain import native_leverage_configuration_schema_v55 as configuration

    tables = set(configuration.TABLES)
    assert {
        models.NativeLeverageConfigurationIntentV1.__tablename__,
        models.NativeLeverageConfigurationAttemptV1.__tablename__,
        models.NativeLeverageConfigurationResultV1.__tablename__,
    } == tables

    def selected(name, kind, parents):
        if kind == "schema":
            return name is None
        return (
            name in tables if kind == "table" else parents.get("table_name") in tables
        )

    def compare(connection):
        context = MigrationContext.configure(
            connection,
            opts={
                "compare_type": True,
                "compare_server_default": True,
                "include_name": selected,
                "include_object": lambda item, name, kind, reflected, counterpart: (
                    name in tables if kind == "table" else item.table.name in tables
                ),
            },
        )
        return compare_metadata(context, Base.metadata)

    async with controlled_current55_database.begin() as connection:
        changes = await connection.run_sync(compare)
    rows = []
    for item in changes:
        for change in item if type(item) is list else (item,):
            operation = change[0]
            if operation.startswith("modify_"):
                rows.append([str(operation), str(change[2]), str(change[3]), []])
            else:
                subject = change[-1]
                table = (
                    subject
                    if operation in ("add_table", "remove_table")
                    else subject.table
                )
                rows.append(
                    [
                        str(operation),
                        str(table.name),
                        None if subject.name is None else str(subject.name),
                        [
                            str(column.name)
                            for column in getattr(subject, "columns", ())
                        ],
                    ]
                )
    request.node.user_properties.append(
        ("ctcc_pg55_metadata_drift_v1", canonical(rows).decode("ascii"))
    )
    assert changes == []


async def _verify_actual_current55_raw_catalog_pair(
    raw_engine, writer_engine, raw_oid, writer_oid
):
    from datetime import timedelta

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.database.repositories import (
        account_history_original_native_read_current0053_v1 as original,
    )
    from app.database.repositories import (
        collector_current_0055_multirole_catalog_v1 as runtime_catalog,
    )
    from app.domain.source_primitives import decode
    from tests.integration.test_account_history_original_frame_repository_v1 import (
        settings,
    )

    raw_factory = async_sessionmaker(
        raw_engine, expire_on_commit=False, autoflush=False
    )
    writer_factory = async_sessionmaker(
        writer_engine, expire_on_commit=False, autoflush=False
    )
    async with raw_factory() as session, session.begin():
        await settings(session)
        assert (
            await session.scalar(
                text(
                    "SELECT has_table_privilege(current_user,"
                    "'public.demo_collector_current_revision_lineages','SELECT')"
                )
            )
            is False
        )
        fence = original._Fence(datetime.now(UTC) + timedelta(seconds=60))
        observed_bytes = await runtime_catalog.verify_actual_raw_history_catalog_v1(
            session, fence, raw_oid, writer_factory, raw_factory
        )
        # These bytes contain both complete PG catalog observations; the
        # smaller default decoder limit is for a single public receipt.
        observed = decode(observed_bytes, maximum=4 * 1024 * 1024)
    current = observed["current0055"]
    assert current["raw"]["role"]["oid"] == raw_oid
    assert current["writer"]["role"]["oid"] == writer_oid
    assert current["profile"]["actual_separate_roles_verified"] is True
    assert (
        current["profile"]["native_definitions_sha256"]
        == profile.PG17_NATIVE_BASELINE_SHA256
    )
    assert current["profile"]["account_complete"] is False
    assert current["profile"]["execution_authority"] is False
    async with raw_factory() as session, session.begin():
        await settings(session)
        fence = original._Fence(datetime.now(UTC) + timedelta(seconds=60))
        with pytest.raises(runtime_catalog.Current0055MultiRoleError):
            await runtime_catalog.verify_actual_raw_history_catalog_v1(
                session, fence, writer_oid, writer_factory, raw_factory
            )


async def test_actual_restricted_configuration_insert_rejects_unstored_current_lineage(
    controlled_current55_database,
):
    """Synthetic flat DB fixture, real writer and trigger, no CURRENT authority."""
    from datetime import timedelta
    from uuid import uuid4

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.database.models.demo_automation import DemoAutomationState
    from app.database.models.okx_demo import OkxDemoSyncCheckpoint
    from app.database.repositories.qualification_ledger import (
        QualificationLedgerRepository,
    )
    from app.domain.source_primitives import sha
    from app.trade_qualification.demo_control import ControlPins, ControlScope, acquire
    from app.trade_qualification.reservations import LedgerScope
    from tests.controlled_writer_coverage_fixtures import _store_control_fixture
    from tests.integration.test_account_history_original_frame_repository_v1 import (
        collector,
        settings,
    )

    engine = controlled_current55_database
    uid = "5501" + str(uuid4().int % 10**18)
    scope = LedgerScope(environment="demo", account_id=uid, settlement_currency="USDT")
    sessions = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    initial = await QualificationLedgerRepository(
        sessions, clock=lambda: datetime.now(UTC)
    ).initialize_capture_scope(scope)
    assert initial.state.account_revision == initial.state.ledger_revision == 0
    async with sessions() as session, session.begin():
        session.add(DemoAutomationState(id=1, session_date=datetime.now(UTC).date()))
        session.add(
            OkxDemoSyncCheckpoint(
                id=1,
                status="reconciled",
                reconciled_at=datetime.now(UTC),
                details={
                    "account_uid_sha256": sha(uid.encode("ascii")),
                    "credential_session_binding_id": "4" * 64,
                },
            )
        )

    grants = (
        ("SELECT,UPDATE", "demo_account_controls"),
        ("SELECT", "demo_control_journal"),
        ("SELECT,UPDATE", "qualification_account_scopes"),
        ("SELECT", "qualification_reservations"),
        ("SELECT", "demo_collector_current_revision_lineages"),
        ("SELECT,INSERT", "qualification_leverage_configuration_intents_v1"),
        ("SELECT,MAINTAIN", "demo_automation_state"),
        ("SELECT,MAINTAIN", "okx_demo_sync_checkpoints"),
        ("SELECT,MAINTAIN", "okx_demo_order_state"),
        ("SELECT,MAINTAIN", "okx_demo_position_state"),
        ("SELECT,MAINTAIN", "okx_demo_algo_order_state"),
    )
    async with collector(engine) as (writer, writer_oid):
        role = writer.url.username
        try:
            async with engine.begin() as connection:
                for rights, table in grants:
                    await connection.execute(
                        text(f'GRANT {rights} ON public.{table} TO "{role}"')
                    )
                state = acquire(
                    None,
                    scope=ControlScope("demo", uid),
                    owner_sha256="a" * 64,
                    pins=ControlPins("1" * 64, "2" * 64, "3" * 64),
                    now=datetime.now(UTC),
                )
                event = await _store_control_fixture(
                    connection,
                    state,
                    command_id=uuid4().hex * 2,
                    action="acquire",
                    previous_sha256=None,
                )
            forged_source = canonical(
                {
                    "current_lineage_sha256": "f" * 64,
                    "current_lineage_json": "{}",
                    "current_reference": {},
                }
            ).decode("ascii")
            async with writer.connect() as connection:
                async with connection.begin():
                    await settings(connection)
                    assert (
                        await connection.scalar(
                            text(
                                "SELECT oid::bigint FROM pg_roles WHERE rolname=session_user"
                            )
                        )
                        == writer_oid
                    )
                    assert (
                        await connection.scalar(
                            text(
                                "SELECT public.qualification_leverage_configuration_local_flat_v55('demo',:uid)"
                            ),
                            {"uid": uid},
                        )
                        is True
                    )
                    assert (
                        await connection.scalar(
                            text(
                                "SELECT public.qualification_leverage_configuration_blocked_v55('demo',:uid)"
                            ),
                            {"uid": uid},
                        )
                        is False
                    )
                with pytest.raises(
                    DBAPIError, match="native_configuration_current_lineage"
                ) as denied:
                    async with connection.begin():
                        await settings(connection)
                        await connection.execute(
                            text("""INSERT INTO public.qualification_leverage_configuration_intents_v1
                            (environment,account_id,settlement_currency,account_revision,previous_ledger_revision,ledger_revision,
                             intent_id,control_revision,control_event_sha256,core_json,source_json,source_sha256,
                             plan_json,plan_sha256,request_json,request_sha256,original_expires_at,phase,latest_result_sequence)
                            VALUES ('demo',:uid,'USDT',0,0,1,:id,1,:event,'{}',:source,:source_sha,
                             '{}',:empty_sha,'{}',:empty_sha,:expiry,'prepared',0)"""),
                            {
                                "uid": uid,
                                "id": sha(b"{}"),
                                "event": event,
                                "source": forged_source,
                                "source_sha": sha(forged_source.encode("ascii")),
                                "empty_sha": sha(b"{}"),
                                "expiry": state.lease_until - timedelta(seconds=1),
                            },
                        )
                assert denied.value.orig.sqlstate == "P0001"
        finally:
            async with engine.begin() as connection:
                for rights, table in reversed(grants):
                    await connection.execute(
                        text(f'REVOKE {rights} ON public.{table} FROM "{role}"')
                    )
    async with engine.begin() as connection:
        revisions = (
            await connection.execute(
                text(
                    "SELECT account_revision,ledger_revision FROM public.qualification_account_scopes WHERE environment='demo' AND account_id=:uid"
                ),
                {"uid": uid},
            )
        ).one()
        assert tuple(revisions) == (0, 0)
        for name in ("intents", "attempts", "results"):
            assert (
                await connection.scalar(
                    text(
                        f"SELECT count(*) FROM public.qualification_leverage_configuration_{name}_v1"
                    )
                )
                == 0
            )


async def test_actual_current55_full_model_has_no_alembic_drift(
    controlled_current55_database, request
):
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from app.database import (
        models,  # noqa: F401 - register the same complete metadata as migrations/env.py.
    )
    from app.database.base import Base

    def compare(connection):
        # Match the normal alembic check configuration, without a table filter.
        return compare_metadata(
            MigrationContext.configure(connection, opts={"compare_type": True}),
            Base.metadata,
        )

    async with controlled_current55_database.begin() as connection:
        assert (
            await connection.scalar(
                text("SELECT version_num FROM public.alembic_version")
            )
            == "0055"
        )
        changes = await connection.run_sync(compare)
    # Report only catalog names and change kinds. Do not persist SQL or values.
    rows = []
    for item in changes:
        for change in item if type(item) is list else (item,):
            operation = str(change[0])
            if operation.startswith("modify_"):
                rows.append([operation, str(change[2]), str(change[3]), []])
            else:
                subject = change[-1]
                table = (
                    subject
                    if operation in ("add_table", "remove_table")
                    else subject.table
                )
                rows.append(
                    [
                        operation,
                        str(table.name),
                        None if subject.name is None else str(subject.name),
                        [
                            str(column.name)
                            for column in getattr(subject, "columns", ())
                        ],
                    ]
                )
    request.node.user_properties.append(
        ("ctcc_pg55_full_metadata_drift_v1", canonical(rows).decode("ascii"))
    )
    assert changes == []
