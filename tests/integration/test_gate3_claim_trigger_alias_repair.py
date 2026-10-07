"""Disposable PostgreSQL acceptance for the already-applied 0029 trigger.

The broken function is restored only inside this synthetic test database.
The 0032 migration must repair admission without changing its durable rows,
trigger identity, privileges, or the 0030 preregistration read barrier.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.mie.validation.gate3_preregistration_seal_observer import (
    Gate3PreregistrationSealObserver,
)
from app.mie.validation.prospective_capture_schedule import build_capture_schedule
from tests.integration.test_gate3_canonical_schedule_claim import (
    isolated_claim_database,  # noqa: F401 - pytest discovers imported fixture
    migrate,
    pin_record,
)
from tests.integration.test_gate3_committed_preregistration_seal import (
    append_seal,
    isolated_prereg_database,  # noqa: F401 - pytest discovers imported fixture
    seal_record,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def _guard_identity(connection):
    return (
        await connection.execute(
            text("""
              SELECT p.oid::text,p.proowner::text,
                     coalesce(p.proacl::text,'') AS acl,
                     p.prosecdef,p.provolatile,p.proconfig::text,
                     t.tgfoid::text
              FROM pg_catalog.pg_proc p
              JOIN pg_catalog.pg_trigger t ON t.tgfoid=p.oid
              WHERE p.oid =
                'public.gate3_schedule_claim_insert_guard()'::pg_catalog.regprocedure
                AND t.tgname='gate3_schedule_claim_insert_guard'
            """)
        )
    ).one()


@pytest.mark.parametrize("isolated_claim_database", ["noncanonical"], indirect=True)
async def test_0032_repairs_existing_guard_without_qualifying_legacy_evidence(
    isolated_claim_database,  # noqa: F811 - fixture injected by pytest
):
    seal, schedule, poison, db_engine, pin_repo, _, _, _ = isolated_claim_database
    record = json.dumps(pin_record(seal, schedule), separators=(",", ":"))
    async with db_engine.begin() as connection:
        # Emulate the installed 0029 trigger body before its source correction.
        installed = await connection.scalar(
            text("""
              SELECT pg_catalog.pg_get_functiondef(
                'public.gate3_schedule_claim_insert_guard()'::pg_catalog.regprocedure)
            """)
        )
        assert installed is not None
        assert "legacy_row.classification" in installed
        deployed = installed.replace("legacy_row", "old")
        assert deployed != installed
        await connection.execute(text(deployed))
        before_identity = await _guard_identity(connection)
        before_legacy = (
            await connection.execute(
                text("""
                  SELECT schedule_sha256,classification,observed_at
                  FROM public.gate3_capture_schedule_legacy_inventory
                """)
            )
        ).all()
        assert len(before_legacy) == 1
        assert before_legacy[0].schedule_sha256 == poison["schedule_sha256"]
        assert before_legacy[0].classification == "noncanonical"

    with pytest.raises(DBAPIError) as failure:
        async with pin_repo.session_factory() as session, session.begin():
            await session.execute(
                text(
                    "SELECT public.gate3_capture_schedule_claim_append("
                    "CAST(:record AS jsonb))"
                ),
                {"record": record},
            )
    origins = (failure.value.orig, getattr(failure.value.orig, "__cause__", None))
    assert "42703" in {getattr(origin, "sqlstate", None) for origin in origins}
    async with db_engine.connect() as connection:
        assert (
            await connection.scalar(
                text("SELECT count(*) FROM public.gate3_capture_schedule_pins")
            )
            == 1
        )
        assert (
            await connection.scalar(
                text("SELECT count(*) FROM public.gate3_capture_schedule_key_claims")
            )
            == 0
        )

    async with db_engine.begin() as connection:
        await connection.run_sync(migrate, "0030", "upgrade")
        await connection.run_sync(migrate, "0031", "upgrade")
        await connection.run_sync(migrate, "0032", "upgrade")
        assert await _guard_identity(connection) == before_identity
        assert (
            await connection.execute(
                text("""
                  SELECT schedule_sha256,classification,observed_at
                  FROM public.gate3_capture_schedule_legacy_inventory
                """)
            )
        ).all() == before_legacy
        repaired = await connection.scalar(
            text("""
              SELECT pg_catalog.pg_get_functiondef(
                'public.gate3_schedule_claim_insert_guard()'::pg_catalog.regprocedure)
            """)
        )
        assert repaired is not None
        assert "legacy_row.classification" in repaired
        assert "old.classification" not in repaired

    # 0030's independent preregistration proof remains mandatory. This old
    # pre-ACK schedule cannot gain a claim merely because 0032 repaired SQL.
    with pytest.raises(DBAPIError, match="gate3_prereg_claim_seal_missing"):
        async with pin_repo.session_factory() as session, session.begin():
            await session.execute(
                text(
                    "SELECT public.gate3_capture_schedule_claim_append("
                    "CAST(:record AS jsonb))"
                ),
                {"record": record},
            )
    async with db_engine.connect() as connection:
        assert (
            await connection.scalar(
                text("SELECT count(*) FROM public.gate3_capture_schedule_key_claims")
            )
            == 0
        )
        assert (
            await connection.execute(
                text("""
                  SELECT schedule_sha256,classification,observed_at
                  FROM public.gate3_capture_schedule_legacy_inventory
                """)
            )
        ).all() == before_legacy
    with pytest.raises(
        DBAPIError, match="gate3_claim_trigger_alias_repair_downgrade_requires_empty"
    ):
        async with db_engine.begin() as connection:
            await connection.run_sync(migrate, "0032", "downgrade")


async def test_0032_repaired_guard_accepts_fresh_prereg_acknowledged_claim(
    isolated_prereg_database,  # noqa: F811 - fixture injected by pytest
):
    (
        seal,
        old_schedule,
        legacy_sha,
        db_engine,
        seal_engine,
        observer_engine,
        _,
        pin_repo,
        _,
    ) = isolated_prereg_database
    assert legacy_sha is None
    async with db_engine.begin() as connection:
        await connection.run_sync(migrate, "0031", "upgrade")
        await connection.run_sync(migrate, "0032", "upgrade")

    await append_seal(seal_engine, seal_record(seal, old_schedule))
    observer = Gate3PreregistrationSealObserver(
        async_sessionmaker(observer_engine, expire_on_commit=False)
    )
    witnessed = await observer.read_seal(
        expected_seal_sha256=seal.canonical_sha256(),
        expected_coordinate_plan_sha256=old_schedule.coordinate_plan.canonical_sha256(),
        coordinate_plan=old_schedule.coordinate_plan,
    )
    accepted = await observer.acknowledge(
        expected_seal_sha256=seal.canonical_sha256(),
        expected_coordinate_plan_sha256=old_schedule.coordinate_plan.canonical_sha256(),
        coordinate_plan=old_schedule.coordinate_plan,
    )
    assert accepted.acknowledged_at >= witnessed.seal_recorded_at
    schedule = build_capture_schedule(
        seal=seal,
        coordinate_plan=old_schedule.coordinate_plan,
        planned_at=accepted.database_readback_at,
    )
    pinned = await pin_repo.publish(schedule=schedule, seal=seal)
    assert pinned.schedule_sha256 == schedule.canonical_sha256()
    assert pinned.execution_authority is False
    async with db_engine.connect() as connection:
        assert (
            await connection.scalar(
                text("SELECT count(*) FROM public.gate3_capture_schedule_key_claims")
            )
            == 1
        )
        # A claim without the later publication ACK remains unreadable.
        assert (
            await connection.scalar(
                text(
                    "SELECT count(*) FROM public.gate3_capture_schedule_claim_read(:sha)"
                ),
                {"sha": schedule.canonical_sha256()},
            )
            == 0
        )


async def test_0032_empty_downgrade_and_reupgrade_keep_repaired_body(
    isolated_prereg_database,  # noqa: F811 - fixture injected by pytest
):
    _, _, _, db_engine, _, _, _, _, _ = isolated_prereg_database
    async with db_engine.begin() as connection:
        await connection.run_sync(migrate, "0031", "upgrade")
        await connection.run_sync(migrate, "0032", "upgrade")
        identity = await _guard_identity(connection)
        repaired = await connection.scalar(
            text("""
              SELECT pg_catalog.pg_get_functiondef(
                'public.gate3_schedule_claim_insert_guard()'::pg_catalog.regprocedure)
            """)
        )
        assert repaired is not None and "legacy_row.classification" in repaired
    async with db_engine.begin() as connection:
        await connection.run_sync(migrate, "0032", "downgrade")
        assert await _guard_identity(connection) == identity
        assert (
            await connection.scalar(
                text("""
              SELECT pg_catalog.pg_get_functiondef(
                'public.gate3_schedule_claim_insert_guard()'::pg_catalog.regprocedure)
            """)
            )
            == repaired
        )
    async with db_engine.begin() as connection:
        await connection.run_sync(migrate, "0032", "upgrade")
        assert await _guard_identity(connection) == identity
