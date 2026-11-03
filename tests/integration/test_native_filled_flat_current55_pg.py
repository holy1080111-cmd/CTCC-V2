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


_CORE_GRANT_ACL_SQL = text("""
SELECT kind,object_oid,grantee,grantor,privilege,is_grantable FROM (
 SELECT 'relation' AS kind,c.oid::bigint AS object_oid,a.grantee::bigint AS grantee,
        a.grantor::bigint AS grantor,a.privilege_type AS privilege,a.is_grantable
 FROM pg_catalog.pg_class c
 JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
 CROSS JOIN LATERAL pg_catalog.aclexplode(c.relacl) a WHERE n.nspname='public'
 UNION ALL
 SELECT 'routine',p.oid::bigint,a.grantee::bigint,a.grantor::bigint,
        a.privilege_type,a.is_grantable
 FROM pg_catalog.pg_proc p
 JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
 CROSS JOIN LATERAL pg_catalog.aclexplode(p.proacl) a WHERE n.nspname='public'
 UNION ALL
 SELECT 'schema',n.oid::bigint,a.grantee::bigint,a.grantor::bigint,
        a.privilege_type,a.is_grantable
 FROM pg_catalog.pg_namespace n
 CROSS JOIN LATERAL pg_catalog.aclexplode(n.nspacl) a WHERE n.nspname='public'
) acl WHERE grantee=ANY(CAST(:role_oids AS bigint[]))
ORDER BY kind,object_oid,grantee,grantor,privilege,is_grantable
""")
_CORE_GRANT_LOGIN_SQL = text("""
SELECT (SELECT oid::bigint FROM pg_catalog.pg_roles WHERE rolname=current_user),
       current_user=session_user,pg_catalog.current_setting('role')='none',
       (SELECT oid::bigint FROM pg_catalog.pg_database
        WHERE datname=pg_catalog.current_database()),
       host(pg_catalog.inet_server_addr()),pg_catalog.inet_server_port(),
       pg_catalog.pg_postmaster_start_time(),
       pg_catalog.pg_backend_pid(),pg_catalog.txid_current()::text
""")


async def _core_grant_acl_snapshot(owner, role_oids):
    async with owner.connect() as connection:
        rows = await connection.execute(
            _CORE_GRANT_ACL_SQL, {"role_oids": list(role_oids)}
        )
        return tuple(tuple(row) for row in rows)


async def _core_grant_direct_login(engine, expected_oid):
    async with engine.begin() as connection:
        row = tuple((await connection.execute(_CORE_GRANT_LOGIN_SQL)).one())
    assert row[:3] == (expected_oid, True, True)
    database_oid, address, port, started, pid, xid = row[3:]
    assert database_oid > 0 and address and port > 0 and started.tzinfo is not None
    assert pid > 0 and xid.isascii() and xid.isdecimal()
    return row[3:7], xid


async def _core_grant_writer_catalog(engine, *, read_only=False):
    from tests.integration.test_account_history_original_frame_repository_v1 import (
        settings,
    )

    async with engine.begin() as connection:
        if read_only:
            await connection.execute(text("SET TRANSACTION READ ONLY"))
        await settings(connection)
        actual = await connection.scalar(text(profile.ACTOR_CATALOG_SQL))
    verify = (
        profile.verify_actual_readonly_writer_catalog
        if read_only
        else profile.verify_actual_writer_catalog
    )
    return verify(actual)


async def _core_grant_forbidden_table_read(engine, table):
    # Callers below supply only fixed test relation names.
    with pytest.raises(DBAPIError) as denied:
        async with engine.begin() as connection:
            await connection.execute(text(f"SELECT 1 FROM public.{table} LIMIT 1"))
    origin = denied.value.orig
    code = getattr(origin, "sqlstate", None) or getattr(
        getattr(origin, "__cause__", None), "sqlstate", None
    )
    assert code == "42501"


