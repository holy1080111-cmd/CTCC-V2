"""Actual isolated PG epoch acceptance, with declared synthetic source seam.

Installs the separate unbound additive SPEC in the disposable DB0058 lab only.
No venue, credentials, source authenticity, Arm or execution acceptance claim.
"""

import asyncio
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.database.models.okx_live import OkxLiveOrderState, OkxLivePositionState
from app.database.repositories import native_live_financial_epoch_v1 as subject
from app.domain import native_live_financial_epoch_schema_v1 as spec
from app.domain.source_primitives import canonical, decode, sha
from app.okx_live import financial_rows_v1 as rows_source
from tests.integration import test_native_live_ledger_v1_pg as live_fixture
from tests.integration.test_gate3_fitted_candidate_custody_pg import (
    _isolated_tls_database_config,
)
from tests.unit import test_native_live_financial_rows_v1 as row_fixture

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
_live58_lab = live_fixture._live58_lab


@pytest.fixture
async def financial_lab(_live58_lab, monkeypatch):
    owner, live_repository, value = _live58_lab
    _, tls = _isolated_tls_database_config()
    role = "ctcc_financial_" + uuid4().hex[:12]
    engine = None
    created = False
    try:
        async with owner.begin() as connection:
            for statement in spec.UPGRADE_SQL:
                await connection.exec_driver_sql(statement)
            await connection.exec_driver_sql(
                f'CREATE ROLE "{role}" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS'
            )
            created = True
            await connection.exec_driver_sql(
                f'GRANT CONNECT ON DATABASE "{owner.url.database}" TO "{role}"'
            )
            for statement in spec.live_financial_collector_grant_sql_v1(role):
                await connection.exec_driver_sql(statement)
            actor = await connection.scalar(
                text("SELECT oid::bigint FROM pg_catalog.pg_roles WHERE rolname=:role"),
                {"role": role},
            )
            await connection.execute(
                text(
                    "UPDATE public.native_live_controls_v1 SET armed=false,emergency_stop=true,arm_id=NULL,arm_expires_at=NULL"
                )
            )
        engine = create_async_engine(
            owner.url.set(username=role),
            poolclass=NullPool,
            connect_args={"ssl": tls},
            hide_parameters=True,
        )
        factory = async_sessionmaker(engine, autoflush=False, expire_on_commit=False)
        pins = subject.LiveFinancialCollectorRolePinsV1(
            live_repository.pins.database_oid,
            live_repository.pins.owner_oid,
            actor,
            role,
        )
        source = rows_source._derive_original_financial_rows_v1(row_fixture._view())
        tail = canonical(
            {
                "schema": "ctcc.native_live.financial_tail_data.v1",
                "environment": "live",
                "execution_authority": False,
                "account_complete": False,
            }
        )
        native_bridge = canonical(
            {
                "schema": "ctcc.native_live.owned_source_data.v1",
                "environment": "live",
                "origin": "https://openapi.okx.com",
                "account_identity_sha256": sha(
                    canonical(["live", value.account_uid, value.main_uid])
                ),
                "financial_tail_sha256": sha(tail),
                "raw_bank_sha256": "2" * 64,
                "execution_authority": False,
                "account_complete": False,
                "fixture_purpose": "synthetic_pg_only",
            }
        )
        document = decode(source.receipt_json, maximum=65536)
        document["source_revision_sha256"] = sha(native_bridge)
        document["financial_tail_sha256"] = sha(tail)
        source = replace(
            source,
            account_uid=value.account_uid,
            main_uid=value.main_uid,
            source_revision_sha256=sha(native_bridge),
            credential_session_sha256=value.credential_session_sha256,
            receipt_json=canonical(document),
        )

        # Deliberately synthetic source seam; only PG behavior is under test.
        class SyntheticSource:
            pass

        source_owner = SyntheticSource()
        lease = SimpleNamespace(
            verify_current=lambda: None,
            _pin=SimpleNamespace(
                origin="https://openapi.okx.com",
                credentials=(
                    "synthetic-pg-key",
                    "synthetic-pg-secret",
                    "synthetic-pg-passphrase",
                ),
            ),
        )
        monkeypatch.setitem(subject.source._SOURCES, source_owner, {"lease": lease})
        monkeypatch.setattr(
            subject.rows_source,
            "read_owned_live_financial_rows_data_v1",
            lambda owner: source,
        )
        monkeypatch.setattr(
            subject.source,
            "_replay_owned_live_source_v1",
            lambda owner, **kwargs: {
                "uid": source.account_uid,
                "main_uid": source.main_uid,
                "source_revision_sha256": source.source_revision_sha256,
                "credential_session_sha256": source.credential_session_sha256,
                "receipt_json": native_bridge,
                "financial_tail_json": tail,
            },
        )
        await subject.preflight_live_financial_collector_v1(
            factory=factory, role_pins=pins
        )
        yield owner, factory, pins, source_owner
    finally:
        if engine is not None:
            await engine.dispose()
        if created:
            # The enclosing source fixture destroys the disposable DB, then its
            # Live role. Drop only our collector grants/role while DB still exists.
            async with owner.begin() as connection:
                await connection.exec_driver_sql(f'DROP OWNED BY "{role}"')
                await connection.exec_driver_sql(f'DROP ROLE "{role}"')


