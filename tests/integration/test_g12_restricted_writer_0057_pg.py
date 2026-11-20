"""Actual isolated TLS PostgreSQL successor tests; synthetic custody only.

Requires the explicit existing disposable TLS lab. No venue API is involved.
No running production database or installed credential is selected here.
"""

from __future__ import annotations

from dataclasses import fields
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.domain import g12_custody_catalog_0057_v1 as catalog
from app.domain import native_database_profile_v1 as profiles
from app.trade_qualification.g12_invocation_custody import (
    G12InvocationCustodyRepository,
)
from scripts import current0057_native_g12_acl_v1 as profile
from scripts import current_contract_source_scopes_v1 as scopes
from tests.integration.test_g12_invocation_custody_repository import (
    _claim,
    _publication,
)
from tests.integration.test_gate3_fitted_candidate_custody_pg import (
    _isolated_tls_database_config,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
ROOT = Path(__file__).resolve().parents[2]


def _migrate(connection, target):
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("path_separator", "os")
    config.set_main_option("script_location", str(ROOT / "migrations"))
    script = ScriptDirectory.from_config(config)
    scope = scopes.database0057_source_scope(ROOT)
    assert scope.actual_source_head in {"0057", "0058"}
    assert scope.database_target == "0057"
    assert script.get_heads() == [scope.actual_source_head]
    current = MigrationContext.configure(connection).get_current_heads()
    downgrade = current == ("0057",) and target == "0056"
    migration = MigrationContext.configure(
        connection,
        opts={
            "fn": lambda heads, _: (
                script._downgrade_revs(target, heads)
                if downgrade
                else script._upgrade_revs(target, heads)
            )
        },
    )
    with Operations.context(migration), migration.begin_transaction():
        migration.run_migrations()
    assert MigrationContext.configure(connection).get_current_heads() == (target,)


@pytest.fixture
async def _isolated_0057_g12_lab(request):
    url, tls = _isolated_tls_database_config()
    nonce = uuid4().hex[:12]
    database = "ctcc_g12_57_" + nonce
    names = tuple("ctcc_g12_" + label + "_" + nonce for label in ("w", "c", "f"))
    admin = create_async_engine(
        url, poolclass=NullPool, connect_args={"ssl": tls}, hide_parameters=True
    )
    owner = None
    writers = []
    created_roles = []
    created_db = False
    try:
        async with admin.connect() as connection:
            await connection.execution_options(isolation_level="AUTOCOMMIT")
            await connection.exec_driver_sql(f'CREATE DATABASE "{database}"')
            created_db = True
        owner = create_async_engine(
            url.set(database=database),
            poolclass=NullPool,
            connect_args={"ssl": tls},
            hide_parameters=True,
        )
        async with owner.begin() as connection:
            await connection.run_sync(lambda sync: _migrate(sync, "0056"))
        async with owner.begin() as connection:
            await connection.run_sync(lambda sync: _migrate(sync, "0057"))
        async with owner.begin() as connection:
            await connection.run_sync(lambda sync: _migrate(sync, "0056"))
        async with owner.begin() as connection:
            await connection.run_sync(lambda sync: _migrate(sync, "0057"))
        async with owner.begin() as connection:
            for name in names:
                await connection.exec_driver_sql(
                    f'CREATE ROLE "{name}" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS'
                )
                created_roles.append(name)
                await connection.exec_driver_sql(
                    f'GRANT CONNECT ON DATABASE "{database}" TO "{name}"'
                )
            previous, delta = profile._plans(*names)
            for grant in previous + delta:
                await connection.exec_driver_sql(grant.sql())
        for name in names:
            writers.append(
                create_async_engine(
                    url.set(database=database, username=name),
                    poolclass=NullPool,
                    connect_args={"ssl": tls},
                    hide_parameters=True,
                )
            )
        from app.demo_automation.native_server_selectors_v2 import (
            ConfiguredNativeDemoFactoriesV2,
        )
        from scripts.current0057_g12_measurement_v1 import record_profile_if_requested

        measured_factories = tuple(
            async_sessionmaker(engine, autoflush=False, expire_on_commit=False)
            for engine in writers
        )
        measured = ConfiguredNativeDemoFactoriesV2(
            *measured_factories, writers[1], writers[2]
        )
        # This fixture is also used by unrelated actual57 operational cases.
        # Their test names must not impersonate one of the four G12 receipts.
        if request.node.path.resolve() == Path(__file__).resolve():
            await record_profile_if_requested(owner, measured, case=request.node.name)
        yield owner, writers, names
    finally:
        for writer in writers:
            await writer.dispose()
        if owner is not None:
            await owner.dispose()
        try:
            async with admin.connect() as connection:
                await connection.execution_options(isolation_level="AUTOCOMMIT")
                if created_db:
                    await connection.exec_driver_sql(
                        f'DROP DATABASE "{database}" WITH (FORCE)'
                    )
                for name in created_roles:
                    await connection.exec_driver_sql(f'DROP ROLE IF EXISTS "{name}"')
        finally:
            await admin.dispose()


async def _observe(engine, names, *, read_only=True):
    async with engine.connect() as connection, connection.begin():
        for sql in (
            "SET TRANSACTION READ ONLY" if read_only else "SET TRANSACTION READ WRITE",
            "SET LOCAL search_path TO pg_catalog, public, pg_temp",
            "SET LOCAL TIME ZONE 'UTC'",
            "SET LOCAL synchronous_commit='on'",
            "SET LOCAL lock_timeout='1000ms'",
            "SET LOCAL statement_timeout='10000ms'",
        ):
            await connection.exec_driver_sql(sql)
        oid = await connection.scalar(
            text(
                "SELECT oid::bigint FROM pg_catalog.pg_roles WHERE rolname=current_user"
            )
        )
        result = await profile.observe_current0057_g12_writer_profile_v1(
            connection,
            writer=names[0],
            custody=names[1],
            frame_reader=names[2],
            expected_writer_oid=oid,
            read_only=read_only,
        )
        xid = await connection.scalar(
            text("SELECT pg_catalog.pg_current_xact_id()::text")
        )
        return result, xid


async def _counts(owner):
    async with owner.connect() as connection:
        return tuple(
            (
                await connection.execute(
                    text(
                        "SELECT (SELECT count(*) FROM public.g12_invocation_claims),(SELECT count(*) FROM public.g12_invocation_publications)"
                    )
                )
            ).one()
        )


async def test_actual0057_restricted_g12_commits_without_update_and_reopens(
    _isolated_0057_g12_lab,
):
    owner, engines, names = _isolated_0057_g12_lab
    writer = engines[0]
    first, xid1 = await _observe(writer, names)
    assert (await _observe(writer, names, read_only=False))[0] == first
    measured = []

    class MeasuredSession(AsyncSession):
        async def execute(self, statement, *args, **kwargs):
            result = await super().execute(statement, *args, **kwargs)
            sql = str(statement)
            if "public.g12_invocation_" in sql:
                xid = (
                    await super().execute(
                        text("SELECT pg_catalog.pg_current_xact_id()::text")
                    )
                ).scalar_one()
                measured.append((self, xid))
            return result

    factory = async_sessionmaker(writer, class_=MeasuredSession, expire_on_commit=False)
    repo = G12InvocationCustodyRepository(factory)
    claim = await repo.claim(_claim())
    publication = await repo.publication(claim, _publication(claim))
    # Exact original six claim/write/read stages used separate real sessions
    # and committed transaction IDs. These synthetic rows issue no authority.
    assert len(measured) == 6
    assert (
        len({id(item[0]) for item in measured})
        == len({item[1] for item in measured})
        == 6
    )
    reopened = G12InvocationCustodyRepository(factory)
    assert await reopened._read_claim(claim) == claim
    assert (
        await reopened.read_publication(claim, publication.publication_sha256)
        == publication
    )
    second, xid2 = await _observe(writer, names)
    assert xid1 != xid2 and second == first
    assert second["database_head"] == "0057" and second["grant_statements"] == 44
    assert second["execution_authority"] is False and second["admission"] == "DENY"
    assert await _counts(owner) == (1, 1)
    for table in catalog.TABLES:
        for operation in (
            f"UPDATE public.{table} SET invocation_id=invocation_id",
            f"DELETE FROM public.{table}",
            f"TRUNCATE public.{table}",
        ):
            with pytest.raises(DBAPIError):
                async with writer.begin() as connection:
                    await connection.exec_driver_sql(operation)
            assert await _counts(owner) == (1, 1)
        for engine in engines[1:]:
            with pytest.raises(DBAPIError):
                async with engine.begin() as connection:
                    await connection.exec_driver_sql(f"SELECT * FROM public.{table}")
    with pytest.raises(DBAPIError):
        async with owner.begin() as connection:
            await connection.run_sync(lambda sync: _migrate(sync, "0056"))
    assert await _counts(owner) == (1, 1)
    final, xid3 = await _observe(writer, names)
    assert final == second and xid3 not in {xid1, xid2}


async def test_actual0057_profile_denies_each_revocation_extra_grant_and_disabled_guard(
    _isolated_0057_g12_lab,
):
    owner, engines, names = _isolated_0057_g12_lab
    writer = engines[0]
    accepted, _ = await _observe(writer, names)
    assert await _counts(owner) == (0, 0)
    for table in catalog.TABLES:
        for right in ("SELECT", "INSERT"):
            async with owner.begin() as connection:
                await connection.exec_driver_sql(
                    f'REVOKE {right} ON public.{table} FROM "{names[0]}"'
                )
            with pytest.raises(profile.Current0057G12ProfileError):
                await _observe(writer, names)
            async with owner.begin() as connection:
                await connection.exec_driver_sql(
                    f'GRANT {right} ON public.{table} TO "{names[0]}"'
                )
            assert (await _observe(writer, names))[0] == accepted
        async with owner.begin() as connection:
            await connection.exec_driver_sql(
                f'GRANT UPDATE ON public.{table} TO "{names[0]}"'
            )
        with pytest.raises(profile.Current0057G12ProfileError):
            await _observe(writer, names)
        async with owner.begin() as connection:
            await connection.exec_driver_sql(
                f'REVOKE UPDATE ON public.{table} FROM "{names[0]}"'
            )
        assert (await _observe(writer, names))[0] == accepted
        trigger = table + "_immutable"
        async with owner.begin() as connection:
            await connection.exec_driver_sql(
                f"ALTER TABLE public.{table} DISABLE TRIGGER {trigger}"
            )
        with pytest.raises(profile.Current0057G12ProfileError):
            await _observe(writer, names)
        async with owner.begin() as connection:
            await connection.exec_driver_sql(
                f"ALTER TABLE public.{table} ENABLE TRIGGER {trigger}"
            )
        assert (await _observe(writer, names))[0] == accepted
    assert await _counts(owner) == (0, 0)


async def test_actual0057_inherited_roles_writer_control_and_frame_keep_actual_head(
    _isolated_0057_g12_lab,
):
    """Full restricted lower-layer proof with stopped synthetic Control only."""
    from app.core import native_frame_reader_0056_v1 as frame
    from app.core import native_locked_demo_control_0056_v1 as locked
    from app.core import native_role_identity_0056_v2 as identity
    from app.core import native_role_witness_0056_v1 as witness
    from app.demo_automation.native_server_selectors_v2 import (
        ConfiguredNativeDemoFactoriesV2,
    )
    from app.domain.source_primitives import decode
    from app.trade_qualification import demo_control
    from tests.controlled_writer_coverage_fixtures import _store_control_fixture

    owner, engines, _ = _isolated_0057_g12_lab
    factories = tuple(
        async_sessionmaker(engine, autoflush=False, expire_on_commit=False)
        for engine in engines
    )
    selected = ConfiguredNativeDemoFactoriesV2(*factories, engines[1], engines[2])
    roles = await witness.observe_restricted_native_roles_0056_witness_v1(
        selected, source=ROOT, profile=profiles.DB0057
    )
    assert roles.observation.database_head == "0057"
    assert "0057" in roles.schema
    assert roles.startup_admission is roles.execution_authority is False
    stable = await identity.observe_restricted_role_identity_0056_v2(
        selected, source=ROOT, profile=profiles.DB0057
    )
    assert "0057" in stable.schema and stable.execution_authority is False
    scope = demo_control.ControlScope("demo", "1" * 32)
    acquired = demo_control.acquire(
        None,
        scope=scope,
        owner_sha256="a" * 64,
        pins=demo_control.ControlPins("1" * 64, "2" * 64, "3" * 64),
        now=datetime.now(UTC),
    )
    async with owner.begin() as connection:
        await _store_control_fixture(
            connection,
            acquired,
            command_id="5" * 64,
            action="acquire",
            previous_sha256=None,
        )
    async with (
        factories[0]() as session,
        session.begin(),
        locked.held_native_writer_control_0056_v1(
            selected,
            session,
            scope,
            roles=roles,
            purpose=locked.NativeWriterControlPurpose0056.PREPARE_OBSERVATION,
            profile=profiles.DB0057,
        ) as held,
    ):
        held.check_held()
        assert held.observation.database_head == "0057"
        assert held.observation.writer_catalog.database_head == "0057"
        assert held.execution_authority is False
        assert held.observation.control.state.arm_request_id is None
        assert held.observation.control.state.arm_expires_at is None
    async with factories[2]() as session, session.begin():
        for sql in (
            "SET TRANSACTION ISOLATION LEVEL READ COMMITTED",
            "SET LOCAL search_path TO pg_catalog, public, pg_temp",
            "SET LOCAL TIME ZONE 'UTC'",
        ):
            await session.execute(text(sql))
        transaction = session.sync_session.get_transaction()

        class Fence:
            def check(self):
                assert session.sync_session.get_transaction() is transaction
                assert transaction.is_active

        raw = await frame.verify_held_frame_catalog_0056_v1(
            session, Fence(), *factories, profile=profiles.DB0057
        )
        observed = decode(raw, 8 * 1024 * 1024)
        assert observed["database_head"] == "0057"
        assert "current0057" in observed and "current0056" not in observed
        assert observed["execution_authority"] is False
        assert observed["current0057"]["profile"]["native_baseline_admission"] is False


async def test_actual0057_native_custody_six_real_sessions_keep_issued_profile(
    _isolated_0057_g12_lab, monkeypatch
):
    """Real PostgreSQL native hooks; synthetic private-owner handoff, no trade."""
    import asyncio

    from app.core import native_role_witness_0056_v1 as witness
    from app.demo_automation.native_server_selectors_v2 import (
        ConfiguredNativeDemoFactoriesV2,
    )
    from app.domain.source_primitives import canonical, sha
    from app.trade_qualification import native_g12_publication_v2 as publisher

    owner, engines, _ = _isolated_0057_g12_lab
    factories = tuple(
        async_sessionmaker(engine, autoflush=False, expire_on_commit=False)
        for engine in engines
    )
    selected = ConfiguredNativeDemoFactoriesV2(*factories, engines[1], engines[2])
    roles = await witness.observe_restricted_native_roles_0056_witness_v1(
        selected, source=ROOT, profile=profiles.DB0057
    )
    async with factories[0]() as session, session.begin():
        oid = await session.scalar(
            text(
                "SELECT oid::bigint FROM pg_catalog.pg_roles WHERE rolname=current_user"
            )
        )
    start = datetime.now(UTC)
    deadline = start + timedelta(seconds=60)
    values = {
        field.name: None
        for field in fields(publisher.candidate.account.IssuedAccountForG11V1)
        if field.init
    }
    values.update(
        session_factory=factories[0],
        database_head="0057",
        writer_role_oid=oid,
        database_factories=selected,
        database_roles=roles,
    )
    issued = publisher.candidate.account.IssuedAccountForG11V1(**values)
    assert issued.execution_authority is False
    repository = G12InvocationCustodyRepository(factories[0])
    initial = _claim()
    document = initial.values
    document["deadline"] = deadline.isoformat()
    raw = canonical(document)
    claim = publisher.custody.G12InvocationClaim(initial.invocation_id, raw, sha(raw))
    binding = object()
    task = asyncio.current_task()
    state = {
        "candidate": {"facts": SimpleNamespace(created_at=start), "deadline": deadline},
        "custody_sessions": [],
        "custody_pending_session": None,
        "custody_claim_raw": claim.claim_json,
        "custody_issued_writer_pin": ("0057", oid),
    }

    def synthetic_owner_handoff(actual_repository, actual_binding, *, key):
        assert actual_repository is repository and actual_binding is binding
        assert (
            key is publisher.custody._NATIVE_V2_KEY and asyncio.current_task() is task
        )
        return state, issued

    # Only the private in-memory account-owner handoff is synthetic. Both
    # physical head/role/catalog checks per stage, all effective ACL checks,
    # native metadata clocks, INSERTs, commits and six independent XIDs are real.
    # This cannot qualify a public candidate, call R7, reserve risk or submit.
    with monkeypatch.context() as handoff:
        handoff.setattr(publisher, "_custody_context", synthetic_owner_handoff)
        committed = await repository.claim(claim, _native_binding=binding)
        prepared_publication = _publication(committed)
        state["custody_publication_raw"] = prepared_publication.publication_json
        publication = await repository.publication(
            committed, prepared_publication, _native_binding=binding
        )
        transcript = publisher._custody_transcript_sha256(state)
        assert len(state["custody_sessions"]) == 6
        assert len({session[2][4] for session in state["custody_sessions"]}) == 6
        assert state["custody_catalog_pin"][:2] == ("0057", oid)
        assert len(transcript) == 64 and publication.execution_authority is False
        with pytest.raises(
            publisher.custody.G12InvocationCustodyError,
            match="g12_custody_claim_unavailable",
        ):
            await repository.claim(claim, _native_binding=binding)
    assert await _counts(owner) == (1, 1)
