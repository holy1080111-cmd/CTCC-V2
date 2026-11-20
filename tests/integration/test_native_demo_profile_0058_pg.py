"""Actual isolated TLS DB0058 Demo graph; no venue, credential or order.

This case is authored for the explicit hosted lab. Collection is not execution.
It creates a UUID-named disposable database and three roles, grants the unchanged
44-statement Demo plan, and proves the real successor graph and held Control.
"""

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.native_role_witness_0056_v1 import (
    observe_restricted_native_roles_0056_witness_v1,
)
from app.database.repositories import native_issuer_transaction as issuer
from app.database.repositories import qualification_native_checkpoint_v2 as checkpoint
from app.database.repositories.demo_control import DemoControlRepository
from app.database.repositories.qualification_ledger import QualificationLedgerRepository
from app.demo_automation.native_server_selectors_v2 import (
    ConfiguredNativeDemoFactoriesV2,
)
from app.domain import native_database_profile_v1 as profiles
from app.domain import native_protective_hold_schema_v58 as hold
from app.trade_qualification.demo_control import ControlScope
from app.trade_qualification.reservations import LedgerScope
from tests.integration.test_gate3_fitted_candidate_custody_pg import (
    _isolated_tls_database_config,
)
from tests.integration.test_native_protective_hold_schema_v58_pg import _upgrade

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
async def _actual58_demo_lab():
    url, tls = _isolated_tls_database_config()
    nonce = uuid4().hex[:12]
    database = "ctcc_demo58_" + nonce
    names = tuple("ctcc_demo58_" + label + "_" + nonce for label in ("w", "c", "f"))
    admin = create_async_engine(
        url, poolclass=NullPool, connect_args={"ssl": tls}, hide_parameters=True
    )
    owner, engines, created_roles, created = None, [], [], False
    try:
        async with admin.connect() as connection:
            await connection.execution_options(isolation_level="AUTOCOMMIT")
            await connection.exec_driver_sql(f'CREATE DATABASE "{database}"')
            created = True
        owner = create_async_engine(
            url.set(database=database),
            poolclass=NullPool,
            connect_args={"ssl": tls},
            hide_parameters=True,
        )
        async with owner.begin() as connection:
            await connection.run_sync(_upgrade)
            for name in names:
                await connection.exec_driver_sql(
                    f'CREATE ROLE "{name}" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS'
                )
                created_roles.append(name)
                await connection.exec_driver_sql(
                    f'GRANT CONNECT ON DATABASE "{database}" TO "{name}"'
                )
            for grant in profiles.grant_plan(profiles.DB0058, names):
                await connection.exec_driver_sql(grant.sql())
        for name in names:
            engines.append(
                create_async_engine(
                    url.set(database=database, username=name),
                    poolclass=NullPool,
                    connect_args={"ssl": tls},
                    hide_parameters=True,
                )
            )
        factories = tuple(
            async_sessionmaker(engine, autoflush=False, expire_on_commit=False)
            for engine in engines
        )
        selected = ConfiguredNativeDemoFactoriesV2(*factories, *engines[1:])
        yield owner, selected, names
    finally:
        for engine in engines:
            await engine.dispose()
        if owner is not None:
            await owner.dispose()
        try:
            async with admin.connect() as connection:
                await connection.execution_options(isolation_level="AUTOCOMMIT")
                if created:
                    await connection.exec_driver_sql(
                        f'DROP DATABASE "{database}" WITH (FORCE)'
                    )
                for name in created_roles:
                    await connection.exec_driver_sql(f'DROP ROLE IF EXISTS "{name}"')
        finally:
            await admin.dispose()