async def test_actual_current55_core_grants_commit_and_verify_three_restricted_logins(
    controlled_current55_database, request
):
    """Exercise the owner provisioner and real logins in a disposable trust DB."""
    from hashlib import sha256
    from re import fullmatch

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.database.repositories import public_runtime_bundle_custody as bundles
    from scripts.current0055_runtime_grants import (
        current55_core_grant_sql,
        provision_current55_core_grants,
    )
    from tests.integration.test_account_history_original_frame_repository_v1 import (
        collector,
    )

    owner = controlled_current55_database
    if fullmatch(r"ctcc-final-[a-f0-9]{12}-postgres", owner.url.host or "") is None:
        pytest.skip(
            "explicit isolated Docker trust fixture required for this role case"
        )
    assert owner.url.username == "ctcc" and owner.url.password is None
    async with (
        collector(owner) as (writer, writer_oid),
        collector(owner) as (custody, custody_oid),
        collector(owner) as (raw, raw_oid),
    ):
        role_oids = (writer_oid, custody_oid, raw_oid)
        assert len(set(role_oids)) == 3
        names = {
            "writer": writer.url.username,
            "custody": custody.url.username,
            "frame_reader": raw.url.username,
        }
        assert all(
            fullmatch(r"ctcc_raw_frame_[a-f0-9]{32}", name) for name in names.values()
        )
        assert all(engine.url.password is None for engine in (writer, custody, raw))
        grants = current55_core_grant_sql(**names)
        initial = await _core_grant_acl_snapshot(owner, role_oids)
        # The fixture only grants schema USAGE before calling the provisioner.
        assert len(initial) == 3
        assert all(
            row[0] == "schema" and row[4:] == ("USAGE", False) for row in initial
        )

        try:
            # Unexpected rights on an unrelated table must not slip through an
            # otherwise genuine owner installation or partially change its ACL.
            async with owner.begin() as connection:
                await connection.exec_driver_sql(
                    f'GRANT SELECT ON TABLE public.audit_logs TO "{names["frame_reader"]}"'
                )
            try:
                drifted = await _core_grant_acl_snapshot(owner, role_oids)
                assert len(drifted) == len(initial) + 1
                async with owner.connect() as connection:
                    with pytest.raises(
                        ValueError,
                        match="current0055_core_grant_scope_or_catalog_changed",
                    ):
                        await provision_current55_core_grants(connection, **names)
                    assert not connection.in_transaction()
                assert await _core_grant_acl_snapshot(owner, role_oids) == drifted
            finally:
                async with owner.begin() as connection:
                    await connection.exec_driver_sql(
                        f'REVOKE SELECT ON TABLE public.audit_logs FROM "{names["frame_reader"]}"'
                    )
            assert await _core_grant_acl_snapshot(owner, role_oids) == initial

            async with owner.connect() as connection:
                result = await provision_current55_core_grants(connection, **names)
                assert not connection.in_transaction()
            assert result == {
                "schema": "ctcc.current55.audited_core_grants.v1",
                "database_head": "0055",
                "native_definitions_sha256": profile.PG17_NATIVE_BASELINE_SHA256,
                "owner_oid": result["owner_oid"],
                "writer_oid": writer_oid,
                "custody_oid": custody_oid,
                "frame_reader_oid": raw_oid,
                "grants": 28,
                "direct_login_readback_pending": True,
                "complete_runtime_role_matrix": False,
                "execution_authority": False,
            }
            assert result["owner_oid"] not in role_oids
            committed = await _core_grant_acl_snapshot(owner, role_oids)
            assert len(committed) > len(initial)
            # PostgreSQL's public schema may be owned by pg_database_owner.
            # Its existing exact fixture grants remain distinct from table ACLs.
            assert tuple(row for row in committed if row[0] == "schema") == initial
            assert all(
                row[3] == result["owner_oid"] and row[5] is False
                for row in committed
                if row[0] != "schema"
            )
            owner_server, owner_xid = await _core_grant_direct_login(
                owner, result["owner_oid"]
            )
            logins = [
                await _core_grant_direct_login(engine, oid)
                for engine, oid in zip((writer, custody, raw), role_oids, strict=True)
            ]
            assert {server for server, _ in logins} == {owner_server}
            assert len({owner_xid, *(xid for _, xid in logins)}) == 4
            for read_only in (False, True):
                observed = await _core_grant_writer_catalog(writer, read_only=read_only)
                assert observed["role_oid"] == writer_oid
                assert observed["native_baseline_admission"] is True
                assert observed["execution_authority"] is False
                assert (
                    observed["native_definitions_sha256"]
                    == result["native_definitions_sha256"]
                )
            await _verify_actual_current55_raw_catalog_pair(
                raw, writer, raw_oid, writer_oid
            )
            await _core_grant_forbidden_table_read(raw, "demo_account_capture_events")
            await _core_grant_forbidden_table_read(
                writer, "public_runtime_raw_bundles_v1"
            )
            await _core_grant_forbidden_table_read(
                custody, "public_runtime_raw_bundles_v1"
            )

            repository = bundles.RuntimeBundleCustodyRepository(
                async_sessionmaker(custody, autoflush=False, expire_on_commit=False)
            )
            await repository.verify_deployment()
            bundle = b'{"declared_synthetic":"current0055_core_custody_role"}'
            bundle_sha = sha256(bundle).hexdigest()
            proof_sha = sha256(b"synthetic-current55-core-proof").hexdigest()
            receipt = await repository.seal_bundle(
                bundle_raw=bundle,
                expected_sha256=bundle_sha,
                proof_envelope_sha256=proof_sha,
            )
            readback = bundles._consume_runtime_bundle_custody_receipt(
                receipt,
                expected_bundle_sha256=bundle_sha,
                expected_proof_envelope_sha256=proof_sha,
            )
            assert readback.bundle_raw == bundle
            assert readback.append_backend_pid > 0 and readback.readback_backend_pid > 0
            assert readback.append_transaction_id != readback.readback_transaction_id

            async with owner.begin() as connection:
                await connection.exec_driver_sql(
                    "REVOKE INSERT ON TABLE public.qualification_report_projection_receipts "
                    f'FROM "{names["writer"]}"'
                )
                await connection.exec_driver_sql(
                    "GRANT SELECT ON TABLE public.public_runtime_raw_bundles_v1 "
                    f'TO "{names["custody"]}"'
                )
            try:
                with pytest.raises(profile.Current0055CatalogError):
                    await _core_grant_writer_catalog(writer)
                with pytest.raises(
                    bundles.RuntimeBundleCustodyError,
                    match="runtime_bundle_custody_unavailable",
                ):
                    await repository.verify_deployment()
            finally:
                async with owner.begin() as connection:
                    await connection.exec_driver_sql(
                        "GRANT INSERT ON TABLE public.qualification_report_projection_receipts "
                        f'TO "{names["writer"]}"'
                    )
                    await connection.exec_driver_sql(
                        "REVOKE SELECT ON TABLE public.public_runtime_raw_bundles_v1 "
                        f'FROM "{names["custody"]}"'
                    )
            assert await _core_grant_acl_snapshot(owner, role_oids) == committed
            request.node.user_properties.append(
                (
                    "ctcc_pg55_core_grants_observation_v1",
                    canonical(
                        {
                            "database_head": "0055",
                            "native_definitions_sha256": result[
                                "native_definitions_sha256"
                            ],
                            "role_oids": role_oids,
                            "owner_xid": owner_xid,
                            "login_xids": [xid for _, xid in logins],
                            "custody_transaction_ids": [
                                readback.append_transaction_id,
                                readback.readback_transaction_id,
                            ],
                            "grant_statements": 28,
                            "unexpected_acl_denied_without_change": True,
                            "missing_writer_privilege_denied": True,
                            "custody_raw_privilege_denied": True,
                            "raw_wrong_role_denied": True,
                            "synthetic_custody_readback": True,
                            "complete_runtime_role_matrix": False,
                            "execution_authority": False,
                        }
                    ).decode("ascii"),
                )
            )
        finally:
            # Drop only fixture-issued ACLs; the enclosing role fixture owns
            # role deletion and the outer test fixture owns the disposable DB.
            async with owner.begin() as connection:
                for grant in reversed(grants):
                    rights_and_target, role = grant.removeprefix("GRANT ").rsplit(
                        " TO ", 1
                    )
                    await connection.exec_driver_sql(
                        f"REVOKE {rights_and_target} FROM {role}"
                    )