async def _counts(owner):
    async with owner.connect() as connection:
        return tuple(
            [
                await connection.scalar(text("SELECT count(*) FROM public." + table))
                for table in spec.TABLES
            ]
        )


async def test_live_epoch_two_workers_create_one_immutable_genesis_and_original_readback(
    financial_lab,
):
    owner, factory, pins, source_owner = financial_lab
    results = await asyncio.gather(
        *(
            subject.observe_owned_live_financial_epoch_v1(
                source_owner, factory=factory, role_pins=pins
            )
            for _ in range(2)
        ),
        return_exceptions=True,
    )
    assert (
        sum(type(value) is subject.OwnedLiveFinancialEpochV1 for value in results) == 1
    )
    assert sum(type(value) is subject.LiveFinancialEpochError for value in results) == 1
    assert await _counts(owner) == (1, 1)


async def test_live_epoch_is_append_only_and_collector_cannot_arm_clear_stop_or_delete(
    financial_lab,
):
    owner, factory, pins, source_owner = financial_lab
    await subject.observe_owned_live_financial_epoch_v1(
        source_owner, factory=factory, role_pins=pins
    )
    for statement in (
        "UPDATE public.native_live_financial_samples_v1 SET equity=1",
        "DELETE FROM public.native_live_financial_epochs_v1",
        "UPDATE public.native_live_controls_v1 SET emergency_stop=false,armed=true",
    ):
        with pytest.raises(DBAPIError):
            async with factory() as session, session.begin():
                await session.execute(text(statement))
    assert await _counts(owner) == (1, 1)


async def test_live_epoch_late_readback_failure_keeps_committed_prefix_and_never_resets(
    financial_lab, monkeypatch
):
    owner, factory, pins, source_owner = financial_lab
    actual = subject._prefix
    calls = 0

    async def late(session, epoch_id):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ValueError("synthetic_independent_readback_failure")
        return await actual(session, epoch_id)

    monkeypatch.setattr(subject, "_prefix", late)
    with pytest.raises(subject.LiveFinancialReadbackRequired):
        await subject.observe_owned_live_financial_epoch_v1(
            source_owner, factory=factory, role_pins=pins
        )
    assert await _counts(owner) == (1, 1)


async def test_live_epoch_check_body_drift_is_denied_even_when_constraint_name_is_preserved(
    financial_lab,
):
    owner, factory, pins, _ = financial_lab
    name = spec.TABLE_CHECKS[spec.TABLES[1]][0][0]
    async with owner.begin() as connection:
        await connection.exec_driver_sql(
            f'ALTER TABLE public.{spec.TABLES[1]} DROP CONSTRAINT "{name}"'
        )
        await connection.exec_driver_sql(
            f'ALTER TABLE public.{spec.TABLES[1]} ADD CONSTRAINT "{name}" CHECK(true)'
        )
    with pytest.raises(subject.LiveFinancialEpochError):
        await subject.preflight_live_financial_collector_v1(
            factory=factory, role_pins=pins
        )


async def test_live_epoch_nonempty_downgrade_preserves_every_saved_sample(
    financial_lab,
):
    owner, factory, pins, source_owner = financial_lab
    await subject.observe_owned_live_financial_epoch_v1(
        source_owner, factory=factory, role_pins=pins
    )
    with pytest.raises(DBAPIError):
        async with owner.begin() as connection:
            for statement in spec.DOWNGRADE_SQL:
                await connection.exec_driver_sql(statement)
    assert await _counts(owner) == (1, 1)