async def test_actual0058_restricted_demo_profile_retains_full_graph_and_protective_guards(
    _actual58_demo_lab, request
):
    owner, selected, _ = _actual58_demo_lab
    roles = await observe_restricted_native_roles_0056_witness_v1(
        selected, source=ROOT, profile=profiles.DB0058
    )
    assert roles.observation.database_head == "0058"
    assert roles.execution_authority is roles.startup_admission is False
    assert checkpoint._profile_for_original_roles_v1(roles) is profiles.DB0058
    scope = LedgerScope(
        environment="demo", account_id="730058", settlement_currency="USDT"
    )
    control_scope = ControlScope(scope.environment, scope.account_id)
    owner_factory = async_sessionmaker(owner, autoflush=False, expire_on_commit=False)
    stopped = await DemoControlRepository(
        owner_factory, clock=lambda: datetime.now(UTC)
    ).latch_stop(control_scope, command_id="a" * 64)
    await QualificationLedgerRepository(
        owner_factory, clock=lambda: datetime.now(UTC)
    ).initialize_capture_scope(scope)
    async with (
        selected.writer_factory() as session,
        session.begin(),
        checkpoint.held_qualification_checkpoint_0056_v1(
            selected, session, control_scope, roles=roles
        ) as held,
    ):
        assert held.profile is profiles.DB0058
        assert (
            held.control.database_head
            == held.control.writer_catalog.database_head
            == "0058"
        )
        assert held.control.control.state == stopped.state
        local = QualificationLedgerRepository(
            selected.writer_factory, clock=lambda: datetime.now(UTC)
        )
        await checkpoint.require_held_event_journal_write_0056_v1(
            local, session, scope, held
        )
        actual_issuer, metadata = await issuer.read_actual_native_issuer_transaction(
            session
        )
        assert actual_issuer.head == "0058" and actual_issuer.role_oid == metadata[3]
        assert (
            actual_issuer.native_definitions_sha256
            == roles.observation.inherited_native_definitions_sha256
        )
        assert (
            await session.scalar(text("SELECT version_num FROM public.alembic_version"))
        ) == "0058"
        assert (
            await session.scalar(
                text(
                    "SELECT pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(prosrc,'UTF8')),'hex') FROM pg_catalog.pg_proc WHERE oid='public.qualification_reservation_update()'::regprocedure"
                )
            )
        ) == hold.CURRENT_BODY_SHA256
        await held.recheck_catalog()
    # Dedicated producer re-observes the actual source-bound PG17/TLS graph;
    # optional emission does not alter this test's assertion or admission.
    # Execute the actual journal route helper and restricted writer verifier,
    # not only its SELECT or a mocked context. This lab has a stopped synthetic
    # scope and no reservations; local empty DATA is not exchange flatness.
    from app.demo_automation import native_journal_readout_v1 as journal
    from scripts.current0058_profile_measurement_v1 import record_profile_if_requested

    factory, engine, names, route = journal._local_writer_pins_v1(selected)
    async with (
        factory() as session,
        session.begin(),
        journal.database._verified_terminal_writer(
            session, engine, roles, names, route, profile=profiles.DB0058
        ),
    ):
        raw = dict(
            (
                await session.execute(
                    journal._QUERY, {"uid": scope.account_id, "limit": 20}
                )
            )
            .mappings()
            .one()
        )
        view = journal._readout(raw, uid=scope.account_id, head="0058", limit=20)
        assert view.total_native == view.unresolved_native == view.unresolved_other == 0
        assert (
            view.entries == ()
            and view.account_complete is view.execution_authority is False
        )

    await record_profile_if_requested(owner, selected, case=request.node.name)


async def test_actual0058_native_g12_custody_six_sessions_keep_real_head(
    _actual58_demo_lab, monkeypatch, request
):
    """Actual SQL58 custody; declared synthetic owner DATA, no public G12 claim."""
    from scripts.current0058_profile_measurement_v1 import record_custody_if_requested
    from tests.native_g12_custody_component_v1 import (
        exercise_native_custody_component_v1,
    )

    owner, selected, _ = _actual58_demo_lab
    result = await exercise_native_custody_component_v1(
        owner, selected, profile=profiles.DB0058, monkeypatch=monkeypatch
    )
    assert result["database_head"] == "0058"
    assert len(result["stages"]) == 6
    assert {row["database_head"] for row in result["stages"]} == {"0058"}
    await record_custody_if_requested(result, case=request.node.name)