_ACCOUNT_SOURCE_RIGHTS = {
    "demo_account_capture_events": {"SELECT", "INSERT"},
    "demo_account_observation_batches": {"SELECT", "INSERT"},
    "demo_account_observation_facts": {"SELECT", "INSERT"},
    "demo_account_observation_coverage": {"SELECT", "INSERT"},
    "demo_account_observation_findings": {"SELECT", "INSERT"},
    "demo_automation_state": {"SELECT", "MAINTAIN"},
    "okx_demo_sync_checkpoints": {"SELECT", "INSERT", "UPDATE"},
    "okx_demo_order_state": {"SELECT", "MAINTAIN"},
    "okx_demo_position_state": {"SELECT", "MAINTAIN"},
    "okx_demo_algo_order_state": {"SELECT", "MAINTAIN"},
    "audit_logs": {"SELECT", "INSERT"},
}
_ACCOUNT_SOURCE_LOCK_TABLES = (
    "demo_automation_state",
    "okx_demo_sync_checkpoints",
    "okx_demo_order_state",
    "okx_demo_position_state",
    "okx_demo_algo_order_state",
)
_ACCOUNT_SOURCE_RIGHTS_SQL = text("""
SELECT c.relname::text, c.oid::bigint, p.privilege,
       pg_catalog.has_table_privilege(c.oid, p.privilege)
FROM pg_catalog.pg_class c
JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
CROSS JOIN (VALUES ('SELECT'), ('INSERT'), ('UPDATE'), ('DELETE'),
                   ('TRUNCATE'), ('REFERENCES'), ('TRIGGER'), ('MAINTAIN')) p(privilege)
WHERE n.nspname='public' AND c.relname::text=ANY(CAST(:names AS text[]))
""")
_ACCOUNT_SOURCE_LOCK_SQL = text("""
SELECT relation::bigint, mode
FROM pg_catalog.pg_locks
WHERE pid=pg_catalog.pg_backend_pid() AND locktype='relation' AND granted
  AND relation::bigint=ANY(CAST(:oids AS bigint[]))
""")


async def _observe_account_source_rights(engine, role_oid, table_oids, *, writer):
    async with engine.begin() as connection:
        login = (await connection.execute(_CORE_GRANT_LOGIN_SQL)).one()
        assert tuple(login[:3]) == (role_oid, True, True)
        rows = (
            await connection.execute(
                _ACCOUNT_SOURCE_RIGHTS_SQL, {"names": list(_ACCOUNT_SOURCE_RIGHTS)}
            )
        ).all()
    assert len(rows) == 8 * len(_ACCOUNT_SOURCE_RIGHTS)
    assert {(name, oid) for name, oid, _, _ in rows} == set(table_oids.items())
    expected = {
        (name, right)
        for name, rights in _ACCOUNT_SOURCE_RIGHTS.items()
        for right in rights
        if writer
    }
    assert {(name, right) for name, _, right, allowed in rows if allowed} == expected
    return login[-1]


async def _observe_account_source_lock(
    writer, role_oid, table_oids, sql_mode, lock_mode
):
    oids = {table_oids[name] for name in _ACCOUNT_SOURCE_LOCK_TABLES}
    # Fixed mode/name inputs only. NOWAIT makes any unexpected fixture lock fail.
    targets = ", ".join(f"public.{name}" for name in _ACCOUNT_SOURCE_LOCK_TABLES)
    async with writer.begin() as connection:
        login = (await connection.execute(_CORE_GRANT_LOGIN_SQL)).one()
        assert tuple(login[:3]) == (role_oid, True, True)
        await connection.exec_driver_sql(
            f"LOCK TABLE {targets} IN {sql_mode} MODE NOWAIT"
        )
        rows = (
            await connection.execute(_ACCOUNT_SOURCE_LOCK_SQL, {"oids": list(oids)})
        ).all()
        assert len(rows) == len(oids)
        assert {tuple(row) for row in rows} == {(oid, lock_mode) for oid in oids}
        return login[-1]


async def _account_source_statement_denied(writer, statement):
    with pytest.raises(DBAPIError) as denied:
        async with writer.begin() as connection:
            await connection.exec_driver_sql(statement)
    origin = denied.value.orig
    code = getattr(origin, "sqlstate", None) or getattr(
        getattr(origin, "__cause__", None), "sqlstate", None
    )
    assert code == "42501"


