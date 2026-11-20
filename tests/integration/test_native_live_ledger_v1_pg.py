"""Authored actual isolated TLS DB0058 ledger acceptance; no venue source claim.

Requires root's genuine additive 0058 migration and the existing disposable TLS
lab. Source acquisition is deliberately synthetic here; only PostgreSQL locks,
caps, immutable binding, rollback and durable readback are under acceptance.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.database.repositories import native_live_ledger_v1 as subject
from app.domain import native_live_ledger_schema_v1 as spec
from app.domain.source_primitives import canonical, decode, sha
from tests.integration.test_gate3_fitted_candidate_custody_pg import (
    _isolated_tls_database_config,
)
from tests.unit.test_native_live_source_ledger_v1 import _value

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
ROOT = Path(__file__).resolve().parents[2]


def _upgrade_actual58(connection):
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("path_separator", "os")
    config.set_main_option("script_location", str(ROOT / "migrations"))
    script = ScriptDirectory.from_config(config)
    assert script.get_heads() == ["0058"]
    assert script.get_revision("0058").down_revision == "0057"
    context = MigrationContext.configure(
        connection, opts={"fn": lambda heads, _: script._upgrade_revs("0058", heads)}
    )
    assert context.get_current_heads() == ()
    with Operations.context(context), context.begin_transaction():
        context.run_migrations()
    assert context.get_current_heads() == ("0058",)


@pytest.fixture
async def _live58_lab(monkeypatch):
    url, tls = _isolated_tls_database_config()
    nonce = uuid4().hex[:12]
    database = "ctcc_live58_" + nonce
    role = "ctcc_live_ledger_" + nonce
    admin = create_async_engine(
        url, poolclass=NullPool, connect_args={"ssl": tls}, hide_parameters=True
    )
    owner = writer = None
    created_db = created_role = False
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
            await connection.run_sync(_upgrade_actual58)
            await connection.exec_driver_sql(
                f'CREATE ROLE "{role}" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS'
            )
            created_role = True
            await connection.exec_driver_sql(
                f'GRANT CONNECT ON DATABASE "{database}" TO "{role}"'
            )
            for sql in spec.live_ledger_grant_sql_v1(role):
                await connection.exec_driver_sql(sql)
            writer_oid = await connection.scalar(
                text("SELECT oid::bigint FROM pg_catalog.pg_roles WHERE rolname=:role"),
                {"role": role},
            )
            db_oid, owner_oid = (
                await connection.execute(
                    text(
                        "SELECT oid::bigint,datdba::bigint FROM pg_catalog.pg_database WHERE datname=current_database()"
                    )
                )
            ).one()
            value = _value(deadline=datetime.now(UTC) + timedelta(seconds=25))
            await connection.execute(
                text("""INSERT INTO public.native_live_controls_v1
             (environment,account_uid,main_uid,credential_session_sha256,live_writer_oid,revision,source_revision_sha256,policy_sha256,process_sha256,
              emergency_stop,armed,arm_id,arm_expires_at,lease_until,per_trade_risk_cap,portfolio_risk_cap,margin_cap,micro_notional_cap)
             VALUES('live',:uid,:uid,:credential,:writer,7,:source,:policy,:process,false,true,:arm,clock_timestamp()+interval '40 seconds',
              clock_timestamp()+interval '45 seconds',0.5,1,5,20)"""),
                {
                    "uid": value.account_uid,
                    "credential": value.credential_session_sha256,
                    "writer": writer_oid,
                    "source": value.source_revision_sha256,
                    "policy": value.policy_sha256,
                    "process": value.process_sha256,
                    "arm": value.arm_id,
                },
            )
        writer = create_async_engine(
            url.set(database=database, username=role),
            poolclass=NullPool,
            connect_args={"ssl": tls},
            hide_parameters=True,
        )
        factory = async_sessionmaker(writer, autoflush=False, expire_on_commit=False)
        repository = subject.NativeLiveLedgerDataRepositoryV1(
            factory, subject.LiveLedgerRolePinsV1(db_oid, owner_oid, writer_oid, role)
        )
        # Declared synthetic source seam: this fixture is not Live source/Arm/qualification evidence.
        monkeypatch.setattr(
            subject,
            "_source",
            lambda *a: {
                "current_readback": SimpleNamespace(
                    usdt_available_equity=SimpleNamespace(available_equity=Decimal(100))
                )
            },
        )
        yield owner, repository, value
    finally:
        if writer is not None:
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
                if created_role:
                    await connection.exec_driver_sql(f'DROP ROLE "{role}"')
        finally:
            await admin.dispose()


async def _counts(owner):
    async with owner.connect() as connection:
        return tuple(
            [
                await connection.scalar(text("SELECT count(*) FROM public." + table))
                for table in spec.TABLES[1:]
            ]
        )


async def test_actual58_two_workers_same_event_one_durable_intent_and_independent_readback(
    _live58_lab,
):
    import asyncio

    owner, repository, value = _live58_lab
    results = await asyncio.gather(
        *(
            repository.reserve_and_commit_intent_data_v1(value, owned_source=object())
            for _ in range(2)
        ),
        return_exceptions=True,
    )
    assert sum(type(result) is dict for result in results) == 1
    assert sum(type(result) is subject.LiveLedgerDataError for result in results) == 1
    assert await _counts(owner) == (1, 1, 1)
    accepted = next(result for result in results if type(result) is dict)
    assert (
        accepted["execution_authority"] is accepted["qualification_verified"] is False
    )
    altered = value.model_copy(
        update={
            "report_sha256": "9" * 64,
            "client_order_id": "CTCCLother",
            "protection_client_id": "CTCCAother",
        }
    )
    with pytest.raises(subject.LiveLedgerDataError):
        await repository.reserve_and_commit_intent_data_v1(
            altered, owned_source=object()
        )
    assert await _counts(owner) == (1, 1, 1)


async def test_actual58_forged_intent_authority_rolls_back_hold_and_journal(
    _live58_lab, monkeypatch
):
    owner, repository, value = _live58_lab
    original = subject.exact_live_intent_document_v1

    def forged(value):
        rid, raw, _ = original(value)
        document = decode(raw, maximum=65536)
        document["execution_authority"] = True
        changed = canonical(document)
        return rid, changed, sha(changed)

    monkeypatch.setattr(subject, "exact_live_intent_document_v1", forged)
    with pytest.raises(subject.LiveLedgerDataError):
        await repository.reserve_and_commit_intent_data_v1(value, owned_source=object())
    assert await _counts(owner) == (0, 0, 0)


async def test_actual58_postcommit_readback_failure_keeps_intent_and_never_retries(
    _live58_lab, monkeypatch
):
    owner, repository, value = _live58_lab

    async def unavailable(*a, **k):
        raise OSError("declared isolated failure")

    monkeypatch.setattr(repository, "read_intent_data_v1", unavailable)
    with pytest.raises(subject.LiveLedgerReadbackRequired):
        await repository.reserve_and_commit_intent_data_v1(value, owned_source=object())
    assert await _counts(owner) == (1, 1, 1)
    with pytest.raises(subject.LiveLedgerDataError):
        await repository.reserve_and_commit_intent_data_v1(value, owned_source=object())
    assert await _counts(owner) == (1, 1, 1)


async def test_actual58_estop_or_expiry_refuses_before_creating_any_hold(_live58_lab):
    owner, repository, value = _live58_lab
    async with owner.begin() as connection:
        await connection.exec_driver_sql(
            "UPDATE public.native_live_controls_v1 SET armed=false,emergency_stop=true,arm_id=NULL,arm_expires_at=NULL,revision=8"
        )
    with pytest.raises(subject.LiveLedgerDataError):
        await repository.reserve_and_commit_intent_data_v1(value, owned_source=object())
    assert await _counts(owner) == (0, 0, 0)
