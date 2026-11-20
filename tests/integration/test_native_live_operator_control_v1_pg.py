"""Actual TLS PostgreSQL operator/CAS contracts with DECLARED synthetic seams.

Requires the actual composed DB0058 migration, including financial tables.
No schema staging, venue source, human Arm or Live execution is claimed here.
The seams isolate the DB transaction/role contract from unavailable venue data.
"""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.database.models.okx_live import OkxLivePositionState
from app.database.repositories import native_live_operator_control_v1 as subject
from app.domain import native_live_operator_control_schema_v1 as spec
from app.domain.source_primitives import decode
from tests.integration import test_native_live_ledger_v1_pg as ledger_fixture
from tests.integration.test_gate3_fitted_candidate_custody_pg import (
    _isolated_tls_database_config,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
_live58_lab = ledger_fixture._live58_lab


@pytest.fixture
async def operator_lab(_live58_lab, monkeypatch):
    owner, writer, value = _live58_lab
    _, tls = _isolated_tls_database_config()
    role = "ctcc_live_operator_" + uuid4().hex[:12]
    engine = None
    created = False
    try:
        async with owner.begin() as connection:
            assert (
                await connection.scalar(
                    text(
                        "SELECT to_regclass('public.native_live_financial_epochs_v1') IS NOT NULL"
                    )
                )
                is True
            )
            assert (
                await connection.scalar(
                    text(
                        "SELECT to_regclass('public.native_live_financial_samples_v1') IS NOT NULL"
                    )
                )
                is True
            )
            await connection.exec_driver_sql(
                f'CREATE ROLE "{role}" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS'
            )
            created = True
            await connection.exec_driver_sql(
                f'GRANT CONNECT ON DATABASE "{owner.url.database}" TO "{role}"'
            )
            for statement in spec.live_operator_control_grant_sql_v1(role):
                await connection.exec_driver_sql(statement)
            actor = await connection.scalar(
                text("SELECT oid::bigint FROM pg_catalog.pg_roles WHERE rolname=:role"),
                {"role": role},
            )
            await connection.execute(
                text(
                    "UPDATE public.native_live_controls_v1 SET armed=false,emergency_stop=true,"
                    "arm_id=NULL,arm_expires_at=NULL,per_trade_risk_cap=0.25,portfolio_risk_cap=0.25,margin_cap=25,micro_notional_cap=25"
                )
            )
        engine = create_async_engine(
            owner.url.set(username=role),
            poolclass=NullPool,
            connect_args={"ssl": tls},
            hide_parameters=True,
        )
        factory = async_sessionmaker(engine, expire_on_commit=False)
        pins = subject.LiveOperatorRolePinsV1(
            writer.pins.database_oid,
            writer.pins.owner_oid,
            actor,
            role,
            writer.pins.writer_oid,
        )
        repository = subject.NativeLiveOperatorControlRepositoryV1(factory, pins)
        now = datetime.now(UTC)
        # EXPLICIT synthetic request/source seams: only actual role checks,
        # PostgreSQL serialization, CAS, constraints and readback are accepted.
        monkeypatch.setattr(subject, "_request", lambda request: request)
        monkeypatch.setattr(subject, "_source", lambda request: None)
        monkeypatch.setattr(subject, "_financial", lambda request: None)
        request = subject.LiveOperatorControlRequestV1(
            "clear_stop",
            object(),
            object(),
            value.account_uid,
            value.main_uid,
            value.credential_session_sha256,
            value.source_revision_sha256,
            value.policy_sha256,
            value.process_sha256,
            7,
            None,
            None,
            now + timedelta(seconds=120),
            (Decimal("0.25"), Decimal("0.25"), Decimal(25), Decimal(25)),
            "a" * 64,
        )
        yield owner, repository, request
    finally:
        if engine is not None:
            await engine.dispose()
        if created:
            async with owner.begin() as connection:
                await connection.exec_driver_sql(f'DROP OWNED BY "{role}"')
                await connection.exec_driver_sql(f'DROP ROLE "{role}"')


async def test_actual_operator_clear_arm_and_separate_readback(operator_lab):
    _, repository, request = operator_lab
    clear = await repository.apply_owned_request_v1(request)
    row = decode(clear.row_json, maximum=16384)
    assert row["armed"] is False and row["emergency_stop"] is False
    now = datetime.now(UTC)
    armed = await repository.apply_owned_request_v1(
        replace(
            request,
            action="arm",
            expected_revision=row["revision"],
            arm_id="b" * 64,
            arm_expires_at=now + timedelta(seconds=20),
        )
    )
    independent = await repository.read_control_data_v1(request.account_uid)
    assert independent == armed and armed.execution_authority is False


async def test_actual_two_operator_workers_same_revision_allow_only_one_arm(
    operator_lab,
):
    _, repository, request = operator_lab
    clear = await repository.apply_owned_request_v1(request)
    revision = decode(clear.row_json, maximum=16384)["revision"]
    deadline = datetime.now(UTC) + timedelta(seconds=20)
    requests = tuple(
        replace(
            request,
            action="arm",
            expected_revision=revision,
            arm_id=c * 64,
            arm_expires_at=deadline,
        )
        for c in ("c", "d")
    )
    results = await asyncio.gather(
        *(repository.apply_owned_request_v1(r) for r in requests),
        return_exceptions=True,
    )
    assert sum(type(r) is subject.LiveOperatorControlReadbackV1 for r in results) == 1
    assert sum(isinstance(r, subject.LiveOperatorControlError) for r in results) == 1


async def test_actual_extra_operator_ledger_write_grant_is_refused_before_positive_change(
    operator_lab,
):
    owner, repository, request = operator_lab
    async with owner.begin() as connection:
        await connection.exec_driver_sql(
            f'GRANT INSERT ON public.native_live_intents_v1 TO "{repository.pins.operator_name}"'
        )
    with pytest.raises(subject.LiveOperatorControlError):
        await repository.apply_owned_request_v1(request)
    async with owner.connect() as connection:
        assert (
            await connection.scalar(
                text("SELECT emergency_stop FROM public.native_live_controls_v1")
            )
            is True
        )


async def test_actual_operator_cannot_write_intents_or_financial_history(operator_lab):
    _, repository, _ = operator_lab
    for table in (
        "native_live_intents_v1",
        "native_live_financial_epochs_v1",
        "native_live_financial_samples_v1",
    ):
        async with repository.factory() as session:
            with pytest.raises(DBAPIError):
                await session.execute(text("DELETE FROM public." + table))
            await session.rollback()


async def test_actual_startup_negative_inhibits_without_source_or_legacy_flat_claim(
    operator_lab,
):
    owner, repository, request = operator_lab
    async with owner.begin() as connection:
        await connection.execute(
            text(
                "UPDATE public.native_live_controls_v1 SET emergency_stop=false,armed=true,"
                "arm_id=:arm,arm_expires_at=clock_timestamp()+interval '10 seconds'"
            ),
            {"arm": "f" * 64},
        )
        # Actual legacy uncertainty must not prevent a negative durable stop.
        await connection.execute(
            OkxLivePositionState.__table__.insert().values(
                position_id="legacy-synthetic",
                instrument_id="ETH-USDT-SWAP",
                position_side="net",
                size=1,
                available_size=1,
                unrealized_pnl=0,
                raw={},
            )
        )
    result = await repository.inhibit_configured_account_v1(
        account_uid=request.account_uid, main_uid=request.main_uid
    )
    row = decode(result.row_json, maximum=16384)
    assert (
        row["armed"] is False
        and row["emergency_stop"] is True
        and row["arm_id"] is None
    )
    async with owner.connect() as connection:
        assert (
            await connection.scalar(
                text("SELECT size FROM public.okx_live_position_state")
            )
            == 1
        )


@pytest.mark.parametrize(
    "kind", ("column", "check", "foreign_key", "index", "schema_create")
)
async def test_actual_live4_catalog_drift_never_becomes_operator_readiness(
    operator_lab, kind
):
    owner, repository, request = operator_lab
    async with owner.begin() as connection:
        if kind == "column":
            await connection.exec_driver_sql(
                "ALTER TABLE public.native_live_controls_v1 ADD COLUMN unreviewed text"
            )
        elif kind == "check":
            name, _ = subject.ledger.schema.TABLE_CHECKS["native_live_controls_v1"][0]
            await connection.exec_driver_sql(
                f"ALTER TABLE public.native_live_controls_v1 DROP CONSTRAINT {name}"
            )
            await connection.exec_driver_sql(
                f"ALTER TABLE public.native_live_controls_v1 ADD CONSTRAINT {name} CHECK(environment IN ('live','demo'))"
            )
        elif kind == "foreign_key":
            await connection.exec_driver_sql(
                "ALTER TABLE public.native_live_reservations_v1 DROP CONSTRAINT fk_native_live_reservations_v1_scope"
            )
            await connection.exec_driver_sql(
                "ALTER TABLE public.native_live_reservations_v1 ADD CONSTRAINT fk_native_live_reservations_v1_scope FOREIGN KEY(environment,account_uid) REFERENCES public.native_live_controls_v1(environment,account_uid) ON DELETE CASCADE"
            )
        elif kind == "index":
            await connection.exec_driver_sql(
                "DROP INDEX public.native_live_active_holds_v1"
            )
            await connection.exec_driver_sql(
                "CREATE INDEX native_live_active_holds_v1 ON public.native_live_reservations_v1(environment,account_uid) WHERE state='reserved'"
            )
        else:
            await connection.exec_driver_sql(
                f'GRANT CREATE ON SCHEMA public TO "{repository.pins.operator_name}"'
            )
    with pytest.raises(
        (
            subject.LiveOperatorControlError,
            subject.ledger.LiveLedgerDataError,
            ValueError,
        )
    ):
        await repository.read_control_data_v1(request.account_uid)