async def test_actual_current55_account_source_acl_delta_and_mirror_locks(
    controlled_current55_database, monkeypatch, request
):
    """Exercise fixed ACLs and real locks using fresh disposable direct logins."""
    from json import loads
    from re import fullmatch

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.trade_qualification.account_capture_journal import checked_event
    from scripts import current0055_native_account_source_acl_v1 as delta
    from scripts import current0055_runtime_grants as core
    from tests.integration.test_account_history_original_frame_repository_v1 import (
        collector,
    )
    from tests.integration.test_account_ingestion_journal_repository import initialize

    owner = controlled_current55_database
    if fullmatch(r"ctcc-final-[a-f0-9]{12}-postgres", owner.url.host or "") is None:
        pytest.skip(
            "explicit isolated Docker trust fixture required for this role case"
        )
    assert owner.url.username == "ctcc" and owner.url.password is None
    async with (
        collector(owner) as (writer, writer_oid),
        collector(owner) as (custody, custody_oid),
        collector(owner) as (raw, raw_oid),
    ):
        role_oids = (writer_oid, custody_oid, raw_oid)
        assert len(set(role_oids)) == 3
        assert all(engine.url.password is None for engine in (writer, custody, raw))
        names = {
            "writer": writer.url.username,
            "custody": custody.url.username,
            "frame_reader": raw.url.username,
        }
        assert all(
            fullmatch(r"ctcc_raw_frame_[a-f0-9]{32}", name) for name in names.values()
        )
        core_sql = core.current55_core_grant_sql(**names)
        delta_sql = delta.current55_native_account_source_grant_sql_v1(**names)
        assert len(core_sql) == 28 and len(delta_sql) == 11

        async def provision_delta():
            async with (
                owner.connect() as owner_connection,
                writer.connect() as writer_connection,
            ):
                try:
                    return await delta.provision_current55_native_account_source_acl_v1(
                        owner_connection, writer_connection, **names
                    )
                finally:
                    assert not owner_connection.in_transaction()
                    assert not writer_connection.in_transaction()

        try:
            async with owner.connect() as connection:
                provisioned = await core.provision_current55_core_grants(
                    connection, **names
                )
                assert not connection.in_transaction()
            core_acl = await _core_grant_acl_snapshot(owner, role_oids)

            async with owner.begin() as connection:
                await connection.exec_driver_sql(
                    "GRANT DELETE ON TABLE public.demo_account_capture_events "
                    f'TO "{names["writer"]}"'
                )
            try:
                drifted = await _core_grant_acl_snapshot(owner, role_oids)
                assert len(drifted) == len(core_acl) + 1
                with pytest.raises(
                    ValueError, match="current0055_core_grant_scope_or_catalog_changed"
                ):
                    await provision_delta()
                assert await _core_grant_acl_snapshot(owner, role_oids) == drifted
            finally:
                async with owner.begin() as connection:
                    await connection.exec_driver_sql(
                        "REVOKE DELETE ON TABLE public.demo_account_capture_events "
                        f'FROM "{names["writer"]}"'
                    )
            assert await _core_grant_acl_snapshot(owner, role_oids) == core_acl

            # Run real catalog checks and issue the real eleven GRANTs first.
            # Fail only after the transaction's effective ACL readback saw them.
            actual_access = core._access

            async def fail_after_grants(
                connection, plan, role_oids, *, granted, targets
            ):
                await actual_access(
                    connection, plan, role_oids, granted=granted, targets=targets
                )
                if len(plan) == len(core_sql) + len(delta_sql):
                    raise RuntimeError("synthetic late account source ACL failure")

            with monkeypatch.context() as patch:
                patch.setattr(core, "_access", fail_after_grants)
                with pytest.raises(
                    RuntimeError, match="synthetic late account source ACL failure"
                ):
                    await provision_delta()
            assert await _core_grant_acl_snapshot(owner, role_oids) == core_acl

            result = await provision_delta()
            owner_oid = provisioned["owner_oid"]
            table_oids = result["delta_table_oids"]
            assert set(table_oids) == set(_ACCOUNT_SOURCE_RIGHTS)
            assert len(set(table_oids.values())) == 11
            assert result == {
                "schema": "ctcc.current55.native_account_source_acl_delta.v1",
                "database_head": "0055",
                "native_definitions_sha256": profile.PG17_NATIVE_BASELINE_SHA256,
                "original_source_catalog_sha256": delta.original.PG17_NATIVE_BASELINE_SHA256,
                "owner_oid": owner_oid,
                "writer_oid": writer_oid,
                "custody_oid": custody_oid,
                "frame_reader_oid": raw_oid,
                "owner_xid": result["owner_xid"],
                "writer_core_readback_xid": result["writer_core_readback_xid"],
                "delta_table_oids": table_oids,
                "delta_grants": 11,
                "direct_login_readback_pending": True,
                "complete_runtime_role_matrix": False,
                "execution_authority": False,
            }
            committed = set(await _core_grant_acl_snapshot(owner, role_oids))
            assert committed == set(core_acl) | {
                ("relation", table_oids[name], writer_oid, owner_oid, right, False)
                for name, rights in _ACCOUNT_SOURCE_RIGHTS.items()
                for right in rights
            }

            owner_server, owner_xid = await _core_grant_direct_login(owner, owner_oid)
            login_xids = []
            acl_xids = []
            for engine, oid in zip((writer, custody, raw), role_oids, strict=True):
                server, xid = await _core_grant_direct_login(engine, oid)
                assert server == owner_server
                login_xids.append(xid)
                acl_xids.append(
                    await _observe_account_source_rights(
                        engine, oid, table_oids, writer=engine is writer
                    )
                )
            share_xid = await _observe_account_source_lock(
                writer, writer_oid, table_oids, "SHARE", "ShareLock"
            )
            exclusive_xid = await _observe_account_source_lock(
                writer,
                writer_oid,
                table_oids,
                "SHARE ROW EXCLUSIVE",
                "ShareRowExclusiveLock",
            )
            xids = [
                result["owner_xid"],
                result["writer_core_readback_xid"],
                owner_xid,
                *login_xids,
                *acl_xids,
                share_xid,
                exclusive_xid,
            ]
            assert all(xid.isascii() and xid.isdecimal() for xid in xids)
            assert len(set(xids)) == len(xids)

            for sql in (
                "UPDATE public.demo_account_capture_events SET event_json=event_json WHERE false",
                "DELETE FROM public.demo_account_capture_events WHERE false",
                (
                    "UPDATE public.demo_account_observation_batches "
                    "SET document_json=document_json WHERE false"
                ),
                "DELETE FROM public.demo_account_observation_batches WHERE false",
                "DELETE FROM public.okx_demo_sync_checkpoints WHERE false",
                "SELECT public.public_runtime_bundle_v1_read(NULL::text)",
            ):
                await _account_source_statement_denied(writer, sql)
            await _core_grant_forbidden_table_read(
                writer, "public_runtime_raw_bundles_v1"
            )
            for engine in (custody, raw):
                await _core_grant_forbidden_table_read(
                    engine, "demo_account_capture_events"
                )
                await _core_grant_forbidden_table_read(
                    engine, "demo_account_observation_batches"
                )
            async with writer.begin() as connection:
                raw_function_oids = (
                    await connection.execute(
                        text("""
                    SELECT p.oid::bigint, pg_catalog.has_function_privilege(p.oid, 'EXECUTE')
                    FROM pg_catalog.pg_proc p
                    JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
                    WHERE n.nspname='public'
                      AND p.proname IN ('account_history_original_frame_v1_read',
                                        'account_history_original_frame_current55_v2_read')
                    """)
                    )
                ).all()
            assert len(raw_function_oids) == 2
            assert all(allowed is False for _, allowed in raw_function_oids)

            factory = async_sessionmaker(
                writer, autoflush=False, expire_on_commit=False
            )
            scope, _, journal, start = await initialize((writer, factory))
            appended = await journal._append(scope, start)
            capture_id = checked_event(start)["capture_id"]
            chain = await journal.read_chain(scope, capture_id)
            assert len(chain) == 1 and chain[0].event == appended.event == start
            assert appended.db_recorded_at <= appended.readback_at
            receipt = loads(appended.receipt_json)
            assert (
                receipt["account_complete"] is receipt["execution_authority"] is False
            )
            assert set(await _core_grant_acl_snapshot(owner, role_oids)) == committed

            request.node.user_properties.append(
                (
                    "ctcc_pg55_native_account_source_acl_v1",
                    canonical(
                        {
                            "database_head": "0055",
                            "native_definitions_sha256": result[
                                "native_definitions_sha256"
                            ],
                            "original_source_catalog_sha256": result[
                                "original_source_catalog_sha256"
                            ],
                            "role_oids": role_oids,
                            "transaction_ids": xids,
                            "delta_table_oids": table_oids,
                            "grant_statements": 11,
                            "share_and_share_row_exclusive_locks_observed": True,
                            "unexpected_acl_denied_without_change": True,
                            "late_failure_rolled_back_all_grants": True,
                            "synthetic_b1_append_and_readback": True,
                            "complete_runtime_role_matrix": False,
                            "execution_authority": False,
                        }
                    ).decode("ascii"),
                )
            )
        finally:
            async with owner.begin() as connection:
                for grant in reversed(core_sql + delta_sql):
                    rights_and_target, role = grant.removeprefix("GRANT ").rsplit(
                        " TO ", 1
                    )
                    await connection.exec_driver_sql(
                        f"REVOKE {rights_and_target} FROM {role}"
                    )


