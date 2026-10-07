"""Disposable PostgreSQL acceptance for the narrow 0028 publication seam.

An explicit isolated DATABASE_URL must allow generated databases and roles.
This fixture runs 0024, 0026 and 0028 DDL directly; it is not full-chain
Alembic or deployment acceptance. No public network or market request occurs.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.mie.validation.gate3_capture_schedule_pin import (
    Gate3CaptureSchedulePinRepository,
)
from app.mie.validation.gate3_capture_schedule_publication_ack import (
    Gate3CaptureSchedulePublicationAckRepository,
    Gate3SchedulePublicationAckError,
)
from tests.durable_migration_fixtures import load_migration
from tests.unit.mie.test_gate3_capture_schedule import valid_schedule

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def migrate(connection, revision: str, direction: str) -> None:
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    module = load_migration(revision)
    module.op = Operations(MigrationContext.configure(connection))
    getattr(module, direction)()


def pin_record(seal, schedule) -> dict[str, str]:
    coordinate = schedule.coordinate_plan
    return {
        "schedule_sha256": schedule.canonical_sha256(),
        "seal_sha256": seal.canonical_sha256(),
        "coordinate_plan_sha256": coordinate.canonical_sha256(),
        "window_key": coordinate.window_key(),
        "holdout_id": coordinate.holdout_id,
        "window_start": coordinate.start_at.isoformat(),
        "window_end": coordinate.end_at.isoformat(),
        "planned_at": schedule.planned_at.isoformat(),
        "coordinate_plan_json": coordinate.canonical_json(),
        "schedule_json": schedule.canonical_json(),
    }


async def raw_pin(connection, record) -> None:
    await connection.execute(
        text("SELECT public.gate3_capture_schedule_append(CAST(:record AS jsonb))"),
        {"record": json.dumps(record, sort_keys=True)},
    )


async def raw_ack(connection, record) -> None:
    await connection.execute(
        text("""
          SELECT public.gate3_capture_schedule_ack_append(
            :schedule_sha,:seal_sha,:coordinate_sha,:window_key,:holdout_id)
        """),
        {
            "schedule_sha": record["schedule_sha256"],
            "seal_sha": record["seal_sha256"],
            "coordinate_sha": record["coordinate_plan_sha256"],
            "window_key": record["window_key"],
            "holdout_id": record["holdout_id"],
        },
    )


@pytest.fixture
async def isolated_database():
    raw_url = os.environ.get("DATABASE_URL", "")
    if not raw_url:
        pytest.skip("explicit isolated DATABASE_URL required")
    test_database = "ctcc_gate3_ack_" + uuid4().hex
    pin_role = "ctcc_pin_" + uuid4().hex[:18]
    ack_role = "ctcc_ack_" + uuid4().hex[:18]
    assert re.fullmatch(r"ctcc_gate3_ack_[a-f0-9]{32}", test_database)
    assert re.fullmatch(r"ctcc_(pin|ack)_[a-f0-9]{18}", pin_role)
    assert re.fullmatch(r"ctcc_(pin|ack)_[a-f0-9]{18}", ack_role)
    admin = create_async_engine(raw_url, poolclass=NullPool)
    db_engine = pin_engine = ack_engine = None
    created_db = False
    created_roles: list[str] = []
    try:
        async with admin.connect() as connection:
            await connection.execution_options(isolation_level="AUTOCOMMIT")
            await connection.execute(text(f"CREATE DATABASE {test_database}"))
        created_db = True
        db_url = make_url(raw_url).set(database=test_database)
        db_engine = create_async_engine(db_url, poolclass=NullPool)
        async with db_engine.begin() as connection:
            for revision in ("0024", "0026", "0028"):
                await connection.run_sync(migrate, revision, "upgrade")
        for role in (pin_role, ack_role):
            async with admin.begin() as connection:
                await connection.execute(text(f"CREATE ROLE {role} LOGIN"))
            created_roles.append(role)
        async with db_engine.begin() as connection:
            for role in (pin_role, ack_role):
                await connection.execute(
                    text(f"GRANT USAGE ON SCHEMA public TO {role}")
                )
            for function in (
                "gate3_capture_schedule_append(jsonb)",
                "gate3_capture_schedule_read(text)",
            ):
                await connection.execute(
                    text(f"GRANT EXECUTE ON FUNCTION public.{function} TO {pin_role}")
                )
            for function in (
                "gate3_capture_schedule_ack_append(text,text,text,text,text)",
                "gate3_capture_schedule_ack_read(text)",
            ):
                await connection.execute(
                    text(f"GRANT EXECUTE ON FUNCTION public.{function} TO {ack_role}")
                )
        pin_engine = create_async_engine(
            db_url.set(username=pin_role, password=None), poolclass=NullPool
        )
        ack_engine = create_async_engine(
            db_url.set(username=ack_role, password=None), poolclass=NullPool
        )
        pin_repo = Gate3CaptureSchedulePinRepository(
            async_sessionmaker(pin_engine, expire_on_commit=False)
        )
        ack_repo = Gate3CaptureSchedulePublicationAckRepository(
            async_sessionmaker(ack_engine, expire_on_commit=False), pin_repo
        )
        await pin_repo.verify_role()
        await ack_repo.verify_role()
        yield pin_repo, ack_repo, db_engine, pin_engine, ack_engine, ack_role
    finally:
        if ack_engine is not None:
            await ack_engine.dispose()
        if pin_engine is not None:
            await pin_engine.dispose()
        if db_engine is not None:
            await db_engine.dispose()
        if created_db:
            async with admin.connect() as connection:
                await connection.execution_options(isolation_level="AUTOCOMMIT")
                await connection.execute(text(f"DROP DATABASE {test_database}"))
        for role in reversed(created_roles):
            async with admin.begin() as connection:
                await connection.execute(text(f"DROP ROLE {role}"))
        await admin.dispose()


async def test_ack_requires_committed_pin_and_replays_after_restart(
    isolated_database, monkeypatch
):
    pin_repo, ack_repo, db_engine, pin_engine, ack_engine, _ = isolated_database
    seal, schedule = valid_schedule()
    record = pin_record(seal, schedule)
    with pytest.raises(
        Gate3SchedulePublicationAckError, match="schedule_pin_replay_failed"
    ):
        await ack_repo.acknowledge(
            expected_schedule_sha256=record["schedule_sha256"], seal=seal
        )
    # An in-progress 0026 insert is invisible to the independent ACK login.
    async with pin_engine.connect() as pin_connection:
        transaction = await pin_connection.begin()
        try:
            await raw_pin(pin_connection, record)
            with pytest.raises(DBAPIError, match="gate3_schedule_ack_identity_denied"):
                async with ack_engine.begin() as ack_connection:
                    await raw_ack(ack_connection, record)
            await transaction.commit()
        finally:
            if transaction.is_active:
                await transaction.rollback()
    result = await ack_repo.acknowledge(
        expected_schedule_sha256=record["schedule_sha256"], seal=seal
    )
    assert result.server_clock_ordered_committed_pin_observation
    assert not result.trusted_clock_verified
    assert (
        schedule.planned_at
        <= result.schedule_recorded_at
        <= result.acknowledged_at
        < schedule.coordinate_plan.start_at
    )
    assert result.database_readback_at >= result.acknowledged_at
    assert not result.independently_protected
    assert not result.predictive_oos_eligible
    assert not result.execution_authority
    retry = await ack_repo.acknowledge(
        expected_schedule_sha256=record["schedule_sha256"], seal=seal
    )
    assert retry.acknowledged_at == result.acknowledged_at
    original_pin_read = pin_repo.read

    async def late_pin_read(*, expected_schedule_sha256, seal):
        genuine = await original_pin_read(
            expected_schedule_sha256=expected_schedule_sha256, seal=seal
        )
        # Inject a late observation after a genuine DB read, without waiting
        # for the real future window or altering the immutable ACK.
        return replace(
            genuine,
            database_readback_at=schedule.coordinate_plan.start_at,
        )

    with monkeypatch.context() as context:
        context.setattr(pin_repo, "read", late_pin_read)
        late_retry = await ack_repo.acknowledge(
            expected_schedule_sha256=record["schedule_sha256"], seal=seal
        )
    assert late_retry.acknowledged_at == result.acknowledged_at
    async with ack_engine.connect() as connection:
        access = (
            await connection.execute(
                text("""
                  SELECT session_user=current_user AS direct_login,
                    has_table_privilege(current_user,
                      'public.gate3_capture_schedule_pins','INSERT')
                      AS pin_insert,
                    has_table_privilege(current_user,
                      'public.gate3_capture_schedule_publication_acks','SELECT')
                      AS ack_select,
                    has_table_privilege(current_user,
                      'public.gate3_capture_schedule_publication_acks','INSERT')
                      AS ack_insert,
                    has_function_privilege(current_user,
                      'public.gate3_capture_schedule_append(jsonb)'::regprocedure,
                      'EXECUTE') AS pin_append,
                    has_function_privilege(current_user,
                      'public.public_receipt_witness_append(jsonb)'::regprocedure,
                      'EXECUTE') AS witness_append
                """)
            )
        ).one()
    assert access.direct_login
    assert not any(
        (
            access.pin_insert,
            access.ack_select,
            access.ack_insert,
            access.pin_append,
            access.witness_append,
        )
    )
    with pytest.raises(DBAPIError, match="permission denied"):
        async with ack_engine.begin() as connection:
            await connection.execute(
                text("SELECT * FROM public.gate3_capture_schedule_publication_acks")
            )
    with pytest.raises(DBAPIError, match="permission denied"):
        async with ack_engine.begin() as connection:
            await connection.execute(
                text("""
                  INSERT INTO public.gate3_capture_schedule_publication_acks
                    (schedule_sha256) VALUES (:sha)
                """),
                {"sha": record["schedule_sha256"]},
            )
    with pytest.raises(DBAPIError, match="permission denied"):
        async with ack_engine.begin() as connection:
            await raw_pin(connection, record)
    restarted_engine = create_async_engine(ack_engine.url, poolclass=NullPool)
    try:
        restarted = Gate3CaptureSchedulePublicationAckRepository(
            async_sessionmaker(restarted_engine, expire_on_commit=False), pin_repo
        )
        replay = await restarted.read(
            expected_schedule_sha256=record["schedule_sha256"], seal=seal
        )
        assert replay.schedule == result.schedule
        assert replay.acknowledged_at == result.acknowledged_at
        assert replay.database_readback_at >= result.database_readback_at
    finally:
        await restarted_engine.dispose()
    with pytest.raises(DBAPIError, match="gate3_schedule_ack_immutable"):
        async with db_engine.begin() as connection:
            await connection.execute(
                text("""
                  UPDATE public.gate3_capture_schedule_publication_acks
                  SET holdout_id='forged' WHERE schedule_sha256=:sha
                """),
                {"sha": record["schedule_sha256"]},
            )
    with pytest.raises(DBAPIError, match="gate3_schedule_ack_downgrade_requires_empty"):
        async with db_engine.begin() as connection:
            await connection.run_sync(migrate, "0028", "downgrade")


async def test_same_transaction_schedule_writer_cannot_ack(isolated_database):
    _, ack_repo, db_engine, _, ack_engine, ack_role = isolated_database
    seal, schedule = valid_schedule()
    record = pin_record(seal, schedule)
    async with db_engine.begin() as connection:
        await connection.execute(
            text(
                "GRANT EXECUTE ON FUNCTION "
                "public.gate3_capture_schedule_append(jsonb) TO " + ack_role
            )
        )
    try:
        with pytest.raises(
            Gate3SchedulePublicationAckError,
            match="restricted_schedule_ack_role_required",
        ):
            await ack_repo.verify_role()
        with pytest.raises(DBAPIError, match="gate3_schedule_ack_role_denied"):
            async with ack_engine.begin() as connection:
                await raw_pin(connection, record)
                await connection.execute(text("SAVEPOINT after_pin"))
                await raw_ack(connection, record)
    finally:
        async with db_engine.begin() as connection:
            await connection.execute(
                text(
                    "REVOKE EXECUTE ON FUNCTION "
                    "public.gate3_capture_schedule_append(jsonb) FROM " + ack_role
                )
            )
    with pytest.raises(
        Gate3SchedulePublicationAckError, match="schedule_pin_replay_failed"
    ):
        await ack_repo.read(
            expected_schedule_sha256=record["schedule_sha256"], seal=seal
        )


async def test_witness_table_grant_cannot_be_used_by_ack_role(isolated_database):
    pin_repo, ack_repo, db_engine, _, ack_engine, ack_role = isolated_database
    seal, schedule = valid_schedule()
    record = pin_record(seal, schedule)
    await pin_repo.publish(schedule=schedule, seal=seal)
    async with db_engine.begin() as connection:
        await connection.execute(
            text(
                "GRANT SELECT ON TABLE public.public_receipt_witness_revisions TO "
                + ack_role
            )
        )
    try:
        with pytest.raises(
            Gate3SchedulePublicationAckError,
            match="restricted_schedule_ack_role_required",
        ):
            await ack_repo.verify_role()
        with pytest.raises(DBAPIError, match="gate3_schedule_ack_role_denied"):
            async with ack_engine.begin() as connection:
                await raw_ack(connection, record)
    finally:
        async with db_engine.begin() as connection:
            await connection.execute(
                text(
                    "REVOKE SELECT ON TABLE public.public_receipt_witness_revisions "
                    "FROM " + ack_role
                )
            )
    await ack_repo.verify_role()


async def test_search_path_shadow_and_other_schema_create_are_denied(
    isolated_database,
):
    pin_repo, ack_repo, db_engine, pin_engine, ack_engine, ack_role = isolated_database
    pin_role = pin_engine.url.username
    assert pin_role is not None
    async with db_engine.begin() as connection:
        await connection.execute(text("CREATE SCHEMA ctcc_ack_shadow"))
        await connection.execute(
            text("""
          CREATE FUNCTION ctcc_ack_shadow.has_function_privilege(
            pg_catalog.name,pg_catalog.regprocedure,pg_catalog.text)
          RETURNS boolean LANGUAGE sql AS $$ SELECT false $$
        """)
        )
        for role in (pin_role, ack_role):
            await connection.execute(
                text("GRANT USAGE ON SCHEMA ctcc_ack_shadow TO " + role)
            )
    for engine, guard, function in (
        (
            pin_engine,
            pin_repo._role_guard,
            "public.gate3_capture_schedule_append(jsonb)",
        ),
        (
            ack_engine,
            ack_repo._role_guard,
            "public.gate3_capture_schedule_ack_read(text)",
        ),
    ):
        async with engine.connect() as connection:
            await connection.execute(text("SET search_path=ctcc_ack_shadow,pg_catalog"))
            shadowed = await connection.scalar(
                text("""
                  SELECT has_function_privilege(current_user,
                    CAST(:function AS pg_catalog.regprocedure),
                    'EXECUTE')
                """),
                {"function": function},
            )
            assert shadowed is False
            actual = await connection.scalar(
                text("""
                  SELECT pg_catalog.has_function_privilege(current_user,
                    CAST(:function AS pg_catalog.regprocedure),'EXECUTE')
                """),
                {"function": function},
            )
            assert actual is True
            async with AsyncSession(bind=connection) as session:
                await guard(session)
    seal, schedule = valid_schedule()
    record = pin_record(seal, schedule)
    await pin_repo.publish(schedule=schedule, seal=seal)
    async with db_engine.begin() as connection:
        await connection.execute(
            text("GRANT CREATE ON SCHEMA ctcc_ack_shadow TO " + ack_role)
        )
    try:
        with pytest.raises(
            Gate3SchedulePublicationAckError,
            match="restricted_schedule_ack_role_required",
        ):
            await ack_repo.verify_role()
        with pytest.raises(DBAPIError, match="gate3_schedule_ack_role_denied"):
            async with ack_engine.begin() as connection:
                await raw_ack(connection, record)
    finally:
        async with db_engine.begin() as connection:
            await connection.execute(
                text("REVOKE CREATE ON SCHEMA ctcc_ack_shadow FROM " + ack_role)
            )
    await ack_repo.verify_role()


async def test_ack_exact_identity_and_concurrent_append_fail_fast(isolated_database):
    pin_repo, ack_repo, _, _, ack_engine, _ = isolated_database
    seal, schedule = valid_schedule()
    record = pin_record(seal, schedule)
    await pin_repo.publish(schedule=schedule, seal=seal)
    with pytest.raises(DBAPIError, match="gate3_schedule_ack_identity_denied"):
        async with ack_engine.begin() as connection:
            await raw_ack(connection, {**record, "seal_sha256": "f" * 64})
    async with ack_engine.connect() as first:
        transaction = await first.begin()
        try:
            await raw_ack(first, record)
            with pytest.raises(DBAPIError, match="gate3_schedule_ack_busy"):
                async with ack_engine.begin() as second:
                    await raw_ack(second, record)
            await transaction.commit()
        finally:
            if transaction.is_active:
                await transaction.rollback()
    replay = await ack_repo.read(
        expected_schedule_sha256=record["schedule_sha256"], seal=seal
    )
    assert replay.server_clock_ordered_committed_pin_observation
    assert not replay.trusted_clock_verified


async def test_raw_ack_of_noncanonical_pin_is_denied_on_combined_replay(
    isolated_database,
):
    _, ack_repo, _, pin_engine, ack_engine, _ = isolated_database
    seal, schedule = valid_schedule()
    record = pin_record(seal, schedule)
    changed_json = json.dumps(
        json.loads(record["schedule_json"]), indent=2, sort_keys=True
    )
    assert changed_json != record["schedule_json"]
    record["schedule_json"] = changed_json
    record["schedule_sha256"] = hashlib.sha256(changed_json.encode()).hexdigest()
    async with pin_engine.begin() as connection:
        await raw_pin(connection, record)
    # Raw SQL only proves the stored bytes and hash were already committed.
    # It cannot certify that those bytes are the canonical Python contract.
    async with ack_engine.begin() as connection:
        await raw_ack(connection, record)
    async with ack_engine.connect() as connection:
        row = (
            await connection.execute(
                text("""
                  SELECT * FROM public.gate3_capture_schedule_ack_read(:sha)
                """),
                {"sha": record["schedule_sha256"]},
            )
        ).one()
        assert row.schedule_sha256 == record["schedule_sha256"]
    with pytest.raises(
        Gate3SchedulePublicationAckError, match="schedule_pin_replay_failed"
    ):
        await ack_repo.read(
            expected_schedule_sha256=record["schedule_sha256"], seal=seal
        )


async def test_empty_0028_downgrade_and_reupgrade(isolated_database):
    _, _, db_engine, _, _, _ = isolated_database
    async with db_engine.begin() as connection:
        await connection.run_sync(migrate, "0028", "downgrade")
    async with db_engine.connect() as connection:
        assert (
            await connection.scalar(
                text(
                    "SELECT to_regclass('public.gate3_capture_schedule_publication_acks')"
                )
            )
            is None
        )
    async with db_engine.begin() as connection:
        await connection.run_sync(migrate, "0028", "upgrade")
    async with db_engine.connect() as connection:
        assert (
            await connection.scalar(
                text(
                    "SELECT to_regclass('public.gate3_capture_schedule_publication_acks')"
                )
            )
            is not None
        )