async def test_live_epoch_legacy_ambiguous_intent_blocks_even_when_native_source_reports_flat(
    financial_lab,
):
    owner, factory, pins, source_owner = financial_lab
    async with owner.begin() as connection:
        await connection.execute(
            text("""INSERT INTO public.okx_live_execution_intents
 (idempotency_key,request_hash,action,status,instrument_id,detail_codes)
 VALUES('legacySyntheticBlocked',:hash,'cancel_order','ambiguous','ETH-USDT-SWAP','[]'::jsonb)"""),
            {"hash": "a" * 64},
        )
    with pytest.raises(subject.LiveFinancialEpochError):
        await subject.observe_owned_live_financial_epoch_v1(
            source_owner, factory=factory, role_pins=pins
        )
    assert await _counts(owner) == (0, 0)


@pytest.mark.parametrize("kind", ("position", "pending_order", "latch"))
async def test_live_epoch_legacy_local_exposure_pending_or_latch_cannot_be_ignored(
    financial_lab, kind
):
    owner, factory, pins, source_owner = financial_lab
    async with owner.begin() as connection:
        if kind == "position":
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
        elif kind == "pending_order":
            await connection.execute(
                OkxLiveOrderState.__table__.insert().values(
                    order_id="legacy-synthetic",
                    instrument_id="ETH-USDT-SWAP",
                    side="buy",
                    order_type="limit",
                    state="live",
                    size=1,
                    accumulated_fill_size=0,
                    reduce_only=False,
                    attached_algo_orders=[],
                    raw={},
                )
            )
        else:
            await connection.execute(
                text("""INSERT INTO public.okx_live_sync_checkpoints
 (id,status,order_count,position_count,algo_order_count,safety_latched,safety_latch_code,safety_latch_version,safety_latched_at,details)
 VALUES(1,'safety_latched',0,0,0,true,'synthetic_legacy_stop',1,clock_timestamp(),'{}'::jsonb)""")
            )
    with pytest.raises(subject.LiveFinancialEpochError):
        await subject.observe_owned_live_financial_epoch_v1(
            source_owner, factory=factory, role_pins=pins
        )
    assert await _counts(owner) == (0, 0)


async def test_live_epoch_original_legacy_global_lock_prevents_concurrent_legacy_submission(
    financial_lab,
):
    owner, factory, pins, source_owner = financial_lab
    async with owner.connect() as lock_owner:
        await lock_owner.execute(text("SELECT pg_advisory_lock(1680010)"))
        try:
            with pytest.raises(subject.LiveFinancialEpochError):
                await subject.observe_owned_live_financial_epoch_v1(
                    source_owner, factory=factory, role_pins=pins
                )
        finally:
            await lock_owner.execute(text("SELECT pg_advisory_unlock(1680010)"))
    assert await _counts(owner) == (0, 0)


async def test_live_epoch_collector_rehashed_insert_is_not_an_authenticated_prefix(
    financial_lab,
):
    owner, factory, pins, source_owner = financial_lab
    await subject.observe_owned_live_financial_epoch_v1(
        source_owner, factory=factory, role_pins=pins
    )
    async with factory() as session, session.begin():
        first_raw = await session.scalar(
            text(
                "SELECT sample_json FROM public.native_live_financial_samples_v1 WHERE sequence=1"
            )
        )
        first = decode(first_raw.encode("ascii"), maximum=2097152)
        first.update(
            sequence=2,
            previous_sha256=sha(first_raw.encode("ascii")),
            observed_at=(row_fixture.NOW + timedelta(seconds=1)).isoformat(),
            source_revision_sha256="f" * 64,
            equity="1",
            loss_streak=0,
        )
        forged = canonical(first)
        await session.execute(
            text("""INSERT INTO public.native_live_financial_samples_v1(epoch_id,sequence,previous_sha256,observed_at,source_revision_sha256,equity,sample_json,sample_sha256)
 VALUES(:epoch,2,:previous,:observed,:source,1,:raw,:sha)"""),
            {
                "epoch": first["epoch_id"],
                "previous": first["previous_sha256"],
                "observed": row_fixture.NOW + timedelta(seconds=1),
                "source": "f" * 64,
                "raw": forged.decode("ascii"),
                "sha": sha(forged),
            },
        )
    with pytest.raises(subject.LiveFinancialEpochError):
        await subject.observe_owned_live_financial_epoch_v1(
            source_owner, factory=factory, role_pins=pins
        )
    assert await _counts(owner) == (1, 2)