async def test_actual_current55_restricted_native_lifecycle_recovery(
    controlled_current55_database, monkeypatch, request
):
    """Use the real restricted repository and recovery on isolated PostgreSQL."""
    from datetime import timedelta
    from decimal import Decimal
    from re import fullmatch
    from uuid import uuid4

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.config.settings import Settings
    from app.core import native_only_lifespan as lifespan
    from app.database.models.demo_automation import (
        DemoAutomationRun,
        DemoAutomationState,
    )
    from app.database.models.observability import DemoSoakSession
    from app.database.repositories.demo_automation import DemoAutomationRepository
    from app.demo_automation.service import SafeDemoAutomation
    from app.domain.demo_automation import (
        DemoAutomationActiveTrade,
        DemoAutomationRunResult,
    )
    from scripts import current0055_native_account_source_acl_v1 as account
    from scripts import current0055_native_lifecycle_acl_v1 as lifecycle
    from scripts import current0055_runtime_grants as core
    from tests.integration.test_account_history_original_frame_repository_v1 import (
        collector,
    )

    owner = controlled_current55_database
    if fullmatch(r"ctcc-final-[a-f0-9]{12}-postgres", owner.url.host or "") is None:
        pytest.skip(
            "explicit isolated Docker trust fixture required for this role case"
        )
    assert owner.url.username == "ctcc" and owner.url.password is None
    async with (
        collector(owner) as (writer, writer_oid),
        collector(owner) as (custody, custody_oid),
        collector(owner) as (raw, raw_oid),
    ):
        role_oids = (writer_oid, custody_oid, raw_oid)
        assert len(set(role_oids)) == 3
        assert all(engine.url.password is None for engine in (writer, custody, raw))
        names = {
            "writer": writer.url.username,
            "custody": custody.url.username,
            "frame_reader": raw.url.username,
        }
        assert all(
            fullmatch(r"ctcc_raw_frame_[a-f0-9]{32}", name) for name in names.values()
        )
        core_sql = core.current55_core_grant_sql(**names)
        account_sql = account.current55_native_account_source_grant_sql_v1(**names)
        lifecycle_sql = lifecycle.current55_native_lifecycle_grant_sql_v1(**names)
        assert (len(core_sql), len(account_sql), len(lifecycle_sql)) == (28, 11, 3)

        async def provision_lifecycle():
            async with (
                owner.connect() as owner_connection,
                writer.connect() as writer_connection,
            ):
                try:
                    return await lifecycle.provision_current55_native_lifecycle_acl_v1(
                        owner_connection, writer_connection, **names
                    )
                finally:
                    assert not owner_connection.in_transaction()
                    assert not writer_connection.in_transaction()

        try:
            async with owner.connect() as connection:
                owner_receipt = await core.provision_current55_core_grants(
                    connection, **names
                )
            async with (
                owner.connect() as owner_connection,
                writer.connect() as writer_connection,
            ):
                await account.provision_current55_native_account_source_acl_v1(
                    owner_connection, writer_connection, **names
                )
            before_acl = set(await _core_grant_acl_snapshot(owner, role_oids))
            async with owner.begin() as connection:
                await connection.exec_driver_sql(
                    "GRANT INSERT ON TABLE public.demo_automation_runs "
                    f'TO "{names["writer"]}"'
                )
            try:
                drifted = set(await _core_grant_acl_snapshot(owner, role_oids))
                assert len(drifted - before_acl) == 1
                with pytest.raises(
                    ValueError, match="current0055_core_grant_scope_or_catalog_changed"
                ):
                    await provision_lifecycle()
                assert set(await _core_grant_acl_snapshot(owner, role_oids)) == drifted
            finally:
                async with owner.begin() as connection:
                    await connection.exec_driver_sql(
                        "REVOKE INSERT ON TABLE public.demo_automation_runs "
                        f'FROM "{names["writer"]}"'
                    )
            assert set(await _core_grant_acl_snapshot(owner, role_oids)) == before_acl

            async with writer.begin() as connection:
                await connection.exec_driver_sql(
                    "LOCK TABLE public.demo_automation_state IN SHARE MODE NOWAIT"
                )
                with pytest.raises(DBAPIError) as lock_denied:
                    await provision_lifecycle()
                assert getattr(lock_denied.value.orig, "sqlstate", None) == "55P03"
                assert (
                    set(await _core_grant_acl_snapshot(owner, role_oids)) == before_acl
                )

            receipt = await provision_lifecycle()
            owner_oid = owner_receipt["owner_oid"]
            assert receipt["owner_oid"] == owner_oid
            assert receipt["role_oids"] == dict(zip(names, role_oids, strict=True))
            assert receipt["database_head"] == "0055"
            assert (
                receipt["native_definitions_sha256"]
                == profile.PG17_NATIVE_BASELINE_SHA256
            )
            assert (
                receipt["delta_grants"] == 3 and receipt["execution_authority"] is False
            )
            assert receipt["complete_runtime_role_matrix"] is False
            expected_rights = {
                "demo_automation_state": {"SELECT", "MAINTAIN", "UPDATE"},
                "demo_automation_runs": {"SELECT"},
                "demo_soak_sessions": {"SELECT"},
            }
            table_oids = receipt["lifecycle_table_oids"]
            assert set(table_oids) == set(expected_rights)
            assert len(set(table_oids.values())) == 3
            committed_acl = set(await _core_grant_acl_snapshot(owner, role_oids))
            assert committed_acl == before_acl | {
                ("relation", table_oids[table], writer_oid, owner_oid, right, False)
                for table, right in (
                    ("demo_automation_state", "UPDATE"),
                    ("demo_automation_runs", "SELECT"),
                    ("demo_soak_sessions", "SELECT"),
                )
            }
            async with writer.begin() as connection:
                direct = tuple((await connection.execute(_CORE_GRANT_LOGIN_SQL)).one())
                assert direct[:3] == (writer_oid, True, True)
                rights = (
                    await connection.execute(
                        _ACCOUNT_SOURCE_RIGHTS_SQL, {"names": list(expected_rights)}
                    )
                ).all()
            assert len(rights) == 24
            assert {(table, oid) for table, oid, _, _ in rights} == set(
                table_oids.items()
            )
            assert {
                (table, right) for table, _, right, allowed in rights if allowed
            } == {
                (table, right)
                for table, values in expected_rights.items()
                for right in values
            }
            xids = (
                receipt["owner_xid"],
                receipt["writer_core_readback_xid"],
                direct[-1],
            )
            assert len(set(xids)) == 3 and all(
                xid.isascii() and xid.isdecimal() for xid in xids
            )

            for table in expected_rights:
                # A zero-row statement still checks the actual write privilege.
                await _account_source_statement_denied(
                    writer,
                    f"INSERT INTO public.{table} (id) SELECT id FROM public.{table} WHERE false",
                )
                await _account_source_statement_denied(
                    writer, f"DELETE FROM public.{table} WHERE false"
                )

            def factory(engine):
                return async_sessionmaker(
                    engine, autoflush=False, expire_on_commit=False
                )

            writer_factory = factory(writer)
            owner_factory = factory(owner)
            repository = DemoAutomationRepository(writer_factory)
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
                **dict.fromkeys(lifespan._MUST_BE_FALSE, False),
            )
            # Unrelated collaborators have no operations. Any attempted exchange,
            # market, strategy or background action fails instead of running.
            absent = object()
            automation = SafeDemoAutomation(
                settings=settings,
                repository=repository,
                strategy_service=absent,
                risk_service=absent,
                demo_service=absent,
                public_client=absent,
                strategy_control_repository=absent,
                market_hub=absent,
                market_client=absent,
            )
            assert await repository.load_state() is None
            with pytest.raises(
                lifespan.NativeOnlyLifespanError,
                match="native_only_control_state_missing",
            ):
                await lifespan.start_native_only(settings, writer_factory, automation)
            assert await repository.load_state() is None
            assert (await automation.status()).recovered is False

            instant = datetime.now(UTC)
            run = DemoAutomationRunResult(
                trigger="manual",
                execute=False,
                started_at=instant,
                completed_at=instant,
                results=[],
            )
            exposure = DemoAutomationActiveTrade(
                instrument_id="BTC-USDT-SWAP",
                settlement_currency="USDT",
                direction="long",
                client_order_id="lifecycleFixture1",
                exchange_order_id="10001",
                protection_client_order_id="lifecycleFixtureP1",
                contracts=Decimal(2),
                leverage=3,
                margin_mode="isolated",
                estimated_margin=Decimal(10),
                estimated_stop_loss_amount=Decimal(1),
                start_equity=Decimal(500),
                started_at=instant,
            )
            active_trades = {exposure.instrument_id: exposure.model_dump(mode="json")}
            soak_id = uuid4()
            async with owner_factory.begin() as session:
                session.add_all(
                    [
                        DemoAutomationState(
                            id=1,
                            control_revision=0,
                            session_date=instant.date(),
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
            revision_zero = await repository.load_state()
            with pytest.raises(
                lifespan.NativeOnlyLifespanError,
                match="native_only_control_state_invalid",
            ):
                await lifespan.start_native_only(settings, writer_factory, automation)
            assert await repository.load_state() == revision_zero

            async with owner_factory.begin() as session:
                await session.execute(
                    text("""
                    UPDATE public.demo_automation_state SET control_revision=1,
                        armed=true, restart_latch_required=true WHERE id=1
                """)
                )
                session.add(
                    DemoSoakSession(
                        id=soak_id,
                        state="running",
                        execute=False,
                        symbols=[exposure.instrument_id],
                        interval_seconds=60,
                        duration_minutes=1,
                        max_runs=1,
                        started_at=instant,
                        planned_end_at=instant + timedelta(minutes=1),
                    )
                )
            before = await repository.load_state()
            assert before["_control_revision"] == 1 and before["armed"] is True
            assert before["_restart_latch_required"] is True
            with pytest.raises(
                lifespan.NativeOnlyLifespanError,
                match="native_only_running_soak_unresolved",
            ):
                await lifespan.start_native_only(settings, writer_factory, automation)
            assert await repository.load_state() == before
            assert (await automation.status()).recovered is False
            async with owner_factory.begin() as session:
                row = await session.get(DemoSoakSession, soak_id)
                assert row.state == "running"
                row.state = "stopped"

            from app import main as app_main

            bindings = {
                "settings": settings,
                "AsyncSessionFactory": writer_factory,
                "safe_demo_automation": automation,
                "engine": writer,
            }
            originals = {name: getattr(app_main, name) for name in bindings}
            initial_pool = writer.sync_engine.pool
            with monkeypatch.context() as patch:
                for name, value in bindings.items():
                    patch.setattr(app_main, name, value)
                async with app_main.lifespan(app_main.app):
                    after = await repository.load_state()
                    assert after["_control_revision"] == 2
                    assert after["armed"] is False
                    assert (
                        after["emergency_stop"]
                        is after["locked"]
                        is after["_restart_latch_required"]
                        is True
                    )
                    assert (
                        "restart_requires_explicit_clear_stop" in after["lock_reasons"]
                    )
                    assert after["active_trades"] == active_trades
                    preserved = (
                        "active_trades",
                        "active_instrument_id",
                        "active_client_order_id",
                        "active_start_equity",
                        "active_started_at",
                        "baseline_equity",
                        "daily_pnl",
                        "trades_today",
                        "consecutive_losses",
                    )
                    assert all(after[key] == before[key] for key in preserved)
                    assert await automation.history() == [run]
                    status = await automation.status()
                    assert status.recovered and status.emergency_stop and status.locked
                    assert (
                        not status.armed
                        and not status.running
                        and status.active_trades == [exposure]
                    )
                    assert writer.sync_engine.pool is initial_pool
            assert writer.sync_engine.pool is not initial_pool
            assert all(
                getattr(app_main, name) is value for name, value in originals.items()
            )
            assert await automation.status() == status
            assert await repository.load_state() == after

            # The real legacy DB guard still forbids clearing the sticky latch.
            with pytest.raises(DBAPIError) as blocked:
                async with writer.begin() as connection:
                    await connection.exec_driver_sql("""
                        UPDATE public.demo_automation_state SET restart_latch_required=false,
                            control_revision=control_revision+1 WHERE id=1
                    """)
            assert getattr(blocked.value.orig, "sqlstate", None) == "23514"
            assert await repository.load_state() == after
            assert (
                set(await _core_grant_acl_snapshot(owner, role_oids)) == committed_acl
            )
            request.node.user_properties.append(
                (
                    "ctcc_pg55_native_lifecycle_acl_v1",
                    canonical(
                        {
                            "database_head": "0055",
                            "native_definitions_sha256": receipt[
                                "native_definitions_sha256"
                            ],
                            "control_guard_body_sha256": receipt[
                                "legacy_control_guard_body_sha256"
                            ],
                            "role_oids": role_oids,
                            "transaction_ids": xids,
                            "lifecycle_table_oids": table_oids,
                            "grant_statements": 3,
                            "control_revision_before": 1,
                            "control_revision_after": 2,
                            "missing_and_revision_zero_denied": True,
                            "running_soak_denied": True,
                            "conflicting_lock_denied_without_grants": True,
                            "armed": False,
                            "emergency_stop": True,
                            "sticky_latch_preserved": True,
                            "tracked_exposure_preserved": True,
                            "app_lifespan_entered_and_exited": True,
                            "restricted_engine_disposed": True,
                            "insert_and_delete_denied": True,
                            "execution_authority": False,
                            "complete_runtime_role_matrix": False,
                        }
                    ).decode("ascii"),
                )
            )
        finally:
            async with owner.begin() as connection:
                for grant in reversed(core_sql + account_sql + lifecycle_sql):
                    rights_and_target, role = grant.removeprefix("GRANT ").rsplit(
                        " TO ", 1
                    )
                    await connection.exec_driver_sql(
                        f"REVOKE {rights_and_target} FROM {role}"
                    )


async def test_actual_current55_restricted_b3_append_and_independent_readback(
    monkeypatch, controlled_current55_database, request
):
    """Finalize synthetic B1, then write and replay B3 as the actual writer."""
    from json import loads
    from re import fullmatch

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.database.repositories.account_observation_index import (
        AccountObservationIndexRepository,
    )
    from app.trade_qualification.account_capture_journal import digest
    from scripts import current0055_native_account_source_acl_v1 as account_acl
    from scripts import current0055_runtime_grants as core_acl
    from tests.integration.test_account_history_original_frame_repository_v1 import (
        collector,
    )
    from tests.integration.test_account_history_query_verifier_repository import now
    from tests.integration.test_account_observation_index_repository import prepared

    owner = controlled_current55_database
    if fullmatch(r"ctcc-final-[a-f0-9]{12}-postgres", owner.url.host or "") is None:
        pytest.skip("explicit isolated Docker fixture required; no acceptance credit")
    assert owner.url.username == "ctcc" and owner.url.password is None
    async with (
        collector(owner) as (writer, writer_oid),
        collector(owner) as (custody, custody_oid),
        collector(owner) as (frame_reader, frame_oid),
    ):
        names = {
            "writer": writer.url.username,
            "custody": custody.url.username,
            "frame_reader": frame_reader.url.username,
        }
        assert len({writer_oid, custody_oid, frame_oid}) == 3
        assert all(
            engine.url.password is None for engine in (writer, custody, frame_reader)
        )
        core_sql = core_acl.current55_core_grant_sql(**names)
        account_sql = account_acl.current55_native_account_source_grant_sql_v1(**names)
        assert len(core_sql) == 28 and len(account_sql) == 11
        try:
            async with owner.connect() as owner_connection:
                await core_acl.provision_current55_core_grants(
                    owner_connection, **names
                )
            async with (
                owner.connect() as owner_connection,
                writer.connect() as writer_connection,
            ):
                acl = (
                    await account_acl.provision_current55_native_account_source_acl_v1(
                        owner_connection, writer_connection, **names
                    )
                )
            assert acl["delta_grants"] == 11
            assert acl["execution_authority"] is False

            factory = async_sessionmaker(
                writer, autoflush=False, expire_on_commit=False
            )
            # prepared() makes a full finalized B1 source via local MockTransport.
            # It uses only synthetic test credentials and makes no provider call.
            repo, scope, reference, window = await prepared(
                monkeypatch, (owner, factory)
            )
            original = await repo.journal.read_chain(scope, reference.capture_id)
            assert original and original[-1].event.event_json
            assert all(item.db_recorded_at <= item.readback_at for item in original)

            appended = await repo.append(scope, reference, window, expected_revision=0)
            document = loads(appended.document_json)
            receipt = loads(appended.replay.receipt_json)
            assert appended.sequence == 1
            assert appended.event_sha256 == digest(appended.document_json)
            assert document["receipt_sha256"] == appended.replay.receipt_sha256
            assert document["source_membership_count"] > 0
            assert document["coverage_membership_count"] > 0
            assert all(
                value["account_complete"] is False
                and value["execution_authority"] is False
                and value["admission"] == "DENY"
                for value in (document, receipt)
            )

            # Fresh repository reads the committed B1/B3 and immutable bytes.
            restarted = AccountObservationIndexRepository(factory, clock=now)
            assert await restarted.read(scope) == (appended,)
            original_after = await restarted.journal.read_chain(
                scope, reference.capture_id
            )
            assert [(item.event, item.db_recorded_at) for item in original_after] == [
                (item.event, item.db_recorded_at) for item in original
            ]

            b3_tables = (
                "demo_account_observation_batches",
                "demo_account_observation_facts",
                "demo_account_observation_coverage",
                "demo_account_observation_findings",
            )
            async with writer.begin() as connection:
                login = (
                    await connection.execute(
                        text("SELECT current_user,session_user,current_setting('role')")
                    )
                ).one()
                assert tuple(login) == (names["writer"], names["writer"], "none")
                rights = (
                    await connection.execute(
                        text("""
                            SELECT c.relname, p.right,
                                   pg_catalog.has_table_privilege(c.oid, p.right)
                            FROM pg_catalog.pg_class c
                            JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
                            CROSS JOIN (VALUES ('SELECT'), ('INSERT'), ('UPDATE'),
                                               ('DELETE'), ('TRUNCATE'), ('REFERENCES'),
                                               ('TRIGGER'), ('MAINTAIN')) p(right)
                            WHERE n.nspname='public'
                              AND c.relname::text=ANY(CAST(:names AS text[]))
                        """),
                        {"names": list(b3_tables)},
                    )
                ).all()
            assert len(rights) == 8 * len(b3_tables)
            assert {
                (table, right) for table, right, permitted in rights if permitted
            } == {
                (table, right) for table in b3_tables for right in ("SELECT", "INSERT")
            }
            for table in b3_tables:
                column = "sequence" if table.endswith("_batches") else "ordinal"
                await _account_source_statement_denied(
                    writer, f"UPDATE public.{table} SET {column}={column} WHERE false"
                )
                await _account_source_statement_denied(
                    writer, f"DELETE FROM public.{table} WHERE false"
                )
            assert await restarted.read(scope) == (appended,)

            request.node.user_properties.append(
                (
                    "ctcc_pg55_native_b3_restricted_writer_v1",
                    canonical(
                        {
                            "database_head": "0055",
                            "core_grants": 28,
                            "account_source_grants": 11,
                            "synthetic_b1_fully_finalized": True,
                            "b3_commit_and_independent_readback": True,
                            "b3_extra_privileges_denied": True,
                            "account_complete": False,
                            "execution_authority": False,
                            "admission": "DENY",
                        }
                    ).decode("ascii"),
                )
            )
        finally:
            async with owner.begin() as connection:
                for grant in reversed(core_sql + account_sql):
                    rights_and_target, role = grant.removeprefix("GRANT ").rsplit(
                        " TO ", 1
                    )
                    await connection.exec_driver_sql(
                        f"REVOKE {rights_and_target} FROM {role}"
                    )
